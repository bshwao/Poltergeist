"""Browser CSRF/cookies and device-only pairing boundaries over ASGI."""

import os
import secrets
import subprocess
import sys
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from coordinator.app import COOKIE, create_app
from coordinator.settings import CoordinatorSettings, service_origin

ORIGIN = "https://coordinator.test"
PASSWORD = "  preserve these private spaces  "


@pytest.fixture
def client(tmp_path):
    settings = CoordinatorSettings(tmp_path / "coordinator.db", ORIGIN)
    with TestClient(create_app(settings, now=lambda: 1800000000.), base_url=ORIGIN) as value:
        yield value


def account(client, name):
    headers = {"Origin": ORIGIN}
    response = client.post("/v1/auth/register", json={"username": name, "password": PASSWORD}, headers=headers)
    assert response.status_code == 201, response.text
    login = client.post("/v1/auth/login", json={"username": name, "password": PASSWORD}, headers=headers)
    assert login.status_code == 200, login.text
    payload = login.json()
    token = login.cookies[COOKIE]
    client.cookies.clear()
    headers.update({"Cookie": f"{COOKIE}={token}", "X-CSRF-Token": payload["csrf_token"], "X-Poltergeist-Client": "browser"})
    return payload["account"], headers, login


def pairing(client, owner_headers, *, approval=True):
    secret = "pa_" + secrets.token_urlsafe(32)
    created = client.post("/v1/pairing", json={"private_secret": secret, "display_name": "Local computer",
        "requested_scopes": ["status:read", "run:execute:test"]})
    assert created.status_code == 201, created.text
    created = created.json()
    claim = client.post("/v1/pairing/claim", json={"code": created["code"]}, headers=owner_headers)
    assert claim.status_code == 200, claim.text
    credential = "pd_" + secrets.token_urlsafe(32)
    if approval:
        response = client.post(f"/v1/pairing/{created['attempt_id']}/approve", headers={"Authorization": "Pairing " + secret},
            json={"request_id": str(uuid4()), "expected_account_id": claim.json()["claimed_account"]["account_id"],
                "expected_revision": claim.json()["revision"], "credential": credential,
                "policy": {"scopes": ["status:read", "run:execute:test"]}})
        assert response.status_code == 200, response.text
    return created, secret, credential


def test_factory_has_no_backend_side_effects_or_dotenv_inheritance(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///private-user-database.db")
    target = tmp_path / "not-created/coordinator.db"
    app = create_app(CoordinatorSettings(target, ORIGIN))
    assert not target.exists()
    code = "import coordinator.app,sys; assert 'backend.main' not in sys.modules; assert 'simulator' not in sys.modules; assert 'carla' not in sys.modules"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_cookie_flags_password_preservation_and_read_only_csrf_bootstrap(client):
    identity, headers, login = account(client, "alice")
    cookie = login.headers["set-cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie
    assert "domain=" not in cookie and PASSWORD not in login.text and "ps_" not in login.text
    assert client.get("/v1/auth/session", headers={"Cookie": headers["Cookie"]}).status_code == 403
    first = client.get("/v1/auth/session", headers=headers)
    second = client.get("/v1/auth/session", headers=headers)
    assert first.status_code == 200 and first.json()["account"] == identity
    assert first.json()["csrf_token"] == second.json()["csrf_token"]
    assert first.headers["cache-control"] == "no-store" and first.headers["x-content-type-options"] == "nosniff"
    assert client.post("/v1/auth/logout", json={}, headers={key: value for key, value in headers.items() if key != "X-CSRF-Token"}).status_code == 403
    assert client.post("/v1/auth/logout", json={}, headers={**headers, "X-CSRF-Token": "wrong"}).status_code == 403
    assert client.post("/v1/auth/logout", json={}, headers=headers).status_code == 204
    assert client.get("/v1/auth/session", headers=headers).status_code == 401


def test_cross_origin_unknown_fields_size_and_local_api_routes_are_rejected(client):
    identity, headers, login = account(client, "alice")
    assert client.post("/v1/auth/login", json={"username": "alice", "password": PASSWORD}, headers={"Origin": "https://other.test"}).status_code == 403
    assert client.get("/v1/auth/session", headers={**headers, "Origin": "https://other.test"}).status_code == 403
    assert client.options("/v1/auth/session", headers={"Origin": "https://other.test", "Access-Control-Request-Method": "GET"}).status_code == 403
    invalid = client.post("/v1/auth/login", headers={"Origin": ORIGIN}, json={"username": "alice", "password": PASSWORD, "owner_id": str(uuid4())})
    assert invalid.status_code == 400 and PASSWORD not in invalid.text and "owner_id" not in invalid.text
    large = client.post("/v1/auth/login", content=b"x" * 32769, headers={"Content-Type": "application/json", "Origin": ORIGIN})
    assert large.status_code == 413
    for route in ("/agents", "/readiness", "/simulations", "/optimizations", "/artifacts", "/openapi.json"):
        assert client.get(route).status_code == 404


def test_two_account_device_isolation_private_approval_and_revoke(client):
    alice, alice_headers, _ = account(client, "alice")
    bob, bob_headers, _ = account(client, "bob")
    created, secret, credential = pairing(client, alice_headers)
    device_id = created["device_id"]
    assert client.get("/v1/devices", headers=bob_headers).json() == []
    for identifier in (device_id, str(uuid4())):
        assert client.get(f"/v1/devices/{identifier}", headers=bob_headers).status_code == 404
        assert client.post(f"/v1/devices/{identifier}/revoke", json={}, headers=bob_headers).status_code == 404
    assert client.get(f"/v1/devices/{device_id}", headers=alice_headers).json()["owner"]["account_id"] == alice["account_id"]
    poll = f"/v1/pairing/{created['attempt_id']}"
    assert client.get(poll, headers={"Authorization": "Pairing " + created["code"]}).status_code == 401
    assert client.get(poll, headers=alice_headers).status_code == 403
    assert client.get(poll, headers={"Authorization": "Pairing " + secret}).json()["state"] == "approved"
    device_auth = {"Authorization": "Bearer " + credential}
    connection = client.get("/v1/device/session", headers=device_auth)
    assert connection.status_code == 200 and connection.json()["owner_id"] == alice["account_id"]
    assert connection.json()["remote_execution_enabled"] is False
    assert client.get("/v1/device/session", headers={**device_auth, "Origin": ORIGIN}).status_code == 403
    assert client.get("/v1/device/session", headers={"Authorization": "Bearer " + alice_headers["Cookie"].split("=", 1)[1]}).status_code == 401
    assert client.post(f"/v1/devices/{device_id}/revoke", json={}, headers=alice_headers).status_code == 200
    assert client.get("/v1/device/session", headers=device_auth).status_code == 401


def test_browser_claim_alone_does_not_create_owned_device(client):
    alice, headers, _ = account(client, "alice")
    created, secret, credential = pairing(client, headers, approval=False)
    assert client.get("/v1/devices", headers=headers).json() == []
    assert client.get("/v1/device/session", headers={"Authorization": "Bearer " + credential}).status_code == 401
    poll = client.get(f"/v1/pairing/{created['attempt_id']}", headers={"Authorization": "Pairing " + secret})
    assert poll.status_code == 200 and poll.json()["claimed_account"]["account_id"] == alice["account_id"]


@pytest.mark.parametrize("value", ["http://example.com", "https://user:password@example.com", "https://example.com/path", "https://example.com?a=b",
    "https://example.com#fragment", "https://foo\\bar", "https://example..com", "https://example.com:99999"])
def test_configured_origin_rejects_unsafe_or_ambiguous_service_addresses(value):
    with pytest.raises(ValueError):
        service_origin(value)


def test_insecure_transport_is_explicit_test_only_and_restricted_to_loopback():
    assert service_origin("http://127.0.0.1:8910", allow_insecure_loopback=True) == "http://127.0.0.1:8910"
    with pytest.raises(ValueError):
        service_origin("http://example.com", allow_insecure_loopback=True)


def test_login_rotates_and_revokes_the_previous_browser_cookie(client):
    _, headers, _ = account(client, "alice")
    first = headers["Cookie"]
    login = client.post("/v1/auth/login", json={"username": "alice", "password": PASSWORD}, headers=headers)
    assert login.status_code == 200
    second = login.cookies[COOKIE]
    client.cookies.clear()
    assert second not in first
    assert client.get("/v1/auth/session", headers={**headers, "Cookie": first}).status_code == 401
    assert client.get("/v1/auth/session", headers={**headers, "Cookie": f"{COOKIE}={second}"}).status_code == 200


@pytest.mark.parametrize("header", ["Origin", "Authorization", "Cookie", "X-CSRF-Token", "Content-Type"])
def test_duplicate_security_headers_are_rejected(client, header):
    response = client.post("/v1/auth/login", content=b'{}',
        headers=[("Origin", ORIGIN), ("Content-Type", "application/json"), (header, "first"), (header, "second")])
    assert response.status_code == 400
