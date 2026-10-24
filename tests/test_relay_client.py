"""Actual outbound WSS with a private fixture CA and bounded lifecycle checks."""

from contextlib import contextmanager
import asyncio
from concurrent.futures import Future, TimeoutError as FutureTimeout
from dataclasses import replace
from http import HTTPStatus
from itertools import count
import logging
import json
import os
from pathlib import Path
import shutil
import socket as socket_module
import ssl
import subprocess
import sys
from threading import Event, Lock, Thread, Timer
from time import monotonic
import traceback
from uuid import uuid4

import pytest
from websockets.exceptions import ConnectionClosed
from websockets.asyncio.client import ClientConnection
from websockets.asyncio.server import ServerConnection, serve

from backend.remote_receipts import RemoteBinding
from companion.relay_client import RelayClient, RelayClientError
from companion.relay_transport import RelayConnector
from shared.models.coordination import DevicePolicy
from shared.models.remote import (ClientReady, DeviceStatusReply, MAX_RELAY_BYTES, RelayCommand,
    RelayPing, RelayPong, RelayReply, ServerHello, StatusCommand, canonical_json, parse_message)


CREDENTIAL = "pd_fixture_private_relay_sentinel"


def wait_until(predicate, timeout=10):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if result := predicate():
            return result
        Event().wait(0.01)
    raise AssertionError("The fixture relay did not reach its expected state")


def make_certificates(tmp_path):
    """Create localhost SAN certificates in an exclusively owned subdirectory."""
    root = Path(tmp_path) / "relay-certificates"
    root.mkdir()
    executable = shutil.which("openssl")
    if executable is None and os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git/usr/bin/openssl.exe"
        if candidate.is_file():
            executable = str(candidate)
    if executable is None:
        pytest.fail("The native TLS fixture requires OpenSSL (Git for Windows includes it)")
    commands = [
        ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "ca.key", "-out", "ca.pem",
         "-days", "2", "-subj", "/CN=Poltergeist fixture CA", "-addext", "basicConstraints=critical,CA:TRUE",
         "-addext", "keyUsage=critical,keyCertSign,cRLSign", "-addext", "subjectKeyIdentifier=hash",
         "-addext", "authorityKeyIdentifier=keyid:always"],
        ["req", "-newkey", "rsa:2048", "-nodes", "-keyout", "server.key", "-out", "server.csr",
         "-subj", "/CN=localhost"],
        ["x509", "-req", "-in", "server.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial",
         "-out", "server.pem", "-days", "2", "-extfile", "server.ext"],
    ]
    root.joinpath("server.ext").write_text("subjectAltName=DNS:localhost\nbasicConstraints=critical,CA:FALSE\n"
        "extendedKeyUsage=serverAuth\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
        "subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n", encoding="ascii")
    for command in commands:
        result = subprocess.run([executable, *command], cwd=root, capture_output=True, timeout=30)
        assert result.returncode == 0, "Fixture certificate generation failed"
    return {"ca": root / "ca.pem", "cert": root / "server.pem", "key": root / "server.key"}


@pytest.fixture(scope="module")
def tls(tmp_path_factory):
    files = make_certificates(tmp_path_factory.mktemp("relay-tls"))
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(files["cert"], files["key"])
    trusted = ssl.create_default_context(cafile=str(files["ca"]))
    assert trusted.check_hostname and trusted.verify_mode == ssl.CERT_REQUIRED
    return server, trusted


def quiet_logger(name):
    logger = logging.Logger(name, level=logging.CRITICAL + 1)
    logger.addHandler(logging.NullHandler())
    return logger


def connect(uri, **options):
    return RelayConnector(uri, **options).connect()


class _ServerBridge:
    """Only plain handler logic runs in an executor; TLS stays on its loop."""
    def __init__(self, connection, loop):
        self.connection, self.loop = connection, loop

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def _result(self, operation, timeout):
        future = asyncio.run_coroutine_threadsafe(operation, self.loop)
        try:
            return future.result(timeout=timeout)
        except FutureTimeout:
            future.cancel()
            raise TimeoutError("Fixture server action timed out") from None

    def recv(self, timeout=None):
        return self._result(self.connection.recv(), timeout)

    def send(self, value):
        return self._result(self.connection.send(value), 10)

    def close(self, code=1000):
        return self._result(self.connection.close(code=code), 5)

    def __iter__(self):
        while True:
            try:
                yield self.recv()
            except ConnectionClosed:
                return


@contextmanager
def tls_server(tls, handler, *, process_request=None):
    errors, handshake_failures = [], []
    startup, diagnostic_lock = Future(), Lock()
    phases, slow_stacks, connections, monitors = [], [], {}, {}
    connection_ids = count(1)
    started = monotonic()
    loop = asyncio.new_event_loop()
    closing = asyncio.Event()

    def phase(socket, value, *, new=False):
        with diagnostic_lock:
            key = id(getattr(socket, "_fixture_native", socket))
            if new or key not in connections:
                connections[key] = next(connection_ids)
            if len(phases) < 256:
                phases.append({"connection": connections[key], "phase": value,
                               "elapsed_s": round(monotonic() - started, 3)})
            if value == "http_handshake_complete" or value.startswith("http_handshake_error_"):
                if monitor := monitors.get(key):
                    monitor.cancel()

    def capture_stacks():
        # Static locations only: no source lines, locals, headers or exception
        # values. Capture while the handshake is still waiting, before timeout
        # cleanup removes the client/server receive threads.
        snapshot = [{"thread": identity, "stack": [
            f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
            for frame in traceback.extract_stack(stack)[-10:]]}
            for identity, stack in sys._current_frames().items()]
        with diagnostic_lock:
            if len(slow_stacks) < 4:
                slow_stacks.append(snapshot)

    class FailureTypes(logging.Handler):
        def emit(self, record):
            # Never format messages, headers, exception text or traceback data.
            # Preserve only bounded exception types for a failed fixture setup.
            if record.exc_info and len(handshake_failures) < 16:
                handshake_failures.append(type(record.exc_info[1]).__name__[:64])

    fixture_logger = logging.Logger("fixture.relay", level=logging.INFO)
    fixture_logger.propagate = False
    fixture_logger.addHandler(FailureTypes())

    def guarded(socket):
        try:
            handler(socket)
        except ConnectionClosed:
            pass
        except BaseException as error:
            errors.append(error)

    class ObservedConnection(ServerConnection):
        def connection_made(self, transport):
            super().connection_made(transport)
            phase(self, "tls_complete", new=True)
            monitors[id(self)] = loop.call_later(3, capture_stacks)

        async def handshake(self, *args, **kwargs):
            phase(self, "http_handshake_begin")
            try:
                result = await super().handshake(*args, **kwargs)
            except BaseException as error:
                phase(self, "http_handshake_error_" + type(error).__name__)
                raise
            phase(self, "http_handshake_complete")
            return result

    def request_phase(connection, request):
        phase(connection, "http_request_parsed")
        if process_request is not None:
            return process_request(connection, request)

    def response_phase(connection, request, response):
        phase(connection, "http_response_" + str(response.status_code))

    async def async_handler(connection):
        phase(connection, "open_handler")
        await asyncio.to_thread(guarded, _ServerBridge(connection, loop))

    async def lifecycle():
        async with serve(async_handler, "127.0.0.1", 0, ssl=tls[0], process_request=request_phase,
                         process_response=response_phase, create_connection=ObservedConnection,
                         compression=None, ping_interval=None, close_timeout=1, open_timeout=10,
                         logger=fixture_logger) as server:
            startup.set_result(server.sockets[0].getsockname()[1])
            await closing.wait()

    def run():
        asyncio.set_event_loop(loop)
        loop.set_exception_handler(lambda loop, context: None)
        try:
            loop.run_until_complete(lifecycle())
        except BaseException as error:
            errors.append(error)
            if not startup.done():
                startup.set_exception(AssertionError("Fixture TLS server failed to start"))
        finally:
            for monitor in monitors.values():
                monitor.cancel()
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(loop.shutdown_default_executor())
            loop.close()

    thread = Thread(target=run, name="fixture-wss-io", daemon=True)
    thread.start()
    try:
        port = startup.result(timeout=10)
        yield f"https://localhost:{port}"
    except BaseException as error:
        error.add_note("Fixture WSS handshake error types: " + ", ".join(handshake_failures))
        error.add_note("Fixture WSS phases: " + json.dumps(phases, separators=(",", ":")))
        error.add_note("Fixture WSS slow-handshake thread locations: " + json.dumps(slow_stacks, separators=(",", ":")))
        raise
    finally:
        if not loop.is_closed():
            loop.call_soon_threadsafe(closing.set)
        thread.join(15)
        assert not thread.is_alive(), "Fixture WSS loop or handler did not stop"
    assert not errors, f"Fixture WSS handler failed: {[type(error).__name__ for error in errors]}"


class Dispatch:
    def __init__(self):
        self.calls = []

    def dispatch(self, binding, command):
        self.calls.append((binding, command))
        return DeviceStatusReply(request_id=command.request_id, accepting=True, active=False, waiting_remote_runs=0)


def enrollment(origin):
    binding = RemoteBinding(origin, uuid4(), uuid4(), 1)
    policy = DevicePolicy(scopes=["status:read"])
    return binding, policy, CREDENTIAL


def hello_for(values, **changes):
    binding, policy, _ = values
    return ServerHello(device_id=binding.device_id, owner_id=binding.owner_id,
        ownership_generation=binding.generation, connection_generation=3, policy_revision=1, policy=policy,
        **changes)


def fields(hello):
    return {"device_id": hello.device_id, "ownership_generation": hello.ownership_generation,
            "connection_generation": hello.connection_generation}


def test_actual_wss_auth_ready_ping_command_reply_and_private_logging(tls, caplog):
    caplog.set_level(logging.DEBUG)
    values = []
    received, done = [], Event()

    def handler(socket):
        assert socket.request.path == "/v1/device/connect"
        assert socket.request.headers["Authorization"] == "Bearer " + CREDENTIAL
        assert CREDENTIAL not in socket.request.path
        assert "Sec-WebSocket-Extensions" not in socket.request.headers
        hello = hello_for(values)
        socket.send(canonical_json(hello))
        ready = parse_message(socket.recv(timeout=5))
        assert isinstance(ready, ClientReady) and ready.remote_execution_enabled
        received.append(ready)
        ping = RelayPing(**fields(hello), nonce=uuid4())
        socket.send(canonical_json(ping))
        pong = parse_message(socket.recv(timeout=5))
        assert isinstance(pong, RelayPong) and pong.nonce == ping.nonce
        command = StatusCommand(request_id=uuid4())
        socket.send(canonical_json(RelayCommand(**fields(hello), command=command)))
        reply = parse_message(socket.recv(timeout=5))
        assert isinstance(reply, RelayReply) and reply.reply.request_id == command.request_id
        received.append(reply)
        done.set()
        socket.recv(timeout=10)

    with tls_server(tls, handler) as origin:
        values[:] = enrollment(origin)
        service = Dispatch()
        client = RelayClient(origin, lambda: tuple(values), service, remote_execution_enabled=True, _ssl_context=tls[1])
        assert client.snapshot() == {"state": "disconnected", "remote_execution_enabled": True, "running": False}
        client.start()
        try:
            assert done.wait(10)
            assert client.snapshot()["state"] == "connected"
            assert len(service.calls) == 1
            assert service.calls[0][0] == values[0]
            assert service.calls[0][1].request_id == received[1].reply.request_id
        finally:
            assert client.stop()
    assert CREDENTIAL not in caplog.text and "Authorization" not in caplog.text
    assert client.snapshot()["state"] == "disconnected" and not client.snapshot()["running"]


@pytest.mark.parametrize("failure", ["untrusted", "hostname"])
def test_actual_tls_verification_failure_sends_no_http_credential(tls, failure):
    http_requests = []

    def observe(socket, request):
        http_requests.append(request)

    with tls_server(tls, lambda socket: pytest.fail("Unverified TLS reached a WebSocket handler"), process_request=observe) as origin:
        if failure == "hostname":
            # Fixture cert has DNS:localhost only; an IP origin must fail.
            origin = origin.replace("localhost", "127.0.0.1")
            context = tls[1]
        else:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        values, service = enrollment(origin), Dispatch()
        client = RelayClient(origin, lambda: values, service, _ssl_context=context)
        client.start()
        try:
            wait_until(lambda: client.snapshot()["state"] == "offline")
            assert http_requests == [] and service.calls == []
        finally:
            assert client.stop()


@pytest.mark.parametrize("same_origin", [False, True])
def test_actual_redirect_is_refused_without_forwarding_authorization(tls, same_origin):
    requests, target_requests = [], []

    def target_handler(socket):
        target_requests.append(socket.request)

    with tls_server(tls, target_handler) as target:
        def redirect(socket, request):
            requests.append((request.path, request.headers.get("Authorization")))
            location = (f"wss://localhost:{socket.local_address[1]}" if same_origin else "wss" + target[5:]) + "/redirect-target"
            response = socket.respond(HTTPStatus.TEMPORARY_REDIRECT, "Fixture redirect")
            response.headers["Location"] = location
            return response

        with tls_server(tls, lambda socket: None, process_request=redirect) as origin:
            values, service = enrollment(origin), Dispatch()
            client = RelayClient(origin, lambda: values, service, _ssl_context=tls[1])
            client.start()
            try:
                wait_until(lambda: client.snapshot()["state"] == "offline")
                assert requests and all(item == ("/v1/device/connect", "Bearer " + CREDENTIAL) for item in requests)
                assert target_requests == [] and service.calls == []
            finally:
                assert client.stop()


@pytest.mark.parametrize("failure", ["device", "owner", "ownership_generation", "policy_revision", "policy"])
def test_actual_wss_hello_must_match_local_approval(tls, failure):
    values, closed = [], Event()

    def handler(socket):
        hello = hello_for(values)
        updates = {"device": {"device_id": uuid4()}, "owner": {"owner_id": uuid4()},
            "ownership_generation": {"ownership_generation": 2}, "policy_revision": {"policy_revision": 2},
            "policy": {"policy": DevicePolicy(scopes=["result:read"])}}
        socket.send(canonical_json(hello.model_copy(update=updates[failure])))
        with pytest.raises(ConnectionClosed):
            socket.recv(timeout=5)
        closed.set()

    with tls_server(tls, handler) as origin:
        values[:] = enrollment(origin)
        service = Dispatch()
        client = RelayClient(origin, lambda: tuple(values), service, _ssl_context=tls[1])
        client.start()
        try:
            assert closed.wait(10)
            assert service.calls == []
        finally:
            assert client.stop()


@pytest.mark.parametrize("failure", ["device", "ownership_generation", "connection_generation", "unknown", "oversized", "disabled"])
def test_actual_wss_refuses_stale_unknown_oversized_and_disabled_commands(tls, failure):
    values, closed, closes = [], Event(), []

    def handler(socket):
        hello = hello_for(values)
        socket.send(canonical_json(hello))
        assert isinstance(parse_message(socket.recv(timeout=5)), ClientReady)
        message = RelayCommand(**fields(hello), command=StatusCommand(request_id=uuid4())).model_dump(mode="json")
        if failure == "device":
            message["device_id"] = str(uuid4())
        elif failure in ("ownership_generation", "connection_generation"):
            message[failure] += 1
        elif failure == "unknown":
            message["kind"] = "unknown"
        raw = "x" * (MAX_RELAY_BYTES + 1) if failure == "oversized" else canonical_json(message)
        socket.send(raw)
        with pytest.raises(ConnectionClosed) as caught:
            socket.recv(timeout=5)
        closes.append(caught.value.rcvd.code)
        closed.set()

    with tls_server(tls, handler) as origin:
        values[:] = enrollment(origin)
        service = Dispatch()
        client = RelayClient(origin, lambda: tuple(values), service, remote_execution_enabled=failure != "disabled", _ssl_context=tls[1])
        client.start()
        try:
            assert closed.wait(10)
            assert service.calls == []
            if failure == "oversized":
                assert closes == [1009]
        finally:
            assert client.stop()


def test_native_stop_closes_only_owned_socket_and_allows_clean_restart(tls):
    values, owned_connections = [], []

    def handler(socket):
        if socket.request.path == "/witness":
            for message in socket:
                socket.send(message)
        else:
            socket.send(canonical_json(hello_for(values)))
            assert isinstance(parse_message(socket.recv(timeout=5)), ClientReady)
            owned_connections.append(socket)
            socket.recv(timeout=10)

    with tls_server(tls, handler) as origin:
        values[:] = enrollment(origin)
        with connect("wss" + origin[5:] + "/witness", ssl=tls[1], proxy=None, compression=None,
                     ping_interval=None, close_timeout=1, logger=quiet_logger("fixture.witness")) as witness:
            client = RelayClient(origin, lambda: tuple(values), Dispatch(), _ssl_context=tls[1])
            client.start()
            try:
                wait_until(lambda: client.snapshot()["state"] == "connected")
                wait_until(lambda: len(owned_connections) == 1)
                with pytest.raises(RelayClientError, match="existing relay"):
                    client.start()
                assert client.stop() and client.stop()
                witness.send("witness stays open")
                assert witness.recv(timeout=5) == "witness stays open"
                client.start()
                wait_until(lambda: client.snapshot()["state"] == "connected")
                wait_until(lambda: len(owned_connections) == 2)
            finally:
                assert client.stop()
    assert len(owned_connections) == 2


def _paused_connector(sending, closed):
    class PausedConnection(ClientConnection):
        async def drain(self):
            if self.paused:
                sending.set()
            return await super().drain()

        def connection_lost(self, error):
            closed.set()
            super().connection_lost(error)

    def connector(uri, **options):
        return RelayConnector(uri, **options, create_connection=PausedConnection)
    return connector


def _pause_writes(facade):
    async def pause():
        facade._owner._connection.pause_writing()
    facade._owner.submit(pause()).result(timeout=5)


def test_native_stop_interrupts_send_backpressure_and_joins_io_owner(tls):
    values, trigger, sending, release, closed = [], Event(), Event(), Event(), Event()

    def handler(socket):
        hello = hello_for(values)
        socket.send(canonical_json(hello))
        assert isinstance(parse_message(socket.recv(timeout=5)), ClientReady)
        assert trigger.wait(10)
        socket.send(canonical_json(RelayCommand(**fields(hello), command=StatusCommand(request_id=uuid4()))))
        assert release.wait(30)

    with tls_server(tls, handler) as origin:
        values[:] = enrollment(origin)
        client = RelayClient(origin, lambda: tuple(values), Dispatch(), remote_execution_enabled=True,
            _ssl_context=tls[1], _connector=_paused_connector(sending, closed))
        finished, results = Event(), []

        def stop():
            results.append(client.stop())
            finished.set()

        stopper = Thread(target=stop, name="fixture-stop-under-backpressure", daemon=True)
        client.start()
        try:
            wait_until(lambda: client.snapshot()["state"] == "connected")
            connection = client._socket
            _pause_writes(connection)
            trigger.set()
            assert sending.wait(10)
            stopper.start()
            assert finished.wait(18), "Relay stop did not join its pending write and TLS owner"
            assert results == [True]
            assert closed.is_set() and not connection._owner._thread.is_alive()
            assert client.snapshot()["state"] == "disconnected" and not client.snapshot()["running"]
        finally:
            trigger.set()
            release.set()
            if stopper.ident is not None:
                stopper.join(5)
                assert not stopper.is_alive(), "Fixture stop thread did not terminate"
            assert client.stop()


class ClockSocket:
    def __init__(self, hello, clock, messages=()):
        self.hello, self.clock = hello, clock
        self.messages = iter(messages)
        self.sent, self.timeouts = [], []

    def recv(self, timeout):
        self.timeouts.append(timeout)
        if self.hello is not None:
            hello, self.hello = self.hello, None
            return canonical_json(hello)
        at, value = next(self.messages)
        self.clock[0] = at
        if isinstance(value, Exception):
            raise value
        return canonical_json(value)

    def send(self, value):
        self.sent.append(parse_message(value))


def test_native_send_deadline_aborts_only_blocked_connection_without_explicit_stop(tls):
    values, trigger, sending, release, aborted = [], Event(), Event(), Event(), Event()

    def handler(socket):
        if socket.request.path == "/witness":
            for message in socket:
                socket.send(message)
        else:
            hello = hello_for(values)
            socket.send(canonical_json(hello))
            assert isinstance(parse_message(socket.recv(timeout=5)), ClientReady)
            assert trigger.wait(10)
            socket.send(canonical_json(RelayCommand(**fields(hello), command=StatusCommand(request_id=uuid4()))))
            assert release.wait(30)

    with tls_server(tls, handler) as origin:
        values[:] = enrollment(origin)
        with connect("wss" + origin[5:] + "/witness", ssl=tls[1], proxy=None, compression=None,
                     ping_interval=None, close_timeout=1, logger=quiet_logger("fixture.deadline-witness")) as witness:
            client = RelayClient(origin, lambda: tuple(values), Dispatch(), remote_execution_enabled=True,
                                 _ssl_context=tls[1], _send_timeout_s=2,
                                 _connector=_paused_connector(sending, aborted))
            client.start()
            try:
                wait_until(lambda: client.snapshot()["state"] == "connected")
                connection = client._socket
                _pause_writes(connection)
                trigger.set()
                assert sending.wait(10)
                assert aborted.wait(10), "Automatic relay write deadline did not abort its native transport"
                assert not client._stop.is_set()
                wait_until(lambda: client.snapshot()["state"] == "offline" or client._socket is not connection)
                assert client.snapshot()["running"]
                assert connection._owner._finished.wait(5)
                witness.send("unrelated TLS connection remains usable")
                assert witness.recv(timeout=5) == "unrelated TLS connection remains usable"
            finally:
                trigger.set()
                release.set()
                assert client.stop()


@pytest.mark.parametrize("deadline", [True, 0, -1, 31, float("inf"), float("nan"), "1"])
def test_write_deadline_cannot_be_disabled_or_exceed_production_limit(deadline):
    values = enrollment("https://fixture.example")
    with pytest.raises(ValueError, match="write deadline"):
        RelayClient(values[0].origin, lambda: values, Dispatch(), _send_timeout_s=deadline)


def test_heartbeat_expiry_uses_monotonic_deadline_and_valid_ping_refresh(monkeypatch):
    values = enrollment("https://fixture.example")
    hello, clock = hello_for(values), [0.]
    ping = RelayPing(**fields(hello), nonce=uuid4())
    socket = ClockSocket(hello, clock, [(80., ping), (160., TimeoutError()), (171., TimeoutError())])
    monkeypatch.setattr("companion.relay_client.monotonic", lambda: clock[0])
    client = RelayClient(values[0].origin, lambda: values, Dispatch())
    with pytest.raises(RelayClientError, match="heartbeat expired"):
        client._serve(socket, *values)
    assert [type(message) for message in socket.sent] == [ClientReady, RelayPong]
    assert socket.sent[1].nonce == ping.nonce
    assert socket.timeouts == [10, 1, 1, 1]


@pytest.mark.parametrize("change", ["binding", "policy", "credential"])
def test_local_enrollment_changes_stop_dispatch_before_next_message(change):
    values = enrollment("https://fixture.example")
    binding, policy, credential = values
    changed = {"binding": (replace(binding, generation=2), policy, credential),
        "policy": (binding, DevicePolicy(scopes=["result:read"]), credential),
        "credential": (binding, policy, "pd_changed_private_sentinel")}[change]
    hello = hello_for(values)
    socket = ClockSocket(hello, [0.], [(0., RelayCommand(**fields(hello), command=StatusCommand(request_id=uuid4())))])
    service = Dispatch()
    client = RelayClient(binding.origin, lambda: changed, service)
    with pytest.raises(RelayClientError, match="binding changed"):
        client._serve(socket, *values)
    assert service.calls == [] and len(socket.sent) == 1


def test_stop_keeps_thread_ownership_until_blocked_worker_really_exits():
    class BlockedThread:
        alive = True
        joined = []

        def is_alive(self):
            return self.alive

        def join(self, timeout):
            self.joined.append(timeout)

    class OwnedSocket:
        def __init__(self):
            self.closed = False
            self.socket = self

        def shutdown(self, how):
            assert how == socket_module.SHUT_RDWR
            self.closed = True

    values = enrollment("https://fixture.example")
    client = RelayClient(values[0].origin, lambda: values, Dispatch())
    thread, socket, unrelated = BlockedThread(), OwnedSocket(), OwnedSocket()
    client._thread, client._socket = thread, socket
    assert not client.stop()
    assert client._thread is thread and thread.joined == [15]
    assert socket.closed and not unrelated.closed
    with pytest.raises(RelayClientError, match="existing relay"):
        client.start()
    thread.alive = False
    assert client.stop() and client._thread is None
    assert client.snapshot()["state"] == "disconnected"
