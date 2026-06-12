"""Server structural-validation tests: content editable, structure locked."""
import copy
import json
import time

import pytest
from fastapi.testclient import TestClient

import server.runs as runs_mod
from server.app import app
from server.specs import build_defaults


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def defaults():
    return build_defaults()


@pytest.fixture
def fast_pipeline(monkeypatch):
    """Replace the real pipeline with an instant fake that emits a couple of
    events and produces no artifacts — POST /runs flow stays real."""
    def _fake_runner(*, start_date, end_date, input_dir, output_dir,
                     emitter=None, **kwargs):
        emitter.phase_started("discovery")
        emitter.decision(
            "denominator_selection",
            inputs_summary={"is_reusable": False},
            output="units_distributed",
            reason="stub",
            regulatory_basis=["MDCG 2022-21"],
        )
        emitter.complete(artifacts=[], validation={"passed": True, "error_count": 0})
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


def _error_messages(response):
    return json.dumps(response.json()["detail"])


# ── GET /defaults ────────────────────────────────────────────────────

def test_defaults_shape(client):
    resp = client.get("/defaults")
    assert resp.status_code == 200
    body = resp.json()
    assert body["period"] == {"start": "2023-01-01", "end": "2023-12-31"}
    expected_inputs = {
        "sales", "complaints", "capa", "fsca", "ract", "external_events",
        "literature", "device_context", "pms_plan", "previous_psur",
        "clinical_safety", "clinical_performance",
    }
    assert set(body["inputs"].keys()) == expected_inputs
    sales = body["inputs"]["sales"]
    assert sales["kind"] == "table"
    assert [c["name"] for c in sales["columns"]] == [
        "date", "device_model", "device_name", "region", "units_sold",
    ]
    assert all({"name", "type", "required"} <= set(c) for c in sales["columns"])
    assert len(sales["rows"]) > 0
    dc = body["inputs"]["device_context"]
    assert dc["kind"] == "json"
    assert "device_trade_names" in dc["value"]


# ── POST /runs: accepted content edits ───────────────────────────────

def test_content_edit_accepted(client, defaults, fast_pipeline):
    sales_rows = copy.deepcopy(defaults["inputs"]["sales"]["rows"])
    sales_rows[0]["units_sold"] = 9999  # edit content freely
    resp = client.post("/runs", json={
        "period": defaults["period"],
        "inputs": {"sales": {"rows": sales_rows}},
    })
    assert resp.status_code == 201, resp.text
    run_id = resp.json()["run_id"]
    assert _wait_for(client, run_id) == "completed"

    # SSE replay: all events from seq 0, named by kind
    with client.stream("GET", f"/runs/{run_id}/events") as stream:
        body = "".join(stream.iter_text())
    assert "event: progress" in body
    assert "event: decision" in body
    assert "event: complete" in body
    datas = [json.loads(line[len("data: "):])
             for line in body.splitlines() if line.startswith("data: ")]
    assert [d["seq"] for d in datas] == list(range(len(datas)))


def test_json_content_edit_accepted(client, defaults, fast_pipeline):
    dc = copy.deepcopy(defaults["inputs"]["device_context"]["value"])
    dc["device_description"] = "An edited description."  # content edit OK
    resp = client.post("/runs", json={
        "period": defaults["period"],
        "inputs": {"device_context": {"value": dc}},
    })
    assert resp.status_code == 201, resp.text
    assert _wait_for(client, resp.json()["run_id"]) == "completed"


# ── POST /runs: structural rejections (422) ──────────────────────────

def test_added_column_rejected(client, defaults):
    rows = copy.deepcopy(defaults["inputs"]["sales"]["rows"])
    rows[0]["bonus_column"] = "nope"
    resp = client.post("/runs", json={
        "period": defaults["period"],
        "inputs": {"sales": {"rows": rows}},
    })
    assert resp.status_code == 422
    msg = _error_messages(resp)
    assert "bonus_column" in msg and "unexpected column" in msg
    assert "rows[0]" in msg


def test_removed_column_rejected(client, defaults):
    rows = copy.deepcopy(defaults["inputs"]["sales"]["rows"])
    del rows[2]["region"]
    resp = client.post("/runs", json={
        "period": defaults["period"],
        "inputs": {"sales": {"rows": rows}},
    })
    assert resp.status_code == 422
    msg = _error_messages(resp)
    assert "missing column 'region'" in msg
    assert "rows[2]" in msg


def test_renamed_column_rejected(client, defaults):
    rows = copy.deepcopy(defaults["inputs"]["sales"]["rows"])
    for row in rows:
        row["qty"] = row.pop("units_sold")
    resp = client.post("/runs", json={
        "period": defaults["period"],
        "inputs": {"sales": {"rows": rows}},
    })
    assert resp.status_code == 422
    msg = _error_messages(resp)
    assert "missing column 'units_sold'" in msg
    assert "unexpected column 'qty'" in msg


def test_type_violation_rejected(client, defaults):
    rows = copy.deepcopy(defaults["inputs"]["sales"]["rows"])
    rows[1]["units_sold"] = "lots and lots"
    resp = client.post("/runs", json={
        "period": defaults["period"],
        "inputs": {"sales": {"rows": rows}},
    })
    assert resp.status_code == 422
    msg = _error_messages(resp)
    assert "units_sold" in msg and "integer" in msg


def test_bad_date_rejected(client, defaults):
    rows = copy.deepcopy(defaults["inputs"]["complaints"]["rows"])
    rows[0]["event_date"] = "07/01/2023"
    resp = client.post("/runs", json={
        "period": defaults["period"],
        "inputs": {"complaints": {"rows": rows}},
    })
    assert resp.status_code == 422
    assert "ISO date" in _error_messages(resp)


def test_json_removed_key_rejected(client, defaults):
    dc = copy.deepcopy(defaults["inputs"]["device_context"]["value"])
    del dc["sterility_status"]
    resp = client.post("/runs", json={
        "period": defaults["period"],
        "inputs": {"device_context": {"value": dc}},
    })
    assert resp.status_code == 422
    assert "missing key 'sterility_status'" in _error_messages(resp)


def test_json_added_key_rejected(client, defaults):
    ract = copy.deepcopy(defaults["inputs"]["ract"]["value"])
    ract["hazards"][0]["surprise"] = 1
    resp = client.post("/runs", json={
        "period": defaults["period"],
        "inputs": {"ract": {"value": ract}},
    })
    assert resp.status_code == 422
    msg = _error_messages(resp)
    assert "unexpected key 'surprise'" in msg
    assert "hazards[0]" in msg


def test_unknown_input_name_rejected(client, defaults):
    resp = client.post("/runs", json={
        "period": defaults["period"],
        "inputs": {"surprise_input": {"rows": [{"a": 1}]}},
    })
    assert resp.status_code == 422
    assert "unknown input" in _error_messages(resp)


def test_invalid_period_rejected(client):
    resp = client.post("/runs", json={
        "period": {"start": "2023-12-31", "end": "2023-01-01"},
        "inputs": {},
    })
    assert resp.status_code == 422


# ── Concurrency guard ────────────────────────────────────────────────

def test_demo_busy_when_saturated(client, defaults, monkeypatch):
    import threading

    release = threading.Event()

    def _slow_runner(*, emitter=None, **kwargs):
        release.wait(timeout=10)
        emitter.complete(artifacts=[], validation={"passed": True, "error_count": 0})
        return {"artifacts": {}, "artifacts_meta": [], "is_valid": True, "errors": []}

    monkeypatch.setattr(runs_mod, "PIPELINE_RUNNER", _slow_runner)
    monkeypatch.setenv("MAX_CONCURRENT_RUNS", "1")

    first = client.post("/runs", json={"period": defaults["period"], "inputs": {}})
    assert first.status_code == 201
    second = client.post("/runs", json={"period": defaults["period"], "inputs": {}})
    assert second.status_code == 409
    assert second.json()["detail"] == "demo_busy"

    release.set()
    assert _wait_for(client, first.json()["run_id"]) == "completed"


def test_unknown_run_404(client):
    assert client.get("/runs/nope").status_code == 404
    assert client.get("/runs/nope/artifacts").status_code == 404
    assert client.get("/runs/nope/artifacts/x.docx").status_code == 404
