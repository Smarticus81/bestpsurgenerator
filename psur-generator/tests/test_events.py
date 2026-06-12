"""Emitter tests: envelope, ordering, thread-safety, replay semantics."""
import threading
from datetime import datetime

from events import (
    CANONICAL_CITATIONS,
    NoopEmitter,
    ProgressEmitter,
    QueueEmitter,
)


def test_envelope_fields_and_seq_monotonicity():
    received = []
    em = ProgressEmitter(callback=received.append)

    em.phase_started("discovery", detail="data/input")
    em.decision(
        "denominator_selection",
        inputs_summary={"is_reusable": False},
        output="units_distributed",
        reason="Single-use device.",
        regulatory_basis=["MDCG 2022-21"],
        section="C_volume_of_sales_and_population_exposure",
    )
    em.error("boom")
    em.complete(artifacts=[], validation={"passed": True, "error_count": 0})

    assert [e["seq"] for e in received] == [0, 1, 2, 3]
    assert [e["kind"] for e in received] == ["progress", "decision", "error", "complete"]
    for e in received:
        # ts must be parseable ISO-8601 UTC
        datetime.fromisoformat(e["ts"])

    progress = received[0]
    assert progress["phase"] == "discovery"
    assert progress["status"] == "started"

    decision = received[1]
    assert decision["decision"] == "denominator_selection"
    assert decision["regulatory_basis"] == ["MDCG 2022-21"]
    assert decision["section"] == "C_volume_of_sales_and_population_exposure"
    assert decision["reason"]


def test_decision_drops_non_canonical_citations():
    received = []
    em = ProgressEmitter(callback=received.append)
    em.decision(
        "x",
        inputs_summary={},
        output="y",
        reason="r",
        regulatory_basis=["MDCG 2022-21", "Totally Made Up Reg 99", "ISO 14971"],
    )
    assert received[0]["regulatory_basis"] == ["MDCG 2022-21", "ISO 14971"]
    assert "Totally Made Up Reg 99" not in CANONICAL_CITATIONS


def test_noop_emitter_swallows_everything():
    em = NoopEmitter()
    em.phase_started("statistics")
    em.error("nothing happens")
    stamped = em.emit({"kind": "progress", "phase": "x", "status": "started"})
    assert stamped["seq"] == 2  # still stamps, just doesn't dispatch


def test_thread_safety_and_strict_ordering():
    em = QueueEmitter()
    n_threads, per_thread = 8, 50

    def worker(tid):
        for i in range(per_thread):
            em.emit({"kind": "progress", "phase": "generation",
                     "status": "started", "detail": f"t{tid}-{i}"})

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    em.close()

    events = em.events_since(0)
    seqs = [e["seq"] for e in events]
    assert len(events) == n_threads * per_thread
    assert seqs == list(range(n_threads * per_thread))  # contiguous + ordered
    assert len(set(seqs)) == len(seqs)  # unique


def test_queue_emitter_replay_then_live_then_close():
    em = QueueEmitter()
    em.phase_started("discovery")
    em.phase_completed("discovery")

    collected = []
    done = threading.Event()

    def consumer():
        for ev in em.iter_events(start_seq=0, poll_timeout=0.05):
            collected.append(ev)
        done.set()

    t = threading.Thread(target=consumer)
    t.start()

    em.phase_started("parsing")
    em.complete(artifacts=[], validation={"passed": False, "error_count": 3})
    em.close()

    assert done.wait(timeout=5.0)
    t.join(timeout=5.0)
    kinds = [e["kind"] for e in collected]
    assert kinds == ["progress", "progress", "progress", "complete"]
    assert [e["seq"] for e in collected] == [0, 1, 2, 3]

    # Replay-from-zero after close still returns the full history
    assert [e["seq"] for e in em.events_since(0)] == [0, 1, 2, 3]
