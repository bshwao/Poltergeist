"""Scene features preserve snapshot evidence and never advance a simulator."""

import base64
import math
from types import SimpleNamespace as NS

import pytest

from scenarios import build_scenario
from shared.models import (
    ActorState, BoundingBoxExtent, ControlInput, Rotation, TelemetryFrame, Transform, Vector3,
)
from simulator.carla_adapter import CarlaSimulationAdapter
from simulator.errors import SimulationError
from simulator.reference_scene import CarlaReferenceScene, resample_route
from simulator.test_adapter import TestSimulationAdapter


def pose(x=0.0, y=0.0, yaw=0.0, z=0.0):
    return Transform(position=Vector3(x=float(x), y=float(y), z=float(z)), rotation=Rotation(yaw_deg=float(yaw)))


class Location:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = x, y, z


class NativeRotation:
    def __init__(self, pitch=0.0, yaw=0.0, roll=0.0):
        self.pitch, self.yaw, self.roll = pitch, yaw, roll


class NativeTransform:
    def __init__(self, location, rotation):
        self.location, self.rotation = location, rotation


CARLA = NS(Location=Location, Rotation=NativeRotation, Transform=NativeTransform, LaneType=NS(Driving="Driving"))


class Waypoint:
    def __init__(self, x=0.0, y=0.0, yaw=0.0, lane_type="Driving", end=200.0):
        self.transform = NativeTransform(Location(x, y), NativeRotation(yaw=yaw))
        self.road_id, self.section_id, self.lane_id, self.s = 1, 0, -1, x
        self.lane_type, self.lane_width, self.is_junction = lane_type, 4.0, False
        self.left_lane_marking = NS(type="Solid", color="White", width=0.125)
        self.right_lane_marking = NS(type="Broken", color="White", width=0.125)
        self.end = end

    def next(self, distance):
        p = self.transform.location
        if p.x + distance > self.end:
            return []
        return [Waypoint(p.x + distance, p.y, end=self.end)]

    def get_left_lane(self):
        return None

    def get_right_lane(self):
        if self.lane_type == "Driving":
            lane = Waypoint(self.transform.location.x, 4.0, lane_type="Sidewalk")
            lane.lane_id = -2
            return lane
        return None


class RoadMap:
    def __init__(self, end=200.0):
        self.samples, self.end = 0, end

    def get_waypoint(self, location, **kwargs):
        return Waypoint(location.x, location.y, end=self.end)

    def generate_waypoints(self, spacing):
        self.samples += 1
        return [Waypoint(float(x)) for x in range(-40, 42)]


class World:
    def __init__(self, actors=(), road_map=None):
        self.actors = list(actors)
        self.road_map = road_map or RoadMap()
        self.ticks = 0

    def get_map(self):
        return self.road_map

    def get_actors(self):
        return self.actors

    def tick(self):
        self.ticks += 1
        raise AssertionError("Scene extraction must never tick CARLA")


def state(actor_id, x=0.0, y=0.0, yaw=0.0, *, box=True, velocity=True):
    return ActorState(actor_id=actor_id, transform=pose(x, y, yaw),
        bounding_box_transform=pose(x + 0.25, y, yaw, z=0.8) if box else None,
        bounding_box_extent=BoundingBoxExtent(x=2.0, y=0.9, z=0.8) if box else None,
        velocity_mps=Vector3(x=5.0) if velocity else None, speed_limit_mps=13.9)


def frame(actors=(), ego=None):
    return TelemetryFrame(frame=100, time_s=0.0, ego=ego or state("ego"),
                          actors=None if actors is None else list(actors))


def provider(actors=(), routes=None, road_map=None):
    world = World(actors, road_map)
    manager = NS(actor_ids={actor.id: getattr(actor, "telemetry_id", f"world:{actor.id}") for actor in actors},
                 _routes={} if routes is None else routes)
    return CarlaReferenceScene(world, CARLA, manager, "ego"), world


def test_route_projection_and_metric_spacing():
    route = [pose(0), pose(10), pose(10, 10, 90)]
    result = resample_route(route, pose(2.25, 0.5), horizon_m=12, spacing_m=1)
    assert len(result) == 13
    assert result[0].position.x == pytest.approx(2.25)
    assert result[0].position.y == 0
    assert result[-1].position.x == pytest.approx(10)
    assert result[-1].position.y == pytest.approx(4.25)


def test_route_end_is_not_extrapolated_or_snapped():
    assert resample_route([pose(0), pose(2)], pose(0))[-1].position.x == 2
    assert resample_route([pose(0), pose(2)], pose(100)) is None
    assert resample_route([pose(0), pose(2)], pose(1, z=10)) is None
    assert resample_route([], pose()) is None
    assert resample_route([pose(), pose()], pose()) is None


def test_selected_junction_route_is_preserved_without_mutating_manager():
    selected = [pose(0), pose(10), pose(10, 20, 90), pose(10, 90, 90)]
    scene, world = provider(routes={"ego": selected})
    route = scene._route_ahead(pose(0))
    assert route[15].position.x == pytest.approx(10)
    assert route[15].position.y == pytest.approx(5)
    assert len(selected) == 4
    assert world.ticks == 0


def test_connected_route_has_bounded_horizon_and_stops_at_end():
    scene, _ = provider()
    route = scene._route_ahead(pose())
    assert len(route) == 81
    assert route[-1].position.x == pytest.approx(80)
    limited, _ = provider(road_map=RoadMap(end=5))
    assert limited._route_ahead(pose())[-1].position.x == 5


def test_dynamic_geometry_comes_from_frame_not_actor_get_transform():
    def forbidden():
        raise AssertionError("Do not reread dynamic motion outside the snapshot")
    actor = NS(id=23, type_id="vehicle.tesla.model3", telemetry_id="lead", get_transform=forbidden)
    scene, world = provider([actor])
    sample = state("lead", 7.0)
    objects = scene._objects(frame([sample]), None)
    assert objects[0].transform == sample.bounding_box_transform
    assert objects[0].velocity_mps == sample.velocity_mps
    assert objects[0].extent == sample.bounding_box_extent
    assert world.ticks == 0


@pytest.mark.parametrize(("blueprint", "kind"), [("walker.pedestrian.0001", "pedestrian"),
    ("vehicle.ford.ambulance", "emergency"), ("vehicle.carlamotors.firetruck", "emergency"),
    ("vehicle.dodge.charger_police", "emergency"), ("vehicle.audi.a2", "vehicle")])
def test_native_actor_classification(blueprint, kind):
    scene, _ = provider([NS(id=1, type_id=blueprint, telemetry_id="actor")])
    assert scene._objects(frame([state("actor")]), None)[0].kind == kind


def test_unavailable_dynamic_features_are_not_fabricated():
    scene, _ = provider([NS(id=1, type_id="vehicle.audi.a2", telemetry_id="actor")])
    assert scene._objects(frame(None), None) is None
    assert scene._objects(frame([state("actor", box=False)]), None) is None
    assert scene._objects(frame([state("missing")]), None) is None
    assert scene._objects(frame([]), None) == []
    item = scene._objects(frame([state("actor", velocity=False)]), None)[0]
    assert item.velocity_mps is None


def test_static_prop_uses_real_box_offset_and_preserves_unknown_motion():
    prop = NS(id=9, type_id="static.prop.trafficcone01",
        get_transform=lambda: NativeTransform(Location(5, 2), NativeRotation(yaw=90)),
        bounding_box=NS(location=Location(1, 0, 0.5), rotation=NativeRotation(), extent=Location(0.2, 0.2, 0.5)))
    scene, _ = provider([prop])
    item = scene._objects(frame(), None)[0]
    assert item.kind == "static"
    assert item.transform.position.x == pytest.approx(5)
    assert item.transform.position.y == pytest.approx(3)
    assert item.velocity_mps is None and item.is_static is None
    prop.get_velocity = lambda: Location(0.0, 0.0, 0.0)
    measured = scene._objects(frame(), None)[0]
    assert measured.velocity_mps == Vector3()
    assert measured.is_static is True


def test_signals_filter_lane_heading_height_and_distance_and_cache_geometry():
    calls = {"near": 0, "far": 0, "geometry": 0}
    def points():
        calls["geometry"] += 1
        return [Waypoint(12)]
    def near_state():
        calls["near"] += 1
        return "Red"
    def far_state():
        calls["far"] += 1
        raise AssertionError("Distant signals must not trigger state RPCs")
    near = NS(id=1, type_id="traffic.traffic_light", get_stop_waypoints=points, get_state=near_state)
    far = NS(id=2, type_id="traffic.traffic_light", get_stop_waypoints=lambda: [Waypoint(100)], get_state=far_state)
    scene, _ = provider([near, far])
    route = [pose(x) for x in range(81)]
    for _ in range(2):
        objects = scene._signals([near, far], frame(), route)
        assert len(objects) == 1
        assert objects[0].traffic_light_state == "red"
        assert objects[0].transform.position.x == 12
    assert calls == {"near": 2, "far": 0, "geometry": 1}
    assert not scene._on_route(pose(12, yaw=90), route)
    assert not scene._on_route(pose(12, z=5), route)
    assert not scene._on_route(pose(12, y=4), route)


def test_stop_sign_token_clears_after_observed_stationary_approach():
    stop = NS(id=4, type_id="traffic.stop", get_transform=lambda: NativeTransform(Location(8), NativeRotation()),
              trigger_volume=NS(location=Location()))
    scene, _ = provider([stop])
    route = [pose(x) for x in range(81)]
    assert scene._signals([stop], frame(), route)[0].kind == "stop_sign"
    stopped = state("ego", 7)
    stopped.velocity_mps = Vector3()
    assert scene._signals([stop], frame(ego=stopped), route) == []
    stopped.velocity_mps = Vector3(x=2.0)
    assert scene._signals([stop], frame(ego=stopped), route) == []


def test_raster_is_unknown_when_map_geometry_unavailable():
    road_map = NS(generate_waypoints=lambda step: [], get_waypoint=lambda *a, **k: None)
    scene, _ = provider(road_map=road_map)
    assert scene._raster(pose()) is None
    assert scene._patches is None


def test_map_cache_has_real_road_sidewalk_solid_and_broken_patches():
    scene, world = provider()
    scene._load_map()
    assert {patch.category for patch in scene._patches} == {1, 2, 3, 4}
    assert world.road_map.samples == 1
    assert len(scene._patch_grid) > 1
    assert world.ticks == 0


def test_raster_axes_palette_and_map_cache():
    pytest.importorskip("cv2")
    import numpy as np
    scene, world = provider()
    first = scene._raster(pose())
    assert (first.width, first.height, first.ego_row, first.ego_column) == (128, 128, 64, 64)
    assert first.classes is None
    classes = np.frombuffer(base64.b64decode(first.data_base64), dtype=np.uint8).reshape(128, 128)
    assert classes[64, 64] == 1
    assert classes[72, 64] == 2  # Sidewalk at world +y = ego-right.
    assert classes[80, 64] == 0
    assert 3 in classes and 4 in classes
    scene._raster(pose(2))
    assert world.road_map.samples == 1
    rotated = scene._raster(pose(yaw=90))
    classes = np.frombuffer(base64.b64decode(rotated.data_base64), dtype=np.uint8).reshape(128, 128)
    assert classes[64, 72] == 2  # Same +y sidewalk becomes ego-forward.
    assert world.ticks == 0


def test_synthetic_adapter_identifies_its_limits_and_copies_scene():
    adapter = TestSimulationAdapter()
    initial = adapter.open(build_scenario("sudden_braking"))
    scene = adapter.agent_context(initial)
    assert scene.source == "synthetic"
    assert scene.road_raster is None
    assert len(scene.route) == 81
    assert scene.speed_limit_mps == 13.9
    assert scene.objects[0].kind == "vehicle"
    assert scene.objects[0].actor_id == "lead"
    scene.objects[0].transform.position.x = 1000.0
    assert adapter.agent_context(initial).objects[0].transform.position.x != 1000.0
    adapter.close()
    with pytest.raises(SimulationError, match="not open"):
        adapter.agent_context(initial)


def test_carla_adapter_context_requires_open_and_provider_is_lazy(monkeypatch):
    adapter = CarlaSimulationAdapter()
    with pytest.raises(SimulationError, match="not open"):
        adapter.agent_context(frame())
    calls = []
    class FakeProvider:
        def __init__(self, world, carla, manager, ego_id):
            calls.append((world, carla, manager, ego_id))
        def context(self, observed):
            return observed.frame
    monkeypatch.setattr("simulator.reference_scene.CarlaReferenceScene", FakeProvider)
    adapter.world, adapter.carla, adapter.actor_manager, adapter.ego_id = object(), object(), object(), "ego"
    adapter._opened = True
    assert adapter.agent_context(frame()) == 100
    assert adapter.agent_context(frame()) == 100
    assert len(calls) == 1


def test_full_scene_context_uses_native_frame_evidence_and_never_ticks():
    scene, world = provider([NS(id=1, type_id="vehicle.audi.a2", telemetry_id="lead")])
    context = scene.context(frame([state("lead", 10)]))
    assert context.source == "carla_ground_truth"
    assert context.objects[0].transform.position.x == 10.25
    assert len(context.route) == 81
    assert context.speed_limit_mps == 13.9
    assert world.ticks == 0
