"""Static local registry identity, history, and trusted API boundary."""

from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from backend.config import Settings
from backend.database import Database
from backend.main import create_app
from backend.persistence_errors import ConflictError, DatabaseInitializationError, NotFoundError
from backend.services.agent_registry_service import AgentRegistryService
from shared.models import AgentRegistration, AgentSnapshot
from simulator.agents.fingerprints import AgentFingerprintError, build_agent_snapshot, verify_agent_snapshot


@pytest.fixture
def package(tmp_path):
    directory = tmp_path / "agent"
    directory.mkdir()
    (directory / "agent.py").write_text("raise RuntimeError('static inspection must not import me')\n", encoding="utf-8")
    (directory / "helper.py").write_text("DEFAULT_SPEED = 3.0\n", encoding="utf-8")
    (directory / "model.bin").write_bytes(b"local declared model bytes")
    (directory / "requirements.txt").write_text("not-a-real-dependency==0.0.0\n", encoding="utf-8")
    (directory / "agent.json").write_text(json.dumps({
        "protocol_version": "1.2", "agent_id": "registry-example", "name": "Registry example",
        "version": "1.0", "entry_point": "agent:Example", "assets": ["model.bin"],
        "requirements_file": "requirements.txt", "configuration": {"speed": 3.0, "enabled": False},
    }), encoding="utf-8")
    return directory


def request(package, **changes):
    return AgentRegistration(package_path=str(package), python_executable=sys.executable,
                             trusted_local_code=True, **changes)


@pytest.fixture
def registry():
    database = Database("sqlite:///:memory:")
    database.initialize()
    yield AgentRegistryService(database)
    database.dispose()


def test_inspection_is_inert_and_records_effective_configuration_and_hashes(package, registry):
    registered = registry.register(request(package, configuration={"speed": 9.0}))
    snapshot = registered.snapshot
    assert registered.active and registered.id == snapshot.registration_id
    assert snapshot.manifest.name == "Registry example"
    assert snapshot.manifest.configuration == snapshot.configuration == {"speed": 9.0, "enabled": False}
    assert snapshot.observation_mode == "simulator_state"
    assert snapshot.agent_control_mode == "authoritative"
    assert {file.path for file in snapshot.files} == {"agent.json", "agent.py", "helper.py", "requirements.txt", "model.bin"}
    assert all(len(file.sha256) == 64 and file.size_bytes > 0 for file in snapshot.files)
    assert len(snapshot.python_sha256) == len(snapshot.package_sha256) == 64
    assert Path(snapshot.python_executable) == Path(sys.executable).absolute()
    assert any(file.path.endswith("fingerprints.py") for file in snapshot.runtime_files)
    ready = registry.readiness(registered.id)
    assert ready.ready and ready.inspection == "static_only" and ready.inference_checked is False
    assert ready.issues == [] and len(ready.limitations) == 3
    assert registry.resolve(registered.id) == snapshot


def test_registered_versions_are_immutable_detached_and_deactivation_preserves_history(package, registry):
    first = registry.register(request(package))
    second = registry.register(request(package, configuration={"speed": 8.0}))
    first.snapshot.configuration["speed"] = 100.0
    assert registry.get(first.id).snapshot.configuration["speed"] == 3.0
    assert registry.get(second.id).snapshot.configuration["speed"] == 8.0
    assert [item.id for item in registry.list(limit=1)] == [second.id]
    assert [item.id for item in registry.list(limit=1, offset=1)] == [first.id]
    snapshot = registry.get(first.id).snapshot
    assert not registry.deactivate(first.id).active
    assert not registry.deactivate(first.id).active
    assert [item.id for item in registry.list(active=True)] == [second.id]
    assert [item.id for item in registry.list(active=False)] == [first.id]
    with pytest.raises(ConflictError, match="inactive"):
        registry.resolve(first.id)
    registry.verify(snapshot)  # An already queued immutable snapshot remains meaningful.
    assert registry.get(first.id).snapshot == snapshot
    assert registry.readiness(first.id).issues == ["Agent registration is inactive"]
    with pytest.raises(NotFoundError):
        registry.get(uuid4())


@pytest.mark.parametrize("filename", ["agent.py", "helper.py", "model.bin", "requirements.txt", "agent.json"])
def test_changed_package_inputs_reject_new_launch_without_rewriting_version(package, registry, filename):
    registered = registry.register(request(package))
    snapshot = registered.snapshot.model_dump(mode="json")
    path = package / filename
    if filename == "agent.json":
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["version"] = "2.0"
        path.write_text(json.dumps(manifest), encoding="utf-8")
    else:
        path.write_bytes(path.read_bytes() + b"changed")
    assert registry.readiness(registered.id).ready is False
    with pytest.raises(ConflictError, match="changed"):
        registry.resolve(registered.id)
    assert registry.get(registered.id).snapshot.model_dump(mode="json") == snapshot


def test_added_and_removed_package_files_are_identity_changes_but_caches_are_excluded(package):
    snapshot = build_agent_snapshot(request(package), uuid4())
    cache = package / "__pycache__"
    cache.mkdir()
    (cache / "agent.cpython-314.pyc").write_bytes(b"generated bytecode")
    verify_agent_snapshot(snapshot)
    (package / "another.py").write_text("x=1\n", encoding="utf-8")
    with pytest.raises(AgentFingerprintError, match="changed"):
        verify_agent_snapshot(snapshot)
    (package / "another.py").unlink()
    (package / "helper.py").unlink()
    with pytest.raises(AgentFingerprintError, match="changed"):
        verify_agent_snapshot(snapshot)


def test_external_model_and_interpreter_bytes_are_pinned_without_running_them(package, tmp_path, registry):
    model = tmp_path / "weights.bin"
    model.write_bytes(b"weights")
    interpreter = tmp_path / "python.exe"
    interpreter.write_bytes(b"not executed during inspection")
    registered = registry.register(AgentRegistration(
        package_path=str(package), python_executable=str(interpreter), model_files=[str(model)], trusted_local_code=True,
    ))
    assert registry.readiness(registered.id).ready  # Explicitly static, not a Python/inference probe.
    assert registered.snapshot.model_files[0].path == str(model.resolve())
    model.write_bytes(b"changed weights")
    with pytest.raises(ConflictError, match="model_files"):
        registry.resolve(registered.id)
    model.write_bytes(b"weights")
    interpreter.write_bytes(b"changed interpreter")
    with pytest.raises(ConflictError, match="python_sha256"):
        registry.resolve(registered.id)


def test_runtime_file_identity_is_revalidated(package, tmp_path, monkeypatch):
    import simulator.agents.fingerprints as fingerprints

    runtime = tmp_path / "runtime.py"
    runtime.write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.setattr(fingerprints, "_runtime_files", lambda: [fingerprints._file(runtime)])
    snapshot = build_agent_snapshot(request(package), uuid4())
    runtime.write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(AgentFingerprintError, match="runtime_files"):
        verify_agent_snapshot(snapshot)


@pytest.mark.parametrize("explicit", [False, True])
def test_reference_ai_automatically_pins_default_or_configured_checkpoint(package, tmp_path, monkeypatch, explicit):
    from simulator.agents.reference_ai import checkpoint

    manifest_path = package / "agent.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["agent_id"] = "poltergeist-reference-ai"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    default = tmp_path / "default.ckpt"
    configured = tmp_path / "configured.ckpt"
    default.write_bytes(b"default local checkpoint")
    configured.write_bytes(b"configured local checkpoint")
    monkeypatch.setattr(checkpoint, "DEFAULT_CHECKPOINT", default)
    configuration = {"checkpoint_path": str(configured)} if explicit else {}
    snapshot = build_agent_snapshot(request(package, configuration=configuration), uuid4())
    expected = str((configured if explicit else default).resolve())
    assert snapshot.configuration["checkpoint_path"] == expected
    assert [item.path for item in snapshot.model_files] == [expected]
    verify_agent_snapshot(snapshot)


@pytest.mark.parametrize("location", ["file", "parent", "parent-traversal"])
def test_external_models_reject_mutable_lexical_aliases_before_canonicalization(package, tmp_path, location):
    model_directory = tmp_path / "model-v1"
    model_directory.mkdir()
    (model_directory / "weights.bin").write_bytes(b"version one model")
    link = tmp_path / "latest"
    try:
        link.symlink_to(model_directory / "weights.bin" if location == "file" else model_directory,
                        target_is_directory=location != "file")
    except OSError:
        pytest.skip("Host does not permit creating symlinks")
    if location == "file":
        declared = str(link)
    elif location == "parent":
        declared = str(link / "weights.bin")
    else:
        # A normalizing abspath check would discard `latest/..` first and miss
        # the link, although the model's own Path.resolve/open still sees it.
        declared = str(link / ".." / "model-v1" / "weights.bin")
    with pytest.raises(AgentFingerprintError, match="symlinks or junctions"):
        build_agent_snapshot(request(package, model_files=[declared], configuration={"weights_path": declared}), uuid4())


def test_external_model_parent_junction_is_rejected_before_resolving(package, tmp_path, monkeypatch):
    import simulator.agents.fingerprints as fingerprints

    directory = tmp_path / "linked-parent"
    directory.mkdir()
    model = directory / "weights.bin"
    model.write_bytes(b"local weights")
    real_link = fingerprints._link
    monkeypatch.setattr(fingerprints, "_link", lambda path: path == directory or real_link(path))
    with pytest.raises(AgentFingerprintError, match="symlinks or junctions"):
        build_agent_snapshot(request(package, model_files=[str(model)]), uuid4())


def test_sensor_registration_rejects_scripted_control_even_before_a_launch(package):
    path = package / "agent.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest.update(protocol_version="1.3", observation_mode="sensors", sensors=[{
        "id": "lidar", "type": "sensor.lidar.ray_cast",
    }])
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(AgentFingerprintError, match="authoritative"):
        build_agent_snapshot(request(package, agent_control_mode="scripted"), uuid4())


@pytest.mark.parametrize("value", [None, False, 0, 1, "true"])
def test_explicit_boolean_trust_acknowledgment_is_required(package, value):
    with pytest.raises(ValidationError, match="trusted_local_code"):
        AgentRegistration(package_path=str(package), trusted_local_code=value)


@pytest.mark.parametrize("field,value", [
    ("load_timeout_s", 0), ("step_timeout_s", float("inf")), ("terminate_timeout_s", -1),
    ("max_message_bytes", True), ("max_message_bytes", 64 * 1024 * 1024 + 1),
])
def test_process_options_are_bounded_and_json_safe(package, field, value):
    with pytest.raises(ValidationError):
        request(package, process_options={field: value})


@pytest.mark.parametrize("override", [0, 0.0, 1, 1.0])
def test_snapshot_configuration_distinguishes_boolean_integer_and_float(package, override):
    snapshot = build_agent_snapshot(request(package), uuid4()).model_dump(mode="json")
    snapshot["configuration"]["enabled"] = override
    with pytest.raises(ValidationError, match="configuration"):
        AgentSnapshot.model_validate(snapshot)


def test_snapshot_rejects_unsorted_or_duplicated_file_lists(package):
    payload = build_agent_snapshot(request(package), uuid4()).model_dump(mode="json")
    payload["files"].append(deepcopy(payload["files"][0]))
    with pytest.raises(ValidationError, match="sorted and unique"):
        AgentSnapshot.model_validate(payload)


@pytest.mark.parametrize("location", ["file", "directory"])
def test_package_symlinks_cannot_escape_or_hide_unfingerprinted_code(package, tmp_path, location):
    target = tmp_path / "outside"
    target.mkdir()
    (target / "payload.py").write_text("raise Exception()\n", encoding="utf-8")
    link = package / ("linked.py" if location == "file" else "linked")
    try:
        link.symlink_to(target / "payload.py" if location == "file" else target, target_is_directory=location == "directory")
    except OSError:
        pytest.skip("Host does not permit creating symlinks")
    with pytest.raises(AgentFingerprintError, match="links|junctions"):
        build_agent_snapshot(request(package), uuid4())


def test_venv_interpreter_symlink_path_is_preserved(package, tmp_path):
    executable = tmp_path / "venv-python"
    try:
        executable.symlink_to(sys.executable)
    except OSError:
        pytest.skip("Host does not permit creating symlinks")
    snapshot = build_agent_snapshot(AgentRegistration(
        package_path=str(package), python_executable=str(executable), trusted_local_code=True,
    ), uuid4())
    assert snapshot.python_executable == str(executable.absolute())
    assert snapshot.python_executable != str(executable.resolve())


def test_registry_history_survives_database_restart(package, tmp_path):
    url = "sqlite:///" + (tmp_path / "state.db").as_posix()
    database = Database(url)
    database.initialize()
    saved = AgentRegistryService(database).register(request(package))
    AgentRegistryService(database).deactivate(saved.id)
    database.dispose()
    database = Database(url)
    database.initialize()
    try:
        loaded = AgentRegistryService(database).get(saved.id)
        assert not loaded.active and loaded.snapshot == saved.snapshot and loaded.created_at == saved.created_at
    finally:
        database.dispose()


def test_registry_http_round_trip_is_static_and_does_not_start_worker(package, tmp_path):
    storage = tmp_path / "api-storage"
    settings = Settings(_env_file=None, storage_dir=storage, database_url="sqlite:///" + (storage / "runs.db").as_posix())
    application = create_app(settings)
    with TestClient(application) as client:
        assert client.get("/agents").json() == []
        missing_trust = request(package).model_dump(mode="json")
        del missing_trust["trusted_local_code"]
        assert client.post("/agents", json=missing_trust).status_code == 422
        response = client.post("/agents", json=request(package).model_dump(mode="json"))
        assert response.status_code == 201, response.text
        saved = response.json()
        agent_id = saved["id"]
        assert client.get(f"/agents/{agent_id}").json() == saved
        assert client.get("/agents?active=true&limit=1").json() == [saved]
        ready = client.get(f"/agents/{agent_id}/readiness").json()
        assert ready["ready"] and ready["inspection"] == "static_only" and not ready["inference_checked"]
        assert client.post(f"/agents/{agent_id}/deactivate").json()["active"] is False
        assert client.get("/agents?active=true").json() == []
        assert client.get(f"/agents/{agent_id}/readiness").json()["ready"] is False
        assert client.get(f"/agents/{uuid4()}").status_code == 404
        assert client.get("/agents?limit=1001").status_code == 422
        assert client.delete(f"/agents/{agent_id}").status_code == 405
        assert application.state.runtime._worker is None


def test_registration_errors_are_actionable_422_without_persisting_a_version(package, tmp_path):
    settings = Settings(_env_file=None, storage_dir=tmp_path / "artifacts", database_url="sqlite:///:memory:")
    with TestClient(create_app(settings)) as client:
        bad = request(package).model_dump(mode="json")
        bad["python_executable"] = str(tmp_path / "missing-python")
        response = client.post("/agents", json=bad)
        assert response.status_code == 422 and "Python executable" in response.json()["detail"]
        assert client.get("/agents").json() == []


def test_revision_three_upgrade_preserves_old_jobs_and_adds_registry_atomically(tmp_path):
    import backend.migrations as migrations

    path = tmp_path / "old.db"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE schema_migrations (revision INTEGER PRIMARY KEY NOT NULL,name TEXT NOT NULL,checksum TEXT NOT NULL,applied_at TEXT NOT NULL)")
        for revision, name, statements, checksum in (
            (1, "initial_persistence", migrations._INITIAL_DDL, migrations._INITIAL_CHECKSUM),
            (2, "simulation_jobs", migrations._JOBS_DDL, migrations._JOBS_CHECKSUM),
            (3, "optimization_journal", migrations._OPTIMIZATION_DDL, migrations._OPTIMIZATION_CHECKSUM),
        ):
            for statement in statements:
                connection.execute(statement)
            connection.execute("INSERT INTO schema_migrations VALUES (?,?,?,'2026-01-01')", (revision, name, checksum))
        simulation_id = str(uuid4())
        connection.execute("INSERT INTO simulations VALUES (?,NULL,'{}',NULL,'queued','test',1,'2026-01-01','2026-01-01')", (simulation_id,))
        connection.execute("INSERT INTO simulation_jobs VALUES (1,?,'{}','{}',0,0,'2026-01-01')", (simulation_id,))
    database = Database("sqlite:///" + path.as_posix())
    database.initialize()
    try:
        with database.engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT simulation_id,agent_snapshot FROM simulation_jobs").all() == [(simulation_id, None)]
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM agent_registrations").scalar() == 0
            assert connection.exec_driver_sql("SELECT MAX(revision) FROM schema_migrations").scalar() == 6
    finally:
        database.dispose()


def test_agent_registry_migration_rolls_back_table_column_and_history_on_failure(tmp_path, monkeypatch):
    import backend.migrations as migrations

    path = tmp_path / "old.db"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE schema_migrations (revision INTEGER PRIMARY KEY NOT NULL,name TEXT NOT NULL,checksum TEXT NOT NULL,applied_at TEXT NOT NULL)")
        for revision, name, statements, checksum in (
            (1, "initial_persistence", migrations._INITIAL_DDL, migrations._INITIAL_CHECKSUM),
            (2, "simulation_jobs", migrations._JOBS_DDL, migrations._JOBS_CHECKSUM),
            (3, "optimization_journal", migrations._OPTIMIZATION_DDL, migrations._OPTIMIZATION_CHECKSUM),
        ):
            for statement in statements:
                connection.execute(statement)
            connection.execute("INSERT INTO schema_migrations VALUES (?,?,?,'2026-01-01')", (revision, name, checksum))
    monkeypatch.setattr(migrations, "_AGENTS_DDL", migrations._AGENTS_DDL + ("INVALID MIGRATION SQL",))
    with pytest.raises(DatabaseInitializationError):
        Database("sqlite:///" + path.as_posix()).initialize()
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT MAX(revision) FROM schema_migrations").fetchone() == (3,)
        assert "agent_snapshot" not in {row[1] for row in connection.execute("PRAGMA table_info(simulation_jobs)")}
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='agent_registrations'").fetchall() == []
