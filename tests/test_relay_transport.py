"""TLS owner lifecycle, receive polling and sanitized facade failure contracts."""

import asyncio
from threading import Event, Thread, get_ident
from time import monotonic

import pytest

from companion import relay_transport as transport


def test_loop_bootstrap_failure_resolves_start_without_logging_input(monkeypatch, capsys):
    def fail():
        raise OSError("private_handshake_credential_sentinel")
    monkeypatch.setattr(transport.asyncio, "new_event_loop", fail)
    owner = transport._Owner("wss://fixture.example/v1/device/connect", {})
    started = monotonic()
    with pytest.raises(transport.RelayClientError) as caught:
        owner.start()
    assert monotonic() - started < 2
    assert "sentinel" not in str(caught.value)
    assert owner._ready.is_set() and owner._finished.is_set() and owner._opened.done()
    assert not owner._thread.is_alive()
    assert "sentinel" not in capsys.readouterr().err


def test_abort_before_first_lifecycle_step_resolves_start_and_joins_owner(monkeypatch):
    owner = transport._Owner("wss://fixture.example/v1/device/connect", {})
    ready = owner._ready.set

    def abort_before_first_step():
        ready()
        owner.abort()

    monkeypatch.setattr(owner._ready, "set", abort_before_first_step)
    with pytest.raises(transport.RelayClientError):
        owner.start()
    assert owner._opened.done() and owner._finished.is_set()
    assert not owner._thread.is_alive()


def test_cancelled_open_retains_owner_until_real_executor_work_finishes(monkeypatch):
    entered, release, caller_returned = Event(), Event(), Event()
    errors = []

    def resolver():
        entered.set()
        assert release.wait(5)

    class Resolving:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            await asyncio.get_running_loop().run_in_executor(None, resolver)

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(transport, "_NoRedirect", Resolving)
    owner = transport._Owner("wss://fixture.example/v1/device/connect", {})

    def open_connection():
        try:
            owner.start()
        except transport.RelayClientError:
            errors.append("closed")
        finally:
            caller_returned.set()

    caller = Thread(target=open_connection, daemon=True)
    caller.start()
    try:
        assert entered.wait(2)
        owner.abort()
        assert not caller_returned.wait(.1)
        assert owner._thread.is_alive() and not owner._finished.is_set()
    finally:
        release.set()
        caller.join(3)
    assert not caller.is_alive() and not owner._thread.is_alive()
    assert errors == ["closed"] and owner._finished.is_set()


@pytest.fixture
def observed_websocket(monkeypatch):
    observations = {"threads": [], "receives": 0}

    class WebSocket:
        def __init__(self):
            self.transport = self
            self.messages = asyncio.Queue()

        def abort(self):
            observations["threads"].append(get_ident())

        async def recv(self):
            observations["threads"].append(get_ident())
            observations["receives"] += 1
            return await self.messages.get()

        async def send(self, value):
            observations["threads"].append(get_ident())
            if value == "fail":
                raise ValueError("private_handshake_credential_sentinel")
            await self.messages.put(value)

    class Context:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            observations["threads"].append(get_ident())
            return WebSocket()

        async def __aexit__(self, *args):
            observations["threads"].append(get_ident())

    monkeypatch.setattr(transport, "_NoRedirect", Context)
    return observations


def test_timed_out_receive_keeps_one_reader_and_does_not_drop_later_message(observed_websocket):
    with transport.RelayConnector("wss://fixture.example/v1/device/connect").connect() as connection:
        owner = connection._owner
        for _ in range(3):
            with pytest.raises(TimeoutError):
                connection.recv(timeout=.02)
        assert observed_websocket["receives"] == 1
        connection.send("message arriving between receive polls")
        assert connection.recv(timeout=1) == "message arriving between receive polls"
        assert observed_websocket["receives"] == 1
        connection.socket.shutdown(2)
    assert set(observed_websocket["threads"]) == {owner._thread.ident}
    assert owner._finished.is_set() and not owner._thread.is_alive() and owner._loop.is_closed()


def test_async_send_exception_is_consumed_and_externally_fixed(observed_websocket, capsys, caplog):
    with transport.RelayConnector("wss://fixture.example/v1/device/connect").connect() as connection:
        owner = connection._owner
        with pytest.raises(transport.RelayClientError) as caught:
            connection.send("fail")
        assert "sentinel" not in str(caught.value)
    assert "sentinel" not in caplog.text + capsys.readouterr().err
    assert owner._finished.is_set() and not owner._thread.is_alive()


def test_preemptively_aborted_connector_creates_no_io_owner():
    connector = transport.RelayConnector("wss://fixture.example/v1/device/connect")
    connector.abort()
    with pytest.raises(transport.RelayClientError):
        connector.connect()
    assert connector._owner is None
