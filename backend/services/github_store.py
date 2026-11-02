"""Durable publication intent and outcome, separate from simulation ownership."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3

from backend.persistence_errors import ConflictError, NotFoundError
from companion.model_assets import _plain
from shared.models.github import GitHubPublication


class GitHubStore:
    def __init__(self, storage):
        self.path = Path(storage) / "github-publications.sqlite3"

    @contextmanager
    def connection(self):
        _plain(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, isolation_level=None, timeout=5)
        try:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("CREATE TABLE IF NOT EXISTS github_publications (id TEXT PRIMARY KEY, record TEXT NOT NULL, context TEXT NOT NULL, publication_key TEXT UNIQUE, reserved_day TEXT)")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def read(connection, identity):
        row = connection.execute("SELECT record, context FROM github_publications WHERE id=?", (str(identity),)).fetchone()
        if row is None:
            raise NotFoundError("GitHub publication does not exist")
        return GitHubPublication.model_validate_json(row[0]), row[1]

    @staticmethod
    def write(connection, record):
        connection.execute("UPDATE github_publications SET record=? WHERE id=?", (record.model_dump_json(), str(record.id)))

    def create(self, record, context):
        with self.connection() as connection:
            if connection.execute("SELECT 1 FROM github_publications WHERE id=?", (str(record.id),)).fetchone():
                raise ConflictError("This request already has a saved GitHub preview; review its history")
            if connection.execute("SELECT COUNT(*) FROM github_publications").fetchone()[0] >= 10000:
                raise ConflictError("The GitHub publication journal reached its storage limit")
            connection.execute("INSERT INTO github_publications(id, record, context) VALUES (?,?,?)", (str(record.id), record.model_dump_json(), context))
        return record

    def get(self, identity):
        with self.connection() as connection:
            return self.read(connection, identity)[0]

    def history(self):
        with self.connection() as connection:
            return [GitHubPublication.model_validate_json(row[0]) for row in connection.execute("SELECT record FROM github_publications ORDER BY rowid DESC LIMIT 30")]

    def reserve(self, identity, digest, *, now, key, limit):
        with self.connection() as connection:
            record, context = self.read(connection, identity)
            if digest != record.preview.digest:
                raise ConflictError("The publication preview changed; review its current digest")
            if record.state != "preview":
                return record, context, False
            if now >= record.preview.expires_at:
                raise ConflictError("This GitHub publication preview expired; create a fresh preview")
            other = connection.execute("SELECT id FROM github_publications WHERE publication_key=? AND id<>?", (key, str(identity))).fetchone()
            if other:
                raise ConflictError("This exact check already has a saved or uncertain publication. Review publication history instead of creating a duplicate.")
            if connection.execute("SELECT COUNT(*) FROM github_publications WHERE reserved_day=?", (now.date().isoformat(),)).fetchone()[0] >= limit:
                raise ConflictError("The configured daily GitHub publication limit is exhausted")
            record = record.model_copy(update={"state": "publishing", "message": "Publication reserved once. Lost responses and retries never automatically repeat the write."})
            self.write(connection, record)
            connection.execute("UPDATE github_publications SET publication_key=?, reserved_day=? WHERE id=?", (key, now.date().isoformat(), str(identity)))
            return record, context, True

    def finish(self, record):
        with self.connection() as connection:
            self.write(connection, record)
            if record.state == "failed":
                connection.execute("UPDATE github_publications SET publication_key=NULL WHERE id=?", (str(record.id),))
        return record
