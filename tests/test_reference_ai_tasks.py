"""Explicit goals add declared tracking assistance without skipping inference."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from shared.models import (
    ActorState, AgentObservation, AgentReset, AgentSceneContext, ControlInput,
    DrivingTask, Rotation, Transform, Vector3,
)
from simulator.agents.reference_ai import ReferenceAIAgent
from simulator.agents.reference_ai.features import PlanTFeatureBuilder
from simulator.agents.sdk import AgentCompatibilityError


def pose(x=0.0, y=0.0, z=0.0, yaw=0.0):
    return Transform(position=Vector3(x=x, y=y, z=z), rotation=Rotation(yaw_deg=yaw))


def goal(**changes):
    return DrivingTask.model_validate({"destination": pose(10.0)} | changes)


def observation(*, task=None, route=None, position=None, speed=8.0):
    return AgentObservation(
        protocol_version="1.2", frame=100, time_s=0.0, dt_s=0.05,
        ego=ActorState(actor_id="ego", transform=position or pose(), velocity_mps=Vector3(x=speed)),
        driving_task=task, target_speed_mps=task.target_speed_mps if task else None,
        scene=AgentSceneContext(source="synthetic", route=route if route is not None else [pose(), pose(10.0)],
                                objects=[], speed_limit_mps=50.0 / 3.6),
    )


def reset(task):
    return AgentReset(protocol_version="1.2", seed=0, map_name="synthetic", ego_actor_id="ego",
                      fixed_delta_seconds=0.05, destination=task.destination,
                      route=[pose(), task.destination], driving_task=task)


def test_finite_task_route_repeats_its_actual_endpoint_without_extrapolating():
    value = observation(task=goal())
    result = PlanTFeatureBuilder(input_bev=False).build(value)
    assert result.route[:8] == [[2.5 + index, 0.0] for index in range(8)]
    assert result.route[8:] == [[10.0, 0.0]] * 12
    assert result.route_endpoint_padded is True
    assert max(row[0] for row in result.route) == 10.0
    value.driving_task = None
    with pytest.raises(ValueError, match="21.5 metres"):
        PlanTFeatureBuilder(input_bev=False).build(value)


def test_finite_task_corner_route_keeps_its_geometry_before_endpoint_padding():
    value = observation(task=goal(destination=pose(5.0, 5.0)), route=[pose(), pose(5.0), pose(5.0, 5.0)])
    result = PlanTFeatureBuilder(input_bev=False).build(value)
    assert result.route[0] == [2.5, 0.0]
    assert result.route[3] == [5.0, 0.5]
    assert result.route[-1] == [5.0, 5.0]


def test_single_destination_route_is_an_explicit_fixed_size_terminal_input():
    value = observation(task=goal(), route=[pose(10.0)], position=pose(10.0))
    result = PlanTFeatureBuilder(input_bev=False).build(value)
    assert result.route == [[0.0, 0.0]] * 20
    assert result.route_endpoint_padded is True
    value.driving_task = None
    with pytest.raises(ValueError, match="route"):
        PlanTFeatureBuilder(input_bev=False).build(value)


def test_long_task_routes_and_legacy_routes_produce_identical_neural_inputs():
    value = observation(task=goal(destination=pose(40.0)), route=[pose(), pose(40.0)])
    task_features = PlanTFeatureBuilder(input_bev=False).build(value)
    value.driving_task = None
    legacy_features = PlanTFeatureBuilder(input_bev=False).build(value)
    assert task_features == legacy_features
    assert task_features.route_endpoint_padded is False


@pytest.mark.parametrize("changes,expected", [
    ({}, None), ({"target_speed_mps": 3.0}, 3.0),
    ({"completion_speed_mps": 0.0}, (4.0 * 8.0) ** 0.5),
    ({"completion_speed_mps": 1.0}, (1.0 + 4.0 * 8.0) ** 0.5),
    ({"target_speed_mps": 3.0, "completion_speed_mps": 0.0}, 3.0),
])
def test_task_tracking_speed_caps_are_optional_and_derived_from_explicit_goals(changes, expected):
    cap, assistance, metadata = ReferenceAIAgent._tracking_cap(observation(task=goal(**changes)))
    if expected is None:
        assert cap is None and assistance == []
    else:
        assert cap == pytest.approx(expected)
        assert assistance
    assert metadata["task_destination_distance_m"] == 10.0


def test_goal_stopping_uses_actual_distance_and_separate_elevation_guard():
    task = goal(completion_speed_mps=0.0)
    inside = observation(task=task, position=pose(9.0))
    cap, assistance, metadata = ReferenceAIAgent._tracking_cap(inside)
    assert cap == 0.0
    assert assistance == ["task_destination_approach_speed_cap"]
    assert metadata["task_approach_deceleration_mps2"] == 2.0
    above = observation(task=task, position=pose(9.0, z=3.0))
    cap, assistance, metadata = ReferenceAIAgent._tracking_cap(above)
    assert cap is None and assistance == []
    assert metadata["task_destination_height_error_m"] == 3.0
    assert metadata["task_approach_speed_cap_mps"] is None


class FakeArray:
    def __init__(self, rows):
        self.rows = rows

    def __iter__(self):
        return iter(self.rows)

    def __getitem__(self, index):
        return self.rows[index]

    def tolist(self):
        return self.rows


class FakeTensor:
    def __init__(self, rows):
        self.rows = rows

    def __getitem__(self, index):
        assert index == 0
        return self

    def detach(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return FakeArray(self.rows)


class CountingModel:
    """Unit fixture only; real checkpoint inference has separate opt-in tests."""

    def __init__(self):
        self.calls = 0

    def __call__(self, batch):
        self.calls += 1
        return FakeTensor([[float(index), 0.0] for index in range(1, 21)]), FakeTensor([[2.5 * index, 0.0] for index in range(1, 9)])

    def parameters(self):
        return iter([SimpleNamespace(dtype="float32")])


class TrackingFixture:
    def __init__(self, learned_speed):
        self.learned_speed = learned_speed

    def reset(self):
        pass

    def control(self, path, waypoints, current_speed, *, speed_cap_mps=None):
        upstream = min(self.learned_speed, 0.1) if current_speed < 0.01 else self.learned_speed
        tracking = upstream if speed_cap_mps is None else min(upstream, speed_cap_mps)
        return ControlInput(throttle=1.0 if tracking >= current_speed else 0.0,
                            brake=1.0 if tracking < current_speed else 0.0), upstream


def prepared_fixture(monkeypatch, task, *, learned_speed=10.0):
    import simulator.agents.reference_ai.agent as agent_module

    monkeypatch.setattr(agent_module, "tensor_batch", lambda *args: None)
    agent = ReferenceAIAgent()
    agent._model = CountingModel()
    agent._features = PlanTFeatureBuilder(input_bev=False)
    agent._controller = TrackingFixture(learned_speed)
    agent._device = SimpleNamespace(type="cpu")
    agent._torch = SimpleNamespace(inference_mode=nullcontext, __version__="unit-fixture", version=SimpleNamespace(cuda=None))
    agent.reset(reset(task))
    return agent


def test_each_task_tick_still_infers_and_declares_speed_and_route_assistance(monkeypatch):
    task = goal(target_speed_mps=3.0)
    agent = prepared_fixture(monkeypatch, task)
    first = agent.step(observation(task=task))
    second = agent.step(observation(task=task))
    assert agent._model.calls == 2
    assert first.control.brake == 1.0
    assert first.diagnostics.assistance == ["deterministic_waypoint_tracking", "finite_route_endpoint_padding", "task_target_speed_cap"]
    assert first.diagnostics.metadata["learned_desired_speed_mps"] == 10.0
    assert first.diagnostics.metadata["upstream_tracking_desired_speed_mps"] == 10.0
    assert first.diagnostics.metadata["tracking_target_speed_mps"] == 3.0
    assert first.diagnostics.target_speed_mps == 3.0
    assert second.diagnostics.metadata["route_endpoint_padded"] is True


def test_goal_braking_assistance_is_visible_and_never_accelerates_above_learned_speed(monkeypatch):
    task = goal(completion_speed_mps=0.0)
    agent = prepared_fixture(monkeypatch, task)
    result = agent.step(observation(task=task, position=pose(9.0)))
    assert agent._model.calls == 1
    assert result.diagnostics.target_speed_mps == 0.0
    assert "task_destination_approach_speed_cap" in result.diagnostics.assistance
    assert result.diagnostics.metadata["learned_desired_speed_mps"] == 10.0
    assert result.control.brake == 1.0
    slow_agent = prepared_fixture(monkeypatch, goal(target_speed_mps=12.0), learned_speed=2.0)
    slow = slow_agent.step(observation(task=goal(target_speed_mps=12.0)))
    assert slow.diagnostics.target_speed_mps == 2.0
    assert "task_target_speed_cap" not in slow.diagnostics.assistance


def test_standstill_startup_assistance_preserves_the_actual_raw_neural_speed(monkeypatch):
    task = goal(target_speed_mps=3.0)
    agent = prepared_fixture(monkeypatch, task)
    result = agent.step(observation(task=task, speed=0.0))
    assert result.diagnostics.metadata["learned_desired_speed_mps"] == 10.0
    assert result.diagnostics.metadata["upstream_tracking_desired_speed_mps"] == 0.1
    assert result.diagnostics.metadata["tracking_target_speed_mps"] == 0.1
    assert result.diagnostics.target_speed_mps == 0.1
    assert "upstream_startup_speed_limit" in result.diagnostics.assistance
    assert "task_target_speed_cap" not in result.diagnostics.assistance
    assert agent._model.calls == 1


def test_reference_driver_rejects_global_route_ownership_instead_of_claiming_a_planner(monkeypatch):
    task = goal(route_owner="agent")
    agent = prepared_fixture(monkeypatch, goal())
    context = AgentReset(protocol_version="1.2", seed=0, map_name="synthetic", ego_actor_id="ego",
                         fixed_delta_seconds=0.05, destination=task.destination, driving_task=task)
    with pytest.raises(AgentCompatibilityError, match="global route planning"):
        agent.reset(context)


def test_actual_tracker_returns_raw_learned_speed_while_applying_an_explicit_cap():
    np = pytest.importorskip("numpy")
    pytest.importorskip("scipy")
    from simulator.agents.reference_ai.controller import PlanTController

    path = np.stack((np.arange(1.0, 21.0), np.zeros(20)), axis=1)
    waypoints = np.stack((np.arange(1.0, 9.0) * 2.5, np.zeros(8)), axis=1)
    raw_control, raw_target = PlanTController().control(path, waypoints, 8.0)
    capped, learned_target = PlanTController().control(path, waypoints, 8.0, speed_cap_mps=3.0)
    assert learned_target == raw_target == 10.0
    assert raw_control.throttle > 0.0
    assert capped.brake == 1.0 and capped.throttle == 0.0
    assert capped.steer == raw_control.steer
