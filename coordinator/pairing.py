"""Atomic local-confirmation enrollment and owner-scoped device metadata."""

from datetime import UTC, datetime
import hmac
import json
import re
import secrets
import sqlite3
import time
from uuid import uuid4

from shared.models.coordination import (AccountIdentity, DevicePolicy, DeviceView,
    PairingApproveInput, PairingCreateInput, PairingCreated, PairingStatus)

from .auth import AuthError, audit, hash_token


ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def timestamp(value):
    return datetime.fromtimestamp(value, UTC)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class PairingService:
    def __init__(self, store, auth, *, now=time.time, ttl_s=300):
        self.store, self.auth, self.now, self.ttl_s = store, auth, now, ttl_s

    def _expire(self, connection, now):
        connection.execute("UPDATE pairing_attempts SET state='expired',revision=revision+1,terminal_at=?,code_hash=NULL "
            "WHERE state IN ('pending','claimed') AND expires_at<=?", (now, now))
        connection.execute("DELETE FROM pairing_attempts WHERE terminal_at IS NOT NULL AND terminal_at<?", (now - 86400,))

    def _private(self, connection, attempt_id, secret):
        row = connection.execute("SELECT * FROM pairing_attempts WHERE id=?", (str(attempt_id),)).fetchone()
        if row is None or not isinstance(secret, str) or not hmac.compare_digest(row["secret_hash"], hash_token(secret)):
            raise AuthError(401, "pairing_authorization_required")
        return row

    def _status(self, connection, row):
        owner = None
        if row["claimed_account_id"]:
            account = connection.execute("SELECT id,username FROM accounts WHERE id=?", (row["claimed_account_id"],)).fetchone()
            owner = AccountIdentity(account_id=account["id"], username=account["username"])
        return PairingStatus(attempt_id=row["id"], device_id=row["device_id"], state=row["state"],
            revision=row["revision"], expires_at=timestamp(row["expires_at"]), claimed_account=owner,
            requested_scopes=json.loads(row["requested_scopes_json"]))

    def create(self, request, *, source):
        request = PairingCreateInput.model_validate(request)
        self.auth.rate_limit("pair-create:" + hash_token(source), 10, 900)
        now = self.now()
        attempt_id, device_id = str(uuid4()), str(uuid4())
        with self.store.transaction() as connection:
            self._expire(connection, now)
            if connection.execute("SELECT count(*) FROM pairing_attempts WHERE state IN ('pending','claimed')").fetchone()[0] >= 1000:
                raise AuthError(503, "pairing_busy", retry_after=60)
            if connection.execute("SELECT count(*) FROM pairing_attempts").fetchone()[0] >= 10000:
                raise AuthError(503, "pairing_busy", retry_after=60)
            for _ in range(5):
                code = "".join(secrets.choice(ALPHABET) for _ in range(10))
                try:
                    connection.execute("INSERT INTO pairing_attempts(id,device_id,secret_hash,code_hash,state,created_at,expires_at,display_name,requested_scopes_json) "
                        "VALUES (?,?,?,?,'pending',?,?,?,?)", (attempt_id, device_id, hash_token(request.private_secret), hash_token(code),
                        now, now + self.ttl_s, request.display_name, canonical(request.model_dump(mode="json")["requested_scopes"])))
                    break
                except sqlite3.IntegrityError:
                    continue
            else:
                raise AuthError(503, "pairing_busy", retry_after=3)
            audit(connection, now, "pairing_create", "allowed", attempt_id=attempt_id)
        return PairingCreated(attempt_id=attempt_id, device_id=device_id, code=code, expires_at=timestamp(now + self.ttl_s))

    def claim(self, account_id, code, *, source):
        self.auth.rate_limit("pair-claim-account:" + str(account_id), 10, 900)
        self.auth.rate_limit("pair-claim-source:" + hash_token(source), 20, 900)
        now = self.now()
        with self.store.transaction() as connection:
            self._expire(connection, now)
            row = connection.execute("SELECT * FROM pairing_attempts WHERE code_hash=?", (hash_token(code),)).fetchone()
            if row is None or row["state"] not in ("pending", "claimed") or row["expires_at"] <= now:
                raise AuthError(400, "pairing_unavailable")
            if row["state"] == "claimed":
                if row["claimed_account_id"] != str(account_id):
                    raise AuthError(400, "pairing_unavailable")
            else:
                connection.execute("UPDATE pairing_attempts SET state='claimed',claimed_account_id=?,revision=revision+1 WHERE id=? AND state='pending'",
                    (str(account_id), row["id"]))
                audit(connection, now, "pairing_claim", "allowed", account_id=str(account_id), attempt_id=row["id"])
            return self._status(connection, connection.execute("SELECT * FROM pairing_attempts WHERE id=?", (row["id"],)).fetchone())

    def poll(self, attempt_id, secret):
        now = self.now()
        with self.store.transaction() as connection:
            self._expire(connection, now)
            row = self._private(connection, attempt_id, secret)
            return self._status(connection, row)

    def approve(self, attempt_id, secret, request):
        request = PairingApproveInput.model_validate(request)
        payload_hash = hash_token(canonical(request.model_dump(mode="json")))
        now = self.now()
        with self.store.transaction() as connection:
            self._expire(connection, now)
            row = self._private(connection, attempt_id, secret)
            if row["state"] == "approved":
                if row["approval_request_id"] == str(request.request_id) and hmac.compare_digest(row["approval_digest"], payload_hash):
                    return self._view(connection, str(request.expected_account_id), row["device_id"])
                raise AuthError(409, "pairing_changed")
            if row["state"] != "claimed" or row["expires_at"] <= now:
                raise AuthError(409, "pairing_changed")
            if row["revision"] != request.expected_revision or row["claimed_account_id"] != str(request.expected_account_id):
                raise AuthError(409, "pairing_changed")
            requested = set(json.loads(row["requested_scopes_json"]))
            if not set(request.policy.scopes).issubset(requested):
                raise AuthError(403, "permission_denied")
            account = connection.execute("SELECT id FROM accounts WHERE id=? AND disabled_at IS NULL", (row["claimed_account_id"],)).fetchone()
            if account is None:
                raise AuthError(409, "pairing_changed")
            connection.execute("INSERT INTO devices(id,owner_id,display_name,policy_json,created_at) VALUES (?,?,?,?,?)",
                (row["device_id"], row["claimed_account_id"], row["display_name"], request.policy.model_dump_json(), now))
            try:
                connection.execute("INSERT INTO device_credentials(id,device_id,ownership_generation,token_hash,created_at) VALUES (?,?,1,?,?)",
                    (str(uuid4()), row["device_id"], hash_token(request.credential), now))
            except sqlite3.IntegrityError:
                raise AuthError(409, "credential_unavailable") from None
            connection.executemany("INSERT INTO device_grants VALUES (?,1,?)",
                [(row["device_id"], scope.value) for scope in request.policy.scopes])
            connection.execute("UPDATE pairing_attempts SET state='approved',approval_request_id=?,approval_digest=?,revision=revision+1,terminal_at=?,code_hash=NULL WHERE id=?",
                (str(request.request_id), payload_hash, now, row["id"]))
            audit(connection, now, "pairing_approve", "allowed", account_id=row["claimed_account_id"], device_id=row["device_id"], attempt_id=row["id"])
            return self._view(connection, row["claimed_account_id"], row["device_id"])

    def deny(self, attempt_id, secret, expected_revision):
        with self.store.transaction() as connection:
            self._expire(connection, self.now())
            row = self._private(connection, attempt_id, secret)
            if row["state"] == "denied":
                return self._status(connection, row)
            if row["state"] not in ("pending", "claimed") or row["revision"] != expected_revision:
                raise AuthError(409, "pairing_changed")
            connection.execute("UPDATE pairing_attempts SET state='denied',revision=revision+1,terminal_at=?,code_hash=NULL WHERE id=?",
                (self.now(), row["id"]))
            audit(connection, self.now(), "pairing_deny", "allowed", attempt_id=row["id"])
            return self._status(connection, connection.execute("SELECT * FROM pairing_attempts WHERE id=?", (row["id"],)).fetchone())

    def _view(self, connection, owner_id, device_id, *, include_revoked=False):
        row = connection.execute("SELECT d.*,a.username FROM devices d JOIN accounts a ON a.id=d.owner_id WHERE d.id=? AND d.owner_id=?",
            (str(device_id), str(owner_id))).fetchone()
        if row is None or (row["revoked_at"] is not None and not include_revoked):
            raise AuthError(404, "resource_not_found")
        return DeviceView(device_id=row["id"], display_name=row["display_name"],
            owner=AccountIdentity(account_id=row["owner_id"], username=row["username"]),
            ownership_generation=row["ownership_generation"], policy_revision=row["policy_revision"],
            policy=DevicePolicy.model_validate_json(row["policy_json"]), created_at=timestamp(row["created_at"]),
            revoked_at=timestamp(row["revoked_at"]) if row["revoked_at"] is not None else None)

    def device(self, owner_id, device_id):
        with self.store.transaction(write=False) as connection:
            return self._view(connection, owner_id, device_id)

    def devices(self, owner_id):
        with self.store.transaction(write=False) as connection:
            identifiers = [row[0] for row in connection.execute("SELECT id FROM devices WHERE owner_id=? ORDER BY created_at DESC,id LIMIT 100", (str(owner_id),))]
            return [self._view(connection, owner_id, identifier, include_revoked=True) for identifier in identifiers]

    def revoke(self, owner_id, device_id):
        now = self.now()
        with self.store.transaction() as connection:
            self._view(connection, owner_id, device_id, include_revoked=True)
            connection.execute("UPDATE devices SET revoked_at=COALESCE(revoked_at,?),connection_generation=connection_generation+1 WHERE id=? AND owner_id=?",
                (now, str(device_id), str(owner_id)))
            connection.execute("UPDATE device_credentials SET revoked_at=COALESCE(revoked_at,?) WHERE device_id=?", (now, str(device_id)))
            connection.execute("UPDATE commands SET status='rejected',error_code='permission_denied',updated_at=?,completed_at=? "
                "WHERE device_id=? AND status='queued'", (now, now, str(device_id)))
            audit(connection, now, "device_revoke", "allowed", account_id=str(owner_id), device_id=str(device_id))
            return self._view(connection, owner_id, device_id, include_revoked=True)

    def authenticate_device(self, credential):
        if not isinstance(credential, str) or not re.fullmatch(r"pd_[A-Za-z0-9_-]{43}", credential):
            raise AuthError(401, "device_authentication_required")
        now = self.now()
        with self.store.transaction(write=False) as connection:
            row = connection.execute("SELECT d.*,c.ownership_generation AS credential_generation FROM device_credentials c "
                "JOIN devices d ON d.id=c.device_id JOIN accounts a ON a.id=d.owner_id "
                "WHERE c.token_hash=? AND c.revoked_at IS NULL AND d.revoked_at IS NULL AND a.disabled_at IS NULL "
                "AND (c.expires_at IS NULL OR c.expires_at>?)", (hash_token(credential), now)).fetchone()
            if row is None or row["credential_generation"] != row["ownership_generation"]:
                raise AuthError(401, "device_authentication_required")
            return {"device_id": row["id"], "owner_id": row["owner_id"], "ownership_generation": row["ownership_generation"],
                    "policy_revision": row["policy_revision"], "connection_generation": row["connection_generation"],
                    "policy": DevicePolicy.model_validate_json(row["policy_json"])}

    def revoke_credential(self, credential):
        # A retired credential can only repeat its own revocation, never reconnect.
        if not isinstance(credential, str) or not re.fullmatch(r"pd_[A-Za-z0-9_-]{43}", credential):
            raise AuthError(401, "device_authentication_required")
        with self.store.transaction(write=False) as connection:
            row = connection.execute("SELECT d.id,d.owner_id FROM device_credentials c JOIN devices d ON d.id=c.device_id "
                "WHERE c.token_hash=?", (hash_token(credential),)).fetchone()
            if row is None:
                raise AuthError(401, "device_authentication_required")
        self.auth.rate_limit("device-revoke:" + hash_token(credential), 20, 60)
        self.revoke(row["owner_id"], row["id"])
        return {"device_id": row["id"], "revoked": True}
