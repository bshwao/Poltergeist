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


def test_explicit_consent_enables_only_the_current_connection(desktop):
    controller, store = desktop
    store.record = saved_approval()
    controller.start()
    displayed = controller.pairing_status()
    assert displayed['ownership_generation'] == 1
    enabled = controller.connect_relay(allow_remote_execution=True, displayed_status=copy.deepcopy(displayed))
    assert Relay.created[-1].enabled is True
    assert enabled == {'state': 'connected', 'owned': True, 'running': True,
        'admission': True, 'remote_execution_enabled': True}
    assert controller.snapshot()['relay'] == enabled
    assert 'allowed' in controller.snapshot()['hosted']
    diagnostics = controller.diagnostics()
    assert diagnostics['remote_execution_enabled'] is True
    assert diagnostics['relay'] == enabled
    assert controller.disconnect_relay()
    assert controller.relay_status() == {'state': 'disconnected', 'owned': False, 'running': False,
        'admission': False, 'remote_execution_enabled': False}
    paused = controller.connect_relay()
    assert Relay.created[-1].enabled is False and paused['remote_execution_enabled'] is False
    assert 'paused' in controller.snapshot()['hosted']


@pytest.mark.parametrize('choice', [1, 0, 'true', None, []], ids=['one', 'zero', 'text', 'none', 'list'])
def test_relay_consent_requires_an_actual_checkbox_bool(desktop, choice):
    controller, store = desktop
    store.record = saved_approval()
    controller.start()
    with pytest.raises(ValueError, match='checkbox'):
        controller.connect_relay(allow_remote_execution=choice, displayed_status=controller.pairing_status())
    assert not Relay.created and not controller.relay_status()['owned']


@pytest.mark.parametrize('field', ['missing', 'state', 'origin', 'device_id', 'owner_id', 'generation', 'policy', 'bool_generation'])
def test_missing_or_stale_displayed_consent_cannot_create_a_relay(desktop, field):
    controller, store = desktop
    store.record = saved_approval()
    controller.start()
    displayed = controller.pairing_status()
    if field == 'missing':
        displayed = None
    elif field == 'state':
        displayed['state'] = 'pending_approval'
    elif field == 'origin':
        displayed['origin'] = 'https://other.example'
    elif field in {'device_id', 'owner_id'}:
        displayed[field] = str(uuid4())
    elif field == 'generation':
        displayed['ownership_generation'] = 2
    elif field == 'bool_generation':
        displayed['ownership_generation'] = True
    else:
        displayed['policy']['max_duration_s'] = 60.
    with pytest.raises(ConflictError, match='review'):
        controller.connect_relay(allow_remote_execution=True, displayed_status=displayed)
    assert not Relay.created and not controller._relay_admission
    assert controller.relay_status()['remote_execution_enabled'] is False


def test_changed_protected_policy_rejects_previously_displayed_consent(desktop):
    controller, store = desktop
    store.record = saved_approval()
    controller.start()
    displayed = controller.pairing_status()
    store.record['approval']['policy']['max_duration_s'] = 60.
    with pytest.raises(ConflictError, match='Pairing changed'):
        controller.connect_relay(allow_remote_execution=True, displayed_status=displayed)
    assert not Relay.created
    current = controller.pairing_status()
    assert controller.connect_relay(allow_remote_execution=True, displayed_status=current)['remote_execution_enabled']


def test_failed_disconnect_truthfully_retains_ownership_but_disables_execution(desktop):
    controller, store = desktop
    store.record = saved_approval()
    controller.start()
    controller.connect_relay(allow_remote_execution=True, displayed_status=controller.pairing_status())
    relay = Relay.created[-1]
    relay.clean = False
    assert controller.disconnect_relay() is False
    expected = {'state': 'disconnecting', 'owned': True, 'running': True,
        'admission': False, 'remote_execution_enabled': False}
    assert controller.relay_status() == expected
    assert controller.snapshot()['relay'] == expected
    assert controller.diagnostics()['remote_execution_enabled'] is False
    with pytest.raises(ConflictError, match='Disconnect'):
        controller.connect_relay(allow_remote_execution=True, displayed_status=controller.pairing_status())
    assert controller.close() is False and controller._owned
    relay.clean = True
    assert controller.disconnect_relay()
    assert not controller.relay_status()['owned']


@pytest.mark.parametrize('change', ['owner', 'policy', 'credential', 'record_missing'])
def test_protected_approval_changes_fence_owned_relay_and_public_enabled_state(desktop, change):
    controller, store = desktop
    store.record = saved_approval()
    controller.start()
    controller.connect_relay(allow_remote_execution=True, displayed_status=controller.pairing_status())
    relay = Relay.created[-1]
    binding, _, _ = relay.enrollment()
    if change == 'owner':
        store.record['approval']['expected_account_id'] = str(uuid4())
    elif change == 'policy':
        store.record['approval']['policy']['max_duration_s'] = 60.
    elif change == 'credential':
        store.record['approval']['credential'] = 'pd_' + 'c' * 43
    else:
        store.record = None
    status = controller.relay_status()
    assert status['owned'] and status['running']
    assert not status['admission'] and not status['remote_execution_enabled']
    assert relay.service.dispatch(binding, StatusCommand(request_id=uuid4())).code == 'permission_denied'


def test_public_relay_projection_drops_extra_or_arbitrary_transport_details(desktop):
    controller, store = desktop
    store.record = saved_approval()
    controller.start()
    controller.connect_relay(allow_remote_execution=True, displayed_status=controller.pairing_status())
    relay = Relay.created[-1]
    relay.snapshot = lambda: {'state': 'offline', 'running': True, 'remote_execution_enabled': True,
        'error': store.record['approval']['credential'], 'path': 'C:/private/relay-sentinel'}
    public = controller.snapshot()
    assert public['relay'] == {'state': 'offline', 'owned': True, 'running': True,
        'admission': True, 'remote_execution_enabled': False}
    assert store.record['approval']['credential'] not in str(public)
    assert store.record['private_secret'] not in str(public)
    assert 'C:/private/relay-sentinel' not in str(public)


def test_existing_private_proof_constructor_flag_remains_explicit_and_compatible(tmp_path):
    store = Store()
    Relay.created = []
    controller = CompanionController(tmp_path / 'proof', relay_factory=Relay,
        credential_store_factory=lambda _: store, _private_remote_execution=True)
    controller.open()
    try:
        controller.configure_pairing('https://coordinator.example')
        store.record = saved_approval()
        controller.start()
        assert controller.connect_relay()['remote_execution_enabled'] is True
        assert Relay.created[-1].enabled is True
    finally:
        assert controller.close()


def test_partial_start_failure_retains_transport_ownership_until_cleanup(desktop):
    controller, store = desktop
    store.record = saved_approval()
    controller.start()
    class PartialStart(Relay):
        def start(self):
            self.running = True
            self.clean = False
            raise RuntimeError('pd_private_failure_sentinel')
    controller._relay_factory = PartialStart
    with pytest.raises(ConflictError, match='could not start') as error:
        controller.connect_relay(allow_remote_execution=True, displayed_status=controller.pairing_status())
    assert 'pd_private_failure_sentinel' not in str(error.value)
    status = controller.relay_status()
    assert status['owned'] and status['running'] and status['state'] == 'disconnecting'
    assert not status['admission'] and not status['remote_execution_enabled']
    assert controller._owned and controller._runtime is not None
    Relay.created[-1].clean = True
    assert controller.disconnect_relay()
