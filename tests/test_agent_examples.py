"""Reference and editable starter agents exercise the public SDK lifecycle."""

import importlib.util
import json
import math
from pathlib import Path
import shutil

import pytest

from shared.models import (
    ActorState, AgentManifest, AgentObservation, AgentReset, BoundingBoxExtent,
    ControlInput, Rotation, Transform, Vector3,
)
from simulator.agents import BaselineAgent
from simulator.agents.sdk import AgentSession, AutonomousAgent
from simulator.agents.sdk_reference import BASELINE_AGENT_MANIFEST, BaselineSDKAgent
from simulator.protocols import DrivingObservation


STARTER = Path(__file__).resolve().parents[1] / "examples" / "agents" / "starter"


def actor(actor_id="ego", *, x=0.0, y=0.0, yaw=0.0, speed=0.0):
    angle = math.radians(yaw)
    return ActorState(
        actor_id=actor_id,
        transform=Transform(position=Vector3(x=x, y=y), rotation=Rotation(yaw_deg=yaw)),
        velocity_mps=Vector3(x=speed * math.cos(angle), y=speed * math.sin(angle)),
        bounding_box_extent=BoundingBoxExtent(x=2.0, y=1.0, z=1.0),
    )


def observation(**changes):
    return AgentObservation.model_validate({
        "frame": 0,
        "time_s": 0.0,
        "dt_s": 0.05,
        "ego": actor(),
        "actors": [],
        "target_speed_mps": 12.0,
        "route_heading_deg": 0.0,
        "traffic_light_red": False,
        **changes,
    })


def reset_context(**changes):
    return AgentReset.model_validate({
        "seed": 7,
        "map_name": "Town03",
        "ego_actor_id": "ego",
        "fixed_delta_seconds": 0.05,
        **changes,
    })


@pytest.mark.parametrize("changes", [
    {},
    {"ego": actor(speed=20.0)},
    {"route_heading_deg": 30.0},
    {"traffic_light_red": True},
    {"actors": [actor("lead", x=5.0)]},
    {"ego": actor(speed=10.0), "actors": [actor("crossing", x=10.0, y=-10.0, yaw=90.0, speed=10.0)]},
    {"ego": actor(speed=10.0), "actors": [actor("parallel", x=10.0, y=5.0, speed=10.0)]},
])
def test_reference_preserves_existing_baseline_controls(changes):
    value = observation(**changes)
    expected = BaselineAgent().run_step(DrivingObservation(
        ego=value.ego,
        actors=tuple(value.actors),
        dt_s=value.dt_s,
        target_speed_mps=value.target_speed_mps,
        route_heading_deg=value.route_heading_deg,
        traffic_light_red=value.traffic_light_red,
    ))
    with AgentSession(BaselineSDKAgent(), BASELINE_AGENT_MANIFEST) as session:
        session.load()
        session.reset(reset_context())
        result = session.step(value)
    assert result.frame == value.frame
    assert result.control == expected
    assert result.diagnostics is None


def test_reference_configuration_is_copied_and_restored_on_episode_reset():
    configuration = {"target_speed_mps": 4.0}
    agent = BaselineSDKAgent()
    agent.load(configuration)
    configuration["target_speed_mps"] = 40.0
    agent.reset(reset_context())
    value = observation(ego=actor(speed=8.0))
    expected = ControlInput(brake=0.5)
    assert agent.step(value).control == expected
    agent.reset(reset_context(seed=123))
    assert agent.step(value).control == expected
    agent.close()
    with pytest.raises(RuntimeError, match="Load"):
        agent.reset(reset_context())


@pytest.mark.parametrize("configuration", [
    {"target_speed_mps": -1.0},
    {"minimum_clearance_m": True},
    {"emergency_ttc_s": float("nan")},
    {"unknown_option": 1},
    [],
])
def test_reference_rejects_invalid_or_unknown_configuration(configuration):
    agent = BaselineSDKAgent()
    with pytest.raises((TypeError, ValueError)):
        agent.load(configuration)
    agent.close()


def test_reference_requires_load_reset_and_known_actor_observation():
    agent = BaselineSDKAgent()
    with pytest.raises(RuntimeError, match="Load and reset"):
        agent.step(observation())
    agent.load({})
    with pytest.raises(RuntimeError, match="Load and reset"):
        agent.step(observation())
    agent.reset(reset_context())
    with pytest.raises(ValueError, match="actors=None means unavailable"):
        agent.step(observation(actors=None))
    assert agent.step(observation(actors=[])).control.throttle == 1.0
    agent.close()
    agent.close()


def test_starter_package_can_be_copied_loaded_and_exercised(tmp_path):
    package = tmp_path / "my_agent"
    shutil.copytree(STARTER, package)
    manifest = AgentManifest.model_validate_json((package / "agent.json").read_text(encoding="utf-8"))
    assert manifest.observation_mode == "simulator_state"
    assert manifest.control_mode == "vehicle_control"
    assert manifest.sensors == manifest.assets == []
    assert (package / manifest.requirements_file).is_file()

    module_name, class_name = manifest.entry_point.split(":")
    spec = importlib.util.spec_from_file_location("trusted_starter_for_test", package / f"{module_name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    agent_class = getattr(module, class_name)
    assert issubclass(agent_class, AutonomousAgent)

    with AgentSession(agent_class(), manifest) as session:
        session.load()
        session.reset(reset_context(protocol_version=manifest.protocol_version))
        initial = session.step(observation(protocol_version=manifest.protocol_version))
        stopped = session.step(observation(protocol_version=manifest.protocol_version, frame=1, time_s=0.05, traffic_light_red=True))
        session.reset(reset_context(protocol_version=manifest.protocol_version, seed=8))
        restarted = session.step(observation(protocol_version=manifest.protocol_version))
    assert initial.control.throttle == restarted.control.throttle == 1.0
    assert stopped.control == ControlInput(brake=1.0)
    assert initial.diagnostics is stopped.diagnostics is restarted.diagnostics is None


def test_reference_manifest_declares_no_learned_sensor_or_weight_capability():
    payload = json.loads(BASELINE_AGENT_MANIFEST.model_dump_json())
    assert payload["entry_point"] == "simulator.agents.sdk_reference:BaselineSDKAgent"
    assert payload["observation_mode"] == "simulator_state"
    assert payload["sensors"] == payload["assets"] == []
