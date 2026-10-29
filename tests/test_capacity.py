"""Admission, bounded rate state, monitoring and generation-safe relay capacity."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from coordinator.app import create_app
from coordinator.auth import AuthError
from coordinator.limits import RequestLimits
from coordinator.relay import RelayHub
from coordinator.settings import CoordinatorSettings
from shared.models.remote import StatusCommand
from test_coordinator_relay import setup, enroll, execute


def test_storage_quota_preserves_idempotent_retry_and_other_owners(setup):
    service, pairing, store, now, alice, bob, device, credential, identity = setup
    service.max_commands_per_account = 1
    request = StatusCommand(request_id=uuid4())
    first = service.admit(alice, device, request, connected=True)
    assert service.admit(alice, device, request, connected=False) == first
    with pytest.raises(AuthError) as error:
        service.admit(alice, device, StatusCommand(request_id=uuid4()), connected=True)
    assert error.value.code == "command_storage_full"
    other, _ = enroll(pairing, bob)
    assert service.admit(bob, other.device_id, StatusCommand(request_id=uuid4()), connected=True).status == "queued"


def test_pairing_quota_is_per_owner_and_revoke_releases_slot(setup):
    service, pairing, store, now, alice, bob, device, credential, identity = setup
    pairing.max_devices_per_account = 1
    with pytest.raises(AuthError) as error:
        enroll(pairing, alice)
    assert error.value.code == "device_quota"
    assert str(enroll(pairing, bob)[0].owner.account_id) == bob
    pairing.revoke(alice, device)
    assert enroll(pairing, alice)[0].device_id != device


def test_connection_limit_refusal_does_not_fence_another_connection(setup):
    service, pairing, store, now, alice, bob, device, credential, identity = setup
    other, other_credential = enroll(pairing, bob)
    hub = RelayHub(service, max_connections=1)
    hub.connections[str(device)] = object()
    closed = []
    class Socket:
        async def close(self, code):
            closed.append(code)
    asyncio.run(hub.connect(Socket(), other_credential))
    assert closed == [1008] and hub.capacity_refusals == 1
    service.connection_current(credential, identity)
    with store.transaction(write=False) as connection:
        assert connection.execute("SELECT connection_generation FROM devices WHERE id=?", (str(other.device_id),)).fetchone()[0] == 0


def test_request_windows_bound_memory_and_expire_without_weakening_other_users():
    now = [0.]
    limits = RequestLimits(monotonic=lambda: now[0], max_buckets=2)
    limits.consume("alice", 1)
    with pytest.raises(AuthError) as error:
        limits.consume("alice", 1)
    assert error.value.status == 429 and error.value.retry_after == 61
    limits.consume("bob", 1)
    with pytest.raises(AuthError) as error:
        limits.consume("carol", 1)
    assert error.value.status == 503 and len(limits.buckets) == 2
    now[0] = 60.
    limits.consume("carol", 1)
    assert list(limits.buckets) == ["carol"]


def test_operations_require_independent_secret_and_expose_only_aggregate_counts(tmp_path):
    token = "A" * 43
    settings = CoordinatorSettings(tmp_path / "coordinator.db", "https://coordinator.test", monitor_token=token)
    assert token not in repr(settings)
    with TestClient(create_app(settings), base_url=settings.public_origin) as client:
        assert client.get("/v1/operations").status_code == 404
        assert client.get("/v1/operations", headers={"Authorization": "Bearer wrong"}).status_code == 404
        response = client.get("/v1/operations", headers={"Authorization": "Bearer " + token})
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        value = response.json()
        assert value["routing"] == "single_process" and value["connected_devices"] == 0
        assert token not in response.text and str(tmp_path) not in response.text and "owner_id" not in response.text


@pytest.mark.parametrize("values", [{"max_connections": 0}, {"max_transfers": 33}, {"max_devices_per_account": True}, {"monitor_token": "short"}])
def test_invalid_capacity_configuration_is_rejected(tmp_path, values):
    with pytest.raises(ValueError):
        CoordinatorSettings(tmp_path / "coordinator.db", "https://coordinator.test", **values)


@pytest.mark.parametrize("more_body", [False, True])
def test_slow_or_suspended_body_cannot_renew_the_absolute_receive_budget(tmp_path, monkeypatch, more_body):
    from coordinator.app import RequestBoundary
    clock, sent, reached = [0.], [], []
    monkeypatch.setattr("coordinator.app.time", SimpleNamespace(monotonic=lambda: clock[0]))
    async def application(*args):
        reached.append(True)
    async def receive():
        clock[0] = 11.
        return {"type": "http.request", "body": b"{}", "more_body": more_body}
    async def send(message):
        sent.append(message)
    boundary = RequestBoundary(application, settings=CoordinatorSettings(tmp_path / "db", "https://coordinator.test"))
    asyncio.run(boundary({"type": "http", "scheme": "https", "method": "POST",
        "headers": [(b"host", b"coordinator.test")]}, receive, send))
    assert reached == [] and sent[0]["status"] == 408 and boundary.active_http == 0
