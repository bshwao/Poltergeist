"""Isolated account, pairing and outbound-device relay ASGI factory."""

from contextlib import asynccontextmanager
import hmac
import re
import sqlite3
import time
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from shared.models.coordination import (AccountIdentity, AccountInput, BrowserSession,
    EmptyInput, PairingApproveInput, PairingClaimInput, PairingCreateInput,
    PairingDenyInput, PasswordChangeInput)
from shared.models.remote import parse_message

from .auth import AuthError, AuthService
from .commands import CommandService
from .pairing import PairingService, timestamp
from .relay import RelayHub
from .settings import CoordinatorSettings, service_origin
from .store import Store, StoreError

COOKIE = "__Host-poltergeist-session"


def failure(status, code, *, retry_after=None):
    response = JSONResponse({"error": {"code": code, "message": "The request could not be completed."}}, status_code=status)
    response.headers["Cache-Control"] = "no-store"
    if retry_after is not None:
        response.headers["Retry-After"] = str(retry_after)
    return response


class RequestBoundary:
    def __init__(self, app, *, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            raw = scope["headers"]
            headers = {key.lower(): value for key, value in raw}
            guarded = (b"host", b"origin", b"authorization", b"content-type", b"content-length", b"cookie", b"x-csrf-token", b"sec-websocket-protocol")
            forbidden = (scope.get("query_string") or scope.get("subprotocols") or b"sec-websocket-protocol" in headers or
                b"origin" in headers or b"cookie" in headers or
                any(sum(key.lower() == name for key, value in raw) > 1 for name in guarded) or
                sum(len(key) + len(value) for key, value in raw) > 16384)
            try:
                scheme = {"wss": "https", "ws": "http"}.get(scope["scheme"], scope["scheme"])
                actual = service_origin(scheme + "://" + headers.get(b"host", b"").decode("ascii"),
                    allow_insecure_loopback=self.settings.test_allow_insecure_loopback)
            except (ValueError, UnicodeError):
                actual = None
            if forbidden or actual != self.settings.public_origin:
                await send({"type": "websocket.close", "code": 1008})
                return
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope["headers"]}
        guarded = (b"host", b"origin", b"authorization", b"content-type", b"content-length", b"cookie", b"x-csrf-token")
        if any(sum(key.lower() == name for key, value in scope["headers"]) > 1 for name in guarded):
            await failure(400, "ambiguous_headers")(scope, receive, send)
            return
        if sum(len(key) + len(value) for key, value in scope["headers"]) > 16384:
            await failure(431, "headers_too_large")(scope, receive, send)
            return
        try:
            actual = service_origin(scope["scheme"] + "://" + headers.get(b"host", b"").decode("ascii"),
                allow_insecure_loopback=self.settings.test_allow_insecure_loopback)
        except (ValueError, UnicodeError):
            actual = None
        if actual != self.settings.public_origin:
            await failure(400, "invalid_origin")(scope, receive, send)
            return
        if scope["method"] == "OPTIONS":
            await failure(403, "cross_origin_denied")(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.settings.max_http_body_bytes:
                await failure(413, "request_too_large")(scope, receive, send)
                return
            if not message.get("more_body", False):
                break
        delivered = False
        async def limited_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()
        async def secure_send(message):
            if message["type"] == "http.response.start":
                values = [(key, value) for key, value in message.get("headers", [])
                          if key.lower() not in (b"cache-control", b"x-content-type-options", b"referrer-policy")]
                values.extend([(b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
                               (b"referrer-policy", b"no-referrer")])
                message = {**message, "headers": values}
            await send(message)
        await self.app(scope, limited_receive, secure_send)


def create_app(settings: CoordinatorSettings, *, now=time.time):
    store = Store(settings.database_path)

    @asynccontextmanager
    async def lifespan(app):
        store.initialize()
        app.state.auth = AuthService(store, now=now)
        app.state.pairing = PairingService(store, app.state.auth, now=now, ttl_s=settings.pairing_ttl_s)
        app.state.commands = CommandService(store, now=now)
        app.state.relay = RelayHub(app.state.commands)
        try:
            yield
        finally:
            await app.state.relay.close()
            store.close()

    app = FastAPI(title="Poltergeist coordinator", lifespan=lifespan,
        docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.add_middleware(RequestBoundary, settings=settings)

    @app.exception_handler(AuthError)
    async def auth_error(request, error):
        return failure(error.status, error.code, retry_after=error.retry_after)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        return failure(400, "invalid_request")

    @app.exception_handler(StoreError)
    @app.exception_handler(sqlite3.Error)
    async def persistence_error(request, error):
        return failure(503, "coordinator_unavailable", retry_after=3)

    def browser(request, *, write=False, authenticated=True):
        origin = request.headers.get("origin")
        if origin is not None and origin != settings.public_origin:
            raise AuthError(403, "cross_origin_denied")
        if write:
            if origin != settings.public_origin or request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
                raise AuthError(403, "cross_origin_denied")
        elif request.headers.get("x-poltergeist-client") != "browser":
            raise AuthError(403, "browser_header_required")
        if not authenticated:
            return None
        account = request.app.state.auth.session(request.cookies.get(COOKIE))
        if write:
            supplied = request.headers.get("x-csrf-token", "")
            if not re.fullmatch(r"[A-Za-z0-9_-]{43}", supplied) or not hmac.compare_digest(supplied, account["csrf_token"]):
                raise AuthError(403, "csrf_required")
        return account

    def device_request(request):
        if request.headers.get("origin") is not None or request.headers.get("cookie") is not None:
            raise AuthError(403, "device_client_required")
        if request.method == "POST" and request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
            raise AuthError(400, "invalid_request")

    def private_secret(request):
        device_request(request)
        authorization = request.headers.get("authorization", "")
        if not re.fullmatch(r"Pairing pa_[A-Za-z0-9_-]{43}", authorization):
            raise AuthError(401, "pairing_authorization_required")
        return authorization[8:]

    def source(request):
        return request.client.host if request.client else "unknown"

    @app.get("/healthz")
    def health():
        return {"status": "ok", "protocol_version": "poltergeist-coordinator-v1"}

    @app.post("/v1/auth/register", status_code=201)
    def register(body: AccountInput, request: Request):
        browser(request, write=True, authenticated=False)
        return AccountIdentity(**request.app.state.auth.register(body.username, body.password.get_secret_value(), source=source(request)))

    @app.post("/v1/auth/login")
    def login(body: AccountInput, request: Request, response: Response):
        browser(request, write=True, authenticated=False)
        result = request.app.state.auth.login(body.username, body.password.get_secret_value(), source(request))
        previous = request.cookies.get(COOKIE)
        if previous is not None:
            try:
                request.app.state.auth.logout(previous)
            except AuthError as error:
                if error.status != 401:
                    raise
        response.set_cookie(COOKIE, result["session_token"], max_age=86400,
            expires=timestamp(result["expires_at"]), secure=True, httponly=True, samesite="lax", path="/")
        response.headers["Cache-Control"] = "no-store"
        return BrowserSession(account=AccountIdentity(account_id=result["account_id"], username=result["username"]),
            csrf_token=result["csrf_token"], expires_at=timestamp(result["expires_at"]))

    @app.get("/v1/auth/session")
    def session(request: Request, response: Response):
        account = browser(request)
        response.headers["Cache-Control"] = "no-store"
        return BrowserSession(account=AccountIdentity(account_id=account["account_id"], username=account["username"]),
            csrf_token=account["csrf_token"], expires_at=timestamp(account["expires_at"]))

    @app.post("/v1/auth/logout", status_code=204)
    def logout(body: EmptyInput, request: Request):
        browser(request, write=True)
        request.app.state.auth.logout(request.cookies.get(COOKIE))
        response = Response(status_code=204)
        response.delete_cookie(COOKIE, secure=True, httponly=True, samesite="lax", path="/")
        return response

    @app.post("/v1/auth/password", status_code=204)
    def password(body: PasswordChangeInput, request: Request):
        browser(request, write=True)
        request.app.state.auth.change_password(request.cookies.get(COOKIE), body.old_password.get_secret_value(), body.new_password.get_secret_value())
        response = Response(status_code=204)
        response.delete_cookie(COOKIE, secure=True, httponly=True, samesite="lax", path="/")
        return response

    @app.post("/v1/pairing", status_code=201)
    def create_pairing(body: PairingCreateInput, request: Request):
        device_request(request)
        return request.app.state.pairing.create(body, source=source(request))

    @app.post("/v1/pairing/claim")
    def claim(body: PairingClaimInput, request: Request):
        account = browser(request, write=True)
        return request.app.state.pairing.claim(account["account_id"], body.code, source=source(request))

    @app.get("/v1/pairing/{attempt_id}")
    def poll(attempt_id: UUID, request: Request):
        secret = private_secret(request)
        result = request.app.state.pairing.poll(attempt_id, secret)
        request.app.state.auth.rate_limit("pair-poll:" + str(attempt_id), 20, 60)
        return result

    @app.post("/v1/pairing/{attempt_id}/approve")
    def approve(attempt_id: UUID, body: PairingApproveInput, request: Request):
        secret = private_secret(request)
        request.app.state.pairing.poll(attempt_id, secret)
        request.app.state.auth.rate_limit("pair-approve:" + str(attempt_id), 20, 60)
        return request.app.state.pairing.approve(attempt_id, secret, body)

    @app.post("/v1/pairing/{attempt_id}/deny")
    def deny(attempt_id: UUID, body: PairingDenyInput, request: Request):
        return request.app.state.pairing.deny(attempt_id, private_secret(request), body.expected_revision)

    @app.get("/v1/devices")
    def devices(request: Request):
        return request.app.state.pairing.devices(browser(request)["account_id"])

    @app.get("/v1/devices/{device_id}")
    def device(device_id: UUID, request: Request):
        return request.app.state.pairing.device(browser(request)["account_id"], device_id)

    @app.post("/v1/devices/{device_id}/revoke")
    async def revoke(device_id: UUID, body: EmptyInput, request: Request):
        result = request.app.state.pairing.revoke(browser(request, write=True)["account_id"], device_id)
        await request.app.state.relay.disconnect(device_id)
        return result

    @app.post("/v1/devices/{device_id}/commands", status_code=202)
    async def command(device_id: UUID, request: Request):
        account = browser(request, write=True)
        try:
            body = parse_message(await request.body(), relay=False)
        except ValueError:
            raise AuthError(400, "invalid_request") from None
        result = request.app.state.commands.admit(account["account_id"], device_id, body,
            connected=request.app.state.relay.available(device_id))
        request.app.state.relay.notify(device_id)
        return result

    @app.get("/v1/devices/{device_id}/commands/{request_id}")
    def command_status(device_id: UUID, request_id: UUID, request: Request):
        return request.app.state.commands.get(browser(request)["account_id"], device_id, request_id)

    @app.websocket("/v1/device/connect")
    async def device_connect(socket: WebSocket):
        authorization = socket.headers.get("authorization", "")
        if not re.fullmatch(r"Bearer pd_[A-Za-z0-9_-]{43}", authorization):
            await socket.close(code=1008)
            return
        await socket.app.state.relay.connect(socket, authorization[7:])

    @app.get("/v1/device/session")
    def device_session(request: Request):
        device_request(request)
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer "):
            raise AuthError(401, "device_authentication_required")
        identity = request.app.state.pairing.authenticate_device(authorization[7:])
        return {"device_id": identity["device_id"], "owner_id": identity["owner_id"],
            "ownership_generation": identity["ownership_generation"], "protocol_version": "poltergeist-coordinator-v1",
            "remote_execution_enabled": False}

    @app.post("/v1/device/revoke")
    async def device_revoke(body: EmptyInput, request: Request):
        device_request(request)
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer "):
            raise AuthError(401, "device_authentication_required")
        result = request.app.state.pairing.revoke_credential(authorization[7:])
        await request.app.state.relay.disconnect(result["device_id"])
        return result

    return app
