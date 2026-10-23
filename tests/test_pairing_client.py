"""Companion approval custody and lost-response reconciliation with real services."""

import copy
import json
import os
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from companion.credential_store import CredentialStoreError, WindowsCredentialStore
from companion.pairing_client import PairingClient, PairingClientError
from companion.controller import CompanionController
from companion.profile import CompanionConfig
from backend.persistence_errors import ConflictError
from coordinator.app import COOKIE, create_app
from coordinator.settings import CoordinatorSettings
from shared.models.coordination import DevicePolicy
from shared.origins import service_origin

ORIGIN = "https://coordinator.test"
SCOPES = ["status:read", "run:execute:test"]


@pytest.mark.parametrize("address", ["https://user:private_address_sentinel@host.test",
    "https://host.test:private_address_sentinel", "https://[private_address_sentinel]"])
def test_invalid_origin_errors_omit_submitted_values(address):
    for validate in (service_origin, lambda value: CompanionConfig(coordinator_origin=value)):
        with pytest.raises(ValueError) as error:
            validate(address)
        assert "private_address_sentinel" not in str(error.value)


class MemoryStore:
    """Test fixture only: production uses WindowsCredentialStore."""
    def __init__(self):
        self.record = None
        self.fail_state = None
    def load(self):
        return copy.deepcopy(self.record)
    def save(self, record):
        if record["state"] == self.fail_state:
            raise CredentialStoreError("Injected protection failure")
        self.record = copy.deepcopy(record)
    def clear(self):
        self.record = None


class ApiTransport:
    def __init__(self, api):
        self.api = api
        self.requests = []
        self.lose_response_for = None
        self.changed_response = None
    def request(self, method, path, *, body=None, authorization=None):
        self.requests.append((method, path, copy.deepcopy(body)))
        headers = {"Authorization": authorization} if authorization else {}
        result = self.api.request(method, path, json=body, headers=headers)
        if result.status_code >= 400:
            raise PairingClientError(f"Injected API HTTP {result.status_code}")
        if self.lose_response_for and path.endswith(self.lose_response_for):
            self.lose_response_for = None
            raise PairingClientError("Injected lost response after server commit")
        return self.changed_response if self.changed_response is not None else result.json()


@pytest.fixture
def setup(tmp_path):
    app = create_app(CoordinatorSettings(tmp_path / "coordinator.db", ORIGIN), now=lambda: 1800000000.)
    with TestClient(app, base_url=ORIGIN) as api:
        headers = {"Origin": ORIGIN}
        assert api.post("/v1/auth/register", json={"username": "alice", "password": "a private account passphrase"}, headers=headers).status_code == 201
        login = api.post("/v1/auth/login", json={"username": "alice", "password": "a private account passphrase"}, headers=headers)
        headers.update({"X-CSRF-Token": login.json()["csrf_token"], "Cookie": f"{COOKIE}={login.cookies[COOKIE]}", "X-Poltergeist-Client": "browser"})
        api.cookies.clear()
        transport = ApiTransport(api)
        store = MemoryStore()
        companion = PairingClient(ORIGIN, store, _transport=transport)
        yield api, headers, transport, store, companion


def claim(setup):
    api, headers, transport, store, companion = setup
    created = companion.begin("This computer", SCOPES)
    response = api.post("/v1/pairing/claim", json={"code": created.code}, headers=headers)
    assert response.status_code == 200
    return companion.poll()


def test_local_approval_is_explicit_and_credential_is_saved_before_server_approval(setup):
    api, headers, transport, store, companion = setup
    displayed = claim(setup)
    assert api.get("/v1/devices", headers=headers).json() == []
    assert store.record["state"] == "attempt" and store.record["approval"] is None
    store.fail_state = "pending_approval"
    with pytest.raises(CredentialStoreError):
        companion.approve(displayed, DevicePolicy(scopes=SCOPES))
    assert not any(path.endswith("/approve") for _, path, _ in transport.requests)
    assert api.get("/v1/devices", headers=headers).json() == []
    store.fail_state = None
    approved = companion.approve(displayed, DevicePolicy(scopes=SCOPES))
    assert approved.device_id == displayed.device_id and store.record["state"] == "paired"
    assert companion.resume() == approved


@pytest.mark.parametrize("lost_at", ["/approve", "/v1/device/session"])
def test_lost_response_reuses_saved_credential_and_one_device_after_client_restart(setup, lost_at):
    api, headers, transport, store, companion = setup
    displayed = claim(setup)
    transport.lose_response_for = lost_at
    with pytest.raises(PairingClientError, match="lost response"):
        companion.approve(displayed, DevicePolicy(scopes=SCOPES))
    pending = copy.deepcopy(store.record)
    assert pending["state"] == "pending_approval"
    assert len(api.get("/v1/devices", headers=headers).json()) == 1
    with pytest.raises(PairingClientError):
        companion.forget_unapproved_attempt()
    resumed = PairingClient(ORIGIN, store, _transport=transport)
    status = resumed.resume()
    assert store.record["state"] == "paired" and status.device_id == displayed.device_id
    assert store.record["approval"] == pending["approval"]
    approvals = [body for _, path, body in transport.requests if path.endswith("/approve")]
    assert len(approvals) == 2 and approvals[0] == approvals[1]
    assert len(api.get("/v1/devices", headers=headers).json()) == 1


def test_failed_final_storage_write_remains_reconcilable(setup):
    api, headers, transport, store, companion = setup
    displayed = claim(setup)
    store.fail_state = "paired"
    with pytest.raises(CredentialStoreError):
        companion.approve(displayed, DevicePolicy(scopes=SCOPES))
    assert store.record["state"] == "pending_approval"
    store.fail_state = None
    assert companion.resume().device_id == displayed.device_id


def test_wrong_displayed_owner_or_revision_cannot_be_approved(setup):
    api, headers, transport, store, companion = setup
    displayed = claim(setup)
    changed = displayed.model_copy(update={"revision": displayed.revision + 1})
    with pytest.raises(PairingClientError, match="review"):
        companion.approve(changed, DevicePolicy(scopes=SCOPES))
    assert store.record["state"] == "attempt" and api.get("/v1/devices", headers=headers).json() == []


def test_denial_and_lost_revoke_response_do_not_restore_access(setup):
    api, headers, transport, store, companion = setup
    displayed = claim(setup)
    companion.deny(displayed)
    assert store.load() is None and api.get("/v1/devices", headers=headers).json() == []
    displayed = claim(setup)
    companion.approve(displayed, DevicePolicy(scopes=SCOPES))
    credential = store.record["approval"]["credential"]
    transport.lose_response_for = "/v1/device/revoke"
    with pytest.raises(PairingClientError, match="lost response"):
        companion.revoke()
    assert api.get("/v1/device/session", headers={"Authorization": "Bearer " + credential}).status_code == 401
    companion.revoke()
    assert store.load() is None
    assert api.get("/v1/device/session", headers={"Authorization": "Bearer " + credential}).status_code == 401


def test_saved_origin_is_not_implicitly_rebound_to_another_server(setup):
    api, headers, transport, store, companion = setup
    claim(setup)
    count = len(transport.requests)
    rebound = PairingClient("https://another.test", store, _transport=transport)
    with pytest.raises(PairingClientError, match="does not match"):
        rebound.poll()
    assert len(transport.requests) == count


def test_pending_approval_can_reconcile_after_the_attempt_was_retired(setup):
    api, headers, transport, store, companion = setup
    displayed = claim(setup)
    transport.lose_response_for = "/approve"
    with pytest.raises(PairingClientError):
        companion.approve(displayed, DevicePolicy(scopes=SCOPES))
    pending = copy.deepcopy(store.record)
    with api.app.state.store.transaction() as connection:
        connection.execute("UPDATE pairing_attempts SET terminal_at=terminal_at-86401 WHERE id=?", (str(displayed.attempt_id),))
    # Normal creation performs the expired terminal-attempt cleanup.
    PairingClient(ORIGIN, MemoryStore(), _transport=transport).begin("Another computer", SCOPES)
    with api.app.state.store.transaction(write=False) as connection:
        assert connection.execute("SELECT id FROM pairing_attempts WHERE id=?", (str(displayed.attempt_id),)).fetchone() is None
    reopened = PairingClient(ORIGIN, store, _transport=transport)
    assert reopened.resume().device_id == displayed.device_id
    assert store.record["state"] == "paired" and store.record["approval"] == pending["approval"]
    assert len(api.get("/v1/devices", headers=headers).json()) == 1


def test_desktop_pairing_preserves_local_settings_and_projects_no_credentials(setup, tmp_path):
    api, headers, transport, store, _ = setup
    def factory(origin, credential_store):
        return PairingClient(origin, credential_store, _transport=transport)
    def controller():
        return CompanionController(tmp_path / "desktop", pairing_factory=factory, credential_store_factory=lambda _: store)
    desktop = controller()
    desktop.open()
    try:
        assert desktop.snapshot()["pairing"] == {"state": "unconfigured"}
        desktop.configure_pairing(ORIGIN + "/")
        desktop.start()
        assert desktop.pairing_status()["state"] == "unpaired" and not transport.requests
        created = desktop.begin_pairing("Desktop test", SCOPES)
        assert api.post("/v1/pairing/claim", json={"code": created["code"]}, headers=headers).status_code == 200
        reviewed = desktop.pairing_status(refresh=True)
        assert reviewed["claim"]["claimed_account"]["username"] == "alice"
        paired = desktop.approve_pairing(reviewed["claim"], DevicePolicy(scopes=SCOPES))
        assert paired["state"] == "paired" and not paired["remote_execution_enabled"]
        projection = json.dumps(desktop.snapshot())
        assert store.record["private_secret"] not in projection and store.record["approval"]["credential"] not in projection
        count = len(transport.requests)
        desktop.snapshot()
        assert len(transport.requests) == count  # No ambient connection from refresh of local runs.
        with pytest.raises(ConflictError, match="Revoke"):
            desktop.configure_pairing("https://another.test")
        assert desktop.stop()
        config = desktop.config.model_copy(update={"carla_port": 2100, "setup_completed": True})
        desktop.configure(config)
        assert desktop.config.coordinator_origin == ORIGIN
        with pytest.raises(ConflictError, match="Revoke"):
            desktop.configure(config.model_copy(update={"coordinator_origin": "https://another.test"}))
    finally:
        assert desktop.close()
    reopened = controller()
    reopened.open()
    try:
        assert reopened.config.carla_port == 2100 and reopened.config.setup_completed
        assert reopened.pairing_status()["state"] == "paired"
        assert reopened.pairing_status(refresh=True)["device_id"] == paired["device_id"]
        reopened.revoke_pairing()
        assert reopened.pairing_status()["state"] == "unpaired"
        reopened.configure_pairing("https://another.test")
    finally:
        assert reopened.close()


def test_invalid_server_payload_does_not_echo_credential_in_errors(setup):
    api, headers, transport, store, companion = setup
    secret = "pd_a_secret_echoed_by_an_invalid_server_payload"
    transport.changed_response = {"credential": secret, "not_a_pairing_message": True}
    with pytest.raises(PairingClientError) as error:
        companion.begin("This computer", SCOPES)
    assert secret not in str(error.value) and store.load() is None


@pytest.mark.skipif(os.name != "nt", reason="Native Windows credential integration")
def test_real_dpapi_store_integrates_with_coordinator_approval_and_reopen(setup, tmp_path):
    api, headers, transport, _, _ = setup
    store = WindowsCredentialStore(tmp_path / "CompanionPrototype")
    companion = PairingClient(ORIGIN, store, _transport=transport)
    created = companion.begin("Protected companion", SCOPES)
    assert api.post("/v1/pairing/claim", json={"code": created.code}, headers=headers).status_code == 200
    status = companion.approve(companion.poll(), DevicePolicy(scopes=SCOPES))
    record = store.load()
    assert record["approval"]["credential"].encode() not in store.path.read_bytes()
    assert record["private_secret"].encode() not in store.path.read_bytes()
    reopened = PairingClient(ORIGIN, WindowsCredentialStore(store.profile), _transport=transport)
    assert reopened.resume() == status
    reopened.revoke()
    assert store.load() is None
