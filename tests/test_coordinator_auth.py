"""Account isolation, real hashing, revocation and durable rate limits."""

from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3

from argon2 import PasswordHasher
import pytest

from coordinator.auth import AuthError, AuthService, hash_token
from coordinator.store import Store, StoreError

PASSWORD = "  a private passphrase with spaces  "


@pytest.fixture
def setup(tmp_path):
    now = [1800000000.]
    store = Store(tmp_path / "coordinator.db")
    store.initialize()
    service = AuthService(store, now=lambda: now[0])
    yield service, store, now
    store.close()


def test_constructor_does_not_initialize_and_foreign_database_is_preserved(tmp_path):
    path = tmp_path / "subdir/coordinator.db"
    store = Store(path)
    assert not path.exists()
    with pytest.raises(StoreError, match="Initialize"):
        with store.transaction():
            pass
    foreign = tmp_path / "local-app.db"
    with sqlite3.connect(foreign) as connection:
        connection.execute("CREATE TABLE user_results(value TEXT)")
        connection.execute("INSERT INTO user_results VALUES ('preserve')")
    original = foreign.read_bytes()
    with pytest.raises(StoreError, match="another application"):
        Store(foreign).initialize()
    assert foreign.read_bytes() == original


def test_real_hashes_and_opaque_sessions_do_not_store_password_or_bearer(setup):
    auth, store, now = setup
    account = auth.register("First_User", PASSWORD)
    login = auth.login("FIRST_USER", PASSWORD, "127.0.0.1")
    assert account == {"account_id": login["account_id"], "username": "first_user"}
    session = auth.session(login["session_token"])
    assert session["account_id"] == account["account_id"] and "session_token" not in session
    with store.transaction(write=False) as connection:
        saved = connection.execute("SELECT password_hash FROM accounts").fetchone()[0]
        bearer_hash = connection.execute("SELECT token_hash FROM sessions").fetchone()[0]
    assert saved.startswith("$argon2id$") and auth.hasher.verify(saved, PASSWORD)
    assert bearer_hash == hash_token(login["session_token"])
    assert PASSWORD not in saved and login["session_token"] not in store.path.read_bytes().decode("latin1")
    assert login["session_token"].startswith("ps_")
    with pytest.raises(AuthError) as error:
        auth.session("pd_" + login["session_token"][3:])
    assert error.value.status == 401


def test_password_whitespace_is_preserved_and_bad_values_cannot_alias_a_valid_password(setup):
    auth, store, now = setup
    auth.register("spaces", PASSWORD)
    with pytest.raises(AuthError) as error:
        auth.login("spaces", PASSWORD.strip())
    assert error.value.code == "invalid_credentials"
    auth.register("sentinel", "invalid-login-value")
    for value in ("short", None, "\ud800" * 15):
        with pytest.raises(AuthError) as error:
            auth.login("sentinel", value)
        assert error.value.status == 401


@pytest.mark.parametrize("username,password", [("aa", PASSWORD), ("user name", PASSWORD),
    ("usér", PASSWORD), ("valid", "short"), ("valid", "x" * 129), ("valid", "\ud800" * 15)])
def test_invalid_registration_never_writes_an_account(setup, username, password):
    auth, store, now = setup
    with pytest.raises(AuthError) as error:
        auth.register(username, password)
    assert error.value.status == 400
    with store.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM accounts").fetchone()[0] == 0


def test_idle_absolute_expiry_logout_and_restart(setup):
    auth, store, now = setup
    auth.register("user", PASSWORD)
    first = auth.login("user", PASSWORD)
    recreated = AuthService(store, now=lambda: now[0])
    assert recreated.session(first["session_token"])["csrf_token"] == first["csrf_token"]
    now[0] += 1800
    with pytest.raises(AuthError):
        recreated.session(first["session_token"])
    second = auth.login("user", PASSWORD)
    for _ in range(48):
        now[0] += 1799
        recreated.session(second["session_token"])
    now[0] += 100
    with pytest.raises(AuthError):
        recreated.session(second["session_token"])
    third = auth.login("user", PASSWORD)
    recreated.logout(third["session_token"])
    with pytest.raises(AuthError):
        auth.session(third["session_token"])


def test_password_change_revokes_all_account_sessions_but_not_another_account(setup):
    auth, store, now = setup
    auth.register("first", PASSWORD)
    auth.register("second", PASSWORD)
    first, another = auth.login("first", PASSWORD), auth.login("first", PASSWORD)
    second = auth.login("second", PASSWORD)
    auth.change_password(first["session_token"], PASSWORD, "replacement private passphrase")
    for token in (first["session_token"], another["session_token"]):
        with pytest.raises(AuthError):
            auth.session(token)
    assert auth.session(second["session_token"])["account_id"] == second["account_id"]
    assert auth.login("first", "replacement private passphrase")["account_id"] == first["account_id"]
    with pytest.raises(AuthError):
        auth.login("first", PASSWORD)


def test_failed_login_limits_persist_across_service_restart_and_expire(setup):
    auth, store, now = setup
    for _ in range(5):
        with pytest.raises(AuthError) as error:
            auth.login("unknown", PASSWORD, "192.0.2.1")
        assert error.value.status == 401
    recreated = AuthService(store, now=lambda: now[0])
    with pytest.raises(AuthError) as error:
        recreated.login("unknown", PASSWORD, "192.0.2.2")
    assert error.value.status == 429 and error.value.retry_after > 0
    now[0] += 901
    with pytest.raises(AuthError) as error:
        recreated.login("unknown", PASSWORD, "192.0.2.1")
    assert error.value.status == 401
    with store.transaction(write=False) as connection:
        events = json.dumps([dict(row) for row in connection.execute("SELECT * FROM security_events")])
        counters = json.dumps([dict(row) for row in connection.execute("SELECT * FROM security_counters")])
    assert PASSWORD not in events + counters and "192.0.2.1" not in counters


def test_registration_race_creates_one_account_and_login_rehashes_old_parameters(setup):
    auth, store, now = setup
    def register():
        try:
            return auth.register("race", PASSWORD)
        except AuthError as error:
            return error.status
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: register(), range(2)))
    assert sum(isinstance(value, dict) for value in outcomes) == 1 and 409 in outcomes
    weak = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1).hash(PASSWORD)
    with store.transaction() as connection:
        connection.execute("UPDATE accounts SET password_hash=? WHERE username='race'", (weak,))
    auth.login("race", PASSWORD)
    with store.transaction(write=False) as connection:
        encoded = connection.execute("SELECT password_hash FROM accounts WHERE username='race'").fetchone()[0]
    assert encoded != weak and not auth.hasher.check_needs_rehash(encoded)


def test_transactions_rollback_and_closed_store_refuses_new_operations(setup):
    auth, store, now = setup
    with pytest.raises(RuntimeError):
        with store.transaction() as connection:
            connection.execute("INSERT INTO accounts(id,username,password_hash,created_at) VALUES ('a','rollback','hash',?)", (now[0],))
            raise RuntimeError("rollback")
    with store.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM accounts").fetchone()[0] == 0
    store.close()
    with pytest.raises(StoreError):
        store.initialize()


def test_password_change_cannot_commit_after_its_session_is_revoked(setup, monkeypatch):
    auth, store, _ = setup
    auth.register("alice", PASSWORD)
    login = auth.login("alice", PASSWORD)
    original_hash = auth._hash
    def revoke_before_hash(value):
        auth.logout(login["session_token"])
        return original_hash(value)
    monkeypatch.setattr(auth, "_hash", revoke_before_hash)
    with pytest.raises(AuthError) as error:
        auth.change_password(login["session_token"], PASSWORD, "replacement private passphrase")
    assert error.value.status == 401
    monkeypatch.setattr(auth, "_hash", original_hash)
    assert auth.login("alice", PASSWORD)["account_id"] == login["account_id"]


def test_invalid_service_username_never_aliases_a_real_account(setup):
    auth, _, _ = setup
    auth.register("invalid", PASSWORD)
    for name in (None, "\ud800", "name with spaces"):
        with pytest.raises(AuthError) as error:
            auth.login(name, PASSWORD)
        assert error.value.status == 401
