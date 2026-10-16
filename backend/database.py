"""Explicit SQLite initialization and transaction ownership.

Constructing a Database does not create directories, connect, or migrate.
Only ``initialize`` performs those operations; repositories share the session
owned by ``session`` so multi-record writes commit or roll back together.
Write units reserve SQLite's writer slot before their first read, avoiding
deferred read-to-write upgrade deadlocks under concurrent HTTP/worker writes.
"""

from contextlib import contextmanager, nullcontext
from pathlib import Path
from threading import RLock
from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from .migrations import migrate
from .persistence_errors import ConflictError, DatabaseInitializationError, PersistenceError


class Database:
    def __init__(self, database_url: str):
        try:
            self._url = make_url(database_url)
        except ArgumentError as error:
            raise PersistenceError("Invalid database URL") from error
        if self._url.drivername not in {"sqlite", "sqlite+pysqlite"}:
            raise PersistenceError("Only local SQLite databases are supported in this section")
        if any(part is not None for part in (self._url.host, self._url.port, self._url.username, self._url.password)):
            raise PersistenceError("SQLite must use a local filesystem path without host or credentials")
        if self._url.query:
            raise PersistenceError("SQLite URL query options are unsupported; configure a plain local file or :memory:")
        self._engine: Engine | None = None
        self._lock = RLock()

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            raise PersistenceError("Database is not initialized; call initialize() explicitly")
        return self._engine

    @property
    def file_path(self) -> Path | None:
        """Canonical local path, or None for this instance's private memory DB."""
        return None if self._url.database in (None, "", ":memory:") else Path(self._url.database).resolve()

    def initialize(self) -> None:
        with self._lock:
            if self._engine is not None:
                return
            memory = self._url.database in (None, "", ":memory:")
            if not memory:
                Path(self._url.database).parent.mkdir(parents=True, exist_ok=True)
            engine = create_engine(
                self._url, connect_args={"check_same_thread": False, "timeout": 5.0},
                **({"poolclass": StaticPool} if memory else {}),
            )

            @event.listens_for(engine, "connect")
            def configure_connection(dbapi_connection, _):
                # sqlite3 legacy transaction handling otherwise autocommits DDL.
                dbapi_connection.isolation_level = None
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.close()

            @event.listens_for(engine, "begin")
            def begin_transaction(connection):
                immediate = connection.info.pop("migration_transaction", False) or connection.get_execution_options().get("sqlite_write_transaction", False)
                connection.exec_driver_sql("BEGIN IMMEDIATE" if immediate else "BEGIN")

            try:
                with engine.connect() as connection:
                    connection.info["migration_transaction"] = True
                    with connection.begin():
                        migrate(connection)
            except SQLAlchemyError as error:
                engine.dispose()
                raise DatabaseInitializationError("SQLite initialization or migration failed") from error
            except BaseException:
                engine.dispose()
                raise
            self._engine = engine

    @contextmanager
    def session(self, *, write: bool = True) -> Iterator[Session]:
        """Own a short transaction; readonly units may opt out of the writer slot.

        The default reserves the writer at BEGIN, before any repository reads.
        SQLite's busy timeout can then serialize competing writers without an
        impossible lock upgrade. Expected revisions still reject stale edits.
        Execution options belong to this SQLAlchemy Connection and do not leak
        through the pool into later sessions.
        """
        if type(write) is not bool:
            raise PersistenceError("write must be true or false")
        # A memory database has one connection: serialize its transactions.
        memory = self._url.database in (None, "", ":memory:")
        with self._lock if memory else nullcontext():
            with Session(self.engine, expire_on_commit=False) as session:
                try:
                    with session.begin():
                        session.connection(execution_options={"sqlite_write_transaction": write})
                        yield session
                except IntegrityError as error:
                    raise ConflictError("Database constraint rejected the write") from error
                except OperationalError as error:
                    if "locked" in str(error).lower() or "busy" in str(error).lower():
                        raise ConflictError("Database is busy; retry the transaction with fresh revisions") from error
                    raise PersistenceError("Database operation failed") from error

    def dispose(self) -> None:
        with self._lock:
            if self._engine is not None:
                self._engine.dispose()
                self._engine = None
