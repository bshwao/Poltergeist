"""Functional control behavior and the custom-controller trust boundary."""

from dataclasses import replace
import math

from pydantic import ValidationError
import pytest

from shared.models import ActorState, BoundingBoxExtent, ControlInput, Rotation, Transform, Vector3
from simulator.agents import BaseAgent, BaselineAgent, CustomAgent
from simulator.protocols import DrivingObservation


def state(actor_id="ego", *, x=0.0, y=0.0, yaw=0.0, speed=0.0):
    angle = math.radians(yaw)
    return ActorState(
        actor_id=actor_id,
        transform=Transform(position=Vector3(x=x, y=y), rotation=Rotation(yaw_deg=yaw)),
        velocity_mps=Vector3(x=speed * math.cos(angle), y=speed * math.sin(angle)),
        bounding_box_extent=BoundingBoxExtent(x=2.0, y=1.0, z=1.0),
    )


def observation(**changes):
    return replace(DrivingObservation(
        ego=state(), actors=(), dt_s=0.05, target_speed_mps=12.0,
        route_heading_deg=0.0, traffic_light_red=False,
    ), **changes)


def test_base_agent_is_abstract():
    with pytest.raises(TypeError):
        BaseAgent()


def test_speed_control_accelerates_brakes_and_holds():
    agent = BaselineAgent()
    assert agent.run_step(observation()).throttle == 1.0
    assert agent.run_step(observation(ego=state(speed=20.0))).brake == 1.0
    cruise = agent.run_step(observation(ego=state(speed=12.0)))
    assert cruise.throttle == cruise.brake == 0.0
    assert agent.run_step(observation(target_speed_mps=0.0)).brake == 1.0


def test_configured_target_and_wrapped_route_heading():
    agent = BaselineAgent(target_speed_mps=4.0)
    control = agent.run_step(observation(ego=state(yaw=179.0, speed=8.0), route_heading_deg=-179.0))
    assert control.throttle == 0.0
    assert control.brake == 0.5
    assert control.steer == pytest.approx(2.0 / 45.0)
    assert agent.run_step(observation(route_heading_deg=90.0)).steer == 1.0
    assert agent.run_step(observation(route_heading_deg=-90.0)).steer == -1.0


@pytest.mark.parametrize("field", ["transform", "velocity_mps"])
def test_missing_critical_ego_data_holds_brake(field):
    ego = state()
    setattr(ego, field, None)
    assert BaselineAgent().run_step(observation(ego=ego)) == ControlInput(brake=1.0)


def test_red_light_brakes_and_missing_heading_uses_current_heading():
    control = BaselineAgent().run_step(observation(
        ego=state(yaw=50.0), route_heading_deg=None, traffic_light_red=True,
    ))
    assert control == ControlInput(brake=1.0)


def test_ttc_emergency_braking_for_stopped_and_crossing_actors():
    agent = BaselineAgent()
    stopped = state("lead", x=14.0)
    assert agent.run_step(observation(ego=state(speed=10.0), actors=(stopped,))).brake == 1.0
    crossing = state("crossing", x=10.0, y=-10.0, yaw=90.0, speed=10.0)
    assert agent.run_step(observation(ego=state(speed=10.0), actors=(crossing,))).brake == 1.0


def test_close_ahead_actor_brakes_but_safe_parallel_and_rear_actors_do_not():
    agent = BaselineAgent()
    assert agent.run_step(observation(actors=(state("lead", x=5.0),))).brake == 1.0
    assert agent.run_step(observation(actors=(state("rear", x=-5.0),))).throttle == 1.0
    assert agent.run_step(observation(ego=state(speed=10.0), actors=(
        state("parallel", x=10.0, y=5.0, speed=10.0),
    ))).throttle > 0.0


def test_missing_noncritical_actor_geometry_does_not_invent_contact():
    actor = ActorState(actor_id="unmeasured")
    assert BaselineAgent().run_step(observation(actors=(actor,))).throttle == 1.0


@pytest.mark.parametrize("bad", [-1.0, float("nan"), float("inf"), True, "12", 10 ** 400])
def test_target_speed_validation(bad):
    with pytest.raises(ValueError):
        BaselineAgent(target_speed_mps=bad)
    with pytest.raises(ValueError):
        BaselineAgent().run_step(observation(target_speed_mps=bad))


@pytest.mark.parametrize("dt", [0.0, -0.1, float("nan"), True])
def test_invalid_observation_timestep_rejected(dt):
    with pytest.raises(ValueError):
        BaselineAgent().run_step(observation(dt_s=dt))


def test_custom_controller_observes_input_and_accepts_valid_mapping():
    observed = []
    def controller(value):
        observed.append(value)
        return {"throttle": 0.4, "steer": -0.2}
    value = observation()
    assert CustomAgent(controller).run_step(value) == ControlInput(throttle=0.4, steer=-0.2)
    assert observed == [value]


def test_custom_controller_requires_callable():
    with pytest.raises(TypeError):
        CustomAgent(None)
    with pytest.raises(TypeError):
        CustomAgent()


@pytest.mark.parametrize("result", [None, [], 0.5])
def test_custom_controller_rejects_invalid_return_type(result):
    with pytest.raises(TypeError):
        CustomAgent(lambda _: result).run_step(observation())


@pytest.mark.parametrize("result", [{"throttle": 2.0}, {"steer": float("nan")}, {"brake": True}, {"unknown": 1}])
def test_custom_controller_validates_controls(result):
    with pytest.raises(ValidationError):
        CustomAgent(lambda _: result).run_step(observation())


def test_custom_controller_revalidates_mutated_model():
    control = ControlInput()
    control.throttle = 5.0
    with pytest.raises(ValidationError):
        CustomAgent(lambda _: control).run_step(observation())


def test_custom_controller_exceptions_remain_actionable():
    def broken(_):
        raise RuntimeError("model inference failed")
    with pytest.raises(RuntimeError, match="model inference failed"):
        CustomAgent(broken).run_step(observation())
