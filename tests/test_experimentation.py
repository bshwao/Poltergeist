"""Guided suites and discovery enforce reviewed identities, budgets and evidence."""

from datetime import UTC, datetime, timedelta
from threading import Event
from time import monotonic
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from backend.services.experimentation_service import ExperimentationService
from shared.models import CollisionEvent
from simulator import SimulationRunner
from simulator.test_adapter import TestSimulationAdapter
from test_registered_agent_execution import package, register, calls


class ContactAdapter(TestSimulationAdapter):
    def open(self, recipe):
        frame = super().open(recipe)
        frame.collisions = [CollisionEvent(other_actor_id="world:barrier")]
        return frame


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, database_url=f"sqlite:///{(tmp_path / 'lab.db').as_posix()}",
        storage_dir=tmp_path / "artifacts", simulation_shutdown_timeout_s=5, simulation_progress_interval_s=60.)


def client_for(settings, adapter=ContactAdapter):
    return TestClient(create_app(settings, runner_factory=lambda **kw: SimulationRunner(adapter(), **kw)))


def wait(client, path, predicate):
    deadline, last = monotonic() + 30, None
    while monotonic() < deadline:
        response = client.get(path)
        assert response.status_code == 200, response.text
        last = response.json()
        if predicate(last):
            return last
        Event().wait(.01)
    raise AssertionError(f"Experiment did not finish: {last}")


def proposal(**changes):
    value = {"mode": "test", "goal": {"description": "Find repeatable following collisions", "template": "sudden_braking",
        "failure_kind": "collision", "duration_s": .1, "parameters": {"brake_delay_s": 0.0}},
        "budget": {"max_executions": 6, "max_search_trials": 2, "confirmation_repeats": 2,
            "required_successes": 2, "max_candidates": 2, "max_wall_time_s": 30., "max_simulated_time_s": .6}}
    return value | changes


def propose(client, **changes):
    response = client.post("/experimentation/discovery/plans", json=proposal(**changes))
    assert response.status_code == 201, response.text
    return response.json()


def approve(client, plan, **changes):
    return client.post(f"/experimentation/discovery/plans/{plan['id']}/approve", json={
        "expected_revision": plan["revision"], "digest": plan["digest"], "approve": True, **changes})


def report(client, plan):
    return wait(client, f"/experimentation/discovery/plans/{plan['id']}/report",
        lambda value: value["evidence"]["operation"]["status"] not in {"queued", "running"})


def test_gallery_is_illustratable_and_inert_without_persistence(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        entries = client.get("/experimentation/gallery").json()
        assert len(entries) == 16 and len({row["id"] for row in entries}) == 16
        assert {row["family"] for row in entries if row["level"] == "starter"} == {
            "sudden_braking", "intersection", "pedestrian_crossing", "occluded_pedestrian"}
        assert all(row["learning_goal"] and row["scenario"]["actors"] for row in entries)
        assert app.state.runtime._worker is app.state.runtime._persistence is None
    assert not settings.storage_dir.exists()


def test_discovery_proposal_is_read_only_until_exact_explicit_approval(settings):
    with client_for(settings) as client:
        plan = propose(client)
        assert client.app.state.runtime._worker is None
        assert client.get("/simulations").json() == []
        assert plan["maximum_executions"] == 6 and len(plan["steps"]) >= 3
        assert approve(client, plan, digest="0" * 64).status_code == 409
        assert approve(client, plan, approve=False).status_code == 422
        assert approve(client, plan, approve=1).status_code == 422
        assert approve(client, plan, expected_revision=5).status_code == 409
        assert client.get("/simulations").json() == []
        accepted = approve(client, plan)
        assert accepted.status_code == 202, accepted.text
        repeated = approve(client, plan)
        assert repeated.status_code == 202 and repeated.json()["id"] == accepted.json()["id"]
        finished = report(client, plan)
        operation = finished["evidence"]["operation"]
        assert (operation["status"], operation["error"]) == ("completed", None)
        assert finished["confirmed_failures"] == 2 and finished["executions_used"] == 6
        assert finished["simulated_time_reserved_s"] == pytest.approx(.6)
        for candidate in finished["candidates"]:
            assert candidate["status"] == "confirmed" and candidate["matching_confirmations"] == 2
            assert len(candidate["confirmation_simulation_ids"]) == 2 and candidate["reproduction"]
            assert client.get(f"/failures/{candidate['failure_id']}").json()["reproduced"] is True
        assert len(client.get("/simulations").json()) == 6
        assert approve(client, plan).json()["id"] == plan["id"]
        export = client.get(f"/experimentation/discovery/plans/{plan['id']}/report?download=true")
        assert "attachment" in export.headers["content-disposition"] and export.json() == finished
        # Generic experiment submission cannot bypass the approved-plan path.
        assert client.post("/optimizations", json=accepted.json()["request"]).status_code == 422


def test_no_failure_report_retains_every_negative_and_does_not_invent_confirmation(settings):
    with client_for(settings, TestSimulationAdapter) as client:
        plan = propose(client)
        assert approve(client, plan).status_code == 202
        saved = report(client, plan)
        assert saved["confirmed_failures"] == 0 and saved["candidates"] == []
        assert saved["negative_trials"] == saved["executions_used"] == 2
        assert "No failure was confirmed" in saved["summary"]
        assert client.get("/failures").json() == []


def test_failed_trials_are_unknown_retained_and_never_confirmed(settings):
    class Broken(TestSimulationAdapter):
        def open(self, recipe):
            raise RuntimeError("fixture unavailable")
    with client_for(settings, Broken) as client:
        plan = propose(client)
        assert approve(client, plan).status_code == 202
        saved = report(client, plan)
        assert saved["unknown_trials"] == saved["executions_used"] == 2
        assert saved["confirmed_failures"] == 0 and saved["negative_trials"] == 0
        assert all(t["assessed"] and t["matches"] is None for t in saved["evidence"]["trials"])


def test_budget_reserves_confirmations_and_stops_before_overspending(settings):
    value = proposal()
    value["budget"].update(max_executions=4, max_search_trials=10)
    with client_for(settings) as client:
        plan = propose(client, budget=value["budget"])
        assert approve(client, plan).status_code == 202
        saved = report(client, plan)
        assert saved["executions_used"] == 3 and saved["confirmed_failures"] == 1
        assert saved["evidence"]["operation"]["outcome"]["budget_exhausted"] is True


def test_wall_budget_cancels_active_child_and_deadline_survives_restart(settings):
    entered, release = Event(), Event()
    class Held(ContactAdapter):
        def open(self, recipe):
            entered.set()
            assert release.wait(10)
            return super().open(recipe)
    clock = [datetime.now(UTC)]
    with client_for(settings, Held) as client:
        plan = propose(client)
        optimizer = client.app.state.runtime.optimizer()
        optimizer._now = lambda: clock[0]
        assert approve(client, plan).status_code == 202
        assert entered.wait(5)
        clock[0] += timedelta(seconds=60)
        try:
            saved = report(client, plan)
            assert saved["executions_used"] == 1 and saved["confirmed_failures"] == 0
            assert saved["unassessed_trials"] == 1
            assert saved["evidence"]["operation"]["outcome"]["budget_exhausted"]
            child = client.get(f"/simulations/{saved['evidence']['trials'][0]['simulation_id']}").json()
            assert child["job"]["cancel_requested"]
            original_deadline = saved["plan"]["deadline_at"]
        finally:
            release.set()
    with client_for(settings) as client:
        loaded = client.get(f"/experimentation/discovery/plans/{plan['id']}").json()
        assert loaded["deadline_at"] == original_deadline
        assert approve(client, plan).json()["id"] == plan["id"]
        assert len(client.get("/simulations").json()) == 1


def test_expired_plan_and_invalid_budget_do_not_launch(settings):
    with client_for(settings) as client:
        service = ExperimentationService(client.app.state.runtime.persistence(), now=lambda: datetime.now(UTC) - timedelta(days=2))
        from shared.models.experimentation import DiscoveryProposal
        plan = service.propose(DiscoveryProposal.model_validate(proposal())).model_dump(mode="json")
        assert approve(client, plan).status_code == 409
        bad = proposal()
        bad["budget"]["required_successes"] = 3
        assert client.post("/experimentation/discovery/plans", json=bad).status_code == 422
        bad = proposal()
        bad["budget"]["max_simulated_time_s"] = .1
        assert client.post("/experimentation/discovery/plans", json=bad).status_code == 422
        assert client.get("/simulations").json() == []


def test_saved_suite_crud_run_idempotency_matched_cases_and_export(settings):
    recipe = {"name": "short suite scene", "duration_s": .1, "actors": [{"id": "ego", "type": "ego_vehicle"}]}
    definition = {"kind": "suite", "name": "Weekly checks", "mode": "test", "seeds": [3, 7], "agents": [None],
        "cases": [{"name": "Clear approach", "scenario": recipe}, {"name": "Another approach", "scenario": recipe | {"name": "another"}}]}
    with client_for(settings) as client:
        response = client.post("/experimentation/suites", json={"definition": definition})
        assert response.status_code == 201, response.text
        suite = response.json()
        assert client.app.state.runtime._worker is None
        assert client.get("/experimentation/suites").json() == [suite]
        edit = client.put(f"/experimentation/suites/{suite['id']}", json={"definition": definition, "description": "Saved for repeated use", "expected_revision": 1})
        assert edit.status_code == 200 and edit.json()["revision"] == 2
        assert client.put(f"/experimentation/suites/{suite['id']}", json={"definition": definition, "expected_revision": 1}).status_code == 409
        launch = {"expected_revision": 2, "request_id": str(uuid4())}
        accepted = client.post(f"/experimentation/suites/{suite['id']}/runs", json=launch)
        assert accepted.status_code == 202, accepted.text
        run_id = accepted.json()["id"]
        done = wait(client, f"/optimizations/{run_id}", lambda v: v["operation"]["status"] not in {"queued", "running"})
        assert (done["operation"]["status"], done["operation"]["error"]) == ("completed", None)
        assert len(done["trials"]) == 4
        assert client.get(f"/experimentation/suites/{suite['id']}/runs?limit=1").json() == [done["operation"]]
        assert client.post(f"/experimentation/suites/{suite['id']}/runs", json=launch).json()["id"] == run_id
        result = client.get(f"/experimentation/suite-runs/{run_id}?download=true")
        assert result.status_code == 200, result.text
        assert len(result.json()["cases"]) == 2
        assert all(case["groups"][0]["completed_runs"] == 2 for case in result.json()["cases"])
        assert "attachment" in result.headers["content-disposition"]
        assert client.delete(f"/experimentation/suites/{suite['id']}?expected_revision=1").status_code == 409
        assert client.delete(f"/experimentation/suites/{suite['id']}?expected_revision=2").status_code == 204
        assert client.get(f"/optimizations/{run_id}").json() == done
    with client_for(settings) as client:
        assert client.get(f"/experimentation/suite-runs/{run_id}").status_code == 200


def test_highlights_link_actual_frames_and_feedback_requires_deliberate_consent(settings):
    with client_for(settings) as client:
        launched = client.post("/simulations", json={"mode": "test", "scenario": {
            "name": "Guided first run", "duration_s": .1, "actors": [{"id": "ego", "type": "ego_vehicle"}]}}).json()
        identity = launched["simulation"]["id"]
        wait(client, f"/simulations/{identity}", lambda v: v["result"] is not None)
        highlight = client.get(f"/experimentation/highlights/{identity}")
        assert highlight.status_code == 200, highlight.text
        value = highlight.json()
        frames = client.get(f"/simulations/{identity}/telemetry").json()["frames"]
        actual = {frame["frame"]: frame["time_s"] for frame in frames}
        assert value["mode"] == "test" and "Synthetic" in value["explanation"]
        assert any(item["kind"] == "collision" for item in value["highlights"])
        assert all(actual[item["frame"]] == item["time_s"] for item in value["highlights"])
        feedback = {"simulation_id": identity, "could_explain_outcome": True, "reason_to_return": "Compare my next agent version"}
        assert client.post("/experimentation/feedback", json=feedback).status_code == 422
        assert client.post("/experimentation/feedback", json=feedback | {"consent_to_save": 1}).status_code == 422
        saved = client.post("/experimentation/feedback", json=feedback | {"consent_to_save": True})
        assert saved.status_code == 201 and client.get("/experimentation/feedback").json() == [saved.json()]


def test_default_browser_goal_preflights_every_candidate(settings):
    value = proposal()
    value["goal"].update(seed=7, duration_s=8., parameters={"brake_delay_s": .5},
        ranges={"ego_speed_mps": [8., 25.], "lead_distance_m": [5.5, 35.]})
    value["budget"].update(max_executions=12, max_search_trials=6, max_candidates=3,
        max_wall_time_s=300., max_simulated_time_s=96.)
    with client_for(settings) as client:
        plan = propose(client, **value)
        assert len(plan["scenarios"]) == 6
        assert [s["seed"] for s in plan["scenarios"]] == list(range(7, 13))
        assert client.app.state.runtime._worker is None


def test_approval_executes_frozen_candidates_after_sampler_changes(settings, monkeypatch):
    from optimization.search_manager import SearchManager
    with client_for(settings) as client:
        plan = propose(client)
        def changed_sampler(self):
            raise AssertionError("Approved discovery must never resample its recipes")
        monkeypatch.setattr(SearchManager, "suggest", changed_sampler)
        assert approve(client, plan).status_code == 202
        result = report(client, plan)
        assert result["confirmed_failures"] == 2
        for trial in result["evidence"]["trials"]:
            assert trial["scenario"] == plan["scenarios"][trial["candidate_index"]]


@pytest.mark.parametrize("unknown,expected", [(False, "not_reproduced"), (True, "inconclusive")])
def test_a_matching_search_observation_does_not_count_as_confirmation(settings, unknown, expected):
    class Unknown(TestSimulationAdapter):
        def open(self, recipe):
            frame = super().open(recipe)
            frame.collisions = None
            return frame
        def step(self, *args):
            frame = super().step(*args)
            frame.collisions = None
            return frame
    adapters = iter([ContactAdapter, Unknown if unknown else TestSimulationAdapter, Unknown if unknown else TestSimulationAdapter])
    app = create_app(settings, runner_factory=lambda **kw: SimulationRunner(next(adapters)(), **kw))
    with TestClient(app) as client:
        value = proposal()
        value["budget"].update(max_search_trials=1, max_candidates=1, max_executions=3)
        plan = propose(client, **value)
        assert approve(client, plan).status_code == 202
        result = report(client, plan)
        assert result["confirmed_failures"] == 0 and result["executions_used"] == 3
        candidate = result["candidates"][0]
        assert candidate["status"] == expected and candidate["reproduction"] is None
        assert candidate["matching_confirmations"] == 0 and candidate["pending_confirmations"] == 0
        assert client.get(f"/failures/{candidate['failure_id']}").json()["reproduced"] is (None if unknown else False)


def test_cancelled_discovery_retains_incomplete_confirmation_without_further_work(settings):
    entered, release = Event(), Event()
    class Held(ContactAdapter):
        def open(self, recipe):
            entered.set()
            assert release.wait(10)
            return super().open(recipe)
    with client_for(settings, Held) as client:
        plan = propose(client)
        assert approve(client, plan).status_code == 202 and entered.wait(5)
        try:
            assert client.post(f"/optimizations/{plan['id']}/cancel").status_code == 202
            result = report(client, plan)
            assert result["evidence"]["operation"]["status"] == "cancelled"
            assert result["executions_used"] == result["unassessed_trials"] == 1
            assert result["confirmed_failures"] == 0
        finally:
            release.set()
        assert len(client.get("/simulations").json()) == 1


def test_registered_agent_search_confirmation_and_suite_comparisons(settings, package):
    directory, log = package()
    with client_for(settings) as client:
        agent = register(client, directory, process_options={"close_timeout_s": 10})
        value = proposal()
        value["goal"]["agent_id"] = agent["id"]
        value["budget"].update(max_search_trials=1, max_candidates=1, max_executions=3, max_wall_time_s=120.)
        plan = propose(client, **value)
        assert plan["agent_snapshot"] == agent["snapshot"] and not calls(log)
        assert approve(client, plan).status_code == 202
        result = report(client, plan)
        assert result["confirmed_failures"] == 1 and result["executions_used"] == 3
        assert len([row for row in calls(log) if row["event"] == "load"]) == 3
        scenario = result["candidates"][0]["scenario"]
        suite = client.post("/experimentation/suites", json={"definition": {
            "kind": "suite", "name": "Registered controller comparison", "mode": "test", "agents": [None, agent["id"]],
            "seeds": [9], "cases": [{"name": "Confirmed approach", "scenario": scenario}]}}).json()
        accepted = client.post(f"/experimentation/suites/{suite['id']}/runs", json={"request_id": str(uuid4()), "expected_revision": 1})
        assert accepted.status_code == 202, accepted.text
        suite_id = accepted.json()["id"]
        comparison = wait(client, f"/experimentation/suite-runs/{suite_id}", lambda v: v["evidence"]["operation"]["status"] == "completed")
        assert comparison["evidence"]["operation"]["comparison_agents"][1]["snapshot"] == agent["snapshot"]
        case = comparison["cases"][0]
        assert [group["completed_runs"] for group in case["groups"]] == [1, 1]
        assert case["paired_comparisons"][0]["difference"]["observed_results"] == 1


def test_agent_changes_after_review_reject_approval_without_a_child(settings, package):
    directory, _ = package()
    with client_for(settings) as client:
        agent = register(client, directory)
        value = proposal()
        value["goal"]["agent_id"] = agent["id"]
        plan = propose(client, **value)
        (directory / "weights.bin").write_bytes(b"changed after review")
        assert approve(client, plan).status_code == 409
        assert client.get("/simulations").json() == []
        assert client.get(f"/experimentation/discovery/plans/{plan['id']}").json()["operation_id"] is None


def test_report_rejects_an_execution_policy_changed_after_approval(settings):
    import json
    from backend.models import OptimizationOperationRecord
    from backend.persistence_errors import PersistenceError
    with client_for(settings) as client:
        plan = propose(client)
        assert approve(client, plan).status_code == 202
        report(client, plan)
        persistence = client.app.state.runtime.persistence()
        with persistence.database.session() as session:
            row = session.get(OptimizationOperationRecord, plan["id"])
            payload = json.loads(row.payload)
            payload["request"]["budget"]["max_candidates"] = 3
            row.payload = json.dumps(payload)
        with pytest.raises(PersistenceError, match="approved plan"):
            ExperimentationService(persistence).discovery_report(UUID(plan["id"]))


def test_browser_fixture_uses_current_typed_contracts():
    import json
    from pathlib import Path
    from shared.models.experimentation import DiscoveryPlan, DiscoveryReport, SuiteReport, ReplayHighlights, SavedFeedback
    from shared.models.api import SimulationRecord
    value = json.loads((Path(__file__).parents[1] / "frontend/tests/fixtures/experimentation.json").read_text(encoding="utf-8"))
    for key, model in [("plan", DiscoveryPlan), ("approved_plan", DiscoveryPlan), ("discovery", DiscoveryReport),
            ("suite_report", SuiteReport), ("run", SimulationRecord), ("highlights", ReplayHighlights), ("feedback", SavedFeedback)]:
        model.model_validate(value[key])


def test_interrupted_discovery_reuses_spent_trials_and_original_deadline(tmp_path):
    from test_optimization_service import Harness, BlockedCollisionAdapter, CollisionAdapter, terminal
    from shared.models.experimentation import DiscoveryProposal, DiscoveryApproval
    harness = Harness(tmp_path)
    try:
        entered, release = harness.blocker()
        harness.adapter_factory = lambda: BlockedCollisionAdapter(entered, release)
        first = harness.controller()
        workspace = ExperimentationService(harness.persistence)
        plan = workspace.propose(DiscoveryProposal.model_validate(proposal()))
        workspace.approve(plan.id, DiscoveryApproval(expected_revision=1, digest=plan.digest, approve=True), first)
        assert entered.wait(10)
        child = first.get(plan.id).trials[0].simulation_id
        deadline = workspace.get_document("discovery", plan.id).deadline_at
        assert first.close()
        interrupted = first.get(plan.id)
        assert interrupted.operation.status == "interrupted" and len(interrupted.trials) == 1
        harness.adapter_factory = CollisionAdapter
        second = harness.controller()
        assert second.get(plan.id).operation.status == "interrupted"
        second.resume(plan.id, expected_revision=interrupted.operation.revision)
        release.set()
        finished = terminal(second, plan.id)
        assert finished.operation.status == "completed" and finished.operation.request.deadline_at == deadline
        assert finished.trials[0].simulation_id == child and finished.trials[0].matches is None
        assert len(finished.trials) == 4 and finished.operation.outcome.confirmed_failures == 1
        assert workspace.discovery_report(plan.id).executions_used == 4
    finally:
        harness.close()
