"""Conservative ego traffic-rule observations from CARLA map and actor data.

Signal events require an actual forward stop-line crossing in an associated
lane. Stop signs use their transformed trigger boxes and observed stops, rather
than treating mere proximity to a sign as a violation. A missing API or failed
read removes the affected rule from the checked set for that observation.
"""

from dataclasses import dataclass
from math import hypot, isfinite
from typing import Any

from shared.models import TrafficRule, TrafficViolation


def _xyz(value: Any) -> tuple[float, float, float]:
    result = float(value.x), float(value.y), float(value.z)
    if not all(isfinite(number) for number in result):
        raise ValueError("Non-finite CARLA position/vector")
    return result


def _dot(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _sub(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, ...]:
    return tuple(x - y for x, y in zip(a, b))


def _lane(waypoint: Any) -> tuple[int, int]:
    return int(waypoint.road_id), int(waypoint.lane_id)


def _matrix_point(matrix: Any, point: tuple[float, ...]) -> tuple[float, ...]:
    return tuple(sum(float(matrix[row][column]) * point[column] for column in range(3))
                 + float(matrix[row][3]) for row in range(3))


def _segment_box_interval(
    start: tuple[float, ...], end: tuple[float, ...], extent: Any,
) -> tuple[float, float] | None:
    """Slab intersection in the box's local axes, including its own rotation."""
    enter, leave = 0.0, 1.0
    for first, last, half_size in zip(start, end, _xyz(extent)):
        change = last - first
        if abs(change) < 1e-12:
            if abs(first) > half_size:
                return None
            continue
        lower, upper = sorted(((-half_size - first) / change, (half_size - first) / change))
        enter, leave = max(enter, lower), min(leave, upper)
        if enter > leave:
            return None
    return enter, leave


@dataclass
class _StopVisit:
    known_entry: bool
    stopped_s: float = 0.0
    last_stopped: bool = False
    last_time: float = 0.0


class RuleMonitor:
    SPEEDING_TOLERANCE_MPS = 0.5
    STOP_SPEED_MPS = 0.1
    STOP_DURATION_S = 0.5

    def __init__(
        self, world: Any, ego: Any, carla_module: Any, *, actor_id: str = "ego",
        speeding_tolerance_mps: float = 0.5,
        cache_static_geometry: bool = False,
    ) -> None:
        if isinstance(speeding_tolerance_mps, bool) or not isfinite(speeding_tolerance_mps) or speeding_tolerance_mps < 0:
            raise ValueError("Speeding tolerance must be finite and non-negative")
        if not isinstance(cache_static_geometry, bool):
            raise ValueError("Static-geometry cache option must be a boolean")
        self.world, self.ego, self.carla = world, ego, carla_module
        self.actor_id = actor_id
        self.speeding_tolerance_mps = speeding_tolerance_mps
        # Dedicated CARLA episodes keep map signals fixed. Reusing their lane
        # geometry avoids a blocking get_stop_waypoints RPC for every light on
        # every tick. Signal state and actor membership still remain live reads.
        # The default supports callers whose test/world geometry can move.
        self.cache_static_geometry = cache_static_geometry
        self._light_geometry: dict[int, tuple[Any, ...]] = {}
        self._stop_geometry: dict[int, tuple[Any, Any, Any]] = {}
        self._previous_position: tuple[float, float, float] | None = None
        self._previous_front: tuple[float, float, float] | None = None
        self._previous_lane: tuple[int, int] | None = None
        self._previous_time: float | None = None
        self._light_states: dict[int, Any] = {}
        self._stop_inside: dict[int, bool] = {}
        self._stop_visits: dict[int, _StopVisit] = {}
        self._last_frame: int | None = None

    def _event(self, rule: TrafficRule, source: str, frame: int, details: str) -> TrafficViolation:
        return TrafficViolation(
            rule=rule, actor_id=self.actor_id, source_id=source,
            event_id=f"rule:{self.actor_id}:{rule.value}:{source}:{frame}", details=details,
        )

    def observe(self, frame: int, time_s: float) -> tuple[list[TrafficViolation], list[TrafficRule]]:
        if self._last_frame is not None and frame <= self._last_frame:
            raise ValueError("Rule observations require increasing frame numbers")
        if not isfinite(time_s) or time_s < 0 or (
            self._previous_time is not None and time_s <= self._previous_time
        ):
            raise ValueError("Rule observations require increasing finite simulation times")
        self._last_frame = frame
        events: list[TrafficViolation] = []
        checked: list[TrafficRule] = []
        try:
            transform = self.ego.get_transform()
            location = transform.location
            position = _xyz(location)
            velocity = _xyz(self.ego.get_velocity())
            speed = hypot(*velocity)
            # Compose the full box pose, including local center and rotation.
            box = self.ego.bounding_box
            box_transform = self.carla.Transform(box.location, box.rotation)
            front_location = box_transform.transform(self.carla.Location(x=float(box.extent.x)))
            front = _xyz(transform.transform(front_location))
        except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError):
            self._previous_front = self._previous_lane = None
            self._previous_position = None
            self._previous_time = time_s
            self._light_states.clear()
            self._light_geometry.clear()
            self._stop_geometry.clear()
            self._stop_visits.clear()
            self._stop_inside.clear()
            return events, checked

        try:
            limit = float(self.ego.get_speed_limit()) / 3.6
            if not isfinite(limit) or limit <= 0:
                raise ValueError("Speed limit unavailable")
            checked.append(TrafficRule.SPEEDING)
            if speed > limit + self.speeding_tolerance_mps:
                events.append(self._event(TrafficRule.SPEEDING, f"speed-limit:{limit:g}", frame,
                                          f"Speed {speed:.3f} m/s exceeds limit {limit:.3f} m/s"))
        except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError):
            pass

        waypoint = None
        carla_map = None
        current_lane = None
        try:
            carla_map = self.world.get_map()
            waypoint = carla_map.get_waypoint(location, project_to_road=False, lane_type=self.carla.LaneType.Any)
            if waypoint is not None:
                current_lane = _lane(waypoint)
                source = f"road:{current_lane[0]}:lane:{current_lane[1]}"
                lane_type = waypoint.lane_type
                checked.append(TrafficRule.SIDEWALK)
                if lane_type == self.carla.LaneType.Sidewalk and speed > self.STOP_SPEED_MPS:
                    events.append(self._event(TrafficRule.SIDEWALK, source, frame, "Ego moving on sidewalk lane"))
                if lane_type == self.carla.LaneType.Driving:
                    lane_forward = _xyz(waypoint.transform.get_forward_vector())
                    checked.append(TrafficRule.WRONG_WAY)
                    if speed > 0.5 and _dot(velocity, lane_forward) < -0.1:
                        events.append(self._event(TrafficRule.WRONG_WAY, source, frame,
                                                  "Ego velocity opposes mapped lane direction"))
                elif lane_type == self.carla.LaneType.Sidewalk:
                    # No wrong-way restriction applies outside a driving lane.
                    checked.append(TrafficRule.WRONG_WAY)
        except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError):
            waypoint = None
            current_lane = None

        try:
            actors = self.world.get_actors()
            lights = list(actors.filter("traffic.traffic_light*"))
            stops = list(actors.filter("traffic.stop*"))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            lights = stops = None

        if waypoint is not None and lights is not None:
            if self._observe_lights(lights, front, current_lane, frame, events):
                checked.append(TrafficRule.RED_LIGHT)
        else:
            self._light_states.clear()
            self._light_geometry.clear()
        if waypoint is not None and stops is not None and carla_map is not None:
            if self._observe_stops(stops, carla_map, location, current_lane, speed, time_s, frame, events):
                checked.append(TrafficRule.STOP_SIGN)
        else:
            self._stop_inside.clear()
            self._stop_visits.clear()
            self._stop_geometry.clear()

        self._previous_front, self._previous_lane, self._previous_time = front, current_lane, time_s
        self._previous_position = position
        return events, checked

    def _observe_lights(
        self, lights: list[Any], front: tuple[float, float, float], current_lane: tuple[int, int] | None,
        frame: int, events: list[TrafficViolation],
    ) -> bool:
        complete = self._previous_front is not None and self._previous_lane is not None
        states = {}
        seen = set()
        for light in lights:
            light_id = None
            try:
                light_id = int(light.id)
                seen.add(light_id)
                state = light.get_state()
                states[light_id] = state
                stop_points = self._light_geometry.get(light_id) if self.cache_static_geometry else None
                if stop_points is None:
                    stop_points = tuple(light.get_stop_waypoints())
                    # Unknown/failed geometry must be retried, never frozen as
                    # an empty cached success for the remainder of the run.
                    if stop_points and self.cache_static_geometry:
                        self._light_geometry[light_id] = stop_points
                if not stop_points:
                    complete = False
                for stop in stop_points:
                    lane = _lane(stop)
                    if lane not in {self._previous_lane, current_lane}:
                        continue
                    previous_state = self._light_states.get(light_id)
                    if previous_state is None or state != previous_state:
                        complete = False
                        continue
                    red = self.carla.TrafficLightState.Red
                    known_states = {red, self.carla.TrafficLightState.Green, self.carla.TrafficLightState.Yellow}
                    if state not in known_states:
                        complete = False
                        continue
                    if self._previous_front is None or self._previous_lane != lane:
                        continue
                    origin = _xyz(stop.transform.location)
                    forward = _xyz(stop.transform.get_forward_vector())
                    previous_distance = _dot(_sub(self._previous_front, origin), forward)
                    current_distance = _dot(_sub(front, origin), forward)
                    if previous_distance <= 0 < current_distance:
                        fraction = -previous_distance / (current_distance - previous_distance)
                        crossing = tuple(a + fraction * (b - a) for a, b in zip(self._previous_front, front))
                        difference = _sub(crossing, origin)
                        lateral = abs(difference[0] * -forward[1] + difference[1] * forward[0])
                        if lateral <= float(stop.lane_width) / 2 and abs(difference[2]) <= 3 and state == red:
                            events.append(self._event(TrafficRule.RED_LIGHT, f"carla:{light_id}", frame,
                                                      "Ego front crossed mapped stop line under red signal"))
                            break
            except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError):
                complete = False
                self._light_geometry.pop(light_id, None)
        for light_id in set(self._light_geometry) - seen:
            self._light_geometry.pop(light_id, None)
        self._light_states = states
        return complete

    def _observe_stops(
        self, stops: list[Any], carla_map: Any, location: Any,
        current_lane: tuple[int, int] | None, speed: float, time_s: float,
        frame: int, events: list[TrafficViolation],
    ) -> bool:
        complete = True
        seen = set()
        for sign in stops:
            try:
                sign_id = int(sign.id)
                seen.add(sign_id)
                geometry = self._stop_geometry.get(sign_id) if self.cache_static_geometry else None
                if geometry is None:
                    box = sign.trigger_volume
                    transform = sign.get_transform()
                    center = transform.transform(self.carla.Location(x=box.location.x, y=box.location.y, z=box.location.z))
                    sign_waypoint = carla_map.get_waypoint(center, project_to_road=True, lane_type=self.carla.LaneType.Driving)
                    if sign_waypoint is None:
                        raise ValueError("Stop-sign lane unavailable")
                    if self.cache_static_geometry:
                        self._stop_geometry[sign_id] = box, transform, sign_waypoint
                else:
                    box, transform, sign_waypoint = geometry
                relevant = current_lane == _lane(sign_waypoint)
                inside = bool(box.contains(location, transform)) and relevant
                previous_inside = self._stop_inside.get(sign_id)
                self._stop_inside[sign_id] = inside
                visit = self._stop_visits.get(sign_id)
                if inside:
                    if visit is None:
                        visit = self._stop_visits[sign_id] = _StopVisit(
                            known_entry=previous_inside is False,
                            last_time=time_s, last_stopped=speed <= self.STOP_SPEED_MPS,
                        )
                    else:
                        if visit.last_stopped and speed <= self.STOP_SPEED_MPS:
                            visit.stopped_s += time_s - visit.last_time
                        elif visit.stopped_s < self.STOP_DURATION_S:
                            # Separate brief pauses cannot add up to a full stop.
                            visit.stopped_s = 0.0
                        visit.last_time = time_s
                        visit.last_stopped = speed <= self.STOP_SPEED_MPS
                    if not visit.known_entry:
                        complete = False
                elif visit is not None:
                    if visit.known_entry:
                        if visit.stopped_s < self.STOP_DURATION_S:
                            events.append(self._event(TrafficRule.STOP_SIGN, f"carla:{sign_id}", frame,
                                                      "Ego exited stop trigger without an observed 0.5 s stop"))
                    else:
                        complete = False
                    self._stop_visits.pop(sign_id, None)
                elif previous_inside is False and self._previous_position is not None and (
                    relevant or self._previous_lane == _lane(sign_waypoint)
                ):
                    sign_inverse = transform.get_inverse_matrix()
                    box_inverse = self.carla.Transform(box.location, box.rotation).get_inverse_matrix()
                    start = _matrix_point(box_inverse, _matrix_point(sign_inverse, self._previous_position))
                    end = _matrix_point(box_inverse, _matrix_point(sign_inverse, _xyz(location)))
                    interval = _segment_box_interval(start, end, box.extent)
                    if interval is not None and interval[1] - interval[0] > 1e-9:
                        # Even a coarse sample can prove a roll-through if the
                        # entire interval was too short to contain a full stop.
                        if self._previous_time is not None and time_s - self._previous_time < self.STOP_DURATION_S:
                            events.append(self._event(TrafficRule.STOP_SIGN, f"carla:{sign_id}", frame,
                                                      "Ego crossed entire stop trigger between ticks without time for a stop"))
                        else:
                            complete = False
            except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError):
                complete = False
                # A discontinuous sign read cannot establish whether a stop happened.
                sign_id = getattr(sign, "id", None)
                self._stop_inside.pop(sign_id, None)
                self._stop_visits.pop(sign_id, None)
                self._stop_geometry.pop(sign_id, None)
        for sign_id in set(self._stop_visits) - seen:
            complete = False
            self._stop_visits.pop(sign_id, None)
            self._stop_inside.pop(sign_id, None)
        for sign_id in set(self._stop_geometry) - seen:
            self._stop_geometry.pop(sign_id, None)
        return complete
