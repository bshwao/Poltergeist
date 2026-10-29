"""Recording discovery stays paged; capture requires local approval and live mode."""

import base64
from hashlib import sha256
import json
from types import SimpleNamespace as NS
import struct
import tracemalloc
from uuid import uuid4

import pytest

from backend.storage import ArtifactStore
from companion.evidence import read_evidence
from shared.models.evidence import EvidenceRequest
from shared.models.remote import canonical_json, ExecuteCommand, RemoteRunOptions
from simulator.protocols import SensorSample

from test_remote_service import harness, execute
from test_coordinator_relay import setup, execute as coordinator_execute
from coordinator.auth import AuthError


@pytest.fixture
def captured(tmp_path):
    store = ArtifactStore(tmp_path / "storage", min_free_bytes=0)
    store.initialize()
    run = uuid4()
    report = store.write_report(run, {"private_diagnostic": "C:/private/hidden"})
    writer = store.capture(run)
    camera = b"\x05\x06\x07\xff" * (320 * 180)
    lidar = struct.pack("<ffff", 1., 2., 3., .5) * 5000
    for index in range(30):
        for kind, data, metadata in (("camera", camera, {"encoding": "bgra8", "width": 320, "height": 180}),
                ("lidar", lidar, {"encoding": "float32_xyzi", "channels": 32})):
            writer.write(SensorSample(kind=kind, frame=200 + index, timestamp=10. + index * .05,
                data=data, metadata=metadata | {"debug_path": "C:/private/hidden"}))
    reference = writer.commit()
    result = NS(artifacts=[report, reference], simulation=NS(id=run))
    def request(resource, **values):
        return EvidenceRequest(request_id=uuid4(), simulation_id=run, resource=resource, artifact_index=1, **values)
    return store, result, reference, request


def test_catalogue_and_sample_pages_preserve_native_frames_without_disclosing_paths(captured):
    store, result, reference, request = captured
    catalogue = read_evidence(store, result, request("recordings"))
    assert catalogue.state == "available"
    assert catalogue.recordings[0].artifact_index == 1 and catalogue.recordings[0].sample_count == 60
    assert catalogue.recordings[0].total_bytes == 30 * (320 * 180 * 4 + 5000 * 16)
    seen, offset, version = [], 0, None
    while True:
        page = read_evidence(store, result, request("samples", limit=7, sample_index=len(seen), offset=offset, version=version))
        assert page.state == "available" and len(page.samples) <= 7
        assert sha256(base64.b64decode(page.data_base64)).hexdigest() == page.sha256
        assert "C:/private/hidden" not in canonical_json(page) and "manifest.json" not in canonical_json(page)
        seen.extend(page.samples)
        if page.next_offset is None:
            break
        offset, version = page.next_offset, page.version
    assert [value.sample_index for value in seen] == list(range(60))
    assert [value.frame for value in seen] == [200 + i // 2 for i in range(60)]
    assert seen[0].kind == "camera" and seen[1].kind == "lidar"


def test_selected_sample_uses_its_recorded_index_cursor_and_hash(captured, monkeypatch):
    store, result, reference, request = captured
    page = read_evidence(store, result, request("samples", limit=100))
    selected = page.samples[57]
    monkeypatch.setattr("companion.recording_evidence.MAX_INDEX_SCAN", 1)
    tracemalloc.start()
    reply = read_evidence(store, result, request("sample", sample_index=selected.sample_index, sample_cursor=selected.index_offset))
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert reply.state == "available" and reply.sample == selected and peak < 1024 * 1024
    assert reply.frame == 228 and reply.timestamp == 11.4
    assert len(base64.b64decode(reply.data_base64)) == 65536
    assert read_evidence(store, result, request("sample", sample_index=selected.sample_index)).state == "limit_exceeded"
    wrong = read_evidence(store, result, request("sample", sample_index=56, sample_cursor=selected.index_offset))
    assert wrong.state == "changed"


def test_legacy_recordings_get_a_bounded_virtual_page_index(captured):
    store, result, reference, request = captured
    store.validate_reference(reference).with_name("samples.ndjson").unlink()
    seen, offset, version = [], 0, None
    while True:
        page = read_evidence(store, result, request("samples", limit=9, sample_index=len(seen), offset=offset, version=version))
        assert page.state == "available"
        seen.extend(page.samples)
        if page.next_offset is None:
            break
        offset, version = page.next_offset, page.version
    assert [value.sample_index for value in seen] == list(range(60))
    assert all(value.index_offset is None for value in seen)
    selected = seen[-1]
    assert read_evidence(store, result, request("sample", sample_index=59)).sample == selected


def test_missing_truncated_changed_and_deleted_recordings_do_not_become_valid_empty_lists(captured):
    store, result, reference, request = captured
    page = read_evidence(store, result, request("samples", limit=5))
    sidecar = store.validate_reference(reference).with_name("samples.ndjson")
    sidecar.write_bytes(b"")
    assert read_evidence(store, result, request("samples")).state == "missing"
    assert read_evidence(store, result, request("samples", offset=page.next_offset, version=page.version)).state == "changed"
    store.delete_evidence(reference, result.simulation.id)
    assert read_evidence(store, result, request("recordings")).recordings[0].state == "deleted"
    assert read_evidence(store, result, request("samples")).state == "deleted"
    assert read_evidence(store, result, request("sample")).state == "deleted"


def test_capture_cannot_be_enabled_by_a_website_without_local_permission(harness):
    binding, current, persistence, make, release = harness
    worker, service, _ = make()
    command = execute(options=RemoteRunOptions(camera=True))
    assert service.dispatch(binding, command).code == "policy_denied"
    current[1] = current[1].model_copy(update={"allow_raw_capture": True})
    # Synthetic capture must not masquerade as actual camera/LiDAR evidence.
    assert service.dispatch(binding, command).code == "policy_denied"


def test_coordinator_rejects_unapproved_capture_and_accepts_explicit_live_capture(setup):
    service, pairing, store, now, alice, bob, device, credential, identity = setup
    command = coordinator_execute(options={"camera": True})
    with pytest.raises(AuthError) as rejected:
        service.admit(alice, device, command, connected=True)
    assert rejected.value.code == "policy_denied"
    with store.transaction() as connection:
        policy = json.loads(connection.execute("SELECT policy_json FROM devices").fetchone()[0])
        policy["allow_raw_capture"] = True
        policy["scopes"].append("run:execute:carla")
        connection.execute("UPDATE devices SET policy_json=?", (json.dumps(policy),))
        connection.execute("INSERT INTO device_grants VALUES (?,1,'run:execute:carla')", (str(device),))
    with pytest.raises(AuthError) as rejected:
        service.admit(alice, device, command, connected=True)
    assert rejected.value.code == "policy_denied"
    assert service.admit(alice, device, command.model_copy(update={"mode": "carla"}), connected=True).status == "queued"
