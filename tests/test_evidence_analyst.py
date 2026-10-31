"""Evidence Analyst facts require saved frames, with explicit unknown coverage."""

from io import BytesIO
import json
from threading import Event
from types import SimpleNamespace

import pytest

from backend.storage import ArtifactError
from backend.services import experimentation_service as evidence
from shared.models import TelemetryFrame, ActorState
from test_experimentation import settings, client_for, ContactAdapter, wait


def launch(client):
    response = client.post("/simulations", json={"mode": "test", "scenario": {
        "name": "Analyst contact fixture", "duration_s": .1, "actors": [{"id": "ego", "type": "ego_vehicle"}]}})
    assert response.status_code == 202
    identity = response.json()["simulation"]["id"]
    return wait(client, f"/simulations/{identity}", lambda value: value["simulation"]["status"] == "completed")


def test_facts_have_exact_saved_frame_links_and_deterministic_source_identity(settings):
    with client_for(settings) as client:
        record = launch(client)
        identity = record["simulation"]["id"]
        path = f"/experimentation/analysis/{identity}"
        first = client.get(path)
        assert first.status_code == 200 and first.headers["cache-control"] == "no-store"
        value = first.json()
        assert client.get(path).json() == value
        assert len(value["source_digest"]) == len(value["telemetry_digest"]) == 64
        assert value["state"] == "complete" and value["mode"] == "test"
        contact = next(item for item in value["observations"] if item["kind"] == "contact")
        assert contact["value"] == 1 and contact["references"][0]["frame"] == 0
        assert contact["references"][0]["time_s"] == 0
        assert all(item["references"] for item in value["observations"])
        assert all(item["status"] == "unverified" for item in value["hypotheses"])
        assert value["coverage"]["decision_trace"] == "not_reviewed"
        assert any("Synthetic" in item for item in value["limitations"])
        (settings.storage_dir.parent / "analysis-report.json").write_text(json.dumps(value, indent=2), encoding="utf-8")
        assert len(client.get("/simulations").json()) == 1
        assert "attachment" in client.get(path + "?download=true").headers["content-disposition"]


def test_missing_or_corrupt_evidence_has_no_frame_claims_or_fabricated_zero(settings):
    with client_for(settings) as client:
        record = launch(client)
        identity = record["simulation"]["id"]
        reference = next(item for item in record["result"]["artifacts"] if item["kind"] == "telemetry")
        path = settings.storage_dir / reference["path"]
        assert path.resolve().is_relative_to(settings.storage_dir.resolve())
        path.write_bytes(b"not-json\n")
        corrupt = client.get(f"/experimentation/analysis/{identity}").json()
        assert corrupt["state"] == "unavailable" and corrupt["observations"] == []
        assert corrupt["coverage"]["telemetry_frames"] is None
        path.unlink()
        missing = client.get(f"/experimentation/analysis/{identity}").json()
        assert missing["state"] == "unavailable" and missing["observations"] == []
        assert str(settings.storage_dir) not in str(missing)


def test_unobserved_contacts_remain_unknown(settings):
    class Unobserved(ContactAdapter):
        def open(self, recipe):
            frame = super().open(recipe)
            frame.collisions = None
            return frame
        def step(self, *args, **kwargs):
            frame = super().step(*args, **kwargs)
            frame.collisions = None
            return frame
    with client_for(settings, Unobserved) as client:
        identity = launch(client)["simulation"]["id"]
        value = client.get(f"/experimentation/analysis/{identity}").json()
        contact = next(item for item in value["observations"] if item["kind"] == "contact")
        assert contact["coverage"] == "unavailable" and contact["value"] is None
        assert value["state"] == "partial" and not value["coverage"]["safety_complete"]


def test_valid_json_trace_that_contradicts_saved_coverage_is_not_analyzed(settings):
    with client_for(settings) as client:
        record = launch(client)
        identity = record["simulation"]["id"]
        reference = next(item for item in record["result"]["artifacts"] if item["kind"] == "telemetry")
        path = settings.storage_dir / reference["path"]
        assert path.resolve().is_relative_to(settings.storage_dir.resolve())
        frames = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        for frame in frames:
            frame["collisions"] = None
        path.write_text("".join(json.dumps(frame) + "\n" for frame in frames), encoding="utf-8")
        report = client.get(f"/experimentation/analysis/{identity}").json()
        assert report["state"] == "unavailable" and report["observations"] == []
        assert report["coverage"]["safety_complete"] is None


def test_pending_analysis_does_not_start_additional_work(settings):
    entered, release = Event(), Event()
    class Held(ContactAdapter):
        def open(self, recipe):
            entered.set()
            assert release.wait(20)
            return super().open(recipe)
    with client_for(settings, Held) as client:
        try:
            response = client.post("/simulations", json={"mode": "test", "scenario": {
                "name": "Held analyst fixture", "duration_s": .1, "actors": [{"type": "ego_vehicle"}]}})
            identity = response.json()["simulation"]["id"]
            assert entered.wait(5)
            value = client.get(f"/experimentation/analysis/{identity}").json()
            assert value["state"] == "pending" and value["observations"] == []
            assert value["coverage"]["telemetry_frames"] is None
            assert len(client.get("/simulations").json()) == 1
        finally:
            release.set()


def test_growing_evidence_cannot_bypass_the_total_byte_budget(monkeypatch):
    line = (TelemetryFrame(frame=0, time_s=0., ego=ActorState(actor_id="ego")).model_dump_json() + "\n").encode()
    bytes_read = [0]
    class Counted(BytesIO):
        def readline(self, size=-1):
            value = super().readline(size)
            bytes_read[0] += len(value)
            return value
    class Growing:
        def stat(self): return SimpleNamespace(st_size=0)
        def open(self, mode): return Counted(line * 3)
    result = SimpleNamespace(artifacts=[SimpleNamespace(kind="telemetry")])
    store = SimpleNamespace(get_simulation=lambda _: SimpleNamespace(result=result),
        artifacts=SimpleNamespace(validate_reference=lambda *args: Growing()),
        _validate_trace=lambda *args: pytest.fail("Oversized data reached full-trace validation"))
    monkeypatch.setattr(evidence, "MAX_REVIEW_BYTES", len(line) * 2 - 1)
    with pytest.raises(ArtifactError, match="review limit"):
        evidence.read_review_evidence(store, "owned-fixture")
    assert bytes_read[0] <= evidence.MAX_REVIEW_BYTES
