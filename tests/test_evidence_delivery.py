"""Long evidence stays on disk; ownership, integrity and capacities stay bounded."""

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from types import SimpleNamespace as NS
import tracemalloc
from uuid import uuid4

import pytest

from backend.storage import ArtifactError, ArtifactStore
from companion.evidence import read_evidence
from shared.models import ActorState, TelemetryFrame
from shared.models.coordination import DevicePolicy
from shared.models.evidence import CHUNK_BYTES, EvidenceReply, EvidenceRequest
from shared.models.remote import ClientReady, RelayReply, canonical_json, parse_message
from simulator.protocols import SensorSample

from test_coordinator_relay import web, setup, execute, accepted, await_available
from test_remote_service import harness, wait_for


def test_long_telemetry_pages_are_bounded_and_preserve_frame_unknowns(tmp_path):
    store = ArtifactStore(tmp_path, min_free_bytes=0)
    store.initialize()
    run = uuid4()
    ref = store.write_telemetry(run, (TelemetryFrame(frame=i, time_s=float(i), ego=ActorState(actor_id="ego"))
                                     for i in range(15000)))
    result = NS(artifacts=[ref])
    first = EvidenceRequest(request_id=uuid4(), simulation_id=run, limit=5)
    tracemalloc.start()
    page = read_evidence(store, result, first)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < 1024 * 1024
    assert [f.frame for f in page.frames] == list(range(5))
    assert all(f.collisions is None and f.speed_mps is None for f in page.frames)
    assert sha256(base64.b64decode(page.data_base64)).hexdigest() == page.sha256
    following = first.model_copy(update={"offset": page.next_offset, "version": page.version})
    assert [f.frame for f in read_evidence(store, result, following).frames] == list(range(5, 10))
    path = store.validate_reference(ref)
    with path.open("ab") as target:
        target.write(b"\n")
    assert read_evidence(store, result, following).state == "changed"


def test_recording_sample_chunks_keep_exact_frame_and_whole_sample_digest(tmp_path):
    store = ArtifactStore(tmp_path, min_free_bytes=0)
    store.initialize()
    run = uuid4()
    data = b"\x01\x02\x03\xff" * 50000
    capture = store.capture(run)
    for i in range(30):
        capture.write(SensorSample(kind="camera", frame=100 + i, timestamp=float(i), data=data,
            metadata={"encoding": "bgra8", "width": 500, "height": 100}))
    ref = capture.commit()
    command = EvidenceRequest(request_id=uuid4(), simulation_id=run, resource="sample", sample_index=29)
    tracemalloc.start()
    first = read_evidence(store, NS(artifacts=[ref]), command)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < 1024 * 1024
    assert first.frame == 129 and first.timestamp == 29.
    assert len(base64.b64decode(first.data_base64)) == CHUNK_BYTES
    assert first.sample_sha256 == sha256(data).hexdigest()
    digest, offset, version = sha256(), 0, None
    while True:
        reply = read_evidence(store, NS(artifacts=[ref]), command.model_copy(update={"offset": offset, "version": version}))
        chunk = base64.b64decode(reply.data_base64)
        assert sha256(chunk).hexdigest() == reply.sha256
        digest.update(chunk)
        if reply.next_offset is None:
            break
        offset, version = reply.next_offset, reply.version
    assert digest.hexdigest() == first.sample_sha256


def test_quota_and_free_disk_fail_before_publication_and_retention_is_explicit(tmp_path, monkeypatch):
    store = ArtifactStore(tmp_path, quota_bytes=1024, min_free_bytes=0)
    store.initialize()
    run = uuid4()
    with pytest.raises(ArtifactError, match="storage limit"):
        store._write("report", run, [b"x" * 1025])
    assert store.capacity()["used_bytes"] == 0
    assert not list(tmp_path.glob("*/*/.*.staging"))
    ref = store.write_telemetry(run, [TelemetryFrame(frame=5, time_s=0., ego=ActorState(actor_id="ego"))])
    request = EvidenceRequest(request_id=uuid4(), simulation_id=run)
    store.delete_evidence(ref, run)
    assert read_evidence(store, NS(artifacts=[ref]), request).state == "deleted"
    other = store.write_report(run, {})
    store.validate_reference(other).unlink()
    assert read_evidence(store, NS(artifacts=[other]), request).state == "missing"
    monkeypatch.setattr("backend.storage.shutil.disk_usage", lambda root: NS(free=0))
    with pytest.raises(ArtifactError, match="storage limit"):
        store.write_report(run, {"new": True})


def test_companion_evidence_requires_fresh_local_consent_and_owned_receipt(harness):
    binding, current, persistence, make, release = harness
    worker, service, _ = make()
    run = service.dispatch(binding, execute()).simulation_id
    wait_for(lambda: worker.get(run).result)
    request = EvidenceRequest(request_id=uuid4(), simulation_id=run)
    assert service.dispatch(binding, request).code == "permission_denied"
    current[1] = current[1].model_copy(update={"allow_evidence_read": True})
    assert service.dispatch(binding, request).state == "available"
    assert service.dispatch(binding, request.model_copy(update={"simulation_id": uuid4()})).code == "run_not_found"
    current[1] = current[1].model_copy(update={"allow_evidence_read": False})
    assert service.dispatch(binding, request).code == "permission_denied"


def test_ephemeral_relay_never_persists_evidence_and_is_owner_scoped(web):
    client, app, device, credential, alice, bob = web
    owner = app.state.pairing.authenticate_device(credential)["owner_id"]
    with app.state.store.transaction() as connection:
        policy = DevicePolicy(scopes=["status:read", "run:execute:test", "run:cancel", "result:read"], allow_evidence_read=True)
        connection.execute("UPDATE devices SET policy_json=?", (policy.model_dump_json(),))
    command = execute()
    identity = app.state.commands.connect(credential)
    app.state.commands.admit(owner, device, command, connected=True)
    ack = accepted(command)
    app.state.commands.reply(credential, identity, ack)
    request = EvidenceRequest(request_id=uuid4(), simulation_id=ack.simulation_id)
    url = f"/v1/devices/{device}/evidence"
    assert client.post(url, json=request.model_dump(mode="json"), headers=bob).status_code == 404
    with client.websocket_connect("wss://coordinator.test/v1/device/connect", headers={
            "Authorization": "Bearer " + credential, "X-Poltergeist-Evidence": "v1"}) as socket:
        hello = socket.receive_json()
        assert hello["evidence_supported"] is True
        envelope = {k: hello[k] for k in ("device_id", "ownership_generation", "connection_generation")}
        socket.send_text(canonical_json(ClientReady(**envelope, remote_execution_enabled=True, evidence_supported=True)))
        await_available(app, device)
        with ThreadPoolExecutor() as pool:
            pending = pool.submit(client.post, url, json=request.model_dump(mode="json"), headers=alice)
            delivered = parse_message(socket.receive_text())
            assert delivered.command == request
            busy = client.post(url, json=request.model_copy(update={"request_id": uuid4()}).model_dump(mode="json"), headers=alice)
            assert busy.status_code == 429
            reply = EvidenceReply(request_id=request.request_id, simulation_id=request.simulation_id,
                resource="telemetry", offset=0, state="missing")
            socket.send_text(canonical_json(RelayReply(**envelope, reply=reply)))
            response = pending.result(timeout=5)
            assert response.status_code == 200 and response.json()["state"] == "missing"
            assert response.headers["cache-control"] == "no-store"
        with app.state.store.transaction(write=False) as connection:
            assert connection.execute("SELECT count(*) FROM commands").fetchone()[0] == 1
        assert app.state.relay.transfer_count == 0
        assert client.post(f"/v1/devices/{device}/commands", json=request.model_dump(mode="json"), headers=alice).status_code == 400


@pytest.mark.parametrize("offset,version", [(1, None), (-1, None), (2**41, "0" * 64)])
def test_unbounded_or_unversioned_cursors_are_refused(offset, version):
    with pytest.raises(ValueError):
        EvidenceRequest(request_id=uuid4(), simulation_id=uuid4(), offset=offset, version=version)
