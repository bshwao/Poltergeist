"""Ordered SQLite migrations. Published revisions are immutable.

DDL is deliberately explicit rather than ``metadata.create_all``: a missing
history or a partially populated schema is never silently treated as current.
The caller holds one SQLite transaction, including the history insertion.
"""

from datetime import UTC, datetime
from hashlib import sha256
import re

from sqlalchemy import Connection, inspect

from .persistence_errors import DatabaseInitializationError


CURRENT_REVISION = 5

_INITIAL_DDL = (
    """CREATE TABLE scenarios (
        id VARCHAR(36) PRIMARY KEY NOT NULL,
        payload TEXT NOT NULL,
        revision INTEGER NOT NULL CONSTRAINT ck_scenarios_revision CHECK (revision >= 1),
        created_at VARCHAR(32) NOT NULL,
        updated_at VARCHAR(32) NOT NULL
    )""",
    "CREATE INDEX ix_scenarios_created_id ON scenarios (created_at, id)",
    """CREATE TABLE simulations (
        id VARCHAR(36) PRIMARY KEY NOT NULL,
        scenario_id VARCHAR(36),
        payload TEXT NOT NULL, result TEXT,
        status VARCHAR(16) NOT NULL CONSTRAINT ck_simulations_status
            CHECK (status IN ('queued','running','completed','failed','cancelled')),
        mode VARCHAR(8) NOT NULL CONSTRAINT ck_simulations_mode CHECK (mode IN ('carla','test')),
        revision INTEGER NOT NULL CONSTRAINT ck_simulations_revision CHECK (revision >= 1),
        created_at VARCHAR(32) NOT NULL, updated_at VARCHAR(32) NOT NULL,
        CONSTRAINT ck_simulations_terminal_result CHECK (status NOT IN ('queued','running') OR result IS NULL),
        FOREIGN KEY (scenario_id) REFERENCES scenarios(id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX ix_simulations_created_id ON simulations (created_at, id)",
    "CREATE INDEX ix_simulations_status_mode_created ON simulations (status, mode, created_at, id)",
    "CREATE INDEX ix_simulations_scenario_id ON simulations (scenario_id)",
    """CREATE TABLE failures (
        id VARCHAR(36) PRIMARY KEY NOT NULL,
        simulation_id VARCHAR(36) NOT NULL,
        payload TEXT NOT NULL,
        reproduced BOOLEAN CONSTRAINT ck_failures_reproduced CHECK (reproduced IS NULL OR reproduced IN (0,1)),
        created_at VARCHAR(32) NOT NULL,
        FOREIGN KEY (simulation_id) REFERENCES simulations(id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX ix_failures_created_id ON failures (created_at, id)",
    "CREATE INDEX ix_failures_simulation_id ON failures (simulation_id, created_at, id)",
)

_COLUMNS = {
    "scenarios": {"id", "payload", "revision", "created_at", "updated_at"},
    "simulations": {"id", "scenario_id", "payload", "result", "status", "mode", "revision", "created_at", "updated_at"},
    "failures": {"id", "simulation_id", "payload", "reproduced", "created_at"},
    "schema_migrations": {"revision", "name", "checksum", "applied_at"},
}

_INITIAL_CHECKSUM = sha256("\n".join(_INITIAL_DDL).encode("utf-8")).hexdigest()

_JOBS_DDL = (
    """CREATE TABLE simulation_jobs (
        id INTEGER PRIMARY KEY NOT NULL,
        simulation_id VARCHAR(36) NOT NULL,
        options TEXT NOT NULL, evaluation_settings TEXT NOT NULL,
        cancel_requested BOOLEAN NOT NULL CONSTRAINT ck_simulation_jobs_cancel_requested CHECK (cancel_requested IN (0,1)),
        claimed BOOLEAN NOT NULL CONSTRAINT ck_simulation_jobs_claimed CHECK (claimed IN (0,1)),
        created_at VARCHAR(32) NOT NULL,
        FOREIGN KEY (simulation_id) REFERENCES simulations(id) ON DELETE RESTRICT
    )""",
    "CREATE UNIQUE INDEX ix_simulation_jobs_simulation_id ON simulation_jobs (simulation_id)",
)
_JOBS_CHECKSUM = sha256("\n".join(_JOBS_DDL).encode("utf-8")).hexdigest()
_COLUMNS["simulation_jobs"] = {"id", "simulation_id", "options", "evaluation_settings", "cancel_requested", "claimed", "created_at"}

_OPTIMIZATION_DDL = (
    """CREATE TABLE optimization_operations (
        id VARCHAR(36) PRIMARY KEY NOT NULL,
        payload TEXT NOT NULL,
        status VARCHAR(16) NOT NULL CONSTRAINT ck_optimization_operations_status
            CHECK (status IN ('queued','running','completed','failed','cancelled','interrupted')),
        revision INTEGER NOT NULL CONSTRAINT ck_optimization_operations_revision CHECK (revision >= 1),
        cancel_requested BOOLEAN NOT NULL CONSTRAINT ck_optimization_operations_cancel_requested CHECK (cancel_requested IN (0,1)),
        created_at VARCHAR(32) NOT NULL, updated_at VARCHAR(32) NOT NULL
    )""",
    "CREATE INDEX ix_optimization_operations_created_id ON optimization_operations (created_at, id)",
    "CREATE INDEX ix_optimization_operations_status_created ON optimization_operations (status, created_at, id)",
    """CREATE TABLE optimization_trials (
        operation_id VARCHAR(36) NOT NULL,
        number INTEGER NOT NULL CONSTRAINT ck_optimization_trials_number CHECK (number >= 0),
        simulation_id VARCHAR(36) NOT NULL,
        failure_id VARCHAR(36),
        payload TEXT NOT NULL,
        assessed BOOLEAN NOT NULL CONSTRAINT ck_optimization_trials_assessed CHECK (assessed IN (0,1)),
        PRIMARY KEY (operation_id, number),
        FOREIGN KEY (operation_id) REFERENCES optimization_operations(id) ON DELETE RESTRICT,
        FOREIGN KEY (simulation_id) REFERENCES simulations(id) ON DELETE RESTRICT,
        FOREIGN KEY (failure_id) REFERENCES failures(id) ON DELETE RESTRICT
    )""",
    "CREATE UNIQUE INDEX ix_optimization_trials_simulation_id ON optimization_trials (simulation_id)",
    "CREATE INDEX ix_optimization_trials_failure_id ON optimization_trials (failure_id)",
)
_OPTIMIZATION_CHECKSUM = sha256("\n".join(_OPTIMIZATION_DDL).encode("utf-8")).hexdigest()
_COLUMNS["optimization_operations"] = {"id", "payload", "status", "revision", "cancel_requested", "created_at", "updated_at"}
_COLUMNS["optimization_trials"] = {"operation_id", "number", "simulation_id", "failure_id", "payload", "assessed"}

_AGENTS_DDL = (
    """CREATE TABLE agent_registrations (
        id VARCHAR(36) PRIMARY KEY NOT NULL,
        payload TEXT NOT NULL,
        active BOOLEAN NOT NULL CONSTRAINT ck_agent_registrations_active CHECK (active IN (0,1)),
        created_at VARCHAR(32) NOT NULL
    )""",
    "CREATE INDEX ix_agent_registrations_created_id ON agent_registrations (created_at, id)",
    "CREATE INDEX ix_agent_registrations_active_created_id ON agent_registrations (active, created_at, id)",
    "ALTER TABLE simulation_jobs ADD COLUMN agent_snapshot TEXT",
)
_AGENTS_CHECKSUM = sha256("\n".join(_AGENTS_DDL).encode("utf-8")).hexdigest()
_COLUMNS["agent_registrations"] = {"id", "payload", "active", "created_at"}
_COLUMNS["simulation_jobs"].add("agent_snapshot")

_REMOTE_RECEIPTS_DDL = (
    """CREATE TABLE remote_command_receipts (
        id INTEGER PRIMARY KEY NOT NULL,
        origin VARCHAR(256) NOT NULL, device_id VARCHAR(36) NOT NULL,
        owner_id VARCHAR(36) NOT NULL,
        generation INTEGER NOT NULL CONSTRAINT ck_remote_command_receipts_generation CHECK (generation >= 1),
        request_id VARCHAR(36) NOT NULL,
        operation VARCHAR(8) NOT NULL CONSTRAINT ck_remote_command_receipts_operation CHECK (operation IN ('execute','cancel')),
        payload_sha256 VARCHAR(64) NOT NULL,
        scenario_sha256 VARCHAR(64) CONSTRAINT ck_remote_command_receipts_scenario
            CHECK ((operation = 'execute' AND scenario_sha256 IS NOT NULL) OR (operation = 'cancel' AND scenario_sha256 IS NULL)),
        simulation_id VARCHAR(36) NOT NULL,
        accepted_at VARCHAR(32) NOT NULL,
        revision INTEGER NOT NULL CONSTRAINT ck_remote_command_receipts_revision CHECK (revision >= 1),
        FOREIGN KEY (simulation_id) REFERENCES simulations(id) ON DELETE RESTRICT
    )""",
    "CREATE UNIQUE INDEX ix_remote_command_receipts_request ON remote_command_receipts (origin, device_id, generation, request_id)",
    "CREATE INDEX ix_remote_command_receipts_binding_run ON remote_command_receipts (origin, device_id, generation, simulation_id, operation)",
    """CREATE TABLE remote_run_projections (
        origin VARCHAR(256) NOT NULL, device_id VARCHAR(36) NOT NULL,
        generation INTEGER NOT NULL CONSTRAINT ck_remote_run_projections_generation CHECK (generation >= 1),
        simulation_id VARCHAR(36) NOT NULL, owner_id VARCHAR(36) NOT NULL,
        revision INTEGER NOT NULL CONSTRAINT ck_remote_run_projections_revision CHECK (revision >= 1),
        payload_sha256 VARCHAR(64) NOT NULL,
        PRIMARY KEY (origin, device_id, generation, simulation_id),
        FOREIGN KEY (simulation_id) REFERENCES simulations(id) ON DELETE RESTRICT
    )""",
)
_REMOTE_RECEIPTS_CHECKSUM = sha256("\n".join(_REMOTE_RECEIPTS_DDL).encode("utf-8")).hexdigest()
_COLUMNS["remote_command_receipts"] = {
    "id", "origin", "device_id", "owner_id", "generation", "request_id", "operation",
    "payload_sha256", "scenario_sha256", "simulation_id", "accepted_at", "revision",
}
_COLUMNS["remote_run_projections"] = {
    "origin", "device_id", "generation", "simulation_id", "owner_id", "revision", "payload_sha256",
}


def _table_revision(name: str) -> int:
    if name in {"remote_command_receipts", "remote_run_projections"}:
        return 5
    return 4 if name == "agent_registrations" else 3 if name.startswith("optimization_") else 2 if name == "simulation_jobs" else 1


def _sql(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def _verify_schema(connection: Connection, *, revision: int = CURRENT_REVISION) -> None:
    from .models import Base

    inspector = inspect(connection)
    expected_columns = {
        name: columns - ({"agent_snapshot"} if name == "simulation_jobs" and revision < 4 else set())
        for name, columns in _COLUMNS.items() if _table_revision(name) <= revision
    }
    if set(inspector.get_table_names()) != set(expected_columns):
        raise DatabaseInitializationError("Database tables do not match the recorded migration")
    for name, expected in expected_columns.items():
        columns = inspector.get_columns(name)
        if {column["name"] for column in columns} != expected:
            raise DatabaseInitializationError(f"Database columns do not match migration for {name}")
        if name == "schema_migrations":
            if inspector.get_pk_constraint(name)["constrained_columns"] != ["revision"]:
                raise DatabaseInitializationError("Migration history primary key is incompatible")
            continue
        table = Base.metadata.tables[name]
        for column in columns:
            wanted = table.columns[column["name"]]
            if bool(column["nullable"]) != wanted.nullable or (
                str(column["type"]).lower() != str(wanted.type.compile(dialect=connection.dialect)).lower()
            ):
                raise DatabaseInitializationError(f"Database column definition is incompatible: {name}.{wanted.name}")
        if inspector.get_pk_constraint(name)["constrained_columns"] != [column.name for column in table.primary_key.columns]:
            raise DatabaseInitializationError(f"Database primary key is incompatible: {name}")
        indexes = {(index["name"], tuple(index["column_names"]), bool(index["unique"]),
                    None if index.get("dialect_options", {}).get("sqlite_where") is None
                    else _sql(str(index["dialect_options"]["sqlite_where"]))) for index in inspector.get_indexes(name)}
        wanted_indexes = {(index.name, tuple(column.name for column in index.columns), bool(index.unique),
                           None if index.dialect_options["sqlite"].get("where") is None
                           else _sql(str(index.dialect_options["sqlite"]["where"]))) for index in table.indexes}
        if indexes != wanted_indexes:
            raise DatabaseInitializationError(f"Database indexes are incompatible: {name}")
        checks = {(check["name"], _sql(check["sqltext"])) for check in inspector.get_check_constraints(name)}
        wanted_checks = {(constraint.name, _sql(str(constraint.sqltext))) for constraint in table.constraints if hasattr(constraint, "sqltext")}
        if checks != wanted_checks:
            raise DatabaseInitializationError(f"Database check constraints are incompatible: {name}")
        foreign_keys = {
            (tuple(key["constrained_columns"]), key["referred_table"], tuple(key["referred_columns"]), key["options"].get("ondelete"))
            for key in inspector.get_foreign_keys(name)
        }
        wanted_keys = {
            (tuple(element.parent.name for element in key.elements), key.referred_table.name,
             tuple(element.column.name for element in key.elements), key.ondelete)
            for key in table.foreign_key_constraints
        }
        if foreign_keys != wanted_keys:
            raise DatabaseInitializationError(f"Database foreign keys are incompatible: {name}")


def migrate(connection: Connection) -> None:
    tables = set(inspect(connection).get_table_names())
    if "schema_migrations" not in tables:
        if tables:
            raise DatabaseInitializationError("Refusing an unversioned populated database; migrate or back it up explicitly")
        connection.exec_driver_sql("""CREATE TABLE schema_migrations (
            revision INTEGER PRIMARY KEY NOT NULL, name TEXT NOT NULL, checksum TEXT NOT NULL, applied_at TEXT NOT NULL
        )""")
        history = []
    else:
        columns = {column["name"] for column in inspect(connection).get_columns("schema_migrations")}
        if columns != _COLUMNS["schema_migrations"]:
            raise DatabaseInitializationError("Malformed migration history")
        history = list(connection.exec_driver_sql("SELECT revision, name, checksum FROM schema_migrations ORDER BY revision"))
        expected = [(1, "initial_persistence", _INITIAL_CHECKSUM), (2, "simulation_jobs", _JOBS_CHECKSUM),
                    (3, "optimization_journal", _OPTIMIZATION_CHECKSUM),
                    (4, "agent_registry", _AGENTS_CHECKSUM),
                    (5, "remote_command_receipts", _REMOTE_RECEIPTS_CHECKSUM)][:len(history)]
        if [tuple(row) for row in history] != expected:
            raise DatabaseInitializationError("Unknown, future, or noncontiguous migration history")
        if not history and tables != {"schema_migrations"}:
            raise DatabaseInitializationError("Populated database has no applied migration history")
    if not history:
        for statement in _INITIAL_DDL:
            connection.exec_driver_sql(statement)
        connection.exec_driver_sql(
            "INSERT INTO schema_migrations (revision,name,checksum,applied_at) VALUES (?,?,?,?)",
            (1, "initial_persistence", _INITIAL_CHECKSUM, datetime.now(UTC).isoformat()),
        )
        history = [(1, "initial_persistence", _INITIAL_CHECKSUM)]
    if len(history) == 1:
        # Validate revision 1 before extending it: a migration must not conceal
        # an incompatible or partially modified historical schema.
        _verify_schema(connection, revision=1)
        for statement in _JOBS_DDL:
            connection.exec_driver_sql(statement)
        connection.exec_driver_sql(
            "INSERT INTO schema_migrations (revision,name,checksum,applied_at) VALUES (?,?,?,?)",
            (2, "simulation_jobs", _JOBS_CHECKSUM, datetime.now(UTC).isoformat()),
        )
        history = [*history, (2, "simulation_jobs", _JOBS_CHECKSUM)]
    if len(history) == 2:
        _verify_schema(connection, revision=2)
        for statement in _OPTIMIZATION_DDL:
            connection.exec_driver_sql(statement)
        connection.exec_driver_sql(
            "INSERT INTO schema_migrations (revision,name,checksum,applied_at) VALUES (?,?,?,?)",
            (3, "optimization_journal", _OPTIMIZATION_CHECKSUM, datetime.now(UTC).isoformat()),
        )
        history = [*history, (3, "optimization_journal", _OPTIMIZATION_CHECKSUM)]
    if len(history) == 3:
        _verify_schema(connection, revision=3)
        for statement in _AGENTS_DDL:
            connection.exec_driver_sql(statement)
        connection.exec_driver_sql(
            "INSERT INTO schema_migrations (revision,name,checksum,applied_at) VALUES (?,?,?,?)",
            (4, "agent_registry", _AGENTS_CHECKSUM, datetime.now(UTC).isoformat()),
        )
        history = [*history, (4, "agent_registry", _AGENTS_CHECKSUM)]
    if len(history) == 4:
        _verify_schema(connection, revision=4)
        for statement in _REMOTE_RECEIPTS_DDL:
            connection.exec_driver_sql(statement)
        connection.exec_driver_sql(
            "INSERT INTO schema_migrations (revision,name,checksum,applied_at) VALUES (?,?,?,?)",
            (5, "remote_command_receipts", _REMOTE_RECEIPTS_CHECKSUM, datetime.now(UTC).isoformat()),
        )
    _verify_schema(connection)
