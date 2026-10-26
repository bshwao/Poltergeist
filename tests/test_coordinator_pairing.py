"""Two accounts, atomic local approval, replay, expiry and owner revocation."""

from concurrent.futures import ThreadPoolExecutor
import json
import secrets
from uuid import uuid4

import pytest

from coordinator.auth import AuthError, AuthService
from coordinator.pairing import PairingService
from coordinator.store import Store
from shared.models.coordination import DevicePolicy, PairingApproveInput, PairingCreateInput, PendingApprovalCancelInput


@pytest.fixture
def setup(tmp_path):
    now = [1800000000.]
    store = Store(tmp_path / "coordinator.db")
    store.initialize()
    auth = AuthService(store, now=lambda: now[0])
    alice = auth.register("alice", "alice's private passphrase")
    bob = auth.register("bob", "bob's private passphrase")
    service = PairingService(store, auth, now=lambda: now[0])
    yield service, auth, store, now, alice["account_id"], bob["account_id"]
    store.close()


def attempt(service):
    secret = "pa_" + secrets.token_urlsafe(32)
    request = PairingCreateInput(private_secret=secret, display_name="My computer",
        requested_scopes=["status:read", "run:execute:test", "run:cancel", "result:read"])
    return service.create(request, source="127.0.0.1"), secret


def approval(account_id, revision=2, **updates):
    request = {"request_id": str(uuid4()), "expected_account_id": account_id,
        "expected_revision": revision, "credential": "pd_" + secrets.token_urlsafe(32),
        "policy": {"scopes": ["status:read", "run:execute:test", "run:cancel", "result:read"]}}
    request.update(updates)
    return PairingApproveInput.model_validate(request)


def cancel_request(created, request, **changes):
    return PendingApprovalCancelInput.model_validate({"device_id": created.device_id,
        "owner_id": request.expected_account_id, "credential": request.credential, **changes})


@pytest.mark.parametrize("phase", ["claimed", "expired", "pruned"])
def test_cancel_uncommitted_approval_fences_delayed_approve_and_retries_after_terminal_cleanup(setup, phase):
    service, auth, store, now, alice, bob = setup
    created, secret = attempt(service)
    service.claim(alice, created.code, source="127.0.0.1")
    request = approval(alice)
    if phase != "claimed":
        now[0] += 300
        assert service.poll(created.attempt_id, secret).state == "expired"
        if phase == "pruned":
            now[0] += 86401
    cancellation = cancel_request(created, request)
    first = service.cancel_pending_approval(created.attempt_id, secret, cancellation)
    assert first.outcome == {"claimed": "cancelled", "expired": "expired", "pruned": "absent"}[phase]
    assert service.cancel_pending_approval(created.attempt_id, secret, cancellation) == first
    with pytest.raises(AuthError):
        service.approve(created.attempt_id, secret, request)
    assert service.devices(alice) == [] and service.devices(bob) == []


@pytest.mark.parametrize("pruned", [False, True])
@pytest.mark.parametrize("changed", ["device", "owner", "credential"])
def test_cancel_committed_approval_requires_exact_binding_and_revokes_only_that_device(setup, pruned, changed):
    service, auth, store, now, alice, bob = setup
    created, secret = attempt(service)
    service.claim(alice, created.code, source="127.0.0.1")
    request = approval(alice)
    service.approve(created.attempt_id, secret, request)
    other, other_secret = attempt(service)
    service.claim(bob, other.code, source="127.0.0.2")
    other_request = approval(bob)
    service.approve(other.attempt_id, other_secret, other_request)
    if pruned:
        now[0] += 86401
    changes = {"device": {"device_id": other.device_id}, "owner": {"owner_id": bob},
        "credential": {"credential": other_request.credential}}[changed]
    with pytest.raises(AuthError) as error:
        service.cancel_pending_approval(created.attempt_id, secret, cancel_request(created, request, **changes))
    assert error.value.status == 409
    assert service.authenticate_device(request.credential)["owner_id"] == alice
    assert service.authenticate_device(other_request.credential)["owner_id"] == bob
    outcome = service.cancel_pending_approval(created.attempt_id, secret, cancel_request(created, request))
    assert outcome.outcome == "revoked"
    assert service.cancel_pending_approval(created.attempt_id, secret, cancel_request(created, request)) == outcome
    with pytest.raises(AuthError):
        service.authenticate_device(request.credential)
    assert service.authenticate_device(other_request.credential)["owner_id"] == bob


def test_cancel_approval_race_serializes_and_never_leaves_a_live_credential(setup):
    service, auth, store, now, alice, bob = setup
    created, secret = attempt(service)
    service.claim(alice, created.code, source="127.0.0.1")
    request = approval(alice)
    def approve():
        try:
            return service.approve(created.attempt_id, secret, request)
        except AuthError as failure:
            assert failure.status == 409
            return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        approving = executor.submit(approve)
        cancelling = executor.submit(service.cancel_pending_approval, created.attempt_id, secret, cancel_request(created, request))
        approving.result()
        assert cancelling.result().outcome in ("cancelled", "revoked")
    with pytest.raises(AuthError):
        service.authenticate_device(request.credential)
    with store.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM devices WHERE revoked_at IS NULL").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM devices").fetchone()[0] <= 1
        assert connection.execute("SELECT count(*) FROM device_credentials").fetchone()[0] <= 1


def test_cancel_existing_attempt_needs_its_private_secret_and_rejects_newer_device_generation(setup):
    service, auth, store, now, alice, bob = setup
    created, secret = attempt(service)
    service.claim(alice, created.code, source="127.0.0.1")
    request = approval(alice)
    service.approve(created.attempt_id, secret, request)
    with pytest.raises(AuthError) as error:
        service.cancel_pending_approval(created.attempt_id, "pa_" + secrets.token_urlsafe(32), cancel_request(created, request))
    assert error.value.status == 401
    with store.transaction() as connection:
        connection.execute("UPDATE devices SET ownership_generation=2 WHERE id=?", (str(created.device_id),))
    with pytest.raises(AuthError) as error:
        service.cancel_pending_approval(created.attempt_id, secret, cancel_request(created, request))
    assert error.value.status == 409
    with store.transaction(write=False) as connection:
        assert connection.execute("SELECT revoked_at FROM devices WHERE id=?", (str(created.device_id),)).fetchone()[0] is None


def test_claim_requires_local_approval_and_private_secrets_never_persist(setup):
    service, auth, store, now, alice, bob = setup
    created, secret = attempt(service)
    claimed = service.claim(alice, created.code, source="127.0.0.1")
    assert claimed.state == "claimed" and str(claimed.claimed_account.account_id) == alice
    assert service.devices(alice) == [] and service.devices(bob) == []
    private_poll = service.poll(created.attempt_id, secret)
    assert private_poll.claimed_account.username == "alice" and private_poll.revision == 2
    request = approval(alice)
    device = service.approve(created.attempt_id, secret, request)
    assert str(device.owner.account_id) == alice and device.policy.max_duration_s == 120.
    authenticated = service.authenticate_device(request.credential)
    assert authenticated["device_id"] == str(created.device_id) and authenticated["owner_id"] == alice
    assert len(service.devices(alice)) == 1 and service.devices(bob) == []
    with store.transaction(write=False) as connection:
        rows = json.dumps({table: [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]
            for table in ("pairing_attempts", "device_credentials", "security_events")})
    assert secret not in rows and created.code not in rows and request.credential not in rows


def test_competing_claims_cannot_substitute_owner_or_increase_grants(setup):
    service, auth, store, now, alice, bob = setup
    created, secret = attempt(service)
    service.claim(alice, created.code, source="127.0.0.1")
    with pytest.raises(AuthError) as error:
        service.claim(bob, created.code, source="127.0.0.2")
    assert error.value.code == "pairing_unavailable"
    with pytest.raises(AuthError) as error:
        service.approve(created.attempt_id, secret, approval(bob))
    assert error.value.status == 409
    with pytest.raises(AuthError) as error:
        service.approve(created.attempt_id, secret, approval(alice, policy={"scopes": ["run:execute:carla"]}))
    assert error.value.status == 403
    assert service.devices(alice) == []


def test_approval_retry_is_idempotent_after_server_restart_and_changes_conflict(setup):
    service, auth, store, now, alice, bob = setup
    created, secret = attempt(service)
    service.claim(alice, created.code, source="127.0.0.1")
    request = approval(alice)
    first = service.approve(created.attempt_id, secret, request)
    reopened_store = Store(store.path)
    reopened_store.initialize()
    restarted = PairingService(reopened_store, auth, now=lambda: now[0])
    try:
        assert restarted.approve(created.attempt_id, secret, request) == first
        changed = request.model_copy(update={"credential": "pd_" + secrets.token_urlsafe(32)})
        with pytest.raises(AuthError) as error:
            restarted.approve(created.attempt_id, secret, changed)
        assert error.value.status == 409
        assert len(restarted.devices(alice)) == 1
    finally:
        reopened_store.close()


@pytest.mark.parametrize("phase", ["pending", "claimed"])
def test_exact_expiry_boundary_cannot_approve_or_claim(setup, phase):
    service, auth, store, now, alice, bob = setup
    created, secret = attempt(service)
    if phase == "claimed":
        service.claim(alice, created.code, source="127.0.0.1")
    now[0] += 300
    assert service.poll(created.attempt_id, secret).state == "expired"
    with pytest.raises(AuthError) as error:
        service.claim(alice, created.code, source="127.0.0.1")
    assert error.value.code == "pairing_unavailable"
    with pytest.raises(AuthError):
        service.approve(created.attempt_id, secret, approval(alice))
    assert service.devices(alice) == []


def test_private_poll_and_denial_need_the_secret_and_terminal_states_do_not_reopen(setup):
    service, auth, store, now, alice, bob = setup
    created, secret = attempt(service)
    for bad in (created.code, "pa_" + secrets.token_urlsafe(32)):
        with pytest.raises(AuthError) as error:
            service.poll(created.attempt_id, bad)
        assert error.value.status == 401
    denied = service.deny(created.attempt_id, secret, 1)
    assert denied.state == "denied"
    assert service.deny(created.attempt_id, secret, 1) == denied
    with pytest.raises(AuthError):
        service.claim(alice, created.code, source="127.0.0.1")
    with pytest.raises(AuthError):
        service.approve(created.attempt_id, secret, approval(alice))


def test_racing_claims_have_one_winner_and_replay_cannot_create_another_device(setup):
    service, auth, store, now, alice, bob = setup
    created, secret = attempt(service)
    def claim(owner):
        try:
            return service.claim(owner, created.code, source=owner).claimed_account.account_id
        except AuthError:
            return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, (alice, bob)))
    assert sum(value is not None for value in results) == 1
    winner = str(next(value for value in results if value is not None))
    request = approval(winner)
    with ThreadPoolExecutor(max_workers=2) as executor:
        devices = list(executor.map(lambda _: service.approve(created.attempt_id, secret, request), range(2)))
    assert devices[0] == devices[1]
    with store.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM devices").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM device_credentials").fetchone()[0] == 1


def test_other_owner_gets_uniform_not_found_and_revoked_credentials_stay_revoked(setup):
    service, auth, store, now, alice, bob = setup
    created, secret = attempt(service)
    service.claim(alice, created.code, source="127.0.0.1")
    request = approval(alice)
    service.approve(created.attempt_id, secret, request)
    for identifier in (created.device_id, uuid4()):
        for operation in (service.device, service.revoke):
            with pytest.raises(AuthError) as error:
                operation(bob, identifier)
            assert (error.value.status, error.value.code) == (404, "resource_not_found")
    assert service.authenticate_device(request.credential)["owner_id"] == alice
    assert service.revoke(alice, created.device_id).revoked_at is not None
    restarted = PairingService(store, auth, now=lambda: now[0])
    with pytest.raises(AuthError):
        restarted.authenticate_device(request.credential)
    with pytest.raises(AuthError):
        restarted.approve(created.attempt_id, secret, request)
    assert len(restarted.devices(alice)) == 1


def test_guessing_limit_survives_restart_and_wrong_purpose_credentials_fail(setup):
    service, auth, store, now, alice, bob = setup
    for _ in range(10):
        with pytest.raises(AuthError) as error:
            service.claim(alice, "AAAAAAAAAA", source="127.0.0.1")
        assert error.value.code == "pairing_unavailable"
    restarted = PairingService(store, auth, now=lambda: now[0])
    with pytest.raises(AuthError) as error:
        restarted.claim(alice, "AAAAAAAAAA", source="127.0.0.1")
    assert error.value.status == 429
    session = auth.login("alice", "alice's private passphrase")
    with pytest.raises(AuthError):
        restarted.authenticate_device(session["session_token"])


def test_policy_and_pairing_inputs_reject_paths_unknown_fields_and_unbounded_work():
    from pydantic import ValidationError
    for change in ({"max_duration_s": 121.}, {"max_ticks": 10001}, {"max_waiting_runs": 11},
                   {"allow_map_reload": True}, {"allow_raw_capture": True}, {"python_executable": "C:/private/python.exe"},
                   {"scopes": ["status:read", "status:read"]}):
        with pytest.raises(ValidationError):
            DevicePolicy.model_validate({"scopes": ["status:read"], **change})
