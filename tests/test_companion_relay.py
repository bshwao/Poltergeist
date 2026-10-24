"""Explicit relay ownership and the default-off private acceptance gate."""

import copy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from backend.persistence_errors import ConflictError
from companion.controller import CompanionController
from shared.models.remote import StatusCommand


class Store:
    def __init__(self):
        self.record = None
    def load(self):
        return copy.deepcopy(self.record)
    def clear(self):
        self.record = None


def saved_approval():
    return {"origin": "https://coordinator.example", "state": "paired",
        "created": {"attempt_id": str(uuid4()), "device_id": str(uuid4()),
            "code": "ABCDEFGHJK", "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat()},
        "private_secret": "pa_" + "a" * 43, "requested_scopes": ["status:read"],
        "approval": {"request_id": str(uuid4()), "expected_account_id": str(uuid4()),
            "expected_revision": 2, "credential": "pd_" + "b" * 43,
            "policy": {"scopes": ["status:read"]}}}


class Relay:
    created = []
    def __init__(self, origin, enrollment, service, *, remote_execution_enabled):
        self.enrollment, self.service = enrollment, service
        self.enabled = remote_execution_enabled
        self.running = False
        self.clean = True
        self.created.append(self)
    def start(self):
        self.running = True
    def stop(self):
        if self.clean:
            self.running = False
        return self.clean
    def snapshot(self):
        return {"state": "connected" if self.running else "disconnected",
            "remote_execution_enabled": self.enabled, "running": self.running}


@pytest.fixture
def desktop(tmp_path):
    store = Store()
    Relay.created = []
    controller = CompanionController(tmp_path / "profile", relay_factory=Relay,
        credential_store_factory=lambda _: store)
    controller.open()
    controller.configure_pairing("https://coordinator.example")
    yield controller, store
    for relay in Relay.created:
        relay.clean = True
    assert controller.close()


def test_no_ambient_connection_and_saved_pairing_does_not_enable_execution(desktop):
    controller, store = desktop
    store.record = saved_approval()
    controller.start()
    controller.snapshot()
    assert not Relay.created
    assert controller.relay_status()["state"] == "disconnected"
    result = controller.connect_relay()
    assert result["running"] and result["remote_execution_enabled"] is False
    assert Relay.created[0].enrollment()[0].owner_id.hex == store.record["approval"]["expected_account_id"].replace("-", "")
    assert store.record["approval"]["credential"] not in str(result)
    assert store.record["private_secret"] not in str(result)
    with pytest.raises(ConflictError):
        controller.connect_relay()
    assert controller.stop()
    assert not Relay.created[0].running


def test_connection_requires_started_worker_and_paired_protected_record(desktop):
    controller, store = desktop
    store.record = saved_approval()
    with pytest.raises(ConflictError, match="Start"):
        controller.connect_relay()
    controller.start()
    store.record["state"] = "pending_approval"
    with pytest.raises(ConflictError, match="approval"):
        controller.connect_relay()
    assert not controller._relay_admission and not Relay.created


def test_disconnect_disables_admission_before_a_slow_socket_releases_profile(desktop):
    controller, store = desktop
    store.record = saved_approval()
    controller.start()
    controller.connect_relay()
    relay = Relay.created[0]
    binding, _, _ = relay.enrollment()
    relay.clean = False
    assert controller.stop() is False
    assert controller._owned and controller._runtime is not None
    assert relay.service.dispatch(binding, StatusCommand(request_id=uuid4())).code == "permission_denied"
    with pytest.raises(ConflictError):
        controller.revoke_pairing()
    assert store.record is not None
    relay.clean = True
    assert controller.stop()
    assert controller._runtime is None and controller._owned


def test_protected_binding_change_fences_existing_dispatch_without_network(desktop):
    controller, store = desktop
    store.record = saved_approval()
    controller.start()
    controller.connect_relay()
    relay = Relay.created[0]
    binding, _, _ = relay.enrollment()
    assert relay.service.dispatch(binding, StatusCommand(request_id=uuid4())).kind == "status"
    store.record["approval"]["expected_account_id"] = str(uuid4())
    assert relay.service.dispatch(binding, StatusCommand(request_id=uuid4())).code == "permission_denied"
