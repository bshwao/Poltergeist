"""Contract round trips, immutable run snapshots, evidence, and stale writers."""

from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from backend.database import Database
from backend.persistence_errors import ConflictError, NotFoundError, PersistenceError
from backend.repositories import FailureRepository, ScenarioRepository, SimulationRepository
from scenarios import validate_scenario
from shared.models import (
    ArtifactReference, EvaluationCoverage, EvaluationSettings, FailureReport,
    SafetyMetrics, Simulation, SimulationResult, SimulationRuntime, SimulationStatus, Transform,
)


def scene(**overrides):
    return validate_scenario({"name": "repository-test", "duration_s": 0.1,
                              "actors": [{"type": "ego_vehicle"}]} | overrides)


@pytest.fixture
def database():
    database = Database("sqlite:///:memory:")
    database.initialize()
    yield database
    database.dispose()


def terminal(queued, status="completed", **metric_values):
    started = queued.created_at + timedelta(seconds=1)
    simulation = queued.model_copy(update={"status": SimulationStatus(status), "started_at": started,
        "finished_at": started + timedelta(seconds=1), "progress": 1.0 if status == "completed" else 0.0,
        "error": "setup failed" if status == "failed" else None})
    return SimulationResult(simulation=simulation, metrics=SafetyMetrics(**metric_values),
                            telemetry_frames=0, measured_duration_s=0.0)


def save_result(database, **metric_values):
    queued = Simulation(scenario=scene(), mode="test")
    result = terminal(queued, **metric_values)
    with database.session() as session:
        repository = SimulationRepository(session)
        repository.create(queued)
        repository.complete(result, expected_revision=1)
    return result


def test_scenario_roundtrip_normalizes_defaults_keeps_timezone_and_detaches_snapshots(database):
    original = scene()
    with database.session() as session:
        repository = ScenarioRepository(session)
        saved = repository.create(original)
        assert saved.revision == 1
        assert saved.created_at.tzinfo is not None
        assert saved.created_at.utcoffset() == timedelta(0)
        assert saved.scenario.actors[0].id == "ego"
        saved.scenario.name = "external mutation"
        original.actors.clear()
        assert repository.get(saved.id).scenario.name == "repository-test"
        assert len(repository.get(saved.id).scenario.actors) == 1


def test_scenario_update_uses_revisions_and_delete_does_not_hide_missing(database):
    with database.session() as session:
        repository = ScenarioRepository(session)
        saved = repository.create(scene())
        changed = repository.update(saved.id, scene(name="changed"), expected_revision=1)
        assert changed.revision == 2 and changed.created_at == saved.created_at
        with pytest.raises(ConflictError):
            repository.update(saved.id, scene(), expected_revision=1)
        with pytest.raises(ConflictError):
            repository.delete(saved.id, expected_revision=1)
        repository.delete(saved.id, expected_revision=2)
        with pytest.raises(NotFoundError):
            repository.get(saved.id)


def test_duplicate_ids_roll_back_whole_caller_transaction(database):
    id = uuid4()
    with pytest.raises(ConflictError):
        with database.session() as session:
            repository = ScenarioRepository(session)
            repository.create(scene(), id=id)
            repository.create(scene(), id=id)
    with database.session() as session:
        assert ScenarioRepository(session).list() == []


@pytest.mark.parametrize("repository", [ScenarioRepository, SimulationRepository, FailureRepository])
def test_missing_records_raise_distinct_not_found_error(database, repository):
    with database.session() as session:
        with pytest.raises(NotFoundError):
            repository(session).get(uuid4())


@pytest.mark.parametrize("id", ["", "not-a-uuid", 12, None])
def test_invalid_read_ids_raise_validation_error(database, id):
    with database.session() as session:
        with pytest.raises(PersistenceError, match="UUID"):
            ScenarioRepository(session).get(id)


def test_saved_scenario_edit_never_changes_run_snapshot_and_link_restricts_delete(database):
    with database.session() as session:
        scenarios = ScenarioRepository(session)
        saved = scenarios.create(scene())
        queued = Simulation(scenario=saved.scenario, mode="test")
        runs = SimulationRepository(session)
        runs.create(queued, scenario_id=saved.id)
        scenarios.update(saved.id, scene(name="new recipe"), expected_revision=1)
        assert runs.get(queued.id).simulation.scenario.name == "repository-test"
        assert runs.get(queued.id).scenario_id == saved.id
    with pytest.raises(ConflictError, match="referenced"):
        with database.session() as session:
            ScenarioRepository(session).delete(saved.id, expected_revision=2)
    with database.session() as session:
        assert ScenarioRepository(session).get(saved.id).revision == 2


def test_run_reference_requires_current_matching_saved_recipe(database):
    with database.session() as session:
        saved = ScenarioRepository(session).create(scene())
        with pytest.raises(PersistenceError, match="match"):
            SimulationRepository(session).create(Simulation(scenario=scene(name="different")), scenario_id=saved.id)
        with pytest.raises(NotFoundError):
            SimulationRepository(session).create(Simulation(scenario=scene()), scenario_id=uuid4())


def test_run_lifecycle_and_unknown_result_roundtrip_preserve_mode_and_created_zone(database):
    created = datetime(2026, 5, 1, 10, tzinfo=timezone(timedelta(hours=2)))
    queued = Simulation(scenario=scene(), mode="test", created_at=created)
    with database.session() as session:
        repository = SimulationRepository(session)
        repository.create(queued)
        running = queued.model_copy(update={"status": SimulationStatus.RUNNING, "progress": 0.5,
                                           "started_at": created + timedelta(seconds=1)})
        active = repository.update(running, expected_revision=1)
        assert active.revision == 2
        result = terminal(queued)
        stored = repository.complete(result, expected_revision=2)
        assert stored.revision == 3
        assert stored.result == result
        assert stored.result.metrics.severity is None
        assert stored.result.metrics.collision_count is None
        assert stored.result.simulation.mode == "test"
        assert stored.result.simulation.created_at.utcoffset() == timedelta(hours=2)
        assert stored.created_at == created.astimezone(UTC)
    with database.session() as session:
        assert SimulationRepository(session).get(queued.id).result == result


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_can_finalize_directly_from_queued_without_progress_notifications(database, status):
    queued = Simulation(scenario=scene(), mode="test")
    with database.session() as session:
        repository = SimulationRepository(session)
        repository.create(queued)
        result = terminal(queued, status=status)
        assert repository.complete(result, expected_revision=1).simulation.status == status


@pytest.mark.parametrize("field,value", [
    ("scenario", scene(name="mutated")), ("agent", "custom"), ("mode", "carla"),
    ("created_at", datetime(2020, 1, 1, tzinfo=UTC)),
])
def test_run_identity_is_immutable(database, field, value):
    queued = Simulation(scenario=scene(), mode="test")
    with database.session() as session:
        repository = SimulationRepository(session)
        repository.create(queued)
        changed = queued.model_copy(update={field: value})
        with pytest.raises(PersistenceError, match="immutable"):
            repository.update(changed, expected_revision=1)


@pytest.mark.parametrize("first,second", [(True, 1), (1, 1.0), (False, 0)])
def test_snapshot_identity_distinguishes_json_scalar_types(database, first, second):
    queued = Simulation(scenario=scene(parameters={"custom": first}), mode="test")
    with database.session() as session:
        repository = SimulationRepository(session)
        repository.create(queued)
        changed = queued.model_copy(update={"scenario": scene(parameters={"custom": second})})
        with pytest.raises(PersistenceError, match="immutable"):
            repository.update(changed, expected_revision=1)


def test_snapshot_identity_ignores_dictionary_key_order(database):
    queued = Simulation(scenario=scene(parameters={"a": 1, "b": 2}), mode="test")
    with database.session() as session:
        repository = SimulationRepository(session)
        repository.create(queued)
        changed = queued.model_copy(update={"scenario": scene(parameters={"b": 2, "a": 1})})
        assert repository.update(changed, expected_revision=1).revision == 2


def test_lifecycle_cannot_regress_progress_status_or_start_time(database):
    queued = Simulation(scenario=scene(), mode="test")
    started = queued.created_at + timedelta(seconds=1)
    running = queued.model_copy(update={"status": SimulationStatus.RUNNING, "started_at": started, "progress": 0.5})
    with database.session() as session:
        repository = SimulationRepository(session)
        repository.create(queued)
        repository.update(running, expected_revision=1)
        for bad in [queued, running.model_copy(update={"progress": 0.4}),
                    running.model_copy(update={"started_at": started + timedelta(seconds=1)})]:
            with pytest.raises(PersistenceError):
                repository.update(bad, expected_revision=2)
        with pytest.raises(ConflictError, match="revision"):
            repository.update(running, expected_revision=1)


def test_terminal_records_are_immutable_and_terminal_update_requires_result(database):
    queued = Simulation(scenario=scene(), mode="test")
    result = terminal(queued)
    with database.session() as session:
        repository = SimulationRepository(session)
        with pytest.raises(PersistenceError, match="queued"):
            repository.create(result.simulation)
        repository.create(queued)
        with pytest.raises(PersistenceError, match="complete"):
            repository.update(result.simulation, expected_revision=1)
        repository.complete(result, expected_revision=1)
        with pytest.raises(ConflictError, match="immutable"):
            repository.complete(result, expected_revision=2)


def test_two_sessions_with_stale_revision_cannot_overwrite_the_winner(tmp_path):
    database = Database("sqlite:///" + (tmp_path / "runs.db").as_posix())
    database.initialize()
    queued = Simulation(scenario=scene(), mode="test")
    with database.session() as session:
        SimulationRepository(session).create(queued)
    with database.session() as session:
        stale = SimulationRepository(session).get(queued.id)
    with database.session() as session:
        SimulationRepository(session).complete(terminal(queued), expected_revision=1)
    with pytest.raises(ConflictError, match="revision"):
        with database.session() as session:
            SimulationRepository(session).complete(terminal(stale.simulation), expected_revision=stale.revision)
    with database.session() as session:
        assert SimulationRepository(session).get(queued.id).revision == 2
    database.dispose()


def test_run_filters_pagination_and_stable_id_tiebreaker(database):
    created = datetime(2026, 1, 1, tzinfo=UTC)
    ids = [UUID(int=i) for i in range(1, 5)]
    with database.session() as session:
        repository = SimulationRepository(session)
        for i, id in enumerate(ids):
            queued = Simulation(id=id, scenario=scene(), created_at=created, mode="test" if i < 3 else "carla")
            repository.create(queued)
            if i == 0:
                repository.complete(terminal(queued), expected_revision=1)
        assert [row.simulation.id for row in repository.list()] == list(reversed(ids))
        assert [row.simulation.id for row in repository.list(limit=2, offset=1)] == [ids[2], ids[1]]
        assert len(repository.list(mode="test")) == 3
        assert len(repository.list(status="queued", mode="test")) == 2
        assert len(repository.list(status="completed")) == 1


@pytest.mark.parametrize("arguments", [{"limit": 0}, {"limit": True}, {"limit": 1001},
    {"offset": -1}, {"offset": 1.2}, {"status": "invented"}, {"mode": "fake"}])
def test_run_list_rejects_invalid_filters_and_pagination(database, arguments):
    with database.session() as session:
        with pytest.raises(PersistenceError):
            SimulationRepository(session).list(**arguments)


@pytest.mark.parametrize("revision", [0, -1, True, 1.0])
def test_revision_inputs_are_strict(database, revision):
    with database.session() as session:
        saved = ScenarioRepository(session).create(scene())
        with pytest.raises(PersistenceError):
            ScenarioRepository(session).update(saved.id, scene(), expected_revision=revision)


def report(result, **overrides):
    return FailureReport(simulation_id=result.simulation.id, scenario=result.simulation.scenario,
                         kind="collision", summary="Observed contact", severity=100.0, **overrides)


def test_failure_roundtrip_and_nullable_reproduction_filters(database):
    result = save_result(database, collision_count=1, severity=100.0)
    with database.session() as session:
        repository = FailureRepository(session)
        saved = repository.create(report(result))
        assert saved.reproduced is None
        assert repository.list(reproduced=None) == [saved]
        assert repository.list(reproduced=False) == []
        reproduced = repository.set_reproduced(saved.id, False)
        assert reproduced.reproduced is False
        assert repository.get(saved.id).reproduced is False
        assert repository.list(simulation_id=result.simulation.id, reproduced=False) == [reproduced]
        assert repository.set_reproduced(saved.id, True).reproduced is True
        assert repository.set_reproduced(saved.id, None).reproduced is None
        with pytest.raises(PersistenceError):
            repository.set_reproduced(saved.id, 1)


@pytest.mark.parametrize("mutation,message", [
    ({"scenario": scene(name="different")}, "scenario"),
    ({"severity": 99.0}, "severity"),
    ({"kind": "hard_braking"}, "evidence"),
    ({"kind": "traffic_violation"}, "evidence"),
    ({"kind": "near_miss"}, "evidence"),
])
def test_failure_requires_matching_snapshot_score_and_positive_kind_evidence(database, mutation, message):
    result = save_result(database, collision_count=1, severity=100.0)
    with database.session() as session:
        with pytest.raises(PersistenceError, match=message):
            FailureRepository(session).create(report(result).model_copy(update=mutation))


def test_failure_rejects_queued_unscored_and_safe_results(database):
    queued = Simulation(scenario=scene(), mode="test")
    with database.session() as session:
        SimulationRepository(session).create(queued)
        with pytest.raises(PersistenceError, match="terminal"):
            FailureRepository(session).create(report(terminal(queued)))
    unscored = save_result(database)
    safe = save_result(database, collision_count=0, severity=0.0)
    with database.session() as session:
        with pytest.raises(PersistenceError, match="severity"):
            FailureRepository(session).create(report(unscored))
        with pytest.raises(PersistenceError, match="evidence"):
            FailureRepository(session).create(report(safe).model_copy(update={"severity": 0.0}))


@pytest.mark.parametrize("kind,metrics", [
    ("hard_braking", {"hard_braking_count": 1}),
    ("traffic_violation", {"traffic_violation_count": 1}),
    ("near_miss", {"min_ttc_s": 1.0}),
    ("near_miss", {"min_clearance_m": 0.5}),
])
def test_failure_supports_each_observed_kind(database, kind, metrics):
    result = save_result(database, severity=20.0, **metrics)
    failure = report(result).model_copy(update={"kind": kind, "severity": 20.0})
    with database.session() as session:
        assert FailureRepository(session).create(failure).kind == kind


def test_corrupt_projection_is_not_returned_as_valid_run(database):
    queued = Simulation(scenario=scene(), mode="test")
    with database.session() as session:
        SimulationRepository(session).create(queued)
        session.execute(text("UPDATE simulations SET mode='carla' WHERE id=:id"), {"id": str(queued.id)})
        with pytest.raises(PersistenceError, match="inconsistent"):
            SimulationRepository(session).get(queued.id)


def test_result_roundtrip_keeps_partial_coverage_custom_scoring_runtime_and_artifact_metadata(database):
    queued = Simulation(scenario=scene(), mode="test")
    result = terminal(queued, min_clearance_m=0.25)
    result.metrics.evaluation_settings = EvaluationSettings(ttc_warning_s=1.5, clearance_warning_m=0.75)
    result.metrics.coverage = EvaluationCoverage(total_frames=2, collision_frames=1, has_gaps=True)
    result.runtime = SimulationRuntime(adapter="test", map_name="synthetic", fixed_delta_seconds=0.05,
                                       seed=17, resolved_spawns={"ego": Transform()}, sensor_samples_count=0)
    result.artifacts = [ArtifactReference(kind="telemetry", path="telemetry/final.jsonl", media_type="application/x-ndjson")]
    with database.session() as session:
        repository = SimulationRepository(session)
        repository.create(queued)
        repository.complete(result, expected_revision=1)
    with database.session() as session:
        stored = SimulationRepository(session).get(queued.id).result
        assert stored == result
        assert stored.metrics.severity is None
        assert stored.metrics.collision_count is None
        assert stored.metrics.coverage.has_gaps
        assert stored.runtime.seed == 17
        assert stored.artifacts[0].media_type == "application/x-ndjson"


def test_near_miss_evidence_uses_recorded_custom_thresholds(database):
    queued = Simulation(scenario=scene(), mode="test")
    result = terminal(queued, min_ttc_s=2.0, severity=5.0)
    result.metrics.evaluation_settings = EvaluationSettings(ttc_warning_s=1.0)
    with database.session() as session:
        repository = SimulationRepository(session)
        repository.create(queued)
        repository.complete(result, expected_revision=1)
        failure = report(result).model_copy(update={"kind": "near_miss", "severity": 5.0})
        with pytest.raises(PersistenceError, match="evidence"):
            FailureRepository(session).create(failure)


def test_mutated_contract_instances_are_revalidated_before_storage(database):
    scenario = scene()
    scenario.actors[0].speed_mps = -1.0
    with database.session() as session:
        with pytest.raises(PersistenceError, match="scenario"):
            ScenarioRepository(session).create(scenario)
        simulation = Simulation(scenario=scene())
        simulation.progress = 0.9
        with pytest.raises(PersistenceError, match="simulation"):
            SimulationRepository(session).create(simulation)
