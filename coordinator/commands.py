"""Durable, owner-scoped command admission and fenced device delivery."""

import hmac
from math import ceil
import time
from uuid import uuid4

from shared.models.coordination import DevicePolicy
from shared.models.remote import (COMMAND_ADAPTER, REPLY_ADAPTER, CancelAccepted,
    CommandRejected, ExecuteAccepted, ExecuteCommand, RunResultReply,
    RemoteCommandView, canonical_json, command_scope, parse_message, payload_digest)

from .auth import AuthError, audit, hash_token
from .pairing import timestamp
from .store import StoreError


def _stored(adapter, value):
    try:
        return adapter.validate_json(value) if hasattr(adapter, "validate_json") else adapter.model_validate_json(value)
    except (ValueError, TypeError, RecursionError):
        raise StoreError("Coordinator command data is unavailable") from None


class CommandService:
    def __init__(self, store, *, now=time.time):
        self.store, self.now = store, now

    def _owner(self, connection, owner_id, device_id, *, scope=None):
        row = connection.execute("SELECT d.* FROM devices d JOIN accounts a ON a.id=d.owner_id "
            "WHERE d.id=? AND d.owner_id=? AND d.revoked_at IS NULL AND a.disabled_at IS NULL",
            (str(device_id), str(owner_id))).fetchone()
        if row is None:
            raise AuthError(404, "resource_not_found")
        if scope is not None:
            granted = connection.execute("SELECT 1 FROM device_grants WHERE device_id=? "
                "AND ownership_generation=? AND scope=?", (row["id"], row["ownership_generation"], str(scope))).fetchone()
            policy = _stored(DevicePolicy, row["policy_json"])
            if granted is None or scope not in policy.scopes:
                raise AuthError(403, "permission_denied")
        return row

    def _credential(self, connection, credential, *, expected=None):
        import re
        if not isinstance(credential, str) or not re.fullmatch(r"pd_[A-Za-z0-9_-]{43}", credential):
            raise AuthError(401, "device_authentication_required")
        row = connection.execute("SELECT d.*,c.ownership_generation AS credential_generation "
            "FROM device_credentials c JOIN devices d ON d.id=c.device_id JOIN accounts a ON a.id=d.owner_id "
            "WHERE c.token_hash=? AND c.revoked_at IS NULL AND d.revoked_at IS NULL AND a.disabled_at IS NULL "
            "AND (c.expires_at IS NULL OR c.expires_at>?)", (hash_token(credential), self.now())).fetchone()
        if row is None or row["credential_generation"] != row["ownership_generation"]:
            raise AuthError(401, "device_authentication_required")
        if expected is not None and any(row[key] != expected[key] for key in (
                "id", "owner_id", "ownership_generation", "connection_generation", "policy_revision")):
            raise AuthError(401, "device_connection_fenced")
        return row

    def connect(self, credential):
        with self.store.transaction() as connection:
            row = self._credential(connection, credential)
            connection.execute("UPDATE devices SET connection_generation=connection_generation+1,last_seen_at=? WHERE id=?",
                (self.now(), row["id"]))
            current = dict(connection.execute("SELECT * FROM devices WHERE id=?", (row["id"],)).fetchone())
            current["policy"] = _stored(DevicePolicy, current.pop("policy_json"))
            return current

    def authenticate(self, credential, expected):
        with self.store.transaction() as connection:
            row = self._credential(connection, credential, expected=expected)
            grants = {item[0] for item in connection.execute("SELECT scope FROM device_grants WHERE device_id=? AND ownership_generation=?",
                (row["id"], row["ownership_generation"]))}
            if grants != set(expected["policy"].scopes):
                raise AuthError(403, "permission_denied")
            connection.execute("UPDATE devices SET last_seen_at=? WHERE id=?", (self.now(), row["id"]))
            return dict(row)

    @staticmethod
    def _execution(connection, row, simulation_id):
        return connection.execute("SELECT * FROM commands WHERE device_id=? AND owner_id=? "
            "AND ownership_generation=? AND operation='execute' AND status='accepted' AND simulation_id=?",
            (row["id"], row["owner_id"], row["ownership_generation"], str(simulation_id))).fetchone()

    @staticmethod
    def _view(row):
        reply = _stored(REPLY_ADAPTER, row["reply_json"]) if row["reply_json"] else None
        return RemoteCommandView(request_id=row["request_id"], operation=row["operation"], status=row["status"],
            created_at=timestamp(row["created_at"]), updated_at=timestamp(row["updated_at"]),
            simulation_id=row["simulation_id"], reply=reply)

    def admit(self, owner_id, device_id, command, *, connected):
        # Validate again for direct callers; normalized defaults are part of the digest.
        command = parse_message(canonical_json(command), relay=False)
        digest, payload = payload_digest(command), canonical_json(command)
        now = self.now()
        with self.store.transaction() as connection:
            row = self._owner(connection, owner_id, device_id, scope=command_scope(command))
            existing = connection.execute("SELECT * FROM commands WHERE device_id=? AND ownership_generation=? AND request_id=?",
                (row["id"], row["ownership_generation"], str(command.request_id))).fetchone()
            if existing is not None:
                if existing["owner_id"] != str(owner_id) or not hmac.compare_digest(existing["payload_hash"], digest):
                    raise AuthError(409, "request_conflict")
                return self._view(existing)
            if isinstance(command, ExecuteCommand):
                policy = _stored(DevicePolicy, row["policy_json"])
                if (command.scenario.duration_s > policy.max_duration_s or command.options.max_ticks > policy.max_ticks or
                        ceil(command.scenario.duration_s / command.scenario.fixed_delta_seconds - 1e-9)
                        > min(policy.max_ticks, command.options.max_ticks)):
                    raise AuthError(403, "policy_denied")
            if command.kind in ("cancel", "result") and self._execution(connection, row, command.simulation_id) is None:
                raise AuthError(404, "resource_not_found")
            if not connected:
                raise AuthError(503, "device_unavailable", retry_after=3)
            count = connection.execute("SELECT count(*) FROM commands WHERE device_id=? AND ownership_generation=? AND status='queued'",
                (row["id"], row["ownership_generation"])).fetchone()[0]
            if count >= 32:
                raise AuthError(429, "device_queue_full", retry_after=3)
            identifier = str(uuid4())
            connection.execute("INSERT INTO commands(id,device_id,owner_id,ownership_generation,request_id,operation,payload_hash,"
                "payload_json,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,'queued',?,?)",
                (identifier, row["id"], str(owner_id), row["ownership_generation"], str(command.request_id), command.kind, digest, payload, now, now))
            audit(connection, now, "command_admit", "allowed", account_id=str(owner_id), device_id=row["id"], command_id=identifier)
            return self._view(connection.execute("SELECT * FROM commands WHERE id=?", (identifier,)).fetchone())

    def get(self, owner_id, device_id, request_id):
        with self.store.transaction(write=False) as connection:
            row = self._owner(connection, owner_id, device_id)
            command = connection.execute("SELECT * FROM commands WHERE device_id=? AND owner_id=? AND ownership_generation=? AND request_id=?",
                (row["id"], str(owner_id), row["ownership_generation"], str(request_id))).fetchone()
            if command is None:
                raise AuthError(404, "resource_not_found")
            parsed = _stored(COMMAND_ADAPTER, command["payload_json"])
            self._owner(connection, owner_id, device_id, scope=command_scope(parsed))
            return self._view(command)

    def pending(self, credential, expected):
        with self.store.transaction() as connection:
            row = self._credential(connection, credential, expected=expected)
            commands = connection.execute("SELECT * FROM commands WHERE device_id=? AND ownership_generation=? AND status='queued' "
                "ORDER BY created_at,id LIMIT 32", (row["id"], row["ownership_generation"])).fetchall()
            pending = []
            for stored in commands:
                command = _stored(COMMAND_ADAPTER, stored["payload_json"])
                try:
                    self._owner(connection, row["owner_id"], row["id"], scope=command_scope(command))
                except AuthError:
                    rejected = CommandRejected(request_id=command.request_id, code="permission_denied")
                    connection.execute("UPDATE commands SET status='rejected',reply_json=?,error_code='permission_denied',"
                        "updated_at=?,completed_at=? WHERE id=?", (canonical_json(rejected), self.now(), self.now(), stored["id"]))
                    continue
                connection.execute("UPDATE commands SET delivered_at=? WHERE id=?", (self.now(), stored["id"]))
                pending.append(command)
            return pending

    def delivery_allowed(self, credential, expected, request_id):
        with self.store.transaction(write=False) as connection:
            row = self._credential(connection, credential, expected=expected)
            stored = connection.execute("SELECT * FROM commands WHERE device_id=? AND ownership_generation=? AND request_id=?",
                (row["id"], row["ownership_generation"], str(request_id))).fetchone()
            if stored is None or stored["status"] != "queued":
                return False
            command = _stored(COMMAND_ADAPTER, stored["payload_json"])
            self._owner(connection, row["owner_id"], row["id"], scope=command_scope(command))
            return True

    def reply(self, credential, expected, reply):
        reply = REPLY_ADAPTER.validate_json(canonical_json(reply))
        now = self.now()
        with self.store.transaction() as connection:
            row = self._credential(connection, credential, expected=expected)
            stored = connection.execute("SELECT * FROM commands WHERE device_id=? AND ownership_generation=? AND request_id=?",
                (row["id"], row["ownership_generation"], str(reply.request_id))).fetchone()
            if stored is None or stored["owner_id"] != row["owner_id"]:
                raise AuthError(404, "resource_not_found")
            command = _stored(COMMAND_ADAPTER, stored["payload_json"])
            self._owner(connection, row["owner_id"], row["id"], scope=command_scope(command))
            encoded = canonical_json(reply)
            if stored["status"] != "queued":
                if stored["reply_json"] == encoded:
                    return self._view(stored)
                raise AuthError(409, "request_conflict")
            expected_kind = {"execute": "accepted", "cancel": "cancel_requested", "status": "status", "result": "result"}[command.kind]
            if reply.kind not in (expected_kind, "rejected"):
                raise AuthError(400, "invalid_device_reply")
            simulation_id, projection_revision = None, 0
            if isinstance(reply, ExecuteAccepted):
                conflict = connection.execute("SELECT 1 FROM commands WHERE operation='execute' AND simulation_id=? AND id<>?",
                    (str(reply.simulation_id), stored["id"])).fetchone()
                if conflict is not None:
                    raise AuthError(409, "request_conflict")
                simulation_id = str(reply.simulation_id)
            elif isinstance(reply, CancelAccepted):
                if reply.simulation_id != command.simulation_id or self._execution(connection, row, command.simulation_id) is None:
                    raise AuthError(400, "invalid_device_reply")
                simulation_id = str(reply.simulation_id)
            elif isinstance(reply, RunResultReply):
                summary = reply.summary
                execution = self._execution(connection, row, command.simulation_id)
                if (execution is None or str(summary.device_id) != row["id"] or
                        summary.ownership_generation != row["ownership_generation"] or summary.simulation_id != command.simulation_id or
                        str(summary.execute_request_id) != execution["request_id"]):
                    raise AuthError(400, "invalid_device_reply")
                accepted = _stored(COMMAND_ADAPTER, execution["payload_json"])
                if (summary.mode != accepted.mode or summary.scenario.name != accepted.scenario.name or
                        summary.scenario.sha256 != payload_digest(accepted.scenario)):
                    raise AuthError(400, "invalid_device_reply")
                previous = connection.execute("SELECT projection_revision,reply_json FROM commands WHERE device_id=? AND ownership_generation=? "
                    "AND operation='result' AND status='completed' AND simulation_id=? ORDER BY projection_revision DESC LIMIT 1",
                    (row["id"], row["ownership_generation"], str(command.simulation_id))).fetchone()
                if previous is not None and summary.receipt_revision < previous["projection_revision"]:
                    raise AuthError(409, "stale_projection")
                if previous is not None and summary.receipt_revision == previous["projection_revision"]:
                    prior = _stored(REPLY_ADAPTER, previous["reply_json"])
                    if canonical_json(prior.summary) != canonical_json(summary):
                        raise AuthError(409, "stale_projection")
                simulation_id, projection_revision = str(summary.simulation_id), summary.receipt_revision
            status = "rejected" if isinstance(reply, CommandRejected) else "accepted" if isinstance(reply, ExecuteAccepted) else "completed"
            connection.execute("UPDATE commands SET status=?,reply_json=?,error_code=?,simulation_id=?,accepted_at=?,"
                "completed_at=?,projection_revision=?,updated_at=? WHERE id=?", (status, encoded,
                reply.code if isinstance(reply, CommandRejected) else None, simulation_id,
                reply.accepted_at.timestamp() if isinstance(reply, (ExecuteAccepted, CancelAccepted)) else None,
                None if status == "accepted" else now, projection_revision, now, stored["id"]))
            audit(connection, now, "command_reply", status, account_id=row["owner_id"], device_id=row["id"], command_id=stored["id"])
            return self._view(connection.execute("SELECT * FROM commands WHERE id=?", (stored["id"],)).fetchone())
