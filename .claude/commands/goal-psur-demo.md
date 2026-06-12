---
description: Goal G-3b — turn this PSUR generator into the public streaming web demo, integrated with Regulatory Ground (grkbSamarticusv1) for graph-grounded decision traces
---

# /goal-psur-demo — Data → Draft in 20 Minutes (Python side)

The **canonical, full instructions** for this goal live in the companion repo:
`grkbSamarticusv1/.claude/commands/goal-psur-demo.md`. Read that file first —
it defines the demo script, the decided architecture, the grkb-side
deliverables (API bridge, walkthrough UI, landing-page feature, trace
bridging), constraints, and acceptance criteria. If `grkbSamarticusv1` is not
checked out alongside this repo, get it added before starting.

This file summarizes only what gets built **in this repo**:

1. **Event emitter** (`psur-generator/events.py`) — `ProgressEmitter` with
   `progress` (phase/section lifecycle) and `decision` events
   (`{decision, inputs_summary, output, reason, regulatory_basis}`).
   Instrument: denominator selection, PSUR-vs-PMSR cadence (UK MDR
   44ZL/44ZM), UK MDR activation, IMDRF auto-coding assignments, RACT O1–O5
   occurrence codes, UCL / Western Electric verdicts, audit-remediation
   outcomes, final 331-point validation. Wired through `main.py` and
   `agents/orchestrator.py` as an optional parameter; CLI unchanged with a
   no-op emitter. Never invent regulatory citations.
2. **FastAPI service** (`psur-generator/server/`) — `POST /runs` (content
   editable, **structure strictly validated** against `data/templates/`
   specs; Pydantic per input type), `GET /runs/{id}/events` (SSE,
   replay-from-start), `GET /runs/{id}/artifacts*` (DOCX/JSON/statistics/
   traceability/validation report). Sync pipeline on a worker thread;
   `MAX_CONCURRENT_RUNS` env.
3. **Mock data pack audit** — inputs must exercise every FormQAR-054 section
   A–M plus the UK MDR path: serious incidents, FSCA, a real Western Electric
   trend trip, UK sales rows, ≥1 uncoded complaint, and a Section J
   literature-results input (add template + mock if missing). Update
   `data/templates/INPUT_README.md` for anything added.
4. **Tests** — introduce `pytest`: emitter ordering, structural validation
   accept/reject, end-to-end mock run with a stubbed LLM client asserting the
   expected decision-event set.

Hard constraints (from the canonical file): deterministic-first statistics
stays intact; editable content / locked structure enforced server-side; LLM
keys server-side only; no stubs.
