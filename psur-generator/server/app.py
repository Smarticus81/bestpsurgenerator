"""FastAPI service wrapping the PSUR generation pipeline.

Endpoints:
  GET  /defaults                      — editable mock pack + locked structure specs
  POST /runs                          — validate inputs, start a run (worker thread)
  GET  /runs                          — run history (this process + orphaned workspaces)
  GET  /runs/{run_id}                 — run status
  DELETE /runs/{run_id}               — delete a finished run + its workspace
  GET  /runs/{run_id}/events          — SSE stream (replay from seq 0, then live)
  GET  /runs/{run_id}/artifacts       — list generated artifacts
  GET  /runs/{run_id}/artifacts/{name}— download one artifact

Start with:  uvicorn server.app:app  (from psur-generator/)
LLM API keys are read server-side from the environment / .env, as for the CLI.
"""
import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from server.models import (
    ArtifactList,
    RunCreated,
    RunDeleted,
    RunList,
    RunRequest,
    RunStatus,
    RunSummary,
)
from server.runs import (
    REGISTRY,
    RunRecord,
    delete_workspace,
    orphaned_workspaces,
)
from server.specs import (
    JSON_SPECS,
    TABLE_SPECS,
    build_defaults,
    validate_json_value,
    validate_table_rows,
    write_run_inputs,
)

app = FastAPI(
    title="PSUR Generator Service",
    description=(
        "Data → Draft: EU MDR Art. 86 / MDCG 2022-21 PSUR generation with "
        "streamed progress and regulatory decision events."
    ),
    version="1.0.0",
)


@app.get("/healthz")
def healthz() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/defaults")
def get_defaults() -> Dict[str, Any]:
    """The editable mock data pack + the locked structure of every input."""
    return build_defaults()


@app.post("/runs", response_model=RunCreated, status_code=201)
def create_run(request: RunRequest) -> RunCreated:
    """Validate the submitted inputs (content editable, structure locked)
    and start a generation run on a worker thread."""
    # ── Structural validation: precise per-field messages → 422 ─────
    errors = []
    normalized: Dict[str, Any] = {}
    for name, payload in request.inputs.items():
        if name in TABLE_SPECS:
            if not hasattr(payload, "rows"):
                errors.append({
                    "loc": f"inputs.{name}",
                    "msg": f"input '{name}' is a table input and must be sent as "
                           f"{{\"rows\": [...]}}",
                })
                continue
            errors.extend(validate_table_rows(name, payload.rows))
            normalized[name] = payload.rows
        else:  # JSON input
            if not hasattr(payload, "value"):
                errors.append({
                    "loc": f"inputs.{name}",
                    "msg": f"input '{name}' is a JSON input and must be sent as "
                           f"{{\"value\": {{...}}}}",
                })
                continue
            errors.extend(validate_json_value(name, payload.value))
            normalized[name] = payload.value

    if errors:
        raise HTTPException(status_code=422, detail=errors)

    # ── Concurrency guard ────────────────────────────────────────────
    record = REGISTRY.create(period={
        "start": request.period.start.isoformat(),
        "end": request.period.end.isoformat(),
    })
    if record is None:
        raise HTTPException(status_code=409, detail="demo_busy")

    # ── Materialise the validated inputs into the run workspace ──────
    write_run_inputs(record.input_dir, normalized)

    # ── Run the real pipeline on a worker thread ─────────────────────
    REGISTRY.start(record)
    return RunCreated(run_id=record.run_id)


@app.get("/runs", response_model=RunList)
def list_runs() -> RunList:
    """Run history: every run known to this process, plus orphaned on-disk
    workspaces left by previous processes (status "orphaned"). Newest first."""
    summaries = []
    for record in REGISTRY.list_all():
        result = record.result or {}
        summaries.append(RunSummary(
            run_id=record.run_id,
            status=record.status,
            created_at=record.created_at,
            finished_at=record.finished_at,
            device_name=result.get("device_name"),
            report_type=result.get("report_type"),
        ))
    for path in orphaned_workspaces():
        summaries.append(RunSummary(
            run_id=path.name,
            status="orphaned",
            created_at=datetime.fromtimestamp(
                path.stat().st_mtime, timezone.utc).isoformat(),
        ))
    summaries.sort(key=lambda s: s.created_at or "", reverse=True)
    return RunList(runs=summaries)


@app.delete("/runs/{run_id}", response_model=RunDeleted)
def delete_run(run_id: str) -> RunDeleted:
    """Delete a finished run: drop it from the registry and remove its
    on-disk workspace. Also deletes orphaned workspaces from previous
    processes. Queued/running runs cannot be deleted (409)."""
    outcome = REGISTRY.remove(run_id)
    if outcome == "active":
        raise HTTPException(
            status_code=409,
            detail=f"run '{run_id}' is still queued or running — "
                   "wait for it to finish before deleting",
        )
    workspace_removed = delete_workspace(run_id)
    if outcome == "missing" and not workspace_removed:
        raise HTTPException(status_code=404, detail=f"unknown run '{run_id}'")
    return RunDeleted(run_id=run_id, workspace_removed=workspace_removed)


def _get_record_or_404(run_id: str) -> RunRecord:
    record = REGISTRY.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"unknown run '{run_id}'")
    return record


@app.get("/runs/{run_id}", response_model=RunStatus)
def get_run(run_id: str) -> RunStatus:
    record = _get_record_or_404(run_id)
    result = record.result or {}
    validation = None
    if record.status == "completed":
        validation = {
            "passed": result.get("is_valid", False),
            "error_count": len(result.get("errors", []) or []),
        }
    return RunStatus(
        run_id=record.run_id,
        status=record.status,
        created_at=record.created_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
        device_name=result.get("device_name"),
        report_type=result.get("report_type"),
        error=record.error,
        validation=validation,
    )


@app.get("/runs/{run_id}/events")
def stream_events(run_id: str) -> StreamingResponse:
    """SSE stream of run events: replays all events from seq 0, then
    streams live until the run finishes. Named events:
        event: <kind>\\ndata: <json>\\n\\n
    """
    record = _get_record_or_404(run_id)

    def _gen() -> Iterator[str]:
        for ev in record.emitter.iter_events(start_seq=0):
            payload = json.dumps(ev, default=str)
            yield f"event: {ev.get('kind', 'message')}\nid: {ev.get('seq')}\ndata: {payload}\n\n"

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/runs/{run_id}/artifacts", response_model=ArtifactList)
def list_artifacts(run_id: str) -> ArtifactList:
    record = _get_record_or_404(run_id)
    return ArtifactList(
        run_id=record.run_id,
        status=record.status,
        artifacts=record.artifacts_meta(),
    )


@app.get("/runs/{run_id}/artifacts/{name}")
def download_artifact(run_id: str, name: str) -> FileResponse:
    record = _get_record_or_404(run_id)
    path = record.artifact_path(name)
    if path is None:
        raise HTTPException(
            status_code=404,
            detail=f"artifact '{name}' not found for run '{run_id}'",
        )
    meta = {a["name"]: a for a in record.artifacts_meta()}
    media_type = meta.get(name, {}).get("content_type", "application/octet-stream")
    return FileResponse(path, media_type=media_type, filename=name)
