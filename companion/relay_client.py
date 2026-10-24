"""Explicit outbound TLS relay. Credentials never enter URLs or diagnostics."""

import logging
from math import isfinite
import socket as socket_module
import ssl
from threading import Event, Lock, Thread, Timer, current_thread
from time import monotonic

from websockets.sync.client import reconnect

from shared.models.remote import (ClientReady, MAX_RELAY_BYTES, RelayCommand,
    RelayPing, RelayPong, RelayReply, ServerHello, canonical_json, parse_message)


class RelayClientError(RuntimeError):
    pass


class _NoRedirect(reconnect):
    def process_redirect(self, exc):
        # Even same-origin redirects aren't enrollment or relay endpoints.
        # Returning an exception suppresses all redirect handling in websockets.
        return RelayClientError("The coordinator refused the secure relay handshake")


def _abort_transport(socket):
    # This operation must never acquire websockets' protocol mutex: a blocked
    # send owns that mutex. It only interrupts this connection's OS transport.
    try:
        transport = getattr(socket, "socket", None)
        if transport is not None:
            transport.shutdown(socket_module.SHUT_RDWR)
    except OSError:
        pass


class RelayClient:
    def __init__(self, origin, enrollment, service, *, remote_execution_enabled=False,
                 _ssl_context=None, _connector=None, _send_timeout_s=30):
        from shared.origins import service_origin
        self.origin = service_origin(origin)
        self.enrollment = enrollment
        self.service = service
        self.enabled = bool(remote_execution_enabled)
        self._context = _ssl_context or ssl.create_default_context()
        self._connector = _connector or _NoRedirect
        if (isinstance(_send_timeout_s, bool) or not isinstance(_send_timeout_s, (int, float))
                or not isfinite(_send_timeout_s) or not 0 < _send_timeout_s <= 30):
            raise ValueError("Relay write deadline must be positive and at most thirty seconds")
        self._send_timeout = float(_send_timeout_s)
        self._stop = Event()
        self._lock = Lock()
        self._thread = None
        self._socket = None
        self._state = "disconnected"
        # An isolated logger prevents third-party DEBUG output from recording
        # Authorization headers even when the user's root logger is verbose.
        self._logger = logging.Logger("poltergeist.private-relay", level=logging.CRITICAL + 1)
        self._logger.addHandler(logging.NullHandler())

    def snapshot(self):
        with self._lock:
            return {"state": self._state, "remote_execution_enabled": self.enabled,
                    "running": self._thread is not None and self._thread.is_alive()}

    def _state_is(self, value):
        with self._lock:
            self._state = value

    def start(self):
        with self._lock:
            if self._thread is not None:
                raise RelayClientError("Disconnect the existing relay before reconnecting")
            self._stop.clear()
            self._thread = Thread(target=self._run, name="poltergeist-outbound-relay", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        with self._lock:
            socket, thread = self._socket, self._thread
        if socket is not None:
            # The relay thread owns final close after the native interrupt.
            _abort_transport(socket)
        if thread is not None and thread is not current_thread():
            thread.join(15)
        clean = thread is None or not thread.is_alive()
        if clean:
            with self._lock:
                self._thread = None
                self._state = "disconnected"
        return clean

    def _send(self, socket, message):
        encoded = canonical_json(message)
        if len(encoded.encode("utf-8")) > MAX_RELAY_BYTES:
            raise RelayClientError("The relay reply exceeded its protocol limit")
        # A blocked sync send cannot reach the receive loop's heartbeat check.
        # Abort its own OS transport on deadline, including while a third-party
        # send holds the protocol mutex. Cancellation cannot affect a newer socket.
        deadline = Timer(self._send_timeout, _abort_transport, args=(socket,))
        deadline.daemon = True
        deadline.start()
        try:
            socket.send(encoded)
        finally:
            deadline.cancel()

    def _run(self):
        delay = 1
        while not self._stop.is_set():
            self._state_is("connecting")
            try:
                binding, policy, credential = self.enrollment()
                uri = "wss" + self.origin[5:] + "/v1/device/connect"
                with self._connector(uri, ssl=self._context, proxy=None, compression=None,
                        additional_headers={"Authorization": "Bearer " + credential},
                        open_timeout=10, close_timeout=2, ping_interval=None, ping_timeout=None,
                        max_size=MAX_RELAY_BYTES, max_queue=4, logger=self._logger).connect() as socket:
                    with self._lock:
                        self._socket = socket
                    if self._stop.is_set():
                        break
                    self._serve(socket, binding, policy, credential)
                delay = 1
            except Exception:
                # Fixed states only. Never retain or log credential-bearing
                # exception/HTTP response objects from a failed handshake.
                self._state_is("offline")
            finally:
                with self._lock:
                    self._socket = None
            if self._stop.wait(delay):
                break
            delay = min(delay * 2, 30)
        self._state_is("disconnected")

    def _serve(self, socket, binding, policy, credential):
        hello = parse_message(socket.recv(timeout=10))
        if (not isinstance(hello, ServerHello) or hello.device_id != binding.device_id
                or hello.owner_id != binding.owner_id or hello.ownership_generation != binding.generation
                or hello.policy_revision != 1 or hello.policy != policy):
            raise RelayClientError("The relay doesn't match the locally approved device")
        fields = dict(device_id=hello.device_id, ownership_generation=hello.ownership_generation,
                      connection_generation=hello.connection_generation)
        self._send(socket, ClientReady(**fields, remote_execution_enabled=self.enabled))
        self._state_is("connected")
        last_message = monotonic()
        while not self._stop.is_set():
            if monotonic() - last_message >= hello.dead_timeout_s:
                raise RelayClientError("The relay heartbeat expired")
            current_binding, current_policy, current_credential = self.enrollment()
            if current_binding != binding or current_policy != policy or current_credential != credential:
                raise RelayClientError("The locally approved relay binding changed")
            try:
                raw = socket.recv(timeout=1)
            except TimeoutError:
                continue
            message = parse_message(raw)
            if any(getattr(message, field) != value for field, value in fields.items()):
                raise RelayClientError("A stale relay generation was refused")
            last_message = monotonic()
            if isinstance(message, RelayPing):
                self._send(socket, RelayPong(**fields, nonce=message.nonce))
            elif isinstance(message, RelayCommand) and self.enabled:
                reply = self.service.dispatch(binding, message.command)
                self._send(socket, RelayReply(**fields, reply=reply))
            else:
                raise RelayClientError("The coordinator sent an unsupported relay message")
