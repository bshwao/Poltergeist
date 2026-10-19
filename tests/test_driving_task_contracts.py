"""Navigation goals preserve ownership, explicit assistance, and terminal evidence."""

import pytest
from pydantic import ValidationError

from shared.models import (
    ActorState, AgentDiagnostics, AgentManifest, AgentObservation, AgentReset,
    AgentSceneContext, AgentStepResult, ControlAudit, ControlInput, ControlIntervention,
    DrivingOutcome, DrivingTask, SafetyMetrics, Scenario, Simulation, SimulationResult,
    SimulationStatus, Transform, Vector3,
)
from simulator.agents.sdk import AgentProtocolError, AgentSession, AutonomousAgent


def point(x=0.0, y=0.0, z=0.0):
    return Transform(position=Vector3(x=x, y=y, z=z))


def task(**changes):
    return DrivingTask.model_validate({"destination": point(20.0)} | changes)


def manifest(**changes):
    return AgentManifest.model_validate({
        "protocol_version": "1.2", "agent_id": "navigation-test", "name": "Navigation test",
        "version": "1", "entry_point": "agent:Driver",
    } | changes)


def reset(**changes):
    return AgentReset.model_validate({
        "protocol_version": "1.2", "seed": 0, "map_name": "Town03", "ego_actor_id": "ego",
        "fixed_delta_seconds": 0.05, "destination": point(20.0),
        "route": [point(), point(20.0)], "driving_task": task(),
    } | changes)


def observation(**changes):
    return AgentObservation.model_validate({
        "protocol_version": "1.2", "frame": 40, "time_s": 0.0, "dt_s": 0.05,
        "ego": ActorState(actor_id="ego"), "driving_task": task(),
    } | changes)


def outcome(**changes):
    return DrivingOutcome.model_validate({
        "status": "timed_out", "route_owner": "framework", "control_mode": "authoritative",
        "destination": point(20.0), "termination_reason": "duration_elapsed",
    } | changes)


def test_route_normalizes_destination_without_aliasing_the_last_waypoint():
    value = task(destination=None, route=[point(), point(20.0)])
    assert value.destination == value.route[-1]
    value.route[-1].position.x = 200.0
    assert value.destination.position.x == 20.0


def test_destinations_are_world_positions_and_do_not_require_matching_yaw():
    destination = point(20.0)
    destination.rotation.yaw_deg = 90.0
    value = task(destination=destination, route=[point(), point(20.0)])
    assert value.destination.rotation.yaw_deg == 90.0


def test_route_destination_matching_uses_three_dimensional_distance_tolerance():
    value = task(destination=point(20.0, y=0.0000005), route=[point(), point(20.0)])
    assert value.destination.position.y == 0.0000005
    with pytest.raises(ValidationError, match="final route position"):
        task(destination=point(20.0, y=0.0000008, z=0.0000008), route=[point(), point(20.0)])


@pytest.mark.parametrize("changes", [
    {"destination": None}, {"route": [point()]},
    {"route": [point(), point()]},
    {"route": [point(), point(10.0)]},
    {"route_owner": "agent", "route": [point(), point(20.0)]},
    {"completion_radius_m": 0}, {"completion_height_tolerance_m": 0},
    {"target_speed_mps": -1}, {"completion_speed_mps": -1}, {"completion_hold_s": -1},
    {"target_speed_mps": True}, {"completion_speed_mps": "1"},
    {"stop_on_completion": 1}, {"destination": {"position": {"x": float("nan")}}},
])
def test_invalid_driving_tasks_are_rejected(changes):
    with pytest.raises(ValidationError):
        task(**changes)


def test_same_position_with_different_yaw_does_not_create_a_valid_route_segment():
    last = point()
    last.rotation.yaw_deg = 90.0
    with pytest.raises(ValidationError, match="duplicate"):
        task(destination=None, route=[point(), last])


def test_task_speed_is_optional_and_does_not_replace_initial_actor_speed():
    scenario = Scenario(name="Goal", actors=[{"type": "ego_vehicle", "speed_mps": 4.0}], driving_task=task())
    assert scenario.actors[0].speed_mps == 4.0
    assert scenario.driving_task.target_speed_mps is None
    assert Scenario.model_validate_json(scenario.model_dump_json()) == scenario
    assert Scenario(name="Legacy", actors=[{"type": "ego_vehicle"}]).driving_task is None


@pytest.mark.parametrize("version", ["1.0", "1.1"])
def test_tasks_and_agent_planning_require_explicit_protocol_upgrade(version):
    with pytest.raises(ValidationError):
        manifest(protocol_version=version, navigation_mode="agent_planner")
    with pytest.raises(ValidationError):
        reset(protocol_version=version)
    with pytest.raises(ValidationError):
        observation(protocol_version=version, target_speed_mps=10.0)
    with pytest.raises(ValidationError):
        observation(protocol_version=version, driving_task=None)


def test_sdk12_allows_unforced_speed_and_declared_structured_scene():
    configured = manifest(navigation_mode="agent_planner", required_features=["structured_scene"])
    value = observation(scene=AgentSceneContext(source="synthetic"))
    assert configured.protocol_version == "1.2"
    assert value.target_speed_mps is None


@pytest.mark.parametrize("changes", [
    {"destination": None}, {"destination": point(30.0)}, {"route": []}, {"route": [point()]},
    {"driving_task": task(route=[point(), point(20.0)])},
    {"driving_task": task(route_owner="agent")},
])
def test_reset_requires_an_unambiguous_task_descriptor_and_route(changes):
    with pytest.raises(ValidationError):
        reset(**changes)


def test_resolved_single_point_route_is_only_valid_when_already_at_destination():
    assert reset(route=[point(20.0)]).route == [point(20.0)]
    with pytest.raises(ValidationError, match="resolved route"):
        reset(route=[point(19.0)])


def test_resolved_route_cannot_end_at_a_different_destination_than_its_descriptor():
    with pytest.raises(ValidationError, match="end at the driving task destination"):
        reset(destination=point(100.0), driving_task=task(destination=point(100.0)),
              route=[point(), point(10.0)])


def test_resolved_route_rejects_adjacent_duplicate_positions_even_with_a_different_yaw():
    end = point(20.0)
    rotated = point(20.0)
    rotated.rotation.yaw_deg = 90.0
    with pytest.raises(ValidationError, match="adjacent duplicate positions"):
        reset(route=[point(), end, rotated])


def test_agent_owner_gets_destination_without_framework_navigation_hints():
    goal = task(route_owner="agent")
    assert reset(driving_task=goal, route=[]).route == []
    assert observation(driving_task=goal).route_heading_deg is None
    for changes in (
        {"route_heading_deg": 0.0},
        {"scene": AgentSceneContext(source="synthetic", route=[])},
        {"scene": AgentSceneContext(source="synthetic", route=[point(), point(20.0)])},
    ):
        with pytest.raises(ValidationError):
            observation(driving_task=goal, **changes)


def test_assisted_speed_target_can_differ_from_the_immutable_goal():
    # Explicit scripted execution records the assistance separately. The SDK
    # must not confuse a changing control target with a changing task goal.
    value = observation(driving_task=task(target_speed_mps=12.0), target_speed_mps=0.0)
    assert value.driving_task.target_speed_mps == 12.0
    assert value.target_speed_mps == 0.0


class Driver(AutonomousAgent):
    def __init__(self):
        self.resets = []
        self.steps = []

    def load(self, configuration):
        pass

    def reset(self, context):
        self.resets.append(context)

    def step(self, value):
        self.steps.append(value)
        return AgentStepResult(protocol_version="1.2", frame=value.frame, control=ControlInput())

    def close(self):
        pass


def test_agent_route_ownership_requires_a_planner_capability_before_agent_reset():
    driver = Driver()
    with AgentSession(driver, manifest()) as session:
        session.load()
        with pytest.raises(AgentProtocolError, match="agent_planner"):
            session.reset(reset(driving_task=task(route_owner="agent"), route=[]))
    assert driver.resets == []


@pytest.mark.parametrize("owner", ["framework", "agent"])
def test_agent_planner_can_accept_either_navigation_owner(owner):
    goal = task(route_owner=owner)
    with AgentSession(Driver(), manifest(navigation_mode="agent_planner")) as session:
        session.load()
        session.reset(reset(driving_task=goal, route=[] if owner == "agent" else [point(), point(20.0)]))
        assert session.step(observation(driving_task=goal)).frame == 40


@pytest.mark.parametrize("changed", [
    None, task(destination=point(30.0)), task(target_speed_mps=12.0),
    task(completion_radius_m=5.0), task(stop_on_completion=True), task(route_owner="agent"),
])
def test_each_observation_must_preserve_the_reset_goal(changed):
    driver = Driver()
    with AgentSession(driver, manifest(navigation_mode="agent_planner")) as session:
        session.load()
        session.reset(reset())
        with pytest.raises(AgentProtocolError, match="driving task"):
            session.step(observation(driving_task=changed))
    assert driver.steps == []


def test_agent_input_mutation_cannot_rewrite_the_private_task_snapshot():
    driver = Driver()
    context = reset()
    with AgentSession(driver, manifest()) as session:
        session.load()
        session.reset(context)
        driver.resets[0].driving_task.destination.position.x = 99.0
        context.driving_task.completion_radius_m = 99.0
        session.step(observation())
        driver.steps[0].driving_task.destination.position.x = 100.0
        session.step(observation(frame=41, time_s=0.05))


def test_diagnostics_expose_tracking_assistance_without_claiming_reasoning():
    value = AgentDiagnostics(assistance=["waypoint_tracking_controller"])
    assert AgentDiagnostics.model_validate_json(value.model_dump_json()) == value
    with pytest.raises(ValidationError):
        AgentDiagnostics(assistance=[""])


@pytest.mark.parametrize("changes", [
    {"status": "reached"}, {"reached_at_s": 1.0},
    {"termination_reason": "goal_reached"}, {"status": "cancelled"}, {"status": "failed"},
    {"route_progress_m": 1.0}, {"route_length_m": 5.0},
    {"route_progress_m": 6.0, "route_length_m": 5.0},
    {"route_owner": "agent", "resolved_route": [point(), point(20.0)]},
    {"route_owner": "agent", "route_progress_m": 0.0, "route_length_m": 0.0},
    {"final_distance_m": -1}, {"reached_at_s": float("nan")}, {"initial_speed_mps": True},
])
def test_driving_outcome_rejects_inconsistent_or_invalid_terminal_evidence(changes):
    with pytest.raises(ValidationError):
        outcome(**changes)


@pytest.mark.parametrize("status,reason,reached", [
    ("reached", "goal_reached", 1.0), ("reached", "duration_elapsed", 1.0),
    ("timed_out", "duration_elapsed", None), ("cancelled", "cancelled", None),
    ("failed", "execution_failed", None), ("unavailable", "duration_elapsed", None),
])
def test_terminal_navigation_evidence_round_trips(status, reason, reached):
    value = outcome(status=status, termination_reason=reason, reached_at_s=reached)
    assert DrivingOutcome.model_validate_json(value.model_dump_json()) == value


def test_interventions_preserve_proposed_and_applied_controls_and_time_order():
    first = ControlIntervention(frame=1, time_s=0.0, kind="scripted_ego_braking",
                                proposed_control=ControlInput(throttle=0.7), applied_control=ControlInput(brake=1.0))
    second = ControlIntervention(frame=2, time_s=0.05, kind="scripted_ego_speed_target",
                                 proposed_control=ControlInput(), applied_control=ControlInput(), target_speed_mps=0.0)
    assert outcome(control_mode="scripted", interventions=[first, second]).interventions[0].proposed_control.throttle == 0.7
    with pytest.raises(ValidationError, match="ordered"):
        outcome(control_mode="scripted", interventions=[second, first])


def test_new_navigation_contracts_are_present_in_the_exported_schema():
    from shared.contracts import PoltergeistContracts

    definitions = PoltergeistContracts.model_json_schema(mode="serialization")["$defs"]
    for model in (DrivingTask, DrivingOutcome, ControlIntervention, ControlAudit):
        assert definitions[model.__name__]["additionalProperties"] is False


def test_result_control_audit_and_navigation_outcome_cannot_disagree():
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    simulation = Simulation(
        scenario=Scenario(name="Goal", actors=[{"type": "ego_vehicle"}], driving_task=task()),
        mode="test", status=SimulationStatus.COMPLETED, progress=1.0,
        created_at=now, started_at=now, finished_at=now,
    )
    common = {"simulation": simulation, "metrics": SafetyMetrics(), "telemetry_frames": 1,
              "measured_duration_s": 0.05, "driving_outcome": outcome()}
    audit = ControlAudit(mode="authoritative", controller="sdk_agent", navigation="framework_route")
    result = SimulationResult(**common, control_audit=audit)
    assert SimulationResult.model_validate_json(result.model_dump_json()) == result
    assert SimulationResult(**common).control_audit is None
    with pytest.raises(ValidationError, match="control mode"):
        SimulationResult(**common, control_audit=audit.model_copy(update={"mode": "scripted"}))
    intervention = ControlIntervention(frame=1, time_s=0.0, kind="scripted_ego_braking",
                                      proposed_control=ControlInput(), applied_control=ControlInput(brake=1.0))
    with pytest.raises(ValidationError, match="interventions"):
        SimulationResult(**common, control_audit=audit.model_copy(update={"interventions": [intervention]}))


def test_all_run_control_audits_reject_reordered_assistance_even_without_a_goal():
    values = [ControlIntervention(frame=frame, time_s=frame * 0.05,
                                 kind="scripted_ego_speed_target", proposed_control=ControlInput(),
                                 applied_control=ControlInput(), target_speed_mps=0.0)
              for frame in (2, 1)]
    with pytest.raises(ValidationError, match="ordered"):
        ControlAudit(mode="scripted", controller="baseline", navigation="legacy_assisted", interventions=values)


def test_navigation_payload_sizes_are_bounded():
    route = [point(float(index)) for index in range(4097)]
    with pytest.raises(ValidationError, match="4096"):
        task(destination=None, route=route)
    with pytest.raises(ValidationError, match="4096"):
        reset(route=route)
    with pytest.raises(ValidationError, match="4096"):
        outcome(resolved_route=route)


@pytest.mark.parametrize("controller", ["baseline", "legacy_custom"])
def test_authoritative_audits_require_an_sdk_agent(controller):
    with pytest.raises(ValidationError, match="SDK agent"):
        ControlAudit(mode="authoritative", controller=controller, navigation="framework_route")


def test_authoritative_audits_and_outcomes_cannot_claim_scripted_assistance():
    intervention = ControlIntervention(frame=1, time_s=0.0, kind="scripted_ego_braking",
                                      proposed_control=ControlInput(), applied_control=ControlInput(brake=1.0))
    with pytest.raises(ValidationError, match="no scripted interventions"):
        ControlAudit(mode="authoritative", controller="sdk_agent", navigation="framework_route",
                     interventions=[intervention])
    with pytest.raises(ValidationError, match="must not contain scripted interventions"):
        outcome(interventions=[intervention])


@pytest.mark.parametrize("status,reached,reason", [
    ("reached", 1.0, "cancelled"), ("reached", 1.0, "execution_failed"),
    ("unavailable", None, "cancelled"), ("unavailable", None, "execution_failed"),
])
def test_standalone_outcome_statuses_reject_conflicting_termination_reasons(status, reached, reason):
    with pytest.raises(ValidationError):
        outcome(status=status, reached_at_s=reached, termination_reason=reason)
