"""CARLA ownership, coordinate, and cleanup behavior without a running server."""

from copy import deepcopy
import math
from types import SimpleNamespace as NS

import pytest

from shared.models import ActorState, Rotation, Transform, Vector3
from scenarios import build_scenario
from scenarios.scenario_loader import validate_scenario
from scenarios.scenario_loader import load_template
from simulator.actor_manager import ActorManager
from simulator.errors import SimulationError
from simulator.protocols import ActorCommand
from simulator.transforms import compose, from_carla, rotation_matrix, to_carla
from simulator.world_manager import WorldManager


class Vec:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = x, y, z


class Rot:
    def __init__(self, pitch=0.0, yaw=0.0, roll=0.0):
        self.pitch, self.yaw, self.roll = pitch, yaw, roll


class Pose:
    def __init__(self, location=None, rotation=None):
        self.location, self.rotation = location or Vec(), rotation or Rot()


CARLA = NS(Location=Vec, Vector3D=Vec, Rotation=Rot, Transform=Pose,
           WorldSettings=NS, WeatherParameters=NS, VehicleControl=NS,
           WalkerControl=NS, LaneType=NS(Driving=1, Sidewalk=2, Any=3))


class Blueprint:
    def __init__(self, name):
        self.id = name
        self.attributes = {key: NS(is_modifiable=True, recommended_values=values)
                           for key, values in {"color": ["red", "blue"], "driver_id": ["a", "b"],
                                               "role_name": [], "is_invincible": []}.items()}
        self.values = {}

    def has_attribute(self, name):
        return name in self.attributes

    def get_attribute(self, name):
        return self.attributes[name]

    def set_attribute(self, name, value):
        self.values[name] = value


class Library:
    def find(self, name):
        if name == "vehicle.missing":
            raise IndexError(name)
        return Blueprint(name)


class Waypoint:
    def __init__(self, x=0.0, y=0.0, yaw=0.0, is_junction=False):
        self.transform, self.id = Pose(Vec(x, y, 0.0), Rot(yaw=yaw)), 10
        self.is_junction = is_junction
        self.road_id, self.section_id, self.lane_id, self.s = 1, 0, -1, x

    def next(self, distance):
        return [Waypoint(self.transform.location.x + distance, self.transform.location.y)]


class Map:
    def __init__(self, name="Town03"):
        self.name = f"/Game/Carla/Maps/{name}"
        self.points = [Pose(Vec(float(x), 0.0, 0.5)) for x in (0, 20, 40, 60, 80)]
        self.lane_yaw = 0.0
        self.junction = False
        self.calls = []

    def get_spawn_points(self):
        return deepcopy(self.points)

    def generate_waypoints(self, distance):
        return [Waypoint(float(x)) for x in range(0, 181, 20)]

    def get_waypoint(self, location, project_to_road=True, lane_type=1):
        self.calls.append((location.x, location.y, project_to_road, lane_type))
        if abs(location.x) > 200.0:
            return None
        if lane_type == 1 and abs(location.y) > 2.0:
            return None
        if lane_type == 2:
            if project_to_road:
                return Waypoint(location.x, 4.0)
            if not 2.0 <= abs(location.y) <= 6.0:
                return None
        if lane_type == 3 and abs(location.y) > 6.0:
            return None
        return Waypoint(location.x, location.y, self.lane_yaw, self.junction)


class Actor:
    def __init__(self, world, native_id, blueprint, transform):
        self.world, self.id, self.type_id = world, native_id, blueprint.id
        self.attributes = dict(blueprint.values)
        self.pose = deepcopy(transform)
        self.velocity, self.acceleration = Vec(), Vec()
        self.bounding_box = NS(location=Vec(0.0, 0.0, 0.5), rotation=Rot(), extent=Vec(1.0, 0.5, 0.5))
        self.is_alive, self.controls, self.target_velocities = True, [], []
        self.destroy_result = True

    def get_transform(self):
        return self.pose

    def get_velocity(self):
        return self.velocity

    def get_acceleration(self):
        return self.acceleration

    def get_speed_limit(self):
        return 36.0

    def set_target_velocity(self, velocity):
        self.target_velocities.append(velocity)
        self.velocity = velocity

    def apply_control(self, value):
        self.controls.append(value)

    def destroy(self):
        self.world.destroy_order.append(self.id)
        if isinstance(self.destroy_result, Exception):
            raise self.destroy_result
        if self.destroy_result:
            self.is_alive = False
            self.world.actors.remove(self)
        return self.destroy_result


class World:
    def __init__(self, map_name="Town03"):
        self.map, self.actors = Map(map_name), []
        self.settings = NS(synchronous_mode=False, fixed_delta_seconds=None, no_rendering_mode=True,
                           substepping=False, max_substeps=3, max_substep_delta_time=0.02,
                           deterministic_ragdolls=False, tile_stream_distance=1234)
        self.weather = NS(cloudiness=51.0, precipitation=2.0, wetness=3.0, fog_density=0.0,
                          sun_altitude_angle=45.0, wind_intensity=17.0)
        self.applied_settings, self.weather_updates, self.destroy_order = [], [], []
        self.spawn_attempts = []
        self.fail_spawn_at = None
        self.fail_weather_once = False
        self.no_ground = False
        self.next_id = 1
        self.box_factory = None

    def get_map(self):
        return self.map

    def get_settings(self):
        return self.settings

    def apply_settings(self, value):
        self.applied_settings.append(deepcopy(value))
        self.settings = deepcopy(value)

    def get_weather(self):
        return self.weather

    def set_weather(self, value):
        if self.fail_weather_once:
            self.fail_weather_once = False
            raise RuntimeError("weather failure")
        self.weather_updates.append(deepcopy(value))
        self.weather = deepcopy(value)

    def get_actors(self):
        return list(self.actors)

    def get_blueprint_library(self):
        return Library()

    def ground_projection(self, location, distance):
        if self.no_ground:
            return None
        return NS(location=Vec(location.x, location.y, 0.0))

    def try_spawn_actor(self, blueprint, pose):
        self.spawn_attempts.append((blueprint.id, deepcopy(pose)))
        if self.fail_spawn_at == len(self.spawn_attempts):
            return None
        actor = Actor(self, self.next_id, blueprint, pose)
        self.next_id += 1
        if self.box_factory:
            actor.bounding_box = self.box_factory(actor)
        self.actors.append(actor)
        return actor


class Client:
    def __init__(self, world=None):
        self.world = world or World()
        self.loads = []
        self.maps = ["/Game/Carla/Maps/Town03", "/Game/Carla/Maps/Town04"]

    def get_world(self):
        return self.world

    def get_available_maps(self):
        return self.maps

    def load_world(self, name):
        self.loads.append(name)
        self.world = World(name.rsplit("/", 1)[-1])
        return self.world


def scenario(actors=None, **values):
    return validate_scenario({"name": "resources", "actors": actors or [
        {"id": "ego", "type": "ego_vehicle", "spawn_point_index": 0, "speed_mps": 5.0},
    ], **values})


def test_world_restores_settings_weather_and_preserves_rendering():
    client = Client()
    original_settings, original_weather = deepcopy(client.world.settings), deepcopy(client.world.weather)
    manager = WorldManager(client, CARLA)
    world = manager.open(scenario(fixed_delta_seconds=0.1, weather={"precipitation": 80.0}))
    assert world.settings.synchronous_mode
    assert world.settings.fixed_delta_seconds == 0.1
    assert world.settings.max_substeps == 10
    assert world.settings.max_substep_delta_time == 0.01
    assert world.settings.no_rendering_mode
    assert world.settings.tile_stream_distance == 1234
    assert world.weather.precipitation == 80.0
    assert world.weather.wind_intensity == 17.0
    manager.close()
    assert vars(world.settings) == vars(original_settings)
    assert vars(world.weather) == vars(original_weather)
    manager.close()


def test_world_partial_setup_failure_restores_previous_state():
    client = Client()
    before = deepcopy(client.world.settings)
    client.world.fail_weather_once = True
    manager = WorldManager(client, CARLA)
    with pytest.raises(SimulationError, match="weather failure"):
        manager.open(scenario())
    assert vars(client.world.settings) == vars(before)
    assert manager.world is None


@pytest.mark.parametrize("original,override", [(False, True), (True, False), (True, None), (False, None)])
def test_rendering_override_is_scoped_and_restored(original, override):
    client = Client()
    client.world.settings.no_rendering_mode = original
    manager = WorldManager(client, CARLA, no_rendering_mode=override)
    world = manager.open(scenario())
    assert world.settings.no_rendering_mode is (original if override is None else override)
    manager.close()
    assert world.settings.no_rendering_mode is original


def test_rendering_override_is_restored_after_partial_setup_failure():
    client = Client()
    client.world.settings.no_rendering_mode = False
    client.world.fail_weather_once = True
    manager = WorldManager(client, CARLA, no_rendering_mode=True)
    with pytest.raises(SimulationError, match="weather failure"):
        manager.open(scenario())
    assert client.world.settings.no_rendering_mode is False


def test_world_mismatched_map_is_opt_in_and_unavailable_map_is_actionable():
    client = Client()
    with pytest.raises(SimulationError, match="explicitly allow map reload"):
        WorldManager(client, CARLA).open(scenario(map="Town04"))
    with pytest.raises(SimulationError, match="unavailable"):
        WorldManager(client, CARLA, allow_map_reload=True).open(scenario(map="Town99"))
    assert client.loads == []


def test_reload_and_restore_original_map():
    client = Client()
    before = deepcopy(client.world.settings)
    manager = WorldManager(client, CARLA, allow_map_reload=True)
    assert manager.open(scenario(map="Town04")).map.name.endswith("Town04")
    manager.close()
    assert client.world.map.name.endswith("Town03")
    assert vars(client.world.settings) == vars(before)


@pytest.mark.parametrize("type_id", ["vehicle.other", "walker.pedestrian.1", "sensor.camera.rgb", "controller.ai.walker"])
def test_map_reload_never_destroys_unmanaged_dynamic_actors(type_id):
    client = Client()
    client.world.actors.append(NS(type_id=type_id))
    with pytest.raises(SimulationError, match="unmanaged"):
        WorldManager(client, CARLA, allow_map_reload=True).open(scenario(map="Town04"))
    assert client.loads == []


def test_map_restore_refuses_foreign_actors_added_during_run():
    client = Client()
    manager = WorldManager(client, CARLA, allow_map_reload=True)
    manager.open(scenario(map="Town04"))
    client.world.actors.append(NS(type_id="vehicle.other"))
    with pytest.raises(SimulationError, match="unmanaged"):
        manager.close()
    assert len(client.loads) == 1
    assert len(client.world.actors) == 1


def test_world_close_attempts_weather_after_settings_restore_failure():
    client = Client()
    manager = WorldManager(client, CARLA)
    manager.open(scenario())
    client.world.apply_settings = lambda value: (_ for _ in ()).throw(RuntimeError("settings blocked"))
    with pytest.raises(SimulationError, match="settings blocked"):
        manager.close()
    assert client.world.weather.cloudiness == 51.0


def test_world_close_retries_transient_settings_restore_failure():
    client = Client()
    before = deepcopy(client.world.settings)
    manager = WorldManager(client, CARLA)
    world = manager.open(scenario())
    original_apply = world.apply_settings
    attempts = []

    def transient_apply(value):
        attempts.append(value)
        if len(attempts) == 1:
            raise RuntimeError("transient settings failure")
        original_apply(value)

    world.apply_settings = transient_apply
    with pytest.raises(SimulationError, match="transient settings failure"):
        manager.close()
    assert manager.world is world
    manager.close()
    assert len(attempts) == 2
    assert vars(world.settings) == vars(before)
    assert manager.world is None


def test_world_close_does_not_reload_restored_map_when_retrying_weather():
    client = Client()
    manager = WorldManager(client, CARLA, allow_map_reload=True)
    manager.open(scenario(map="Town04"))
    original_load = client.load_world

    def load_with_transient_weather(name):
        restored = original_load(name)
        restored.fail_weather_once = True
        return restored

    client.load_world = load_with_transient_weather
    with pytest.raises(SimulationError, match="weather failure"):
        manager.close()
    restored_world = client.world
    assert restored_world.map.name.endswith("Town03")
    assert manager.world is restored_world
    assert len(client.loads) == 2
    manager.close()
    assert len(client.loads) == 2
    assert client.world is restored_world
    assert manager.world is None


def test_world_close_retries_transient_map_restore_failure():
    client = Client()
    manager = WorldManager(client, CARLA, allow_map_reload=True)
    active = manager.open(scenario(map="Town04"))
    original_load = client.load_world
    attempts = []

    def transient_load(name):
        attempts.append(name)
        if len(attempts) == 1:
            raise RuntimeError("transient map failure")
        return original_load(name)

    client.load_world = transient_load
    with pytest.raises(SimulationError, match="transient map failure"):
        manager.close()
    assert manager.world is active
    manager.close()
    assert len(attempts) == 2
    assert client.world.map.name.endswith("Town03")
    assert manager.world is None


def test_spawn_resolves_dependencies_and_sets_velocity_once():
    world = World()
    definition = scenario(actors=[
        {"id": "lead", "type": "lead_vehicle", "speed_mps": 3.0,
         "relative_spawn": {"anchor_actor_id": "ego", "offset_m": {"x": 10.0}}},
        {"id": "ego", "type": "ego_vehicle", "spawn_point_index": 1, "speed_mps": 5.0},
    ])
    manager = ActorManager(world, CARLA, definition)
    actors = manager.spawn()
    assert list(actors) == ["ego", "lead"]
    assert manager.resolved_spawns["lead"].position.x == 30.0
    assert actors["ego"].attributes["role_name"] == "ego"
    assert len(actors["ego"].target_velocities) == 1
    assert actors["ego"].target_velocities[0].x == 5.0
    manager.control_npcs({}, 0.05)
    assert len(actors["ego"].target_velocities) == 1
    assert actors["ego"].controls == []
    assert actors["lead"].controls[-1].throttle == 0.0
    native_ids = [actor.id for actor in actors.values()]
    manager.close()
    assert world.destroy_order == list(reversed(native_ids))


def test_extended_template_speeds_reach_carla_without_clamping():
    definition = build_scenario("sudden_braking", parameters={
        "ego_speed_mps": 40, "lead_speed_mps": 35,
    })
    manager = ActorManager(World(), CARLA, definition)
    try:
        actors = manager.spawn()
        assert actors["ego"].target_velocities[0].x == 40
        assert actors["lead"].target_velocities[0].x == 35
        manager.control_npcs({"lead": ActorCommand(target_speed_mps=35)}, 0.05)
        assert actors["lead"].controls[-1].brake == 0
        assert actors["lead"].controls[-1].throttle == 0
    finally:
        manager.close()


def test_relative_transform_and_local_box_rotation_use_full_three_dimensions():
    parent = Transform(position=Vector3(x=10.0, z=5.0),
                       rotation=Rotation(pitch_deg=30.0, yaw_deg=40.0, roll_deg=15.0))
    local = Transform(position=Vector3(x=4.0, y=2.0, z=3.0),
                      rotation=Rotation(pitch_deg=25.0, yaw_deg=-20.0, roll_deg=10.0))
    result = compose(parent, local)
    matrix_parent, matrix_local = rotation_matrix(parent.rotation), rotation_matrix(local.rotation)
    expected = tuple(tuple(sum(matrix_parent[i][k] * matrix_local[k][j] for k in range(3))
                           for j in range(3)) for i in range(3))
    for row, expected_row in zip(rotation_matrix(result.rotation), expected):
        assert row == pytest.approx(expected_row)
    assert result.position.z == pytest.approx(5.0 + 4.0 * 0.5 - 2.0 * math.cos(math.pi / 6) * math.sin(math.pi / 12)
                                             + 3.0 * math.cos(math.pi / 6) * math.cos(math.pi / 12))
    assert from_carla(to_carla(parent, CARLA)) == parent


def test_measured_box_pose_extent_acceleration_and_si_speed_limit():
    world = World()
    manager = ActorManager(world, CARLA, scenario())
    actor = manager.spawn()["ego"]
    actor.pose = Pose(Vec(10.0, 5.0, 1.0), Rot(yaw=90.0))
    actor.bounding_box.location = Vec(2.0, 1.0, 0.5)
    actor.bounding_box.rotation = Rot(yaw=30.0)
    actor.acceleration = Vec(-2.0)
    state = manager.states()[0]
    assert state.bounding_box_extent.x == 1.0
    assert state.bounding_box_transform.position.x == pytest.approx(9.0)
    assert state.bounding_box_transform.position.y == pytest.approx(7.0)
    assert state.bounding_box_transform.rotation.yaw_deg == pytest.approx(120.0)
    assert state.acceleration_mps2.x == -2.0
    assert state.speed_limit_mps == 10.0


def test_speed_limit_rpc_failure_preserves_other_actor_measurements():
    manager = ActorManager(World(), CARLA, scenario())
    ego = manager.spawn()["ego"]
    ego.get_speed_limit = lambda: (_ for _ in ()).throw(RuntimeError("map limit unavailable"))
    state = manager.states()[0]
    assert state.speed_limit_mps is None
    assert state.transform is not None
    assert state.velocity_mps is not None
    assert state.bounding_box_extent is not None


@pytest.mark.parametrize("actor, message", [
    ({"spawn_point_index": 50}, "out of range"),
    ({"blueprint": "vehicle.missing"}, "unavailable"),
    ({"blueprint": "walker.pedestrian.0001"}, "does not match"),
    ({"spawn": {"position": {"y": 20.0}}}, "outside a driving lane"),
    ({"spawn": {"rotation": {"yaw_deg": 90.0}}}, "not aligned"),
])
def test_invalid_spawn_is_actionable(actor, message):
    world = World()
    definition = scenario(actors=[{"id": "ego", "type": "ego_vehicle", **actor}])
    with pytest.raises(SimulationError, match=message):
        ActorManager(world, CARLA, definition).spawn()
    assert world.actors == []


def test_ground_check_rejects_floating_actor_and_missing_geometry():
    world = World()
    world.no_ground = True
    with pytest.raises(SimulationError, match="ground"):
        ActorManager(world, CARLA, scenario()).spawn()


def test_partial_spawn_failure_cleans_only_owned_actors():
    world = World()
    foreign = world.try_spawn_actor(Blueprint("vehicle.other"), Pose(Vec(90.0)))
    world.fail_spawn_at = 3
    manager = ActorManager(world, CARLA, scenario(actors=[
        {"id": "ego", "type": "ego_vehicle", "spawn_point_index": 0},
        {"id": "lead", "type": "lead_vehicle", "spawn_point_index": 1},
    ]))
    with pytest.raises(SimulationError, match="occupied"):
        manager.spawn()
    assert world.actors == [foreign]
    assert foreign.is_alive


def test_actual_box_offsets_detect_spawn_overlap_and_cleanup():
    world = World()
    world.box_factory = lambda actor: NS(location=Vec(10.0 if actor.id == 1 else -10.0, 0.0, 0.5),
                                         rotation=Rot(), extent=Vec(1.0, 0.5, 0.5))
    manager = ActorManager(world, CARLA, scenario(actors=[
        {"id": "ego", "type": "ego_vehicle", "spawn_point_index": 0},
        {"id": "lead", "type": "lead_vehicle", "spawn_point_index": 1},
    ]))
    with pytest.raises(SimulationError, match="actual bounding box overlaps"):
        manager.spawn()
    assert world.actors == []
    assert world.destroy_order == [2, 1]


def test_seeded_fallback_appearance_and_positions_repeat_without_global_rng():
    definition = scenario(actors=[{"id": "ego", "type": "ego_vehicle"},
                                  {"id": "npc", "type": "vehicle"}], seed=91)
    managers = [ActorManager(World(), CARLA, definition) for _ in range(2)]
    for manager in managers:
        manager.spawn()
    assert managers[0].resolved_spawns == managers[1].resolved_spawns
    assert [a.attributes for a in managers[0].actors.values()] == [a.attributes for a in managers[1].actors.values()]


def test_fallback_retries_actual_box_overlap_without_touching_foreign_actor():
    world = World()
    # All but one candidate is occupied; our fake try_spawn_actor deliberately
    # omits physical overlap checks to exercise measured-box collision handling.
    foreign = [world.try_spawn_actor(Blueprint("vehicle.other"), pose) for pose in world.map.points[:-1]]
    manager = ActorManager(world, CARLA, scenario(actors=[{"id": "ego", "type": "ego_vehicle"}], seed=1))
    manager.spawn()
    assert manager.resolved_spawns["ego"].position.x == 80.0
    manager.close()
    assert world.actors == foreign


def test_template_path_validation_rejects_wrong_junction_and_cleans_up():
    world = World()
    definition = load_template("sudden_braking")
    # Explicit coordinates pin this layout and must not trigger auto-placement.
    definition.actors[0].spawn_point_index = None
    definition.actors[0].spawn = Transform(position=Vector3(z=0.5))
    # Both spawn points fit, but the gap includes an unmapped interval.
    original = world.map.get_waypoint

    def gap(location, **kwargs):
        if 5.0 < location.x < 10.0:
            return None
        return original(location, **kwargs)

    world.map.get_waypoint = gap
    with pytest.raises(SimulationError, match="Template approach"):
        ActorManager(world, CARLA, definition).spawn()
    assert world.actors == []


def test_occluder_can_spawn_on_sidewalk_and_stays_stationary():
    world = World()
    manager = ActorManager(world, CARLA, load_template("occluded_pedestrian"))
    actors = manager.spawn()
    manager.control_npcs({}, 0.05)
    assert manager.resolved_spawns["occluder"].position.y > 3.0
    assert actors["occluder"].controls[-1].brake == 1.0


def test_walker_fallback_is_seeded_sidewalk_and_uses_walker_control():
    manager = ActorManager(World(), CARLA, scenario(actors=[
        {"id": "ego", "type": "ego_vehicle", "spawn_point_index": 0},
        {"id": "person", "type": "pedestrian", "speed_mps": 1.4},
    ]))
    person = manager.spawn()["person"]
    assert manager.resolved_spawns["person"].position.y == 4.0
    assert person.controls[-1].speed == 1.4
    assert person.target_velocities == []
    manager.control_npcs({"person": ActorCommand(2.0)}, 0.05)
    assert person.controls[-1].speed == 2.0


def test_foreign_actor_states_are_observed_with_collision_safe_ids():
    world = World()
    foreign = world.try_spawn_actor(Blueprint("vehicle.other"), Pose(Vec(90.0)))
    manager = ActorManager(world, CARLA, scenario(actors=[
        {"id": "world:1", "type": "ego_vehicle", "spawn_point_index": 0},
    ]))
    manager.spawn()
    assert manager.actor_ids[foreign.id] == "world:world:1"
    states = manager.states()
    assert [state.actor_id for state in states] == ["world:1", "world:world:1"]
    assert states[1].transform.position.x == 90.0
    manager.close()
    assert world.actors == [foreign]


def test_snapshot_missing_actor_preserves_unknown_coverage_instead_of_live_state():
    manager = ActorManager(World(), CARLA, scenario())
    manager.spawn()
    missing = manager.states(NS(find=lambda native: None))
    assert missing == [ActorState(actor_id="ego")]


def test_npc_braking_decreases_target_each_tick_and_never_teleports_velocity():
    manager = ActorManager(World(), CARLA, scenario(actors=[
        {"id": "ego", "type": "ego_vehicle", "spawn_point_index": 0},
        {"id": "lead", "type": "lead_vehicle", "spawn_point_index": 1, "speed_mps": 10.0},
    ]))
    lead = manager.spawn()["lead"]
    for _ in range(5):
        manager.control_npcs({"lead": ActorCommand(0.0, deceleration_mps2=2.0)}, 0.1)
    assert manager._targets["lead"] == pytest.approx(9.0)
    assert lead.controls[-1].brake == pytest.approx(0.3)
    assert len(lead.target_velocities) == 1


def test_failed_destroy_surfaces_all_errors_and_allows_retry():
    manager = ActorManager(World(), CARLA, scenario(actors=[
        {"id": "ego", "type": "ego_vehicle", "spawn_point_index": 0},
        {"id": "lead", "type": "lead_vehicle", "spawn_point_index": 1},
    ]))
    actors = manager.spawn()
    actors["ego"].destroy_result = False
    actors["lead"].destroy_result = RuntimeError("blocked")
    with pytest.raises(SimulationError, match="lead.*blocked.*ego.*False"):
        manager.close()
    assert len(manager.actors) == 2
    for actor in actors.values():
        actor.destroy_result = True
    manager.close()
    assert manager.actors == {}


@pytest.mark.parametrize('template', ['sudden_braking', 'pedestrian_crossing', 'occluded_pedestrian'])
def test_builtin_templates_search_complete_layout_when_starter_anchor_is_off_road(template):
    world = World()
    world.map.points[0] = Pose(Vec(0, 50, 0.5))
    definition = load_template(template)
    before = definition.model_dump()
    manager = ActorManager(world, CARLA, definition)
    actors = manager.spawn()
    assert set(actors) == {actor.id for actor in definition.actors}
    assert manager.resolved_spawns['ego'].position.y == pytest.approx(0)
    for actor in definition.actors[1:]:
        expected = compose(manager.resolved_spawns['ego'], Transform(
            position=actor.relative_spawn.offset_m,
            rotation=Rotation(yaw_deg=actor.relative_spawn.yaw_offset_deg)))
        assert manager.resolved_spawns[actor.id] == expected
    manager.close()
    assert world.actors == []
    assert definition.model_dump() == before


def test_auto_layout_retries_occupied_actor_and_removes_partial_attempt():
    world = World()
    world.fail_spawn_at = 2
    manager = ActorManager(world, CARLA, load_template('sudden_braking'))
    actors = manager.spawn()
    assert world.destroy_order == [1]
    assert len(actors) == len(world.actors) == 2
    assert all(actor.is_alive for actor in actors.values())
    manager.close()
    assert world.actors == []


def test_stale_native_spawn_transforms_do_not_corrupt_offsets_overlap_or_initial_velocity():
    world = World()
    world.map.lane_yaw = 180
    create = world.try_spawn_actor
    def stale(blueprint, pose):
        actor = create(blueprint, pose)
        actor.get_transform = lambda: Pose()
        return actor
    world.try_spawn_actor = stale
    definition = scenario(actors=[
        {'id': 'ego', 'type': 'ego_vehicle', 'spawn': {
            'position': {'x': 30, 'z': 0.5}, 'rotation': {'yaw_deg': 180}}, 'speed_mps': 12},
        {'id': 'lead', 'type': 'lead_vehicle', 'relative_spawn': {
            'anchor_actor_id': 'ego', 'offset_m': {'x': 20}}, 'speed_mps': 10},
    ])
    manager = ActorManager(world, CARLA, definition)
    actors = manager.spawn()
    assert manager.resolved_spawns['ego'].position.x == 30
    assert manager.resolved_spawns['lead'].position.x == pytest.approx(10)
    assert actors['ego'].target_velocities[0].x == pytest.approx(-12)
    assert actors['lead'].target_velocities[0].x == pytest.approx(-10)
    manager.close()


def test_selected_junction_route_controls_npc_despite_ambiguous_map_projection():
    world = World()
    manager = ActorManager(world, CARLA, scenario(actors=[
        {'id': 'ego', 'type': 'ego_vehicle', 'spawn_point_index': 0},
        {'id': 'crossing', 'type': 'crossing_vehicle', 'spawn_point_index': 1, 'speed_mps': 10},
    ]))
    crossing = manager.spawn()['crossing']
    manager._routes['crossing'] = [Transform(position=Vector3(x=x)) for x in range(20, 50, 2)]
    world.map.lane_yaw = 90
    world.map.get_waypoint = lambda *args, **kwargs: None
    manager.control_npcs({}, 0.05)
    assert crossing.controls[-1].steer == pytest.approx(0)


def test_template_pedestrian_stops_at_end_of_validated_crossing():
    world = World()
    manager = ActorManager(world, CARLA, load_template('pedestrian_crossing'))
    person = manager.spawn()['pedestrian']
    start = manager.resolved_spawns['pedestrian']
    person.pose = to_carla(compose(start, Transform(position=Vector3(x=8.1))), CARLA)
    manager.control_npcs({'pedestrian': ActorCommand(1.4)}, 0.05)
    assert person.controls[-1].speed == 0
    manager.close()


def test_template_without_suitable_junction_reports_actionable_error_without_spawning():
    world = World()
    with pytest.raises(SimulationError, match='No map-valid intersection layout.*another seed'):
        ActorManager(world, CARLA, load_template('intersection')).spawn()
    assert world.spawn_attempts == []
    assert world.actors == []
