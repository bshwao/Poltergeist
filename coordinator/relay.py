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
from shared.models.evidence import EvidenceReply
from shared.models.lab import LabCommand, LabReply


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
    evidence_supported: bool = False
    recordings_supported: bool = False
    lab_supported: bool = False
    analysis_supported: bool = False
    transfers: dict = field(default_factory=dict)

    def envelope(self):
        return {"device_id": self.identity["id"], "ownership_generation": self.identity["ownership_generation"],
            "connection_generation": self.identity["connection_generation"]}

    async def send(self, message):
        async with self.send_lock:
            await asyncio.wait_for(self.socket.send_text(canonical_json(message)), timeout=30)


class RelayHub:
    """One app worker owns this registry; database generations fence restarts."""
    def __init__(self, commands, *, monotonic=time.monotonic, sleep=asyncio.sleep,
                 max_connections=100, max_transfers=8):
        self.commands, self.monotonic = commands, monotonic
        self.sleep = sleep
        self.connections = {}
        self.max_connections, self.max_transfers = max_connections, max_transfers
        self.transfer_count = 0
        self.evidence_bytes = 0
        self.capacity_refusals = 0

    def available(self, device_id, *, connection=None):
        current = self.connections.get(str(device_id))
        return current is not None and current.ready and current.enabled and self._current(current, connection=connection)

    def _current(self, current, *, connection=None):
        if self._expired(current):
            return False
        try:
            if connection is None:
                self.commands.connection_current(current.credential, current.identity)
            else:
                self.commands._connection_current(connection, current.credential, current.identity)
            return True
        except AuthError:
            return False

    def _expired(self, current):
        now = self.monotonic()
        return (now - current.last_received >= 90
            or (current.ping is not None and now - current.ping[1] >= 90))

    def _check_deadline(self, current):
        if self._expired(current):
            raise TimeoutError("Device heartbeat expired")

    def _require_live(self, current):
        self._check_deadline(current)
        self.commands.connection_current(current.credential, current.identity)

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
            evidence_supported=current.evidence_supported,
            recordings_supported=current.recordings_supported,
            lab_supported=current.lab_supported,
            analysis_supported=current.analysis_supported,
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

    def _check_envelope(self, current, message, *, authenticate=True):
        if (str(message.device_id) != current.identity["id"] or
                message.ownership_generation != current.identity["ownership_generation"] or
                message.connection_generation != current.identity["connection_generation"]):
            raise AuthError(401, "device_connection_fenced")
        if authenticate:
            self.commands.authenticate(current.credential, current.identity)

    async def _receive(self, current):
        while True:
            frame = await asyncio.wait_for(current.socket.receive(), timeout=90)
            if frame["type"] == "websocket.disconnect":
                return
            # Check after the await: a buffered ready/pong/reply cannot revive
            # an expired connection when the event loop resumes after a gap.
            self._check_deadline(current)
            # Text JSON only. Binary recording/evidence transport is not S4.
            raw = frame.get("text")
            if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_RELAY_BYTES:
                raise ValueError("Invalid remote protocol message")
            message = parse_message(raw)
            durable_reply = (isinstance(message, RelayReply) and current.ready and current.enabled
                and message.reply.request_id in current.sent and message.reply.request_id not in current.transfers)
            # Durable replies recheck credentials/grants inside reply() so the
            # activity timestamp and receipt share one FULL-durability commit.
            self._check_envelope(current, message, authenticate=not durable_reply)
            if isinstance(message, ClientReady):
                if current.ready:
                    raise ValueError("Invalid remote protocol message")
                current.ready, current.enabled = True, message.remote_execution_enabled
                current.evidence_supported = current.evidence_supported and message.evidence_supported
                current.recordings_supported = current.recordings_supported and message.recordings_supported
                current.lab_supported = current.lab_supported and message.lab_supported
                current.analysis_supported = current.analysis_supported and message.analysis_supported and current.lab_supported
                current.wake.set()
            elif isinstance(message, RelayPong):
                if current.ping is None or message.nonce != current.ping[0]:
                    raise ValueError("Invalid remote protocol message")
                current.ping = None
            elif isinstance(message, RelayReply):
                pending = current.transfers.get(message.reply.request_id)
                if pending is not None:
                    command, future = pending
                    if isinstance(command, LabCommand):
                        if not isinstance(message.reply, LabReply) and message.reply.kind != "rejected":
                            raise ValueError("Invalid experiment reply")
                        if not future.done():
                            future.set_result(message.reply)
                        current.last_received = self.monotonic()
                        continue
                    if not isinstance(message.reply, EvidenceReply) and message.reply.kind != "rejected":
                        raise ValueError("Invalid evidence reply")
                    if isinstance(message.reply, EvidenceReply) and (
                            message.reply.simulation_id != command.simulation_id or
                            message.reply.resource != command.resource or message.reply.offset != command.offset):
                        raise ValueError("Invalid evidence reply")
                    if (isinstance(message.reply, EvidenceReply) and current.recordings_supported
                            and message.reply.artifact_index != command.artifact_index):
                        raise ValueError("Evidence reply belongs to another recording")
                    if not future.done():
                        future.set_result(message.reply)
                    current.last_received = self.monotonic()
                    continue
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
            self._require_live(current)
            for command in self.commands.pending(current.credential, current.identity):
                if command.request_id in current.sent:
                    continue
                self._require_live(current)
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
            # Server polling is not evidence of activity from the computer.
            self._require_live(current)
            now = self.monotonic()
            if current.ping is None:
                nonce = uuid4()
                current.ping = nonce, now
                await current.send(RelayPing(**current.envelope(), nonce=nonce))

    async def connect(self, socket, credential):
        try:
            # Authenticate before capacity checks; a refused reconnect must not
            # fence the existing generation.
            with self.commands.store.transaction(write=False) as connection:
                candidate = self.commands._credential(connection, credential)
                if candidate["id"] not in self.connections and len(self.connections) >= self.max_connections:
                    self.capacity_refusals += 1
                    raise AuthError(503, "relay_capacity")
            identity = self.commands.connect(credential)
        except Exception:
            # Fail closed without putting database paths or transport exception
            # strings into a public frame or an authentication log.
            await asyncio.wait_for(socket.close(code=1008), timeout=5)
            return
        current = DeviceConnection(socket, credential, identity, last_received=self.monotonic())
        current.evidence_supported = getattr(socket, "headers", {}).get("x-poltergeist-evidence") == "v1"
        current.recordings_supported = (current.evidence_supported
            and getattr(socket, "headers", {}).get("x-poltergeist-recordings") == "v1")
        current.lab_supported = getattr(socket, "headers", {}).get("x-poltergeist-lab") == "v1"
        current.analysis_supported = current.lab_supported and getattr(socket, "headers", {}).get("x-poltergeist-analysis") == "v1"
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
                policy_revision=identity["policy_revision"], policy=identity["policy"],
                evidence_supported=current.evidence_supported, recordings_supported=current.recordings_supported,
                lab_supported=current.lab_supported, analysis_supported=current.analysis_supported))
            tasks = [asyncio.create_task(self._receive(current)), asyncio.create_task(self._deliver(current)),
                asyncio.create_task(self._heartbeat(current))]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except Exception:
            pass
        finally:
            for _, future in current.transfers.values():
                if not future.done():
                    future.set_exception(AuthError(503, "device_unavailable"))
            if self.connections.get(identity["id"]) is current:
                self.connections.pop(identity["id"], None)
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError, Exception):
                    await task
            with suppress(Exception):
                await asyncio.wait_for(socket.close(code=1008), timeout=5)

    async def evidence(self, owner_id, device_id, command):
        generation = self.commands.authorize_evidence(owner_id, device_id, command)
        current = self.connections.get(str(device_id))
        if not self.available(device_id) or not current.evidence_supported:
            raise AuthError(503, "evidence_unavailable", retry_after=3)
        if command.resource in ("recordings", "samples") and not current.recordings_supported:
            raise AuthError(503, "evidence_unavailable", retry_after=3)
        if generation != current.identity["ownership_generation"]:
            raise AuthError(403, "permission_denied")
        # One page in flight per device; no producer queue or full recording buffer.
        if current.transfers or self.transfer_count >= self.max_transfers:
            self.capacity_refusals += 1
            raise AuthError(429, "transfer_busy", retry_after=1)
        future = asyncio.get_running_loop().create_future()
        current.transfers[command.request_id] = (command, future)
        self.transfer_count += 1
        try:
            self._require_live(current)
            await current.send(RelayCommand(**current.envelope(), command=command))
            reply = await asyncio.wait_for(future, timeout=10)
            self._require_live(current)
            self.commands.authorize_evidence(owner_id, device_id, command)
            self.evidence_bytes += len(canonical_json(reply).encode())
            return reply
        except TimeoutError:
            raise AuthError(504, "evidence_timeout", retry_after=1) from None
        finally:
            current.transfers.pop(command.request_id, None)
            self.transfer_count -= 1
            if not future.done():
                future.cancel()

    async def lab(self, owner_id, device_id, command):
        generation = self.commands.authorize_lab(owner_id, device_id, command)
        current = self.connections.get(str(device_id))
        if not self.available(device_id) or not current.lab_supported:
            raise AuthError(503, "lab_unavailable", retry_after=3)
        if command.action.kind in {"analysis_get", "regression_report"} and not current.analysis_supported:
            raise AuthError(503, "analysis_unavailable", retry_after=3)
        if generation != current.identity["ownership_generation"]:
            raise AuthError(403, "permission_denied")
        if current.transfers or self.transfer_count >= self.max_transfers:
            self.capacity_refusals += 1
            raise AuthError(429, "transfer_busy", retry_after=1)
        future = asyncio.get_running_loop().create_future()
        current.transfers[command.request_id] = command, future
        self.transfer_count += 1
        try:
            self._require_live(current)
            await current.send(RelayCommand(**current.envelope(), command=command))
            result = await asyncio.wait_for(future, timeout=10)
            self._require_live(current)
            self.commands.authorize_lab(owner_id, device_id, command)
            return result
        except TimeoutError:
            raise AuthError(504, "lab_timeout", retry_after=1) from None
        finally:
            current.transfers.pop(command.request_id, None)
            self.transfer_count -= 1
            if not future.done():
                future.cancel()
