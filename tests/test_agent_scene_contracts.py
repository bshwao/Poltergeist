"""Structured ground truth is explicit, bounded, and negotiated by SDK version."""

import pytest
import base64
from pydantic import ValidationError

from shared.models import (
    ActorState, AgentManifest, AgentObservation, AgentReset, AgentRoadRaster,
    AgentSceneContext, AgentSceneObject, AgentStepResult, ControlInput,
)
from simulator.agents.sdk import AgentProtocolError, AgentSession, AutonomousAgent


def raster(**changes):
    return AgentRoadRaster.model_validate({
        "width": 2, "height": 2, "pixels_per_meter": 2,
        "ego_row": 1, "ego_column": 1, "classes": [0, 1, 3, 4],
    } | changes)


@pytest.mark.parametrize("changes", [
    {"classes": [0]}, {"classes": [0, 1, 2, 5]}, {"classes": [False, 1, 2, 3]},
    {"width": 513}, {"ego_row": 2}, {"pixels_per_meter": float("nan")},
    {"axes": "world_north_up"},
])
def test_road_raster_rejects_ambiguous_or_invalid_geometry(changes):
    with pytest.raises(ValidationError):
        raster(**changes)


def test_scene_round_trip_preserves_unknowns_and_explicit_coordinate_axes():
    scene = AgentSceneContext(source="carla_ground_truth", road_raster=raster())
    assert AgentSceneContext.model_validate_json(scene.model_dump_json()) == scene
    assert scene.route is None and scene.objects is None and scene.speed_limit_mps is None
    assert scene.road_raster.axes == "rows_right_columns_forward"
    with pytest.raises(ValidationError):
        AgentObservation(frame=0, time_s=0, dt_s=.05, ego=ActorState(actor_id="ego"),
                         target_speed_mps=10, scene=scene)


def test_lossless_raster_encoding_validates_bytes_without_integer_json_expansion():
    encoded = base64.b64encode(bytes([0, 1, 3, 4])).decode("ascii")
    value = raster(classes=None, data_base64=encoded)
    assert value.classes is None
    assert base64.b64decode(value.data_base64) == bytes([0, 1, 3, 4])
    for changes in ({"classes": [0, 1, 3, 4]}, {"data_base64": "invalid!"},
                    {"data_base64": base64.b64encode(bytes([0, 1, 2, 5])).decode("ascii")},
                    {"data_base64": base64.b64encode(bytes([0])).decode("ascii")}):
        with pytest.raises(ValidationError):
            AgentRoadRaster.model_validate(value.model_dump() | changes)


@pytest.mark.parametrize("version,features", [("1.0", ["structured_scene"]), ("1.1", ["unknown"]),
                                             ("1.1", ["structured_scene", "structured_scene"])])
def test_scene_capability_requires_supported_version_and_unique_known_features(version, features):
    with pytest.raises(ValidationError):
        AgentManifest(protocol_version=version, agent_id="test", name="Test", version="1",
                      entry_point="agent:Agent", required_features=features)


def test_scene_rejects_duplicate_object_identities():
    obj = AgentSceneObject(actor_id="car", kind="vehicle", transform={}, extent={"x": 1, "y": 1, "z": 1})
    with pytest.raises(ValidationError):
        AgentSceneContext(source="carla_ground_truth", objects=[obj, obj])


class Driver(AutonomousAgent):
    def load(self, configuration):
        pass

    def reset(self, context):
        pass

    def step(self, observation):
        return AgentStepResult(protocol_version="1.1", frame=observation.frame, control=ControlInput())

    def close(self):
        pass


@pytest.mark.parametrize("mismatch", ["reset_version", "observation_version", "missing_scene"])
def test_session_enforces_scene_and_version_negotiation(mismatch):
    manifest = AgentManifest(protocol_version="1.1", agent_id="test", name="Test", version="1",
                             entry_point="agent:Driver", required_features=["structured_scene"])
    with AgentSession(Driver(), manifest) as session:
        session.load()
        reset = AgentReset(protocol_version="1.0" if mismatch == "reset_version" else "1.1",
                           seed=0, map_name="Town03", ego_actor_id="ego", fixed_delta_seconds=.05)
        if mismatch == "reset_version":
            with pytest.raises(AgentProtocolError):
                session.reset(reset)
            return
        session.reset(reset)
        observation = AgentObservation(protocol_version="1.0" if mismatch == "observation_version" else "1.1",
                                       frame=0, time_s=0, dt_s=.05, ego=ActorState(actor_id="ego"),
                                       target_speed_mps=10)
        with pytest.raises(AgentProtocolError):
            session.step(observation)
