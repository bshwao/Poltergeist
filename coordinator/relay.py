"""Single-process outbound device relay; credentials never enter public messages."""

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
import time
from uuid import uuid4

from fastapi import WebSocket

from shared.models.remote import (ClientReady, RelayCommand, RelayPing, RelayPong,
    RelayReply, ServerHello, canonical_json, parse_message, MAX_RELAY_BYTES)
from shared.models.browser import BrowserConnectionView

from .auth import AuthError


@dataclass(eq=False)
class DeviceConnection:
    socket: WebSocket
    credential: str = field(repr=False)
    identity: dict = field(repr=False)
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    ready: bool = False
    enabled: bool = False
    sent: set = field(default_factory=set)
    ping: tuple | None = None
    last_received: float = 0.
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def envelope(self):
        return {"device_id": self.identity["id"], "ownership_generation": self.identity["ownership_generation"],
            "connection_generation": self.identity["connection_generation"]}

    async def send(self, message):
        async with self.send_lock:
            await asyncio.wait_for(self.socket.send_text(canonical_json(message)), timeout=30)


class RelayHub:
    """One app worker owns this registry; database generations fence restarts."""
    def __init__(self, commands, *, monotonic=time.monotonic, sleep=asyncio.sleep):
        self.commands, self.monotonic = commands, monotonic
        self.sleep = sleep
        self.connections = {}

    def available(self, device_id):
        current = self.connections.get(str(device_id))
        return current is not None and current.ready and current.enabled and self._current(current)

    def _current(self, current):
        if self.monotonic() - current.last_received >= 90:
            return False
        try:
            self.commands.connection_current(current.credential, current.identity)
            return True
        except AuthError:
            return False

    def browser_connection(self, device):
        """Read on the app loop; browser polling never advances last_seen_at."""
        last_seen = self.commands.last_seen(device.owner.account_id, device.device_id)
        if device.revoked_at is not None:
            return BrowserConnectionView(state="revoked", last_seen_at=last_seen)
        current = self.connections.get(str(device.device_id))
        if current is None or not self._current(current):
            return BrowserConnectionView(state="offline", last_seen_at=last_seen)
        state = "connecting" if not current.ready else "connected" if current.enabled else "paused"
        return BrowserConnectionView(state=state, last_seen_at=last_seen,
            protocol_version="poltergeist-relay-v1" if current.ready else None,
            protocol_compatible=True if current.ready else None,
            remote_requests_enabled=current.ready and current.enabled)

    def notify(self, device_id):
        current = self.connections.get(str(device_id))
        if current is not None:
            current.wake.set()

    async def disconnect(self, device_id):
        current = self.connections.pop(str(device_id), None)
        if current is not None:
            current.wake.set()
            with suppress(Exception):
                await asyncio.wait_for(current.socket.close(code=1008), timeout=5)

    async def close(self):
        for device_id in list(self.connections):
            await self.disconnect(device_id)

    def _check_envelope(self, current, message):
        if (str(message.device_id) != current.identity["id"] or
                message.ownership_generation != current.identity["ownership_generation"] or
                message.connection_generation != current.identity["connection_generation"]):
            raise AuthError(401, "device_connection_fenced")
        self.commands.authenticate(current.credential, current.identity)

    async def _receive(self, current):
        while True:
            frame = await asyncio.wait_for(current.socket.receive(), timeout=90)
            if frame["type"] == "websocket.disconnect":
                return
            # Text JSON only. Binary recording/evidence transport is not S4.
            raw = frame.get("text")
            if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_RELAY_BYTES:
                raise ValueError("Invalid remote protocol message")
            message = parse_message(raw)
            self._check_envelope(current, message)
            if isinstance(message, ClientReady):
                if current.ready:
                    raise ValueError("Invalid remote protocol message")
                current.ready, current.enabled = True, message.remote_execution_enabled
                current.wake.set()
            elif isinstance(message, RelayPong):
                if current.ping is None or message.nonce != current.ping[0]:
                    raise ValueError("Invalid remote protocol message")
                current.ping = None
            elif isinstance(message, RelayReply):
                if not current.ready or not current.enabled or message.reply.request_id not in current.sent:
                    raise ValueError("Invalid remote protocol message")
                self.commands.reply(current.credential, current.identity, message.reply)
                current.sent.discard(message.reply.request_id)
            else:
                raise ValueError("Invalid remote protocol message")
            current.last_received = self.monotonic()

    async def _deliver(self, current):
        while True:
            await current.wake.wait()
            current.wake.clear()
            if not current.ready or not current.enabled:
                continue
            for command in self.commands.pending(current.credential, current.identity):
                if command.request_id in current.sent:
                    continue
                # Recheck immediately before each send, never cache the handshake
                # grant decision across an await or another command delivery.
                if not self.commands.delivery_allowed(current.credential, current.identity, command.request_id):
                    continue
                message = RelayCommand(**current.envelope(), command=command)
                current.sent.add(command.request_id)
                await current.send(message)

    async def _heartbeat(self, current):
        while True:
            await self.sleep(30)
            self.commands.authenticate(current.credential, current.identity)
            now = self.monotonic()
            if now - current.last_received >= 90 or (current.ping is not None and now - current.ping[1] >= 90):
                raise TimeoutError("Device heartbeat expired")
            if current.ping is None:
                nonce = uuid4()
                current.ping = nonce, now
                await current.send(RelayPing(**current.envelope(), nonce=nonce))

    async def connect(self, socket, credential):
        try:
            identity = self.commands.connect(credential)
        except Exception:
            # Fail closed without putting database paths or transport exception
            # strings into a public frame or an authentication log.
            await asyncio.wait_for(socket.close(code=1008), timeout=5)
            return
        current = DeviceConnection(socket, credential, identity, last_received=self.monotonic())
        previous = self.connections.get(identity["id"])
        self.connections[identity["id"]] = current
        if previous is not None:
            previous.wake.set()
            with suppress(Exception):
                await asyncio.wait_for(previous.socket.close(code=1008), timeout=5)
        tasks = []
        try:
            await socket.accept()
            await current.send(ServerHello(**current.envelope(), owner_id=identity["owner_id"],
                policy_revision=identity["policy_revision"], policy=identity["policy"]))
            tasks = [asyncio.create_task(self._receive(current)), asyncio.create_task(self._deliver(current)),
                asyncio.create_task(self._heartbeat(current))]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except Exception:
            pass
        finally:
            if self.connections.get(identity["id"]) is current:
                self.connections.pop(identity["id"], None)
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError, Exception):
                    await task
            with suppress(Exception):
                await asyncio.wait_for(socket.close(code=1008), timeout=5)
