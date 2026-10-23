"""Revocable opaque sessions and bounded Argon2id account authentication."""

import hashlib
import re
import secrets
import sqlite3
from threading import BoundedSemaphore
import time
from uuid import uuid4

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError


_HASH_SLOTS = BoundedSemaphore(2)
_TOKEN = re.compile(r"ps_[A-Za-z0-9_-]{43}")
_USERNAME = re.compile(r"[a-z0-9][a-z0-9_-]{2,31}", re.ASCII)


def hash_token(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AuthError(RuntimeError):
    def __init__(self, status, code, *, retry_after=None):
        super().__init__(code)
        self.status, self.code, self.retry_after = status, code, retry_after


def audit(connection, now, event, outcome, *, account_id=None, device_id=None, attempt_id=None, command_id=None):
    connection.execute("INSERT INTO security_events VALUES (?,?,?,?,?,?,?,?)",
        (str(uuid4()), now, event, outcome, account_id, device_id, attempt_id, command_id))


class AuthService:
    def __init__(self, store, *, now=time.time):
        self.store, self.now = store, now
        self.hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1, type=Type.ID)
        self._dummy = self._hash(secrets.token_urlsafe(32))

    def _hash(self, password):
        if not _HASH_SLOTS.acquire(blocking=False):
            raise AuthError(503, "authentication_busy", retry_after=1)
        try:
            return self.hasher.hash(password)
        finally:
            _HASH_SLOTS.release()

    def _verify(self, encoded, password):
        if not _HASH_SLOTS.acquire(blocking=False):
            raise AuthError(503, "authentication_busy", retry_after=1)
        try:
            try:
                return self.hasher.verify(encoded, password)
            except (VerificationError, InvalidHashError):
                return False
        finally:
            _HASH_SLOTS.release()

    @staticmethod
    def username(value):
        if not isinstance(value, str) or not _USERNAME.fullmatch(value.lower()):
            raise AuthError(400, "invalid_account_input")
        return value.lower()

    @staticmethod
    def password(value):
        try:
            valid = isinstance(value, str) and 15 <= len(value) <= 128 and len(value.encode("utf-8")) <= 1024
        except UnicodeError:
            valid = False
        if not valid:
            raise AuthError(400, "invalid_account_input")
        return value

    def _check_limits(self, buckets):
        now = self.now()
        with self.store.transaction(write=False) as connection:
            for bucket, limit, window in buckets:
                row = connection.execute("SELECT count,expires_at FROM security_counters WHERE bucket=?", (bucket,)).fetchone()
                if row is not None and row["expires_at"] > now and row["count"] >= limit:
                    raise AuthError(429, "rate_limited", retry_after=max(1, int(row["expires_at"] - now) + 1))

    def rate_limit(self, bucket, limit, window_s):
        now = self.now()
        with self.store.transaction() as connection:
            connection.execute("DELETE FROM security_counters WHERE expires_at<=?", (now,))
            row = connection.execute("SELECT count,expires_at FROM security_counters WHERE bucket=?", (bucket,)).fetchone()
            if row is not None and row["count"] >= limit:
                raise AuthError(429, "rate_limited", retry_after=max(1, int(row["expires_at"] - now) + 1))
            if row is None:
                if connection.execute("SELECT count(*) FROM security_counters").fetchone()[0] >= 10000:
                    raise AuthError(503, "authentication_busy", retry_after=60)
                connection.execute("INSERT INTO security_counters VALUES (?,?,?,?)", (bucket, 1, now, now + window_s))
            else:
                connection.execute("UPDATE security_counters SET count=count+1 WHERE bucket=?", (bucket,))

    def register(self, username, password, *, source="local"):
        self.rate_limit("register:" + hash_token(source), 10, 900)
        username, password = self.username(username), self.password(password)
        encoded = self._hash(password)
        account_id, now = str(uuid4()), self.now()
        try:
            with self.store.transaction() as connection:
                connection.execute("INSERT INTO accounts(id,username,password_hash,created_at) VALUES (?,?,?,?)",
                    (account_id, username, encoded, now))
                audit(connection, now, "register", "allowed", account_id=account_id)
        except sqlite3.IntegrityError:
            raise AuthError(409, "account_unavailable") from None
        return {"account_id": account_id, "username": username}

    def login(self, username, password, source="local"):
        try:
            normalized = self.username(username)
            valid_username = True
        except AuthError:
            normalized, valid_username = "invalid", False
        buckets = [("login-account:" + hash_token(normalized), 5, 900), ("login-source:" + hash_token(source), 20, 900)]
        self._check_limits(buckets)
        with self.store.transaction(write=False) as connection:
            account = connection.execute("SELECT * FROM accounts WHERE username=?", (normalized,)).fetchone() if valid_username else None
        try:
            password = self.password(password)
            valid_password = True
        except AuthError:
            password = "invalid-login-value"
            valid_password = False
        verified = self._verify(account["password_hash"] if account is not None and valid_password else self._dummy, password)
        if account is None or account["disabled_at"] is not None or not valid_password or not verified:
            for bucket, limit, window in buckets:
                self.rate_limit(bucket, limit, window)
            with self.store.transaction() as connection:
                audit(connection, self.now(), "login", "denied")
            raise AuthError(401, "invalid_credentials")
        replacement = self._hash(password) if self.hasher.check_needs_rehash(account["password_hash"]) else None
        token, csrf, now = "ps_" + secrets.token_urlsafe(32), secrets.token_urlsafe(32), self.now()
        expires = now + 86400
        with self.store.transaction() as connection:
            current = connection.execute("SELECT * FROM accounts WHERE id=?", (account["id"],)).fetchone()
            if current["disabled_at"] is not None or current["password_hash"] != account["password_hash"] or current["auth_revision"] != account["auth_revision"]:
                raise AuthError(401, "invalid_credentials")
            if replacement is not None:
                connection.execute("UPDATE accounts SET password_hash=? WHERE id=?", (replacement, account["id"]))
            connection.execute("INSERT INTO sessions(id,token_hash,account_id,csrf_token,created_at,last_used_at,expires_at,revoked_at,auth_revision) VALUES (?,?,?,?,?,?,?,?,?)",
                (str(uuid4()), hash_token(token), account["id"], csrf, now, now, expires, None, account["auth_revision"]))
            audit(connection, now, "login", "allowed", account_id=account["id"])
        return {"account_id": account["id"], "username": account["username"], "session_token": token,
                "csrf_token": csrf, "expires_at": expires}

    def session(self, token):
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise AuthError(401, "authentication_required")
        now = self.now()
        with self.store.transaction() as connection:
            row = connection.execute("SELECT s.*,a.username,a.disabled_at,a.auth_revision AS current_revision "
                "FROM sessions s JOIN accounts a ON a.id=s.account_id WHERE s.token_hash=?", (hash_token(token),)).fetchone()
            if row is None or row["revoked_at"] is not None or row["disabled_at"] is not None or row["auth_revision"] != row["current_revision"] or now >= row["expires_at"] or now - row["last_used_at"] >= 1800:
                raise AuthError(401, "authentication_required")
            connection.execute("UPDATE sessions SET last_used_at=? WHERE id=?", (now, row["id"]))
        return {"account_id": row["account_id"], "username": row["username"], "csrf_token": row["csrf_token"], "expires_at": row["expires_at"]}

    def logout(self, token):
        self.session(token)
        with self.store.transaction() as connection:
            connection.execute("UPDATE sessions SET revoked_at=? WHERE token_hash=?", (self.now(), hash_token(token)))

    def change_password(self, token, old, new):
        account = self.session(token)
        self.rate_limit("password-change:" + account["account_id"], 5, 900)
        self.password(new)
        with self.store.transaction(write=False) as connection:
            row = connection.execute("SELECT * FROM accounts WHERE id=?", (account["account_id"],)).fetchone()
        try:
            old = self.password(old)
            valid_old = True
        except AuthError:
            old = "invalid-login-value"
            valid_old = False
        verified = self._verify(row["password_hash"] if valid_old else self._dummy, old)
        if not valid_old or not verified:
            raise AuthError(401, "invalid_credentials")
        encoded, now = self._hash(new), self.now()
        with self.store.transaction() as connection:
            current_session = connection.execute("SELECT revoked_at,expires_at,last_used_at FROM sessions WHERE token_hash=?", (hash_token(token),)).fetchone()
            if current_session is None or current_session["revoked_at"] is not None or now >= current_session["expires_at"] or now - current_session["last_used_at"] >= 1800:
                raise AuthError(401, "authentication_required")
            changed = connection.execute("UPDATE accounts SET password_hash=?,auth_revision=auth_revision+1 WHERE id=? AND auth_revision=?",
                (encoded, account["account_id"], row["auth_revision"]))
            if changed.rowcount != 1:
                raise AuthError(409, "account_changed")
            connection.execute("UPDATE sessions SET revoked_at=? WHERE account_id=?", (now, account["account_id"]))
            audit(connection, now, "password_change", "allowed", account_id=account["account_id"])
