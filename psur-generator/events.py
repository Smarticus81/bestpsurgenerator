"""Progress and decision event emission for the PSUR pipeline.

The pipeline emits two classes of events while it runs:

  progress — phase / section lifecycle, used by UIs to drive a stepper.
  decision — every regulatory decision point, with a human-readable reason
             and the canonical citation strings that drove it. A downstream
             consumer maps the citations onto an obligation knowledge graph,
             so ONLY the strings in CANONICAL_CITATIONS may ever be used.

Every event is wrapped in a thread-safe envelope:

    {seq: int (monotonic per run), ts: ISO8601 UTC,
     kind: "progress" | "decision" | "error" | "complete", ...}

Emitters:
  ProgressEmitter — base class; dispatches to an optional callback.
  NoopEmitter     — default for the CLI; drops everything.
  QueueEmitter    — keeps full history and supports blocking iteration,
                    used by the FastAPI server for SSE replay + live stream.
"""
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterator, List, Optional

# ── Pipeline phases (progress events) ────────────────────────────────
PHASES = (
    "discovery",
    "parsing",
    "device_context",
    "imdrf_coding",
    "statistics",
    "charts",
    "generation",
    "audit",
    "remediation",
    "validation",
    "rendering",
    "artifacts",
)

# ── Canonical citation strings ───────────────────────────────────────
# The downstream consumer maps these onto knowledge-graph obligation IDs.
# NEVER invent new strings; if no regulation genuinely drives a decision,
# use an empty list.
CANONICAL_CITATIONS = frozenset({
    "EU MDR Art. 86",
    "MDCG 2022-21",
    "UK MDR 2024 Reg 44ZE",
    "UK MDR 2024 Reg 44ZF",
    "UK MDR 2024 Reg 44ZH",
    "UK MDR 2024 Reg 44ZI",
    "UK MDR 2024 Reg 44ZJ",
    "UK MDR 2024 Reg 44ZK",
    "UK MDR 2024 Reg 44ZL",
    "UK MDR 2024 Reg 44ZM",
    "UK MDR 2024 Reg 44ZN",
    "UK MDR 2024 Reg 44ZQ",
    "ISO 14971",
    "IMDRF Annex A",
    "IMDRF Annex F",
})


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProgressEmitter:
    """Thread-safe event emitter.

    ``emit(event)`` stamps the envelope (monotonic ``seq``, UTC ``ts``)
    under a lock so events from concurrent threads are strictly ordered,
    then dispatches. Subclasses override ``_dispatch``.
    """

    def __init__(self, callback: Optional[Callable[[Dict[str, Any]], None]] = None):
        self._lock = threading.Lock()
        self._seq = 0
        self._callback = callback

    # ── Core ──────────────────────────────────────────────────────
    def emit(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Stamp and dispatch a raw event dict. Returns the stamped event."""
        with self._lock:
            stamped = dict(event)
            stamped["seq"] = self._seq
            self._seq += 1
            stamped["ts"] = _utc_now_iso()
            self._dispatch(stamped)
        return stamped

    def _dispatch(self, event: Dict[str, Any]) -> None:
        if self._callback is not None:
            self._callback(event)

    # ── progress ──────────────────────────────────────────────────
    def progress(self, phase: str, status: str,
                 section: Optional[str] = None,
                 detail: Optional[str] = None) -> None:
        ev: Dict[str, Any] = {"kind": "progress", "phase": phase, "status": status}
        if section is not None:
            ev["section"] = section
        if detail is not None:
            ev["detail"] = detail
        self.emit(ev)

    def phase_started(self, phase: str, section: Optional[str] = None,
                      detail: Optional[str] = None) -> None:
        self.progress(phase, "started", section=section, detail=detail)

    def phase_completed(self, phase: str, section: Optional[str] = None,
                        detail: Optional[str] = None) -> None:
        self.progress(phase, "completed", section=section, detail=detail)

    # ── decision ──────────────────────────────────────────────────
    def decision(self, decision: str, *,
                 inputs_summary: Dict[str, Any],
                 output: Any,
                 reason: str,
                 regulatory_basis: List[str],
                 section: Optional[str] = None) -> None:
        """Emit a traced decision event.

        ``regulatory_basis`` entries are validated against the canonical
        citation list: unknown strings are dropped (never forwarded) so the
        trace consumer never receives an inventable citation.
        """
        basis = [c for c in (regulatory_basis or []) if c in CANONICAL_CITATIONS]
        ev: Dict[str, Any] = {
            "kind": "decision",
            "decision": decision,
            "inputs_summary": inputs_summary,
            "output": output,
            "reason": reason,
            "regulatory_basis": basis,
        }
        if section is not None:
            ev["section"] = section
        self.emit(ev)

    # ── error / complete ──────────────────────────────────────────
    def error(self, message: str) -> None:
        self.emit({"kind": "error", "message": message})

    def complete(self, artifacts: List[Dict[str, Any]],
                 validation: Dict[str, Any]) -> None:
        self.emit({"kind": "complete", "artifacts": artifacts,
                   "validation": validation})


class NoopEmitter(ProgressEmitter):
    """Default emitter — drops every event (the CLI uses this)."""

    def __init__(self) -> None:
        super().__init__(callback=None)

    def _dispatch(self, event: Dict[str, Any]) -> None:  # noqa: D102
        pass


class QueueEmitter(ProgressEmitter):
    """Emitter that records full event history for replay + live streaming.

    The FastAPI server attaches one per run:
      - ``events_since(seq)`` returns history for replay-on-(re)connect.
      - ``iter_events(start_seq)`` blocks for live events until ``close()``.
    """

    def __init__(self) -> None:
        super().__init__(callback=None)
        self._events: List[Dict[str, Any]] = []
        self._cond = threading.Condition()
        self._closed = False

    def _dispatch(self, event: Dict[str, Any]) -> None:
        with self._cond:
            self._events.append(event)
            self._cond.notify_all()

    @property
    def closed(self) -> bool:
        with self._cond:
            return self._closed

    def close(self) -> None:
        """Mark the stream finished (no more events will arrive)."""
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def events_since(self, seq: int = 0) -> List[Dict[str, Any]]:
        """Snapshot of all events with ``seq >= seq``."""
        with self._cond:
            return [e for e in self._events if e["seq"] >= seq]

    def iter_events(self, start_seq: int = 0,
                    poll_timeout: float = 1.0) -> Iterator[Dict[str, Any]]:
        """Yield events from ``start_seq``; replay history then block for
        live events until the emitter is closed."""
        next_seq = start_seq
        while True:
            with self._cond:
                pending = [e for e in self._events if e["seq"] >= next_seq]
                if not pending:
                    if self._closed:
                        return
                    self._cond.wait(timeout=poll_timeout)
                    pending = [e for e in self._events if e["seq"] >= next_seq]
                    if not pending and self._closed:
                        return
            for ev in pending:
                next_seq = ev["seq"] + 1
                yield ev
