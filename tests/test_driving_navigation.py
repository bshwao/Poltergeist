"""Finite task guidance and measured completion do not advance CARLA."""

from types import SimpleNamespace as NS
import math

import pytest

from scenarios import build_scenario
from shared.models import ActorState, DrivingTask, TelemetryFrame, Transform, Vector3
from simulator.carla_adapter import CarlaSimulationAdapter
from simulator.errors import SimulationError
from simulator.navigation import RouteTracker, TaskMonitor, finite_local_route, resolve_carla_route
from simulator.reference_scene import CarlaReferenceScene
from simulator.test_adapter import TestSimulationAdapter


def pose(x=0.0, y=0.0, z=0.0):
    return Transform(position=Vector3(x=float(x), y=float(y), z=float(z)))


def frame(x=0.0, y=0.0, z=0.0, *, time=0.0, speed=0.0, present=True):
    return TelemetryFrame(frame=round(time * 20), time_s=float(time),
        ego=ActorState(actor_id="ego", transform=pose(x, y, z) if present else None,
                       velocity_mps=None if speed is None else Vector3(x=float(speed))), actors=[])


class Location:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = x, y, z


class Rotation:
    def __init__(self, pitch=0.0, yaw=0.0, roll=0.0):
        self.pitch, self.yaw, self.roll = pitch, yaw, roll


class NativeTransform:
    def __init__(self, location, rotation):
        self.location, self.rotation = location, rotation


CARLA = NS(Location=Location, Rotation=Rotation, Transform=NativeTransform,
           LaneType=NS(Driving="Driving"))


class Point:
    def __init__(self, x, y, road=1, s=None):
        self.transform = NativeTransform(Location(x, y, 0), Rotation(yaw=90 if road == 2 else 0))
        self.road_id, self.section_id, self.lane_id = road, 0, -1
        self.s = float(x if s is None else s)
        self.lane_type, self.lane_width = "Driving", 3.5
        self.children = []
        self.calls = 0

    def next(self, distance):
        assert distance == 2.0
        self.calls += 1
        return self.children


class RoadMap:
    """Directed straight road with a genuine northbound junction branch."""
    def __init__(self):
        self.main = [Point(x, 0) for x in range(0, 26, 2)]
        for a, b in zip(self.main, self.main[1:]):
            a.children = [b]
        self.branch = [Point(8, y, road=2, s=y) for y in range(2, 12, 2)]
        self.main[4].children.append(self.branch[0])
        for a, b in zip(self.branch, self.branch[1:]):
            a.children = [b]
        self.points = self.main + self.branch

    def get_waypoint(self, location, **kwargs):
        assert kwargs == {"project_to_road": False, "lane_type": "Driving"}
        closest = min(self.points, key=lambda p: math.hypot(
            p.transform.location.x - location.x, p.transform.location.y - location.y))
        p = closest.transform.location
        return closest if math.hypot(p.x - location.x, p.y - location.y) <= 1.05 else None


def test_completion_requires_height_speed_and_continuous_hold():
    task = DrivingTask(destination=pose(5), completion_radius_m=0.5,
        completion_height_tolerance_m=1.0, completion_speed_mps=0.5, completion_hold_s=0.2)
    monitor = TaskMonitor(task, [pose(), pose(5)], frame(speed=7), "authoritative")
    monitor.update(frame(5, time=0.1, speed=3))
    monitor.update(frame(5, time=0.2))
    monitor.update(frame(5, z=5, time=0.3))
    assert not monitor.reached
    monitor.update(frame(5, time=0.4))
    monitor.update(frame(5, time=0.6))
    assert monitor.reached_at_s == 0.6
    outcome = monitor.outcome("completed", [], "duration_elapsed")
    assert outcome.status == "reached"
    assert outcome.initial_speed_mps == 7.0
    assert outcome.route_progress_m == outcome.route_length_m == 5.0


def test_completion_latches_but_failed_execution_is_not_success():
    task = DrivingTask(destination=pose(5), completion_radius_m=0.1)
    monitor = TaskMonitor(task, [pose(), pose(5)], frame(), "authoritative")
    monitor.update(frame(5, time=0.1))
    monitor.update(frame(9, time=0.2))
    assert monitor.reached
    assert monitor.outcome("completed", [], "duration_elapsed").final_distance_m == 4.0
    failed = monitor.outcome("failed", [], "execution_failed")
    assert failed.status == "failed" and failed.reached_at_s is None


def test_missing_measurements_break_hold_and_do_not_claim_completion():
    task = DrivingTask(destination=pose(), completion_hold_s=0.2, completion_speed_mps=0.5)
    monitor = TaskMonitor(task, [], frame(), "authoritative")
    monitor.update(frame(time=0.1, speed=None))
    monitor.update(frame(time=0.2))
    monitor.update(frame(time=0.3, present=False))
    assert not monitor.reached
    outcome = monitor.outcome("completed", [], "duration_elapsed")
    assert outcome.status == "unavailable" and outcome.final_distance_m is None
    assert outcome.route_length_m is None


def test_route_progress_is_bounded_measured_horizontal_distance():
    task = DrivingTask(destination=pose(10, 10))
    monitor = TaskMonitor(task, [pose(), pose(10), pose(10, 10)], frame(), "scripted")
    monitor.update(frame(10, 5, time=0.1))
    monitor.update(frame(5, time=0.2))
    assert monitor.route_progress_m == 15.0  # Furthest observed progress survives reversing.
    assert monitor.route_length_m == 20.0
    with pytest.raises(SimulationError, match="backwards"):
        monitor.update(frame(time=0.1))


def test_agent_navigation_receives_no_framework_heading_or_progress():
    task = DrivingTask(destination=pose(20), route_owner="agent")
    monitor = TaskMonitor(task, [], frame(), "authoritative")
    assert monitor.local_route(frame()) is None
    assert monitor.guidance_heading(frame()) is None
    assert monitor.outcome("completed", [], "duration_elapsed").route_progress_m is None


def test_local_route_has_exact_terminal_point_and_no_extension():
    route = [pose(), pose(5.25), pose(5.25, 2.25)]
    result = finite_local_route(route, pose(1.1))
    assert result[-1].position == route[-1].position
    assert all(p.position.x <= 5.25 and p.position.y <= 2.25 for p in result)
    assert finite_local_route(route, route[-1])[-1].position == route[-1].position
    assert finite_local_route(route, pose(0, z=10)) is None


def test_carla_global_route_follows_real_directed_junction_edges():
    road_map = RoadMap()
    task = DrivingTask(destination=pose(8, 10))
    route = resolve_carla_route(road_map, CARLA, task, pose())
    assert route[0].position == pose().position
    assert route[-1].position == task.destination.position
    assert any(point.position.x == 8 and point.position.y == 0 for point in route)
    assert all(point.position.y == 0 or point.position.x == 8 for point in route)


def test_destination_at_spawn_keeps_measured_initial_elevation():
    route = resolve_carla_route(RoadMap(), CARLA, DrivingTask(destination=pose()), pose(z=0.3))
    assert route[0].position.z == 0.3
    assert route[-1].position == pose().position


def test_zero_horizontal_route_remains_available_while_waiting_for_goal_hold():
    task = DrivingTask(destination=pose(), completion_hold_s=0.2,
        completion_height_tolerance_m=1.0, completion_speed_mps=0.3)
    route = [pose(z=0.25), pose()]
    monitor = TaskMonitor(task, route, frame(z=0.25), "authoritative")
    assert not monitor.reached
    assert monitor.local_route(frame(z=0.25))[-1].position == pose().position
    assert monitor.route_progress_m == monitor.route_length_m == 0.0
    monitor.update(frame(z=0.25, time=0.2))
    assert monitor.reached
    outcome = monitor.outcome("completed", [], "goal_reached")
    assert outcome.route_progress_m == outcome.route_length_m == 0.0
    assert finite_local_route(route, pose(z=20.0)) is None

    scene = CarlaReferenceScene(NS(get_map=lambda: RoadMap()), CARLA, NS(), "ego")
    scene.configure_driving_task(task, route)
    assert scene._route_ahead(pose(z=0.25))[-1].position == pose().position
    assert scene._route_ahead(pose(z=20.0)) is None


@pytest.mark.parametrize("route", [[pose()], [pose(z=0.25), pose()], [pose(), pose(10)]])
def test_task_guidance_uses_task_height_guard_for_every_route_shape(route):
    task = DrivingTask(destination=route[-1], completion_height_tolerance_m=2.0)
    tracker = RouteTracker(route, height_tolerance_m=2.0)
    assert tracker.local_route(pose(z=3.0)) is None
    scene = CarlaReferenceScene(NS(get_map=lambda: RoadMap()), CARLA, NS(), "ego")
    scene.configure_driving_task(task, route)
    assert scene._route_ahead(pose(z=3.0)) is None
    monitor = TaskMonitor(task, route, frame(z=3.0), "authoritative")
    assert monitor.local_route(frame(z=3.0)) is None
    assert monitor.route_progress_m is None


def test_carla_cannot_plan_against_directed_lane_or_between_disconnected_roads():
    road_map = RoadMap()
    with pytest.raises(SimulationError, match="not reachable"):
        resolve_carla_route(road_map, CARLA, DrivingTask(destination=pose()), pose(20))
    with pytest.raises(SimulationError, match="not reachable"):
        resolve_carla_route(road_map, CARLA, DrivingTask(destination=pose(18)), pose(20))


def test_carla_rejects_roadless_destination_and_wrong_elevation():
    road_map = RoadMap()
    with pytest.raises(SimulationError, match="outside a driving lane"):
        resolve_carla_route(road_map, CARLA, DrivingTask(destination=pose(50, 50)), pose())
    with pytest.raises(SimulationError, match="elevation"):
        resolve_carla_route(road_map, CARLA, DrivingTask(destination=pose(8, z=10)), pose())


def test_carla_route_never_uses_non_driving_graph_edges():
    road_map = RoadMap()
    road_map.main[3].lane_type = "Sidewalk"
    with pytest.raises(SimulationError, match="not reachable"):
        resolve_carla_route(road_map, CARLA, DrivingTask(destination=pose(20)), pose())


def test_carla_supplied_route_checks_corridor_and_forward_connectivity():
    road_map = RoadMap()
    task = DrivingTask(route=[pose(), pose(8), pose(8, 8)])
    assert resolve_carla_route(road_map, CARLA, task, pose()) == task.route
    with pytest.raises(SimulationError, match="outside a driving lane"):
        resolve_carla_route(road_map, CARLA, DrivingTask(route=[pose(), pose(8, 8)]), pose())


def test_supplied_route_keeps_directed_initial_approach_around_turn():
    road_map = RoadMap()
    task = DrivingTask(route=[pose(8, 4), pose(8, 8)])
    route = resolve_carla_route(road_map, CARLA, task, pose(6))
    assert [(p.position.x, p.position.y) for p in route] == [
        (6.0, 0.0), (8.0, 0.0), (8.0, 2.0), (8.0, 4.0), (8.0, 8.0)]


def test_stateful_loop_guidance_continues_on_final_leg_at_second_origin():
    route = [pose(), pose(10), pose(10, 10), pose(0, 10), pose(), pose(0, -10)]
    task = DrivingTask(route=route, target_speed_mps=2.0, completion_radius_m=0.1)
    monitor = TaskMonitor(task, route, frame(), "authoritative")
    for index, point in enumerate(route[1:-1], 1):
        monitor.update(frame(point.position.x, point.position.y, time=float(index)))
    local = monitor.local_route(frame(time=4.0))
    assert local[1].position.x == 0.0 and local[1].position.y < 0.0
    assert monitor.guidance_heading(frame(time=4.0)) == pytest.approx(-90.0)
    assert monitor.route_progress_m == pytest.approx(40.0)
    assert not monitor.reached


def test_near_future_parallel_leg_cannot_shortcut_route_progress():
    route = [pose(), pose(20), pose(20, 1), pose(0, 1), pose(0, 10)]
    tracker = RouteTracker(route)
    tracker.update(pose())
    tracker.update(pose(0, 1))
    assert tracker.progress_m == 0.0
    for _ in range(20):
        tracker.local_route(pose(0, 1))
    assert tracker.progress_m == 0.0  # Repeated queries cannot consume forward allowance.
    tracker.update(pose(5, 1))
    assert tracker.progress_m == pytest.approx(5.0)


def test_self_crossing_route_projects_to_current_traversal():
    route = [pose(), pose(10, 10), pose(0, 10), pose(10), pose(20)]
    tracker = RouteTracker(route)
    for point in [pose(), pose(5, 5), pose(10, 10), pose(0, 10), pose(5, 5)]:
        tracker.update(point)
    assert tracker.progress_m == pytest.approx(10 + 15 * math.sqrt(2))
    local = tracker.local_route(pose(5, 5))
    assert local[1].position.x > 5 and local[1].position.y < 5


def test_finite_scene_uses_cursor_when_route_revisits_spawn():
    route = [pose(), pose(10), pose(10, 10), pose(0, 10), pose(), pose(0, -10)]
    road_map = RoadMap()
    scene = CarlaReferenceScene(NS(get_map=lambda: road_map), CARLA, NS(), "ego")
    scene.configure_driving_task(DrivingTask(route=route), route)
    for point in route[:-1]:
        local = scene._route_ahead(point)
    assert local[1].position.y < 0
    assert local[-1].position == route[-1].position


def test_agent_route_owner_validates_goal_without_running_any_route_search():
    road_map = RoadMap()
    task = DrivingTask(destination=pose(8, 8), route_owner="agent")
    assert resolve_carla_route(road_map, CARLA, task, pose()) == []
    assert sum(p.calls for p in road_map.points) == 0


def test_synthetic_route_is_finite_and_exact_supplied_route_is_preserved():
    adapter = TestSimulationAdapter()
    initial = adapter.open(build_scenario("sudden_braking", seed=0))
    assert len(adapter.agent_context(initial).route) == 81  # Legacy remains unchanged.
    start = initial.ego.transform
    goal = pose(start.position.x + 12.25, start.position.y, start.position.z)
    task = DrivingTask(destination=goal)
    resolved = adapter.prepare_driving_task(task, initial)
    assert resolved == [start, goal]
    assert adapter.agent_context(initial).route[-1].position == goal.position
    task = DrivingTask(route_owner="agent", destination=goal)
    assert adapter.prepare_driving_task(task, initial) == []
    assert adapter.agent_context(initial).route is None
    adapter.close()


def test_finite_carla_scene_does_not_extend_or_mutate_owned_route():
    road_map = RoadMap()
    world = NS(get_map=lambda: road_map, get_actors=lambda: [])
    scene = CarlaReferenceScene(world, CARLA, NS(actor_ids={}, _routes={}), "ego")
    task = DrivingTask(route=[pose(), pose(8)])
    scene.configure_driving_task(task, task.route)
    local = scene._route_ahead(pose(1))
    assert local[-1].position == pose(8).position
    assert sum(p.calls for p in road_map.points) == 0
    local[-1].position.x = 100
    assert scene._route[-1].position.x == 8


def test_agent_owned_scene_still_observes_nearby_traffic_lights():
    road_map = RoadMap()
    light = NS(id=42, type_id="traffic.traffic_light",
        get_stop_waypoints=lambda: [road_map.main[4]], get_state=lambda: "Red")
    world = NS(get_map=lambda: road_map, get_actors=lambda: [light])
    scene = CarlaReferenceScene(world, CARLA, NS(actor_ids={}, _routes={}), "ego")
    scene.configure_driving_task(DrivingTask(destination=pose(8), route_owner="agent"), [])
    scene._raster = lambda pose: None
    context = scene.context(frame())
    assert context.route is None
    assert context.objects[0].kind == "traffic_light"
    assert context.objects[0].traffic_light_state == "red"


def test_adapter_preparation_is_read_only_and_returns_independent_route():
    road_map = RoadMap()
    adapter = CarlaSimulationAdapter(carla_module=CARLA)
    adapter.world = NS(get_map=lambda: road_map)
    adapter.actor_manager, adapter._opened = NS(), True
    task = DrivingTask(destination=pose(8, 8))
    resolved = adapter.prepare_driving_task(task, frame())
    resolved[-1].position.x = 100
    assert adapter._driving_route[-1].position.x == 8


@pytest.mark.parametrize("state,expected", [("red", True), ("green", False), ("yellow", False),
                                            ("off", False), ("unknown", None)])
def test_task_signal_observation_is_measured_without_navigation(state, expected):
    carla = NS(TrafficLightState=NS(Red="red", Green="green", Yellow="yellow", Off="off"))
    adapter = CarlaSimulationAdapter(carla_module=carla)
    adapter.ego = NS(is_at_traffic_light=lambda: True, get_traffic_light_state=lambda: state)
    assert adapter.traffic_light_observation() is expected
    adapter.ego = NS(is_at_traffic_light=lambda: False)
    assert adapter.traffic_light_observation() is False
    adapter.ego = NS()
    assert adapter.traffic_light_observation() is None
