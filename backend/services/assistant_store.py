"""Separate opt-in usage journal; reservation commits before external spending."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3

from backend.persistence_errors import ConflictError, NotFoundError
from companion.model_assets import _plain
from shared.models.assistant import AssistantRecord


class AssistantStore:
    def __init__(self, storage):
        self.path = Path(storage) / "openai-assistant.sqlite3"

    @contextmanager
    def connection(self):
        _plain(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        try:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("CREATE TABLE IF NOT EXISTS assistant_sessions (id TEXT PRIMARY KEY, record TEXT NOT NULL, context TEXT NOT NULL, reserved_day TEXT, reserved_tokens INTEGER NOT NULL DEFAULT 0)")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def read(connection, identity):
        row = connection.execute("SELECT record, context FROM assistant_sessions WHERE id=?", (str(identity),)).fetchone()
        if row is None:
            raise NotFoundError("Assistant request does not exist")
        return AssistantRecord.model_validate_json(row[0]), row[1]

    @staticmethod
    def write(connection, record):
        connection.execute("UPDATE assistant_sessions SET record=? WHERE id=?", (record.model_dump_json(), str(record.id)))

    def create(self, record, context):
        with self.connection() as connection:
            if connection.execute("SELECT 1 FROM assistant_sessions WHERE id=?", (str(record.id),)).fetchone():
                raise ConflictError("This assistant request identity already exists; review its saved record")
            if connection.execute("SELECT COUNT(*) FROM assistant_sessions").fetchone()[0] >= 10000:
                raise ConflictError("The local assistant journal has reached its request limit")
            connection.execute("INSERT INTO assistant_sessions(id,record,context) VALUES (?,?,?)", (str(record.id), record.model_dump_json(), context))
        return record

    def get(self, identity):
        with self.connection() as connection:
            return self.read(connection, identity)[0]

    def history(self):
        with self.connection() as connection:
            return [AssistantRecord.model_validate_json(row[0]) for row in connection.execute("SELECT record FROM assistant_sessions ORDER BY rowid DESC LIMIT 30")]

    def reserve(self, identity, digest, *, day, now, request_limit, token_limit):
        with self.connection() as connection:
            record, context = self.read(connection, identity)
            if record.preview.digest != digest:
                raise ConflictError("The disclosure preview changed; review it again before sending")
            if record.state != "preview":
                return record, context, False
            if now >= record.preview.expires_at:
                raise ConflictError("The disclosure preview expired; create a new preview")
            calls, tokens = connection.execute("SELECT COUNT(*), COALESCE(SUM(reserved_tokens),0) FROM assistant_sessions WHERE reserved_day=?", (day,)).fetchone()
            if calls >= request_limit or tokens + record.preview.budget.max_output_tokens > token_limit:
                raise ConflictError("The configured daily OpenAI request or output-token budget is exhausted")
            record = record.model_copy(update={"state": "running", "message": "One provider call reserved. Reopening or retrying this request never sends it again.", "reserved_output_tokens": record.preview.budget.max_output_tokens})
            self.write(connection, record)
            connection.execute("UPDATE assistant_sessions SET reserved_day=?, reserved_tokens=? WHERE id=?", (day, record.reserved_output_tokens, str(identity)))
            return record, context, True

    def finish(self, record):
        with self.connection() as connection:
            self.write(connection, record)
        return record
