"""Upload boundaries, worker handoff, and strict evidence-gate integration."""
import io
import stat
import zipfile
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import server.app as app_mod
import server.runs as runs_mod
from server import uploads
from pipeline.harness import HarnessContext, HarnessIssue

URL = "/runs/upload?start=2025-01-01&end=2025-12-31"


def pack(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return buffer.getvalue()


@pytest.fixture
def service(monkeypatch, tmp_path):
    registry = runs_mod.RunRegistry()
    monkeypatch.setattr(app_mod, "REGISTRY", registry)
    monkeypatch.setattr(runs_mod, "REGISTRY", registry)
    monkeypatch.setattr(runs_mod, "RUNS_DIR", tmp_path)
    return TestClient(app_mod.app), registry, tmp_path


def post(client, data, url=URL, **kwargs):
    return client.post(url, content=data, headers={"Content-Type": "application/zip"}, **kwargs)


def assert_clean(service):
    client, registry, root = service
    assert registry.active_count() == 0
    assert client.get("/runs").json() == {"runs": []}
    assert list(root.iterdir()) == []


def test_upload_materializes_only_supplied_sources_and_starts_harness(service, monkeypatch):
    client, registry, root = service
    seen = {}

    def runner(**kwargs):
        seen.update(kwargs)
        seen["files"] = {p.name: p.read_bytes() for p in kwargs["input_dir"].iterdir()}
        return {"is_valid": True, "inference": {"provider": "openai"}}

    monkeypatch.setattr(app_mod, "run_uploaded_harness", runner)
    response = post(client, pack([("device_context.json", b"{}"), ("sales.csv", b"a,b\n1,2")]),
                    url=URL + "&first_psur=true")
    assert response.status_code == 201, response.text
    record = registry.get(response.json()["run_id"])
    record.thread.join(timeout=5)
    assert record.status == "completed"
    assert seen["files"] == {"device_context.json": b"{}", "sales.csv": b"a,b\n1,2"}
    assert seen["start_date"] == "2025-01-01"
    assert seen["first_psur"] is True
    assert not (record.workspace / "source-pack.zip").exists()
    assert client.get(f"/runs/{record.run_id}").json()["inference"] == {"provider": "openai"}


@pytest.mark.parametrize("name", ["../outside.csv", "/outside.csv", "folder/sales.csv",
                                      "folder\\sales.csv", "C:evil.csv", "CON.csv",
                                      "sales.csv.", ".hidden.csv", "script.py"])
def test_unsafe_names_rejected_and_cleaned(service, name):
    assert post(service[0], pack([(name, "content")])).status_code == 422
    assert_clean(service)


@pytest.mark.parametrize("data", [b"not a zip", b"", pack([]), pack([("sales.csv", "")]),
                                      pack([("sales.csv", "one"), ("SALES.csv", "two")])])
def test_invalid_archives_rejected_and_cleaned(service, data):
    assert post(service[0], data).status_code == 422
    assert_clean(service)


def test_symlink_rejected(service):
    entry = zipfile.ZipInfo("sales.csv")
    entry.create_system = 3
    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
    assert post(service[0], pack([(entry, "../../outside")])).status_code == 422
    assert_clean(service)


def test_corrupt_member_removes_partially_extracted_files(service):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("sales.csv", "first valid file")
        archive.writestr("complaints.csv", "unique-original-payload")
    damaged = buffer.getvalue().replace(b"unique-original-payload", b"unique-corrupt--payload")
    assert post(service[0], damaged).status_code == 422
    assert_clean(service)


def test_worker_start_failure_releases_upload_reservation(service, monkeypatch):
    def fail_start(self):
        raise RuntimeError("Synthetic thread creation failure")
    monkeypatch.setattr(runs_mod.threading.Thread, "start", fail_start)
    # Exercise the registry failure directly: patching Thread.start globally
    # would also prevent TestClient from starting its own portal thread.
    registry = service[1]
    record = registry.create({"start": "2025-01-01", "end": "2025-12-31"})
    with pytest.raises(RuntimeError, match="Synthetic thread"):
        registry.start(record)
    registry.discard_unstarted(record)
    assert registry.list_all() == []


@pytest.mark.parametrize("limit,value,entries", [
    ("MAX_FILE_BYTES", 3, [("sales.csv", "four")]),
    ("MAX_TOTAL_BYTES", 5, [("sales.csv", "abc"), ("complaints.csv", "def")]),
    ("MAX_FILES", 1, [("sales.csv", "abc"), ("complaints.csv", "def")]),
])
def test_expansion_limits(service, monkeypatch, limit, value, entries):
    monkeypatch.setattr(uploads, limit, value)
    assert post(service[0], pack(entries)).status_code == 422
    assert_clean(service)


def test_compressed_limit_checks_stream_without_content_length(service, monkeypatch):
    monkeypatch.setattr(uploads, "MAX_UPLOAD_BYTES", 10)
    assert post(service[0], iter([b"123456", b"123456"])).status_code == 413
    assert_clean(service)


def test_compressed_limit_checks_content_length(service, monkeypatch):
    monkeypatch.setattr(uploads, "MAX_UPLOAD_BYTES", 10)
    assert post(service[0], b"x" * 11).status_code == 413
    assert_clean(service)


def test_bad_period_and_media_type_do_not_reserve_run(service):
    client = service[0]
    assert post(client, b"x", url="/runs/upload?start=2025-12-31&end=2025-01-01").status_code == 422
    assert post(client, b"x", url="/runs/upload?start=no&end=2025-01-01").status_code == 422
    assert client.post(URL, content=b"x").status_code == 415
    assert_clean(service)


def test_busy_upload_refused(service, monkeypatch):
    client, registry, root = service
    monkeypatch.setenv("MAX_CONCURRENT_RUNS", "1")
    registry.create({"start": "2025-01-01", "end": "2025-12-31"})
    assert post(client, pack([("sales.csv", "x")])).status_code == 409
    assert list(root.iterdir()) == []


def test_uploaded_pack_runs_real_mandatory_input_gate(service):
    client, registry, root = service
    response = post(client, pack([("device_context.json", "{}")]))
    assert response.status_code == 201
    record = registry.get(response.json()["run_id"])
    record.thread.join(timeout=10)
    assert record.status == "failed"
    assert "mandatory inputs missing" in record.error
    assert any(e["kind"] == "error" for e in record.emitter.events_since(0))


def test_harness_adapter_publishes_validation_and_artifacts(service, monkeypatch):
    import server.harness as adapter
    client, registry, root = service

    def fake_harness(**kwargs):
        assert kwargs["interactive"] is False
        assert kwargs["is_first_psur"] is True
        output = kwargs["output_dir"]
        output.mkdir()
        files = [output / name for name in ("PSUR.json", "PSUR.docx", "stats.json")]
        for path in files:
            path.write_bytes(b"synthetic artifact")
        context = HarnessContext()
        context.update("harness_meta", inference={"provider": "openai"})
        return SimpleNamespace(issues=[HarnessIssue(code="TEST", severity="ERROR", message="Synthetic finding")],
                               context=context, json_path=files[0], docx_path=files[1], stats_path=files[2])

    monkeypatch.setattr(adapter, "run_harness", fake_harness)
    response = post(client, pack([("device_context.json", "{}")]), url=URL + "&first_psur=true")
    record = registry.get(response.json()["run_id"])
    record.thread.join(timeout=5)
    status = client.get(f"/runs/{record.run_id}").json()
    assert status["status"] == "completed"
    assert status["validation"] == {"passed": False, "error_count": 1}
    response = client.get(f"/runs/{record.run_id}/artifacts/validation.json")
    assert response.json()["passed"] is False
    assert len(client.get(f"/runs/{record.run_id}/artifacts").json()["artifacts"]) == 4


def test_unclassified_service_input_never_prompts(tmp_path, monkeypatch):
    import pipeline.discovery as discovery
    (tmp_path / "mystery.txt").write_text("unknown evidence")
    monkeypatch.setattr(discovery, "_ai_classify_file", lambda *args: None)
    def unexpected_prompt(*args, **kwargs):
        pytest.fail("service must not read terminal input")
    monkeypatch.setattr(discovery.Prompt, "ask", unexpected_prompt)
    with pytest.raises(ValueError, match="Cannot classify"):
        discovery.auto_discover_inputs(tmp_path, interactive=False)
