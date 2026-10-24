"""Matched experiments run the real journal/queue and, where selected, Python workers.

Short labelled synthetic recipes keep these coordination tests independent of
CARLA and of neural model downloads.
"""
from threading import Event
from time import monotonic
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.main import create_app
from backend.services.comparison_service import distribution
from shared.models.optimization import ComparisonRequest, SearchRequest
from optimization.search_manager import SearchManager
from simulator import SimulationRunner
from simulator.test_adapter import TestSimulationAdapter
from test_optimization_service import Harness
from test_registered_agent_execution import settings, package, register, scene, calls
from test_optimization_api import CollisionAdapter
from test_optimization_repository import database, child
from backend.optimization_repository import OptimizationRepository
from backend.persistence_errors import PersistenceError
from shared.models.optimization import ComparisonAgent, ExecutionSpec


def wait(client, identifier):
    deadline = monotonic() + 90
    while monotonic() < deadline:
        response = client.get(f"/optimizations/{identifier}")
        assert response.status_code == 200, response.text
        view = response.json()
        if view["operation"]["status"] not in {"queued", "running"}:
            return view
        Event().wait(.05)
    pytest.fail("Short comparison did not finalize")


def submit(client, **changes):
    response = client.post("/optimizations", json={"kind": "compare", "scenario": scene(),
        "mode": "test", "agents": [None], "seeds": [7, 11], "repeats": 2, **changes})
    assert response.status_code == 202, response.text
    return response.json()["id"]


@pytest.mark.parametrize("changes", [
    {"agents": [None, None]}, {"seeds": [1, 1]}, {"seeds": []}, {"repeats": True},
    {"agents": [uuid4() for _ in range(8)], "seeds": list(range(20)), "repeats": 2},
])
def test_comparison_rejects_ambiguous_or_excessive_plans(changes):
    with pytest.raises(ValidationError):
        ComparisonRequest(scenario=scene(), **changes)


def test_distribution_preserves_missing_denominators_and_sample_spread():
    result = distribution([1., 3.], 4, "units")
    assert result.mean == 2
    assert result.sample_stddev == pytest.approx(2 ** .5)
    assert (result.eligible_results, result.observed_results, result.unavailable_results) == (4, 2, 2)
    assert distribution([], 3, "units").mean is None
    assert distribution([0.], 1, "units").sample_stddev is None


@pytest.mark.parametrize("change", [{"agent_index": 1}, {"candidate_index": 1}, {"repeat_index": 1}])
def test_journal_rejects_trials_outside_the_planned_cell(database, change):
    with database.session() as session:
        repository = OptimizationRepository(session)
        request = ComparisonRequest(scenario=scene(), mode="test", seeds=[7])
        operation = repository.create(request, execution=ExecutionSpec(mode="test"),
                                      comparison_agents=[ComparisonAgent(agent_id=None, snapshot=None)])
        recipe = request.scenario.model_copy(update={"seed": 7})
        _, trial = child(session, operation, scenario=recipe, role="compare", candidate_index=0)
        with pytest.raises(PersistenceError, match="plan"):
            repository.add_trial(trial.model_copy(update=change))
        assert repository.trials(operation.id) == []


def test_journal_rejects_unmatched_recipe_and_seed(database):
    with database.session() as session:
        repository = OptimizationRepository(session)
        request = ComparisonRequest(scenario=scene(), mode="test", seeds=[7])
        operation = repository.create(request, execution=ExecutionSpec(mode="test"),
                                      comparison_agents=[ComparisonAgent(agent_id=None, snapshot=None)])
        _, trial = child(session, operation, scenario=request.scenario, role="compare", candidate_index=0)
        with pytest.raises(PersistenceError, match="paired scenario"):
            repository.add_trial(trial)
        assert repository.trials(operation.id) == []


def test_search_keeps_explicit_task_and_agent_selection():
    task = scene(sensors=True)["driving_task"]
    request = SearchRequest(template="sudden_braking", agent_id=uuid4(), driving_task=task)
    manager = SearchManager(request)
    first = manager.suggest()
    assert first.driving_task == request.driving_task
    manager.observe(None)
    assert manager.suggest().driving_task == request.driving_task


def test_repeated_baseline_comparison_is_durable_and_exportable(settings):
    app = create_app(settings, runner_factory=lambda **kw: SimulationRunner(CollisionAdapter(), **kw))
    with TestClient(app) as client:
        identifier = submit(client, failure_kind="collision")
        view = wait(client, identifier)
        assert view["operation"]["status"] == "completed", view
        assert [(t["scenario"]["seed"], t["repeat_index"], t["agent_index"]) for t in view["trials"]] == [(7, 0, 0), (7, 1, 0), (11, 0, 0), (11, 1, 0)]
        assert all(t["matches"] is True for t in view["trials"])
        response = client.get(f"/optimizations/{identifier}/comparison?download=true")
        assert response.status_code == 200, response.text
        assert "attachment" in response.headers["content-disposition"]
        report = response.json()
        assert report["evidence"] == view
        group = report["groups"][0]
        assert group["completed_runs"] == group["matching_runs"] == 4
        assert group["metrics"]["collision_count"]["observed_results"] == 4
        assert not report["paired_comparisons"]
    # Reading an exported comparison never starts a simulation worker.
    reopened = create_app(settings)
    with TestClient(reopened) as client:
        assert client.get(f"/optimizations/{identifier}/comparison").json() == report


def test_registered_agent_pairs_and_source_reproduction_minimization(settings, package):
    directory, log = package()
    app = create_app(settings, runner_factory=lambda **kw: SimulationRunner(CollisionAdapter(), **kw))
    with TestClient(app) as client:
        # This test measures comparison/reproduction identity. Give native
        # interpreter exit its own explicit fixture budget; dedicated lifecycle
        # tests retain short close deadlines and process-termination assertions.
        process_options = {"close_timeout_s": 10}
        agent = register(client, directory, process_options=process_options)
        variant = register(client, directory, configuration={"brake": .2}, process_options=process_options)
        identifier = submit(client, agents=[None, agent["id"], variant["id"]], seeds=[4], repeats=1, failure_kind="collision")
        view = wait(client, identifier)
        assert view["operation"]["status"] == "completed", view
        assert view["operation"]["comparison_agents"][1]["snapshot"] == agent["snapshot"]
        report = client.get(f"/optimizations/{identifier}/comparison").json()
        assert [g["completed_runs"] for g in report["groups"]] == [1, 1, 1]
        assert view["operation"]["comparison_agents"][2]["snapshot"] == variant["snapshot"]
        assert agent["snapshot"]["configuration"] != variant["snapshot"]["configuration"]
        assert report["paired_comparisons"][0]["difference"]["observed_results"] == 1
        assert any(c["event"] == "step" for c in calls(log))
        trial = view["trials"][1]
        for kind in ("reproduce", "minimize"):
            response = client.post("/optimizations", json={"kind": kind, "failure_id": trial["failure_id"], "repeats": 1, **({"max_trials": 1} if kind == "minimize" else {})})
            assert response.status_code == 202, response.text
            result = wait(client, response.json()["id"])
            assert result["operation"]["status"] == "completed", result
            assert result["operation"]["agent_snapshot"] == agent["snapshot"]
            child = client.get(f'/simulations/{result["trials"][0]["simulation_id"]}').json()
            assert child["simulation"]["agent_snapshot"] == agent["snapshot"]
        client.post(f'/agents/{agent["id"]}/deactivate')
        rejected = client.post("/optimizations", json={"kind": "reproduce", "failure_id": trial["failure_id"], "repeats": 1})
        assert rejected.status_code == 409


def test_registered_search_launches_selected_worker_and_keeps_task(settings, package, monkeypatch):
    # Sampler behavior is covered independently. Use a short sampled fixture to
    # test identity/task propagation through the real queue and Python worker,
    # without hundreds of IPC exchanges on slow Windows test hosts.
    from scenarios import validate_scenario
    suggest = SearchManager.suggest

    def short_suggest(manager):
        sampled = suggest(manager)
        return validate_scenario(scene() | {"seed": sampled.seed, "driving_task": sampled.driving_task})

    monkeypatch.setattr(SearchManager, "suggest", short_suggest)
    directory, log = package()
    app = create_app(settings, runner_factory=lambda **kw: SimulationRunner(CollisionAdapter(), **kw))
    with TestClient(app) as client:
        agent = register(client, directory)
        task = scene(sensors=True)["driving_task"]
        response = client.post("/optimizations", json={"kind": "search", "template": "sudden_braking",
            "trials": 1, "mode": "test", "agent_id": agent["id"], "driving_task": task, "failure_kind": "collision"})
        assert response.status_code == 202, response.text
        view = wait(client, response.json()["id"])
        assert view["operation"]["status"] == "completed", view
        assert view["operation"]["agent_snapshot"] == agent["snapshot"]
        assert view["operation"]["engine_version"].endswith("/agent-v1")
        assert len(view["trials"]) == 1
        child = client.get(f'/simulations/{view["trials"][0]["simulation_id"]}').json()
        assert child["simulation"]["status"] == "completed", child
        assert child["simulation"]["agent_snapshot"] == agent["snapshot"]
        assert child["simulation"]["scenario"]["driving_task"]["target_speed_mps"] == task["target_speed_mps"]
        assert any(c["event"] == "step" for c in calls(log))


@pytest.mark.parametrize("partial", [True, False])
def test_pair_excludes_partial_coverage_and_runtime_version_mismatches(settings, package, partial):
    directory, _ = package()
    count = 0

    class VersionedAdapter(UnobservedAdapter if partial else CollisionAdapter):
        def __init__(self, version):
            super().__init__()
            self.version = version

        def runtime_info(self):
            return super().runtime_info() | {"client_version": str(self.version)}

    def runner(**kwargs):
        nonlocal count
        count += 1
        return SimulationRunner(VersionedAdapter(count), **kwargs)

    with TestClient(create_app(settings, runner_factory=runner)) as client:
        agent = register(client, directory)
        identifier = submit(client, agents=[None, agent["id"]], seeds=[0], repeats=1)
        assert wait(client, identifier)["operation"]["status"] == "completed"
        report = client.get(f"/optimizations/{identifier}/comparison").json()
        pair = report["paired_comparisons"][0]
        assert pair["difference"]["observed_results"] == 0
        assert pair["difference"]["unavailable_results"] == 1
        assert pair["pairs"][0]["severity_difference"] is None
        assert ("coverage" if partial else "versions") in pair["pairs"][0]["reason"]


class UnobservedAdapter(TestSimulationAdapter):
    def open(self, recipe):
        frame = super().open(recipe)
        frame.collisions = None
        return frame

    def step(self, *args):
        frame = super().step(*args)
        frame.collisions = None
        return frame


def test_unknown_observation_is_not_an_observed_negative(settings):
    app = create_app(settings, runner_factory=lambda **kw: SimulationRunner(UnobservedAdapter(), **kw))
    with TestClient(app) as client:
        identifier = submit(client, seeds=[0], repeats=1, failure_kind="collision")
        view = wait(client, identifier)
        assert view["operation"]["status"] == "completed", view
        assert view["trials"][0]["matches"] is None
        group = client.get(f"/optimizations/{identifier}/comparison").json()["groups"][0]
        assert group["unknown_runs"] == 1 and group["negative_runs"] == 0
        assert group["metrics"]["collision_count"]["observed_results"] == 0
        assert group["metrics"]["collision_count"]["unavailable_results"] == 1


def test_changed_registered_files_stop_comparison_without_baseline_substitution(settings, package):
    directory, _ = package()
    entered, release = Event(), Event()

    class HeldAdapter(CollisionAdapter):
        def open(self, recipe):
            entered.set()
            assert release.wait(20)
            return super().open(recipe)

    app = create_app(settings, runner_factory=lambda **kw: SimulationRunner(HeldAdapter(), **kw))
    with TestClient(app) as client:
        agent = register(client, directory)
        identifier = submit(client, agents=[None, agent["id"]], seeds=[0], repeats=1)
        try:
            assert entered.wait(5)
            with (directory / "agent.py").open("a", encoding="utf-8") as stream:
                stream.write("\n# changed after experiment snapshot\n")
        finally:
            release.set()
        view = wait(client, identifier)
        assert view["operation"]["status"] == "failed", view
        assert len(view["trials"]) == 1
        report = client.get(f"/optimizations/{identifier}/comparison")
        assert report.status_code == 200
        assert report.json()["groups"][1]["simulation_ids"] == []
        assert report.json()["paired_comparisons"][0]["difference"]["observed_results"] == 0


def test_interrupted_comparison_resume_reuses_child_and_preserves_budget(tmp_path):
    harness = Harness(tmp_path)
    entered, release = Event(), Event()

    class HeldAdapter(CollisionAdapter):
        def open(self, recipe):
            entered.set()
            assert release.wait(20)
            return super().open(recipe)

    harness.releases.append(release)
    harness.adapter_factory = HeldAdapter
    try:
        first = harness.controller()
        operation = first.submit(ComparisonRequest(scenario=scene(), mode="test", seeds=[8], repeats=2))
        assert entered.wait(5)
        child_id = first.get(operation.id).trials[0].simulation_id
        assert first.close()
        interrupted = first.get(operation.id).operation
        assert interrupted.status == "interrupted"
        second = harness.controller()
        second.resume(operation.id, expected_revision=interrupted.revision)
        release.set()
        deadline = monotonic() + 30
        while monotonic() < deadline:
            view = second.get(operation.id)
            if view.operation.status not in {"queued", "running"}:
                break
            Event().wait(.05)
        assert view.operation.status == "completed", view
        assert len(view.trials) == 2
        assert view.trials[0].simulation_id == child_id
        assert len({t.simulation_id for t in view.trials}) == 2
    finally:
        release.set()
        harness.close()


def test_cancellation_stops_remaining_matched_cells(settings):
    entered, release = Event(), Event()

    class HeldAdapter(CollisionAdapter):
        def open(self, recipe):
            entered.set()
            assert release.wait(20)
            return super().open(recipe)

    with TestClient(create_app(settings, runner_factory=lambda **kw: SimulationRunner(HeldAdapter(), **kw))) as client:
        identifier = submit(client)
        try:
            assert entered.wait(5)
            response = client.post(f"/optimizations/{identifier}/cancel")
            assert response.status_code == 202
        finally:
            release.set()
        view = wait(client, identifier)
        assert view["operation"]["status"] == "cancelled"
        assert len(view["trials"]) == 1
        summary = client.get(f"/optimizations/{identifier}/comparison")
        assert summary.status_code == 200
        assert summary.json()["groups"][0]["planned_runs"] == 4
