"""In-memory run registry + worker-thread execution of the real pipeline."""
import os
import re
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import BASE_DIR
from events import QueueEmitter
from pipeline.run import run_generation

# The pipeline entry point — module attribute so tests can monkeypatch it.
PIPELINE_RUNNER = run_generation

RUNS_DIR = BASE_DIR / "data" / "runs"

# Run ids are uuid4().hex[:12] (see RunRegistry.create). The same pattern
# gates on-disk workspace deletion so a path can never escape RUNS_DIR.
_RUN_ID_RE = re.compile(r"^[0-9a-f]{12}$")


def max_concurrent_runs() -> int:
    try:
        return max(1, int(os.environ.get("MAX_CONCURRENT_RUNS", "1")))
    except ValueError:
        return 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RunRecord:
    run_id: str
    workspace: Path
    period: Dict[str, str]
    emitter: QueueEmitter = field(default_factory=QueueEmitter)
    status: str = "queued"  # queued | running | completed | failed
    created_at: str = field(default_factory=_utc_now)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    thread: Optional[threading.Thread] = None

    @property
    def input_dir(self) -> Path:
        return self.workspace / "input"

    @property
    def output_dir(self) -> Path:
        return self.workspace / "output"

    def artifacts_meta(self) -> List[Dict[str, Any]]:
        if self.result:
            return list(self.result.get("artifacts_meta", []))
        return []

    def artifact_path(self, name: str) -> Optional[Path]:
        if not self.result:
            return None
        path = (self.result.get("artifacts") or {}).get(name)
        if path is None:
            return None
        path = Path(path)
        return path if path.exists() else None


class RunRegistry:
    """Thread-safe in-memory registry of demo runs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: Dict[str, RunRecord] = {}

    def get(self, run_id: str) -> Optional[RunRecord]:
        with self._lock:
            return self._runs.get(run_id)

    def list_all(self) -> List[RunRecord]:
        with self._lock:
            return list(self._runs.values())

    def known_ids(self) -> set:
        with self._lock:
            return set(self._runs)

    def remove(self, run_id: str) -> str:
        """Remove a finished run from the registry.

        Returns "removed", "active" (queued/running — not removable),
        or "missing" (not known to this process).
        """
        with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                return "missing"
            if record.status in ("queued", "running"):
                return "active"
            del self._runs[run_id]
            return "removed"

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for r in self._runs.values()
                       if r.status in ("queued", "running"))

    def create(self, period: Dict[str, str]) -> Optional[RunRecord]:
        """Create a run record, or None when the service is saturated."""
        with self._lock:
            active = sum(1 for r in self._runs.values()
                         if r.status in ("queued", "running"))
            if active >= max_concurrent_runs():
                return None
            run_id = uuid.uuid4().hex[:12]
            record = RunRecord(
                run_id=run_id,
                workspace=RUNS_DIR / run_id,
                period=dict(period),
            )
            self._runs[run_id] = record
            return record

    def start(self, record: RunRecord) -> None:
        """Run the real pipeline on a worker thread."""
        def _worker() -> None:
            record.status = "running"
            record.started_at = _utc_now()
            try:
                result = PIPELINE_RUNNER(
                    start_date=record.period["start"],
                    end_date=record.period["end"],
                    input_dir=record.input_dir,
                    output_dir=record.output_dir,
                    emitter=record.emitter,
                )
                record.result = result
                record.status = "completed"
            except Exception as ex:  # noqa: BLE001 — surfaced via the run record
                record.error = str(ex)
                record.status = "failed"
            finally:
                record.finished_at = _utc_now()
                record.emitter.close()

        thread = threading.Thread(
            target=_worker, name=f"psur-run-{record.run_id}", daemon=True
        )
        record.thread = thread
        thread.start()


REGISTRY = RunRegistry()


def is_valid_run_id(run_id: str) -> bool:
    return bool(_RUN_ID_RE.fullmatch(run_id))


def orphaned_workspaces() -> List[Path]:
    """On-disk run workspaces with no in-memory record — left behind by a
    previous server process (the registry does not survive restarts).
    Newest first."""
    if not RUNS_DIR.is_dir():
        return []
    known = REGISTRY.known_ids()
    return sorted(
        (p for p in RUNS_DIR.iterdir()
         if p.is_dir() and _RUN_ID_RE.fullmatch(p.name) and p.name not in known),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def delete_workspace(run_id: str) -> bool:
    """Remove a run's on-disk workspace. Returns True if a directory was
    removed. Ids that don't match the run-id pattern are refused so the
    path can never point outside RUNS_DIR."""
    if not is_valid_run_id(run_id):
        return False
    path = RUNS_DIR / run_id
    if path.is_dir():
        shutil.rmtree(path)
        return True
    return False
