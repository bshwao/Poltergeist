"""Separate, explicitly initialized coordinator metadata and transactions."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3
import stat
from threading import RLock


APPLICATION_ID = 0x504F4C43
SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE coordinator_meta (version INTEGER NOT NULL);
INSERT INTO coordinator_meta VALUES (1);
CREATE TABLE accounts (
 id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
 created_at REAL NOT NULL, disabled_at REAL, auth_revision INTEGER NOT NULL DEFAULT 1);
CREATE TABLE sessions (
 id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
 account_id TEXT NOT NULL REFERENCES accounts(id), csrf_token TEXT NOT NULL,
 created_at REAL NOT NULL, last_used_at REAL NOT NULL, expires_at REAL NOT NULL,
 revoked_at REAL, auth_revision INTEGER NOT NULL);
CREATE INDEX sessions_account ON sessions(account_id);
CREATE TABLE pairing_attempts (
 id TEXT PRIMARY KEY, device_id TEXT NOT NULL UNIQUE, secret_hash TEXT NOT NULL,
 code_hash TEXT UNIQUE, state TEXT NOT NULL CHECK(state IN
 ('pending','claimed','approved','denied','expired','cancelled')),
 created_at REAL NOT NULL, expires_at REAL NOT NULL,
 claimed_account_id TEXT REFERENCES accounts(id), revision INTEGER NOT NULL DEFAULT 1,
 approval_request_id TEXT, approval_digest TEXT, terminal_at REAL,
 display_name TEXT NOT NULL, requested_scopes_json TEXT NOT NULL DEFAULT '[]');
CREATE INDEX pairing_expiry ON pairing_attempts(expires_at);
CREATE TABLE devices (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES accounts(id), display_name TEXT NOT NULL,
 ownership_generation INTEGER NOT NULL DEFAULT 1, policy_revision INTEGER NOT NULL DEFAULT 1,
 policy_json TEXT NOT NULL DEFAULT '{}', connection_generation INTEGER NOT NULL DEFAULT 0,
 created_at REAL NOT NULL, revoked_at REAL, last_seen_at REAL);
CREATE INDEX devices_owner ON devices(owner_id);
CREATE TABLE device_credentials (
 id TEXT PRIMARY KEY, device_id TEXT NOT NULL REFERENCES devices(id),
 ownership_generation INTEGER NOT NULL, token_hash TEXT NOT NULL UNIQUE,
 created_at REAL NOT NULL, expires_at REAL, revoked_at REAL);
CREATE UNIQUE INDEX one_active_credential ON device_credentials(device_id) WHERE revoked_at IS NULL;
CREATE TABLE device_grants (
 device_id TEXT NOT NULL REFERENCES devices(id), ownership_generation INTEGER NOT NULL,
 scope TEXT NOT NULL, PRIMARY KEY(device_id,ownership_generation,scope));
CREATE TABLE commands (
 id TEXT PRIMARY KEY, device_id TEXT NOT NULL REFERENCES devices(id),
 owner_id TEXT NOT NULL REFERENCES accounts(id), ownership_generation INTEGER NOT NULL,
 request_id TEXT NOT NULL, operation TEXT NOT NULL, payload_hash TEXT NOT NULL,
 payload_json TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL,
 updated_at REAL NOT NULL, accepted_at REAL, delivered_at REAL, simulation_id TEXT,
 UNIQUE(device_id,ownership_generation,request_id));
CREATE INDEX commands_owner_device ON commands(owner_id,device_id);
CREATE TABLE security_counters (
 bucket TEXT PRIMARY KEY, count INTEGER NOT NULL, window_start REAL NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE security_events (
 id TEXT PRIMARY KEY, created_at REAL NOT NULL, event_code TEXT NOT NULL,
 outcome_code TEXT NOT NULL, account_id TEXT, device_id TEXT, attempt_id TEXT, command_id TEXT);
"""

# Revision 1 is intentionally unchanged so an existing private coordinator can
# be upgraded in one transaction, without rewriting accounts or credentials.
SCHEMA_V2 = """
ALTER TABLE commands ADD COLUMN reply_json TEXT;
ALTER TABLE commands ADD COLUMN error_code TEXT;
ALTER TABLE commands ADD COLUMN completed_at REAL;
ALTER TABLE commands ADD COLUMN projection_revision INTEGER NOT NULL DEFAULT 0;
UPDATE coordinator_meta SET version=2;
"""


def _statements(connection, script):
    for statement in script.split(";"):
        if statement.strip():
            connection.execute(statement)


def _shape(connection):
    """Compare the full SQLite declarations, including constraints and indexes."""
    return [(row[0], row[1], row[2], " ".join(row[3].split()) if row[3] else None)
            for row in connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]


def _verify_schema(connection, version):
    reference = sqlite3.connect(":memory:")
    try:
        _statements(reference, SCHEMA)
        if version == 2:
            _statements(reference, SCHEMA_V2)
        if _shape(connection) != _shape(reference):
            raise StoreError("Unsupported coordinator database schema")
    finally:
        reference.close()


class StoreError(RuntimeError):
    pass


class Store:
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_absolute():
            raise ValueError("Coordinator database path must be absolute")
        self._path_chain = (self.path, *self.path.parents)
        self._initialized = False
        self._closed = False
        self._lock = RLock()
        self._anchor = None

    def _plain_path(self):
        # Reuse immutable names, never filesystem metadata or a prior check.
        if self._path_chain[0] is not self.path:
            self._path_chain = (self.path, *self.path.parents)
        for part in self._path_chain:
            try:
                info = part.lstat()
            except FileNotFoundError:
                continue
            if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", None)
                    in (getattr(stat, "IO_REPARSE_TAG_SYMLINK", -1), getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", -2))):
                raise StoreError("Coordinator database must use an ordinary local path")

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def initialize(self):
        with self._lock:
            if self._closed:
                raise StoreError("Coordinator store is closed")
            if self._initialized:
                return
            self._plain_path()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                tables = {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                identity = connection.execute("PRAGMA application_id").fetchone()[0]
                if identity not in (0, APPLICATION_ID):
                    raise StoreError("Refusing a database belonging to another application")
                if tables:
                    if "coordinator_meta" not in tables or identity != APPLICATION_ID:
                        raise StoreError("Refusing a database belonging to another application")
                    versions = connection.execute("SELECT version FROM coordinator_meta").fetchall()
                    if len(versions) != 1 or versions[0][0] not in (1, SCHEMA_VERSION):
                        raise StoreError("Unsupported coordinator database schema")
                    if tables != {"coordinator_meta", "accounts", "sessions", "pairing_attempts", "devices",
                                  "device_credentials", "device_grants", "commands", "security_counters", "security_events"}:
                        raise StoreError("Unsupported coordinator database schema")
                    _verify_schema(connection, versions[0][0])
                    if versions[0][0] == 1:
                        _statements(connection, SCHEMA_V2)
                else:
                    _statements(connection, SCHEMA)
                    _statements(connection, SCHEMA_V2)
                    connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
                _verify_schema(connection, SCHEMA_VERSION)
                connection.commit()
                connection.execute("PRAGMA journal_mode=WAL")
                # One bounded connection, protected for each complete short
                # transaction. This avoids connection/checkpoint churn while
                # retaining SQLite FULL durability and per-operation path checks.
                self._anchor = self._connect()
                self._initialized = True
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise
            finally:
                connection.close()

    @contextmanager
    def transaction(self, *, write=True):
        with self._lock:
            if self._closed or not self._initialized:
                raise StoreError("Initialize the coordinator store before use")
            self._plain_path()
            connection = self._anchor
            try:
                connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def close(self):
        with self._lock:
            if self._anchor is not None:
                self._anchor.close()
                self._anchor = None
            self._closed = True
            self._initialized = False
