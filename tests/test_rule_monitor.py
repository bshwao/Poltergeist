"""Map and actor fakes isolate crossing semantics and missing-data coverage."""

from fnmatch import fnmatch
from math import cos, radians, sin
from types import SimpleNamespace as NS

import pytest

from shared.models import TrafficRule
from simulator.rule_monitor import RuleMonitor


def vector(x=0.0, y=0.0, z=0.0):
    return NS(x=x, y=y, z=z)


class Pose:
    def __init__(self, location=None, yaw=0.0):
        self.location, self.yaw = location or vector(), yaw

    def get_forward_vector(self):
        return vector(cos(radians(self.yaw)), sin(radians(self.yaw)))

    def transform(self, point):
        angle = radians(self.yaw)
        return vector(
            self.location.x + point.x * cos(angle) - point.y * sin(angle),
            self.location.y + point.x * sin(angle) + point.y * cos(angle),
            self.location.z + point.z,
        )

    def get_inverse_matrix(self):
        angle = radians(self.yaw)
        c, s = cos(angle), sin(angle)
        x, y, z = self.location.x, self.location.y, self.location.z
        return [[c, s, 0.0, -c*x - s*y], [-s, c, 0.0, s*x - c*y],
                [0.0, 0.0, 1.0, -z], [0.0, 0.0, 0.0, 1.0]]


class Box:
    def __init__(self, location=None, extent=None):
        self.location, self.extent = location or vector(), extent or vector(2.0, 1.5, 2.0)
        self.rotation = NS(yaw=0.0)

    def contains(self, point, transform):
        angle = radians(transform.yaw)
        dx, dy, dz = point.x - transform.location.x, point.y - transform.location.y, point.z - transform.location.z
        local = vector(dx * cos(angle) + dy * sin(angle), -dx * sin(angle) + dy * cos(angle), dz)
        return all(abs(getattr(local, axis) - getattr(self.location, axis)) <= getattr(self.extent, axis)
                   for axis in ("x", "y", "z"))


def waypoint(x=0.0, y=0.0, *, yaw=0.0, lane_id=1, lane_type="Driving"):
    return NS(road_id=1, lane_id=lane_id, lane_type=lane_type,
              transform=Pose(vector(x, y), yaw=yaw), lane_width=3.5)


CARLA = NS(Location=vector, Transform=lambda location, rotation: Pose(location, yaw=rotation.yaw),
           LaneType=NS(Any="Any", Driving="Driving", Sidewalk="Sidewalk"),
           TrafficLightState=NS(Red="Red", Green="Green", Yellow="Yellow"))


class Ego:
    def __init__(self):
        self.pose, self.velocity = Pose(vector(-5.0)), vector(5.0)
        self.limit = 36.0
        self.bounding_box = Box(extent=vector(2.0, 1.0, 1.0))

    def get_transform(self):
        return self.pose

    def get_velocity(self):
        return self.velocity

    def get_speed_limit(self):
        return self.limit


class ActorList(list):
    def filter(self, pattern):
        return [actor for actor in self if fnmatch(actor.type_id, pattern)]


class World:
    def __init__(self, actors=()):
        self.actors = ActorList(actors)
        self.waypoint = waypoint()
        self.map_missing = False

    def get_actors(self):
        return self.actors

    def get_map(self):
        if self.map_missing:
            raise RuntimeError("map unavailable")
        return self

    def get_waypoint(self, location, *, project_to_road, lane_type):
        assert lane_type in {"Any", "Driving"}
        return self.waypoint


class Light:
    id, type_id = 81, "traffic.traffic_light"

    def __init__(self, *, lane_id=1):
        self.state, self.stops = "Red", [waypoint(lane_id=lane_id)]

    def get_state(self):
        return self.state

    def get_stop_waypoints(self):
        return self.stops


class CountingLight(Light):
    def __init__(self, *, actor_id=81):
        super().__init__()
        self.id, self.state_reads, self.geometry_reads = actor_id, 0, 0
        self.geometry_error = self.state_error = False

    def get_state(self):
        self.state_reads += 1
        if self.state_error:
            raise RuntimeError("signal state unavailable")
        return super().get_state()

    def get_stop_waypoints(self):
        self.geometry_reads += 1
        if self.geometry_error:
            raise RuntimeError("signal geometry unavailable")
        return super().get_stop_waypoints()


class Stop:
    id, type_id = 82, "traffic.stop"

    def __init__(self, *, yaw=0.0):
        self.trigger_volume, self.pose = Box(), Pose(yaw=yaw)

    def get_transform(self):
        return self.pose


def test_speeding_wrong_way_and_sidewalk_use_map_and_si_units():
    world, ego = World(), Ego()
    ego.velocity = vector(11.0)
    monitor = RuleMonitor(world, ego, CARLA, actor_id="hero")
    events, checked = monitor.observe(1, 0.0)
    assert [(event.rule, event.actor_id) for event in events] == [(TrafficRule.SPEEDING, "hero")]
    assert TrafficRule.SPEEDING in checked
    ego.velocity = vector(-5.0)
    events, _ = monitor.observe(2, 0.05)
    assert [event.rule for event in events] == [TrafficRule.WRONG_WAY]
    assert events[0].source_id == "road:1:lane:1"
    world.waypoint = waypoint(lane_type="Sidewalk")
    events, checked = monitor.observe(3, 0.1)
    assert [event.rule for event in events] == [TrafficRule.SIDEWALK]
    assert TrafficRule.WRONG_WAY in checked


def test_speeding_boundary_and_stationary_sidewalk_are_compliant():
    world, ego = World(), Ego()
    ego.velocity = vector(10.5)
    monitor = RuleMonitor(world, ego, CARLA)
    events, checked = monitor.observe(1, 0.0)
    assert events == []
    assert TrafficRule.SPEEDING in checked
    world.waypoint = waypoint(lane_type="Sidewalk")
    ego.velocity = vector()
    assert monitor.observe(2, 0.05)[0] == []


def test_speeding_threshold_uses_evaluator_settings():
    world, ego = World(), Ego()
    ego.velocity = vector(11.0)
    monitor = RuleMonitor(world, ego, CARLA, speeding_tolerance_mps=2.0)
    assert monitor.observe(1, 0.0)[0] == []


def test_waiting_at_red_does_not_report_crossing_and_front_crossing_does():
    light, ego = Light(), Ego()
    monitor = RuleMonitor(World([light]), ego, CARLA)
    ego.pose.location.x = -3.0  # Front bumper is at -1 m, before stop line.
    assert monitor.observe(1, 0.0)[0] == []
    ego.velocity = vector()
    events, checked = monitor.observe(2, 0.05)
    assert events == []
    assert TrafficRule.RED_LIGHT in checked
    ego.pose.location.x = -1.0
    ego.velocity = vector(5.0)
    events, _ = monitor.observe(3, 0.1)
    assert len(events) == 1
    assert events[0].rule == TrafficRule.RED_LIGHT
    assert events[0].source_id == "carla:81"
    assert events[0].event_id == "rule:ego:red_light:carla:81:3"
    assert monitor.observe(4, 0.15)[0] == []


def test_red_line_for_other_lane_is_not_ego_violation():
    light, ego = Light(lane_id=-1), Ego()
    monitor = RuleMonitor(World([light]), ego, CARLA)
    ego.pose.location.x = -3.0
    monitor.observe(1, 0.0)
    ego.pose.location.x = -1.0
    assert monitor.observe(2, 0.05)[0] == []


def test_stop_line_crossing_uses_rotated_offset_bounding_box_front():
    light, ego = Light(), Ego()
    ego.bounding_box.rotation.yaw = 90.0
    ego.bounding_box.location.x = 0.1
    ego.bounding_box.extent.x = 1.0
    monitor = RuleMonitor(World([light]), ego, CARLA)
    ego.pose.location.x = -0.5  # Actual rotated box front x=-.4; origin+x extent is already past line.
    assert monitor.observe(1, 0.0)[0] == []
    ego.pose.location.x = 0.5
    events, checked = monitor.observe(2, 0.05)
    assert [event.rule for event in events] == [TrafficRule.RED_LIGHT]
    assert TrafficRule.RED_LIGHT in checked


def test_signal_transition_during_crossing_is_unknown():
    light, ego = Light(), Ego()
    monitor = RuleMonitor(World([light]), ego, CARLA)
    ego.pose.location.x = -3.0
    monitor.observe(1, 0.0)
    ego.pose.location.x = -1.0
    light.state = "Green"
    events, checked = monitor.observe(2, 0.05)
    assert events == []
    assert TrafficRule.RED_LIGHT not in checked


def test_map_failure_preserves_speeding_evidence_but_omits_map_checks():
    world, ego = World(), Ego()
    world.map_missing = True
    ego.velocity = vector(15.0)
    events, checked = RuleMonitor(world, ego, CARLA).observe(1, 0.0)
    assert [event.rule for event in events] == [TrafficRule.SPEEDING]
    assert checked == [TrafficRule.SPEEDING]


def test_unknown_speed_limit_omits_speeding_check():
    world, ego = World(), Ego()
    ego.limit = 0.0
    events, checked = RuleMonitor(world, ego, CARLA).observe(1, 0.0)
    assert events == []
    assert TrafficRule.SPEEDING not in checked


def test_stop_trigger_roll_through_has_source_and_stable_frame_id():
    sign, ego = Stop(), Ego()
    monitor = RuleMonitor(World([sign]), ego, CARLA)
    monitor.observe(1, 0.0)
    ego.pose.location.x = 0.0
    events, checked = monitor.observe(2, 0.05)
    assert events == []
    assert TrafficRule.STOP_SIGN in checked
    ego.pose.location.x = 4.0
    events, checked = monitor.observe(3, 0.1)
    assert len(events) == 1
    assert events[0].rule == TrafficRule.STOP_SIGN
    assert events[0].source_id == "carla:82"
    assert events[0].event_id == "rule:ego:stop_sign:carla:82:3"
    assert TrafficRule.STOP_SIGN in checked


def test_observed_stop_satisfies_stop_sign():
    sign, ego = Stop(), Ego()
    monitor = RuleMonitor(World([sign]), ego, CARLA)
    monitor.observe(1, 0.0)
    ego.pose.location.x, ego.velocity = 0.0, vector()
    monitor.observe(2, 0.05)
    monitor.observe(3, 0.55)
    ego.pose.location.x, ego.velocity = 4.0, vector(5.0)
    events, checked = monitor.observe(4, 0.6)
    assert events == []
    assert TrafficRule.STOP_SIGN in checked


def test_multiple_short_pauses_do_not_accumulate_into_full_stop():
    sign, ego = Stop(), Ego()
    monitor = RuleMonitor(World([sign]), ego, CARLA)
    monitor.observe(1, 0.0)
    ego.pose.location.x, ego.velocity = 0.0, vector()
    monitor.observe(2, 0.05)
    monitor.observe(3, 0.35)
    ego.velocity = vector(1.0)
    monitor.observe(4, 0.4)
    ego.velocity = vector()
    monitor.observe(5, 0.45)
    monitor.observe(6, 0.75)
    ego.pose.location.x, ego.velocity = 4.0, vector(5.0)
    assert monitor.observe(7, 0.8)[0][0].rule == TrafficRule.STOP_SIGN


def test_stop_trigger_skipped_between_ticks_still_reports_roll_through():
    sign, ego = Stop(), Ego()
    monitor = RuleMonitor(World([sign]), ego, CARLA)
    monitor.observe(1, 0.0)
    ego.pose.location.x = 5.0
    events, checked = monitor.observe(2, 0.1)
    assert [event.rule for event in events] == [TrafficRule.STOP_SIGN]
    assert TrafficRule.STOP_SIGN in checked


def test_long_unobserved_stop_crossing_is_unknown():
    sign, ego = Stop(), Ego()
    monitor = RuleMonitor(World([sign]), ego, CARLA)
    monitor.observe(1, 0.0)
    ego.pose.location.x = 5.0
    events, checked = monitor.observe(2, 2.0)
    assert events == []
    assert TrafficRule.STOP_SIGN not in checked


def test_spawn_inside_stop_trigger_cannot_assert_prior_noncompliance():
    sign, ego = Stop(), Ego()
    ego.pose.location.x = 0.0
    monitor = RuleMonitor(World([sign]), ego, CARLA)
    events, checked = monitor.observe(1, 0.0)
    assert events == []
    assert TrafficRule.STOP_SIGN not in checked
    ego.pose.location.x = 4.0
    events, checked = monitor.observe(2, 0.05)
    assert events == []
    assert TrafficRule.STOP_SIGN not in checked
    assert TrafficRule.STOP_SIGN in monitor.observe(3, 0.1)[1]


def test_missing_one_sign_does_not_discard_other_positive_events():
    broken, sign, ego = Stop(), Stop(), Ego()
    broken.id, broken.trigger_volume = 99, None
    monitor = RuleMonitor(World([broken, sign]), ego, CARLA)
    monitor.observe(1, 0.0)
    ego.pose.location.x = 0.0
    monitor.observe(2, 0.05)
    ego.pose.location.x = 4.0
    events, checked = monitor.observe(3, 0.1)
    assert [event.rule for event in events] == [TrafficRule.STOP_SIGN]
    assert TrafficRule.STOP_SIGN not in checked


def test_wrong_lane_stop_trigger_is_not_ego_obligation():
    sign, ego, world = Stop(), Ego(), World()
    world.actors.append(sign)
    original = world.get_waypoint

    def get_waypoint(location, *, project_to_road, lane_type):
        return waypoint(lane_id=-1) if project_to_road else original(
            location, project_to_road=project_to_road, lane_type=lane_type,
        )

    world.get_waypoint = get_waypoint
    monitor = RuleMonitor(world, ego, CARLA)
    monitor.observe(1, 0.0)
    ego.pose.location.x = 0.0
    monitor.observe(2, 0.05)
    ego.pose.location.x = 4.0
    assert monitor.observe(3, 0.1)[0] == []


def test_rotated_stop_trigger_uses_world_transformed_box():
    sign, ego = Stop(yaw=90.0), Ego()
    ego.pose.location = vector(0.0, -4.0)
    monitor = RuleMonitor(World([sign]), ego, CARLA)
    monitor.observe(1, 0.0)
    ego.pose.location = vector(0.0, 0.0)
    monitor.observe(2, 0.05)
    ego.pose.location = vector(0.0, 4.0)
    assert monitor.observe(3, 0.1)[0][0].rule == TrafficRule.STOP_SIGN


def test_runtime_missing_actor_set_does_not_assert_signal_or_stop_compliance():
    world, ego = World(), Ego()

    def unavailable():
        raise RuntimeError("actor read failed")

    world.get_actors = unavailable
    monitor = RuleMonitor(world, ego, CARLA)
    monitor.observe(1, 0.0)
    events, checked = monitor.observe(2, 0.05)
    assert events == []
    assert TrafficRule.RED_LIGHT not in checked
    assert TrafficRule.STOP_SIGN not in checked


@pytest.mark.parametrize("frame,time_s", [(1, 0.05), (2, 0.0), (2, float("nan"))])
def test_invalid_observation_order_is_rejected(frame, time_s):
    monitor = RuleMonitor(World(), Ego(), CARLA)
    monitor.observe(1, 0.0)
    with pytest.raises(ValueError):
        monitor.observe(frame, time_s)


def test_static_signal_geometry_is_cached_but_red_crossing_and_live_state_remain_checked():
    light, ego = CountingLight(), Ego()
    monitor = RuleMonitor(World([light]), ego, CARLA, cache_static_geometry=True)
    ego.pose.location.x = -3.0
    monitor.observe(1, 0.0)
    assert TrafficRule.RED_LIGHT in monitor.observe(2, 0.05)[1]
    ego.pose.location.x = -1.0
    events, checked = monitor.observe(3, 0.1)
    assert [event.rule for event in events] == [TrafficRule.RED_LIGHT]
    assert TrafficRule.RED_LIGHT in checked
    assert light.geometry_reads == 1
    assert light.state_reads == 3


def test_geometry_cache_is_opt_in_and_keeps_dynamic_world_default():
    light, ego = CountingLight(), Ego()
    monitor = RuleMonitor(World([light]), ego, CARLA)
    monitor.observe(1, 0.0)
    light.stops = [waypoint(lane_id=-1)]
    ego.pose.location.x = 2.0
    events, checked = monitor.observe(2, 0.05)
    assert events == []
    assert TrafficRule.RED_LIGHT in checked
    assert light.geometry_reads == light.state_reads == 2


@pytest.mark.parametrize("state", ["Green", "Yellow", "Unknown", None])
def test_cached_geometry_preserves_unknown_transition_interval(state):
    light, ego = CountingLight(), Ego()
    monitor = RuleMonitor(World([light]), ego, CARLA, cache_static_geometry=True)
    ego.pose.location.x = -3.0
    monitor.observe(1, 0.0)
    light.state, ego.pose.location.x = state, -1.0
    events, checked = monitor.observe(2, 0.05)
    assert events == []
    assert TrafficRule.RED_LIGHT not in checked
    assert light.geometry_reads == 1
    assert light.state_reads == 2
    if state in {"Unknown", None}:
        assert TrafficRule.RED_LIGHT not in monitor.observe(3, 0.1)[1]


def test_cached_signal_state_failure_invalidates_geometry_and_keeps_interval_unknown():
    light, ego = CountingLight(), Ego()
    monitor = RuleMonitor(World([light]), ego, CARLA, cache_static_geometry=True)
    monitor.observe(1, 0.0)
    light.state_error = True
    events, checked = monitor.observe(2, 0.05)
    assert events == [] and TrafficRule.RED_LIGHT not in checked
    assert monitor._light_geometry == {}
    light.state_error = False
    assert TrafficRule.RED_LIGHT not in monitor.observe(3, 0.1)[1]
    assert TrafficRule.RED_LIGHT in monitor.observe(4, 0.15)[1]
    assert light.geometry_reads == 2
    assert light.state_reads == 4


@pytest.mark.parametrize("unavailable", ["error", "empty", "invalid"])
def test_failed_or_empty_signal_geometry_is_retried_without_hiding_other_violations(unavailable):
    good, bad, ego = CountingLight(), CountingLight(actor_id=99), Ego()
    if unavailable == "error":
        bad.geometry_error = True
    elif unavailable == "empty":
        bad.stops = []
    else:
        bad.stops = [NS()]  # Cannot identify the affected lane.
    monitor = RuleMonitor(World([bad, good]), ego, CARLA, cache_static_geometry=True)
    ego.pose.location.x = -3.0
    monitor.observe(1, 0.0)
    ego.pose.location.x = -1.0
    events, checked = monitor.observe(2, 0.05)
    assert [event.source_id for event in events] == ["carla:81"]
    assert TrafficRule.RED_LIGHT not in checked
    assert bad.geometry_reads == 2
    assert bad.id not in monitor._light_geometry
    bad.geometry_error, bad.stops = False, [waypoint(lane_id=-1)]
    assert TrafficRule.RED_LIGHT in monitor.observe(3, 0.1)[1]
    assert bad.geometry_reads == 3
    assert good.geometry_reads == 1


def test_signal_cache_prunes_destroyed_ids_and_observes_new_light_without_prior_state():
    light, ego = CountingLight(), Ego()
    world = World([light])
    monitor = RuleMonitor(world, ego, CARLA, cache_static_geometry=True)
    monitor.observe(1, 0.0)
    assert set(monitor._light_geometry) == {81}
    world.actors.clear()
    monitor.observe(2, 0.05)
    assert monitor._light_geometry == {}
    replacement = CountingLight(actor_id=81)
    novel = CountingLight(actor_id=100)
    world.actors.extend([replacement, novel])
    assert TrafficRule.RED_LIGHT not in monitor.observe(3, 0.1)[1]
    assert TrafficRule.RED_LIGHT in monitor.observe(4, 0.15)[1]
    assert light.geometry_reads == replacement.geometry_reads == novel.geometry_reads == 1
    assert replacement.state_reads == novel.state_reads == 2


@pytest.mark.parametrize("failure", ["map", "actors", "ego"])
def test_cached_geometry_is_discarded_when_observation_coverage_breaks(failure):
    light, ego, world = CountingLight(), Ego(), World()
    world.actors.append(light)
    monitor = RuleMonitor(world, ego, CARLA, cache_static_geometry=True)
    monitor.observe(1, 0.0)
    def unavailable():
        raise RuntimeError("temporary read failure")
    if failure == "map":
        world.map_missing = True
    elif failure == "actors":
        world.get_actors = unavailable
    else:
        ego.get_transform = unavailable
    assert TrafficRule.RED_LIGHT not in monitor.observe(2, 0.05)[1]
    assert monitor._light_geometry == {}


def test_stop_geometry_cache_preserves_observed_stop_and_roll_through():
    for stop_completely in (False, True):
        sign, ego = Stop(), Ego()
        reads = []
        original = sign.get_transform
        def counted():
            reads.append(True)
            return original()
        sign.get_transform = counted
        monitor = RuleMonitor(World([sign]), ego, CARLA, cache_static_geometry=True)
        monitor.observe(1, 0.0)
        ego.pose.location.x, ego.velocity = 0.0, vector() if stop_completely else vector(5.0)
        monitor.observe(2, 0.05)
        monitor.observe(3, 0.55)
        ego.pose.location.x, ego.velocity = 4.0, vector(5.0)
        events, checked = monitor.observe(4, 0.6)
        assert [event.rule for event in events] == ([] if stop_completely else [TrafficRule.STOP_SIGN])
        assert TrafficRule.STOP_SIGN in checked
        assert len(reads) == 1


def test_stop_geometry_cache_retries_unknown_sign_and_prunes_destroyed_actor():
    sign, ego = Stop(), Ego()
    world = World([sign])
    original = world.get_waypoint
    failed = [True]
    def stop_waypoint(location, *, project_to_road, lane_type):
        return None if project_to_road and failed[0] else original(
            location, project_to_road=project_to_road, lane_type=lane_type)
    world.get_waypoint = stop_waypoint
    monitor = RuleMonitor(world, ego, CARLA, cache_static_geometry=True)
    assert TrafficRule.STOP_SIGN not in monitor.observe(1, 0.0)[1]
    assert monitor._stop_geometry == {}
    failed[0] = False
    assert TrafficRule.STOP_SIGN in monitor.observe(2, 0.05)[1]
    assert set(monitor._stop_geometry) == {82}
    world.actors.clear()
    monitor.observe(3, 0.1)
    assert monitor._stop_geometry == {}


@pytest.mark.parametrize("value", [None, 0, 1, "yes"])
def test_static_geometry_cache_option_requires_bool(value):
    with pytest.raises(ValueError, match="boolean"):
        RuleMonitor(World(), Ego(), CARLA, cache_static_geometry=value)
