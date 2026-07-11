"""Run-history + deletion tests: GET /runs and DELETE /runs/{id}."""
import threading
import time

import pytest
from fastapi.testclient import TestClient

import server.runs as runs_mod
from server.app import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def isolated_registry(monkeypatch, tmp_path):
    """Fresh registry + RUNS_DIR under tmp_path so tests never touch the
    real data/runs and never see runs from other tests."""
    registry = runs_mod.RunRegistry()
    monkeypatch.setattr(runs_mod, "REGISTRY", registry)
    monkeypatch.setattr(runs_mod, "RUNS_DIR", tmp_path)
    # server.app imported REGISTRY by value — patch its reference too.
    import server.app as app_mod
    monkeypatch.setattr(app_mod, "REGISTRY", registry)
    return registry


@pytest.fixture
def fast_pipeline(monkeypatch):
    def _fake_runner(*, start_date, end_date, input_dir, output_dir,
                     emitter=None, **kwargs):
        emitter.complete(artifacts=[], validation={"passed": True,
                                                   "error_count": 0})
        return {
            "device_name": "Stub Device",
            "report_type": "PSUR",
            "artifacts": {},
            "artifacts_meta": [],
            "is_valid": True,
            "errors": [],
        }

    monkeypatch.setattr(runs_mod, "PIPELINE_RUNNER", _fake_runner)
    return _fake_runner


def _wait_for(client, run_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f"/runs/{run_id}").json()["status"]
        if status in ("completed", "failed"):
            return status
        time.sleep(0.05)
    raise TimeoutError("run did not finish")


def _start_run(client):
    resp = client.post("/runs", json={
        "period": {"start": "2023-01-01", "end": "2023-12-31"},
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["run_id"]


# ── GET /runs ────────────────────────────────────────────────────────

def test_list_runs_empty(client, isolated_registry):
    resp = client.get("/runs")
    assert resp.status_code == 200
    assert resp.json() == {"runs": []}


def test_list_runs_includes_completed_run(client, isolated_registry,
                                          fast_pipeline):
    run_id = _start_run(client)
    _wait_for(client, run_id)
    runs = client.get("/runs").json()["runs"]
    assert [r["run_id"] for r in runs] == [run_id]
    assert runs[0]["status"] == "completed"
    assert runs[0]["device_name"] == "Stub Device"


def test_list_runs_includes_orphaned_workspaces(client, isolated_registry,
                                                tmp_path):
    (tmp_path / "abcdef123456").mkdir()
    (tmp_path / "not-a-run-id").mkdir()  # ignored: bad pattern
    runs = client.get("/runs").json()["runs"]
    assert [r["run_id"] for r in runs] == ["abcdef123456"]
    assert runs[0]["status"] == "orphaned"


# ── DELETE /runs/{id} ────────────────────────────────────────────────

def test_delete_completed_run_removes_registry_and_workspace(
        client, isolated_registry, fast_pipeline, tmp_path):
    run_id = _start_run(client)
    _wait_for(client, run_id)
    assert (tmp_path / run_id).is_dir()

    resp = client.delete(f"/runs/{run_id}")
    assert resp.status_code == 200
    assert resp.json() == {"run_id": run_id, "deleted": True,
                           "workspace_removed": True}
    assert not (tmp_path / run_id).exists()
    assert client.get(f"/runs/{run_id}").status_code == 404
    assert client.get("/runs").json()["runs"] == []


def test_delete_running_run_refused(client, isolated_registry, monkeypatch):
    release = threading.Event()

    def _slow_runner(*, emitter=None, **kwargs):
        release.wait(timeout=10)
        emitter.complete(artifacts=[], validation={"passed": True,
                                                   "error_count": 0})
        return {"device_name": "Stub", "report_type": "PSUR",
                "artifacts": {}, "artifacts_meta": [],
                "is_valid": True, "errors": []}

    monkeypatch.setattr(runs_mod, "PIPELINE_RUNNER", _slow_runner)
    run_id = _start_run(client)
    try:
        resp = client.delete(f"/runs/{run_id}")
        assert resp.status_code == 409
    finally:
        release.set()
        _wait_for(client, run_id)


def test_delete_orphaned_workspace(client, isolated_registry, tmp_path):
    orphan = tmp_path / "0123456789ab"
    (orphan / "output").mkdir(parents=True)
    (orphan / "output" / "PSUR.json").write_text("{}")

    resp = client.delete("/runs/0123456789ab")
    assert resp.status_code == 200
    assert resp.json()["workspace_removed"] is True
    assert not orphan.exists()


def test_delete_unknown_run_404(client, isolated_registry):
    assert client.delete("/runs/ffffffffffff").status_code == 404


def test_delete_rejects_non_run_id_paths(client, isolated_registry, tmp_path):
    """Ids that don't match the run-id pattern must never touch the disk."""
    (tmp_path / "..stray").mkdir()
    assert client.delete("/runs/..stray").status_code == 404
    assert (tmp_path / "..stray").is_dir()
