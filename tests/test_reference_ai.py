"""Reference AI contracts and preprocessing; real weights are an opt-in check."""

import importlib.util
import base64
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import pytest

from shared.models import (
    ActorState, AgentManifest, AgentObservation, AgentReset, AgentRoadRaster,
    AgentSceneContext, AgentSceneObject, BoundingBoxExtent, Rotation, Transform, Vector3,
)
from simulator.agents.package import inspect_agent_package
from simulator.agents.reference_ai import ReferenceAIAgent
from simulator.agents.reference_ai.checkpoint import CHECKPOINT_SHA256, DEFAULT_CHECKPOINT, verify_checkpoint
from simulator.agents.reference_ai.features import PlanTFeatureBuilder, relative_box_position, relative_position, speed_category
from simulator.agents.sdk import AgentLifecycleError
from simulator.transforms import rotate

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "examples" / "agents" / "reference_ai"


def pose(x=0., y=0., z=0., yaw=0.):
    return Transform(position=Vector3(x=x, y=y, z=z), rotation=Rotation(yaw_deg=yaw))


def scene_object(actor_id="lead", *, kind="vehicle", x=12., y=0., yaw=0., speed=0., state=None):
    return AgentSceneObject(
        actor_id=actor_id, kind=kind, transform=pose(x, y, yaw=yaw),
        extent=BoundingBoxExtent(x=2., y=1., z=1.),
        velocity_mps=Vector3(x=speed * math.cos(math.radians(yaw)), y=speed * math.sin(math.radians(yaw))),
        traffic_light_state=state,
    )


def observation(*, objects=None, speed=8., yaw=0., route_y=0., raster=True):
    road = None
    if raster:
        classes = [0] * (128 * 128)
        for row in range(56, 72):
            classes[row * 128:(row + 1) * 128] = [3 if row in (56, 71) else 1] * 128
        road = AgentRoadRaster(width=128, height=128, pixels_per_meter=2.,
                               classes=classes, ego_row=64., ego_column=64.)
    return AgentObservation(
        protocol_version="1.1", frame=100, time_s=0., dt_s=0.05,
        ego=ActorState(actor_id="ego", transform=pose(yaw=yaw),
                       velocity_mps=Vector3(x=speed * math.cos(math.radians(yaw)),
                                           y=speed * math.sin(math.radians(yaw)))),
        actors=[], target_speed_mps=12.,
        scene=AgentSceneContext(
            source="synthetic", route=[pose(x=float(i), y=route_y) for i in range(41)],
            objects=objects or [], speed_limit_mps=50. / 3.6, road_raster=road,
        ),
    )


def test_reference_package_declares_learned_scene_capabilities():
    package = inspect_agent_package(PACKAGE)
    assert package.manifest.protocol_version == "1.1"
    assert package.manifest.required_features == ["structured_scene"]
    assert package.manifest.sensors == []
    assert package.manifest.agent_id == "poltergeist-reference-ai"
    assert package.manifest.configuration == {"device": "auto", "torch_threads": 2}


def test_reference_import_is_lightweight_and_does_not_import_torch():
    result = subprocess.run(
        [sys.executable, "-c", "import sys; from simulator.agents.reference_ai import ReferenceAIAgent; "
         "assert not any(x in sys.modules for x in ('torch', 'transformers', 'timm', 'carla', 'scipy'))"],
        cwd=ROOT, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_default_checkpoint_stays_under_ignored_storage():
    assert DEFAULT_CHECKPOINT == ROOT / "storage" / "models" / "plant2" / "epoch=029_final_1.ckpt"


def test_checkpoint_verification_precedes_pickle_or_neural_imports(tmp_path):
    with pytest.raises(FileNotFoundError, match="setup_reference_ai"):
        verify_checkpoint(tmp_path / "missing.ckpt")
    wrong = tmp_path / "wrong.ckpt"
    wrong.write_bytes(b"not a checkpoint")
    with pytest.raises(ValueError, match="unexpected size"):
        ReferenceAIAgent().load({"checkpoint_path": str(wrong)})


@pytest.mark.parametrize("config", [
    {"device": "gpu"}, {"torch_threads": True}, {"torch_threads": 0},
    {"torch_threads": 17}, {"unknown": 1}, {"checkpoint_path": ""}, [],
])
def test_configuration_rejects_unknown_or_invalid_options_before_loading(config):
    with pytest.raises(ValueError):
        ReferenceAIAgent().load(config)


def test_reference_lifecycle_requires_loading_and_close_is_idempotent():
    agent = ReferenceAIAgent()
    with pytest.raises(AgentLifecycleError, match="Load"):
        agent.reset(AgentReset(seed=0, map_name="Town03", ego_actor_id="ego", fixed_delta_seconds=0.05))
    with pytest.raises(AgentLifecycleError, match="Load and reset"):
        agent.step(observation())
    agent.close()
    agent.close()


@pytest.mark.parametrize("limit,category", [(50./3.6, 0), (80./3.6, 1), (100./3.6, 2), (120./3.6, 3), (30./3.6, 0)])
def test_speed_limit_categories_match_training_and_explicit_nearest_adaptation(limit, category):
    assert speed_category(limit) == category


def test_world_objects_use_ego_forward_right_yaw_kmh_width_and_length():
    value = observation(objects=[scene_object(x=0., y=10., yaw=90., speed=3.)], yaw=90.)
    result = PlanTFeatureBuilder(input_bev=False).build(value)
    assert result.objects[0] == pytest.approx([1., 10., 0., 0., 10.8, 2., 4.])
    assert relative_position(Vector3(x=-3., y=4.), pose(yaw=90.))[:2] == pytest.approx((4., 3.))
    assert result.ego_speed_mps == pytest.approx(8.)


@pytest.mark.parametrize("kind,number", [("vehicle", 1.), ("pedestrian", 2.), ("static", 3.), ("stop_sign", 4.), ("traffic_light", 5.), ("emergency", 6.)])
def test_object_class_tokens_are_explicit(kind, number):
    result = PlanTFeatureBuilder(input_bev=False).build(observation(objects=[scene_object(kind=kind, speed=1., state="red")]))
    assert result.objects[0][0] == number


def test_model_uses_supplied_map_route_and_does_not_replace_it_with_heading():
    result = PlanTFeatureBuilder(input_bev=False).build(observation(route_y=5.))
    assert result.route[0] == [2.5, 5.]
    assert result.route[-1] == [21.5, 5.]


def test_route_resampling_follows_corners_and_rejects_missing_forward_coverage():
    value = observation()
    value.scene.route = [pose(x=float(index)) for index in range(11)] + [pose(x=10., y=float(index)) for index in range(1, 31)]
    route = PlanTFeatureBuilder(input_bev=False).build(value).route
    assert route[0] == pytest.approx([2.5, 0.])
    assert route[8] == pytest.approx([10., 0.5])
    assert route[-1] == pytest.approx([10., 11.5])
    value.scene.route = [pose(), pose(x=20.)]
    with pytest.raises(ValueError, match="21.5 metres"):
        PlanTFeatureBuilder(input_bev=False).build(value)


def test_object_box_transform_retains_pitch_and_roll_like_upstream():
    ego = Transform(rotation=Rotation(pitch_deg=20., yaw_deg=60., roll_deg=15.))
    local = Vector3(x=10., y=3., z=2.)
    world = rotate(local, ego.rotation)
    assert relative_box_position(world, ego) == pytest.approx((10., 3., 2.))


@pytest.mark.parametrize("field,value,message", [
    ("route", None, "route"), ("route", [], "route"),
    ("objects", None, "objects"), ("speed_limit_mps", None, "speed limit"),
    ("road_raster", None, "road raster"),
])
def test_missing_features_are_errors_instead_of_fabricated_inputs(field, value, message):
    item = observation()
    setattr(item.scene, field, value)
    with pytest.raises(ValueError, match=message):
        PlanTFeatureBuilder(input_bev=True).build(item)


def test_missing_ego_or_dynamic_motion_is_an_error():
    item = observation(objects=[scene_object()])
    item.scene.objects[0].velocity_mps = None
    with pytest.raises(ValueError, match="velocity"):
        PlanTFeatureBuilder(input_bev=False).build(item)
    item.ego.transform = None
    with pytest.raises(ValueError, match="ego pose"):
        PlanTFeatureBuilder(input_bev=False).build(item)


def test_input_range_is_front_ellipse_and_rear_circle():
    objects = [scene_object("front", x=99.), scene_object("far-front", x=101.),
               scene_object("rear", x=-49.), scene_object("far-rear", x=-51.),
               scene_object("side", x=0., y=51.)]
    features = PlanTFeatureBuilder(input_bev=False).build(observation(objects=objects))
    assert [row[1] for row in features.objects] == [99., -49.]


def test_stationary_walker_history_is_preserved_until_reset_like_upstream():
    builder = PlanTFeatureBuilder(input_bev=False)
    stationary = observation(objects=[scene_object(kind="pedestrian")])
    assert builder.build(stationary).objects == []
    assert len(builder.build(observation(objects=[scene_object(kind="pedestrian", speed=1.)])).objects) == 1
    assert len(builder.build(stationary).objects) == 1
    builder.reset()
    assert builder.build(stationary).objects == []


def test_green_lights_are_not_red_or_yellow_tokens():
    objects = [scene_object("red", kind="traffic_light", state="red"),
               scene_object("yellow", kind="traffic_light", state="yellow"),
               scene_object("green", kind="traffic_light", state="green")]
    assert len(PlanTFeatureBuilder(input_bev=False).build(observation(objects=objects)).objects) == 2


@pytest.mark.parametrize("state", [None, "unknown"])
def test_unknown_light_state_is_not_silently_treated_as_green(state):
    with pytest.raises(ValueError, match="observed traffic light state"):
        PlanTFeatureBuilder(input_bev=False).build(observation(
            objects=[scene_object(kind="traffic_light", state=state)]))


def test_stop_sign_clears_after_a_stop_and_rearms_after_departing():
    builder = PlanTFeatureBuilder(input_bev=False)
    stopped = observation(objects=[scene_object(kind="stop_sign", x=2.)], speed=0.)
    assert builder.build(stopped).objects == []
    assert builder.build(observation(objects=[scene_object(kind="stop_sign", x=8.)])).objects == []
    assert len(builder.build(observation(objects=[scene_object(kind="stop_sign", x=12.)])).objects) == 1


def test_road_semantic_preprocessing_rotates_into_upstream_model_axes():
    value = observation()
    value.scene.road_raster.classes = [0] * (128 * 128)
    # A landmark to the front/right in SDK axes must land left/up in PlanT.
    for row in range(80, 88):
        for col in range(90, 98):
            value.scene.road_raster.classes[row * 128 + col] = 4
    raster = PlanTFeatureBuilder(input_bev=True).build(value).bev_classes
    locations = [(r, c) for r in range(128) for c in range(128) if raster[r][c] == 4]
    assert locations
    assert all(r < 64 and c < 64 for r, c in locations)
    assert sum(cell == 1 for row in raster for cell in row) == 0


def test_raster_support_and_geometry_must_be_explicit():
    value = observation()
    value.scene.road_raster.pixels_per_meter = 4.
    with pytest.raises(ValueError, match="2 pixels/metre"):
        PlanTFeatureBuilder(input_bev=True).build(value)


def test_lossless_compact_raster_produces_identical_neural_input():
    value = observation()
    expected = PlanTFeatureBuilder(input_bev=True).build(value).bev_classes
    raster = value.scene.road_raster
    payload = raster.model_dump(exclude={"classes", "data_base64"})
    value.scene.road_raster = AgentRoadRaster(**payload, data_base64=base64.b64encode(bytes(raster.classes)).decode("ascii"))
    assert value.scene.road_raster.classes is None
    actual = PlanTFeatureBuilder(input_bev=True).build(value).bev_classes
    assert actual == expected


def test_object_token_limit_is_bounded_and_disclosed():
    objects = [scene_object(str(index), x=1. + index / 100.) for index in range(500)]
    features = PlanTFeatureBuilder(input_bev=False).build(observation(objects=objects))
    assert len(features.objects) == 480
    assert features.omitted_objects == 20


def test_upstream_tracking_controller_uses_predictions_and_resets_history():
    np = pytest.importorskip("numpy")
    pytest.importorskip("scipy")
    from simulator.agents.reference_ai.controller import PlanTController

    controller = PlanTController()
    path = np.stack((np.arange(1., 21.), np.zeros(20)), axis=1)
    waypoints = np.stack((np.arange(1., 9.) * 2.5, np.zeros(8)), axis=1)
    straight, target = controller.control(path, waypoints, 8.)
    assert target == pytest.approx(10.)
    assert straight.throttle > 0. and straight.steer == pytest.approx(0.)
    curved = path.copy()
    curved[:, 1] = np.arange(1., 21.) * 0.1
    turning, _ = controller.control(curved, waypoints, 8.)
    assert turning.steer > 0.
    stopped, target = controller.control(path, np.zeros((8, 2)), 8.)
    assert target == 0. and stopped.brake == 1.
    controller.reset()
    assert controller.error_history == []


@pytest.mark.skipif(os.environ.get("POLTERGEIST_REFERENCE_AI_WEIGHTS_TEST") != "1",
                    reason="Set POLTERGEIST_REFERENCE_AI_WEIGHTS_TEST=1 in the prepared reference environment")
def test_real_pretrained_weights_load_and_change_plan_with_scene():
    pytest.importorskip("torch")
    pytest.importorskip("transformers")
    assert verify_checkpoint(DEFAULT_CHECKPOINT) == CHECKPOINT_SHA256
    agent = ReferenceAIAgent()
    try:
        agent.load({"device": "cpu", "torch_threads": 2})
        context = AgentReset(protocol_version="1.1", seed=0, map_name="synthetic", ego_actor_id="ego", fixed_delta_seconds=0.05)
        agent.reset(context)
        clear = agent.step(observation())
        agent.reset(context)
        blocked = agent.step(observation(objects=[scene_object(x=8.)]))
        assert clear.protocol_version == "1.1"
        assert clear.diagnostics.metadata["checkpoint_sha256"] == CHECKPOINT_SHA256
        assert clear.diagnostics.metadata["parameter_count"] > 30_000_000
        assert clear.diagnostics.metadata["inference_wall_time_s"] > 0.
        assert len(clear.diagnostics.planned_trajectory) == 20
        assert clear.diagnostics.planned_trajectory != blocked.diagnostics.planned_trajectory
        agent.reset(context)
        red = agent.step(observation(objects=[scene_object(kind="traffic_light", x=8., state="red")]))
        assert red.diagnostics.metadata["input_object_count"] == 1
        assert clear.diagnostics.planned_trajectory != red.diagnostics.planned_trajectory
        # The CUDA-friendly gather computes the same learned token values as
        # the upstream class masks; no class-specific projection is replaced.
        import torch

        rows = torch.tensor([[float(index), 2., -1., 15., 10., 2., 4.] for index in range(7)])
        expected = torch.zeros((7, 512))
        with torch.inference_mode():
            for index, projection in enumerate(agent._model.tok_emb):
                mask = rows[:, 0] == index
                expected[mask] = projection(rows[mask, 1:])
            actual = agent._model.object_embeddings(rows)
        torch.testing.assert_close(actual, expected, rtol=1e-5, atol=1e-5)
        agent.reset(context)
        repeat = agent.step(observation())
        assert clear.control == repeat.control
    finally:
        agent.close()
