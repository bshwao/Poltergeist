"""Real TLS enrollment requests have a total budget and bounded private errors."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import ssl
from threading import Event, Thread
from time import monotonic, sleep

import pytest

from companion.pairing_client import JsonTransport, PairingClientError
from test_relay_client import tls


@contextmanager
def server(tls, *, delay=0., header_delay=0., body=b'{"value":"' + b'x' * 160 + b'"}', status=200, headers=None):
    observed = []
    release = Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            observed.append((self.path, self.headers.get("Authorization")))
            try:
                release.wait(header_delay)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                if not delay:
                    self.wfile.write(body)
                else:
                    for byte in body:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        if release.wait(delay):
                            break
            except OSError:
                pass  # The deadline closes this owned fixture connection.

        def log_message(self, *args):
            pass

    fixture = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    fixture.daemon_threads = True
    fixture.socket = tls[0].wrap_socket(fixture.socket, server_side=True)
    owner = Thread(target=fixture.serve_forever, daemon=True)
    owner.start()
    try:
        yield f"https://localhost:{fixture.server_port}", observed
    finally:
        release.set()
        fixture.shutdown()
        fixture.server_close()
        owner.join(3)
        assert not owner.is_alive(), "The owned TLS listener did not stop"


def test_default_enrollment_deadline_cannot_be_extended_by_a_trickling_tls_body(tls):
    with server(tls, delay=.08) as (origin, observed):
        client = JsonTransport(origin, _ssl_context=tls[1])
        started = monotonic()
        with pytest.raises(PairingClientError):
            client.request("GET", "/v1/device/session")
        assert monotonic() - started < 12
        assert observed == [("/v1/device/session", None)]
        assert client.close()


def test_isolated_worker_keeps_strict_tls_and_transfers_authorization_only_in_headers(tls):
    secret = "Bearer pd_private_transport_sentinel"
    expected = {"state": "paired", "value": "\u2603"}
    with server(tls, body=json.dumps(expected).encode()) as (origin, observed):
        client = JsonTransport(origin, _ssl_context=tls[1])
        assert client.request("GET", "/v1/device/session", authorization=secret) == expected
        assert client.close()
        assert observed == [("/v1/device/session", secret)]


def test_untrusted_or_wrong_hostname_certificates_cannot_reach_authenticated_http(tls):
    with server(tls, body=b'{}') as (origin, observed):
        for address, trust in ((origin, None), (origin.replace("localhost", "127.0.0.1"), tls[1])):
            client = JsonTransport(address, _ssl_context=trust)
            with pytest.raises(PairingClientError, match="securely"):
                client.request("GET", "/v1/device/session", authorization="Bearer private_tls_sentinel")
            assert client.close()
        assert observed == []


@pytest.mark.parametrize("body", [b'{' + b' ' * 65536, b'{"value":NaN}', b'{"value":1,"value":2}', b'not json'],
    ids=["oversized", "nonfinite", "duplicate_fields", "malformed"])
def test_oversized_or_invalid_server_json_does_not_escape_the_worker(tls, body):
    with server(tls, body=body) as (origin, _):
        client = JsonTransport(origin, _ssl_context=tls[1])
        with pytest.raises(PairingClientError) as failure:
            client.request("GET", "/v1/device/session", authorization="Bearer private_body_sentinel")
        assert "private_body_sentinel" not in str(failure.value)
        assert client.close()


def test_redirect_cannot_forward_private_authorization(tls):
    with server(tls, body=b'{}') as (target, observed):
        with server(tls, status=307, body=b'', headers={"Location": target + "/v1/private_redirect"}) as (origin, source):
            client = JsonTransport(origin, _ssl_context=tls[1])
            with pytest.raises(PairingClientError, match="redirected"):
                client.request("GET", "/v1/device/session", authorization="Bearer private_redirect_sentinel")
            assert client.close()
        assert observed == []
        assert source == [("/v1/device/session", "Bearer private_redirect_sentinel")]


def test_http_error_reports_only_its_status_and_retains_no_response_body(tls):
    with server(tls, status=409, body=b'{"secret":"private_error_sentinel"}') as (origin, _):
        client = JsonTransport(origin, _ssl_context=tls[1])
        with pytest.raises(PairingClientError) as failure:
            client.request("GET", "/v1/device/session", authorization="Bearer private_authorization_sentinel")
        assert str(failure.value) == "The coordinator refused enrollment (HTTP 409)"
        assert client.close()


def test_unsafe_tls_and_invalid_total_timeouts_are_refused_before_network_io(tls):
    context = ssl._create_unverified_context()
    with pytest.raises(ValueError, match="verification"):
        JsonTransport("https://fixture.example", _ssl_context=context)
    for value in (True, 0, -1, float("inf"), float("nan"), 11):
        with pytest.raises(ValueError, match="timeout"):
            JsonTransport("https://fixture.example", _request_timeout_s=value)


def test_stalled_response_headers_share_the_total_budget(tls):
    with server(tls, header_delay=30, body=b'{}') as (origin, observed):
        client = JsonTransport(origin, _ssl_context=tls[1])
        started = monotonic()
        with pytest.raises(PairingClientError):
            client.request("GET", "/v1/device/session")
        assert monotonic() - started < 13
        assert observed == [("/v1/device/session", None)]
        assert client.close()


def test_stalled_tls_handshake_is_terminated_with_its_owned_worker(tls):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(.1)
    port = listener.getsockname()[1]
    accepted, release = Event(), Event()

    def hold():
        while not release.is_set():
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with connection:
                accepted.set()
                release.wait(30)
            return

    owner = Thread(target=hold, daemon=True)
    owner.start()
    client = JsonTransport(f"https://localhost:{port}", _ssl_context=tls[1])
    try:
        started = monotonic()
        with pytest.raises(PairingClientError):
            client.request("GET", "/v1/device/session")
        assert monotonic() - started < 13 and accepted.is_set()
        assert client.close()
    finally:
        release.set()
        listener.close()
        owner.join(3)
        assert not owner.is_alive()


def test_blocked_resolver_cannot_outlive_its_request_or_leave_an_io_owner(tls, tmp_path, monkeypatch):
    from companion import enrollment_process
    from companion import enrollment_worker

    marker, wrapper = tmp_path / "resolver-entered.txt", tmp_path / "blocked_resolver.py"
    wrapper.write_text("import socket,runpy,time,os\nfrom pathlib import Path\n"
        "def blocked(*args,**kwargs):\n"
        f" Path({str(marker)!r}).write_text(str(os.getpid()))\n"
        " time.sleep(60)\n"
        "socket.getaddrinfo=blocked\n"
        f"runpy.run_path({enrollment_worker.__file__!r},run_name='__main__')\n", encoding="utf-8")
    actual, owners = enrollment_process.EnrollmentProcess, []

    def process(worker_path, **options):
        owner = actual(wrapper, **options)
        owners.append(owner)
        return owner

    monkeypatch.setattr(enrollment_process, "EnrollmentProcess", process)
    client = JsonTransport("https://unqueried-dns-fixture.invalid", _ssl_context=tls[1])
    started = monotonic()
    with pytest.raises(PairingClientError):
        client.request("GET", "/v1/device/session", authorization="Bearer private_resolver_sentinel")
    assert monotonic() - started < 13 and marker.exists()
    assert len(owners) == 1 and not owners[0].owned
    assert client.close()


def test_pause_between_construction_and_exchange_cannot_restart_the_request_budget(tls, monkeypatch):
    from companion import enrollment_process
    actual, owners = enrollment_process.EnrollmentProcess, []

    def delayed_constructor(worker_path, **options):
        owner = actual(worker_path, **options)
        owners.append(owner)
        sleep(.3)
        return owner

    monkeypatch.setattr(enrollment_process, "EnrollmentProcess", delayed_constructor)
    with server(tls, body=b'{}') as (origin, observed):
        client = JsonTransport(origin, _ssl_context=tls[1], _request_timeout_s=.1)
        with pytest.raises(PairingClientError, match="time limit"):
            client.request("GET", "/v1/device/session", authorization="Bearer private_delayed_start_sentinel")
        assert len(owners) == 1 and not owners[0].owned
        assert observed == [] and client.close()
