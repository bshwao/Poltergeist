"""Optional CARLA 0.9.16 adapter; importing it does not connect to a server."""

from collections.abc import Mapping
from math import atan2, degrees, hypot
from typing import Any

from shared.models import ActorType, ControlInput, EvaluationSettings, Scenario, TelemetryFrame

from .actor_manager import ActorManager
from .carla_client import CarlaConnectionSettings, connect
from .errors import SimulationError
from .protocols import ActorCommand, DrivingObservation, SensorSample, SimulationOptions
from .rule_monitor import RuleMonitor
from .sensor_manager import SensorManager
from .spectator import position_spectator
from .world_manager import WorldManager


class CarlaSimulationAdapter:
    mode = "carla"

    def __init__(self, *, client=None, carla_module=None, connection_settings=None, options=None, evaluation_settings=None):
        self.options = SimulationOptions() if options is None else SimulationOptions.model_validate(options.model_dump())
        self.client = client
        self.carla = carla_module
        self.connection_settings: CarlaConnectionSettings | None = connection_settings
        self.evaluation_settings = EvaluationSettings() if evaluation_settings is None else EvaluationSettings.model_validate(evaluation_settings.model_dump())
        self.world = None
        self.world_manager = None
        self.actor_manager = None
        self.sensors = None
        self.rules = None
        self.scenario = None
        self.ego = None
        self._origin_time = None
        self._rule_time_offset = 0.0
        self._opened = False
        self._versions = {}
        self._reference_scene = None

    def open(self, scenario: Scenario) -> TelemetryFrame:
        if self._opened:
            raise SimulationError("CARLA adapter is already open")
        self._opened = True
        self.scenario = scenario
        if self.carla is None:
            try:
                import carla
            except ImportError as error:
                raise SimulationError("CARLA 0.9.16 Python API is required; use Python 3.12 or mode='test'") from error
            self.carla = carla
        if self.client is None:
            self.client = connect(self.connection_settings)
        self._versions = {
            "server_version": self.client.get_server_version(),
            "client_version": self.client.get_client_version(),
        }
        for kind, version in self._versions.items():
            if str(version).split("-")[0] != "0.9.16":
                raise SimulationError(f"Expected CARLA 0.9.16 {kind}, received {version!r}")
        self.world_manager = WorldManager(self.client, self.carla, allow_map_reload=self.options.allow_map_reload,
                                          no_rendering_mode=self.options.no_rendering_mode)
        self.world = self.world_manager.open(scenario)
        self.actor_manager = ActorManager(self.world, self.carla, scenario)
        actors = self.actor_manager.spawn()
        ego_spec = next(actor for actor in scenario.actors if actor.type == ActorType.EGO_VEHICLE)
        self.ego_id = ego_spec.id
        self.ego = actors[self.ego_id]
        self.sensors = SensorManager(
            self.world, self.ego, self.carla, options=self.options,
            actor_ids=self.actor_manager.actor_ids,
            actor_id_resolver=lambda native: self.actor_manager.actor_ids.get(native, f"world:{native}"),
        )
        self.sensors.open()
        self.rules = RuleMonitor(
            self.world, self.ego, self.carla, actor_id=self.ego_id,
            speeding_tolerance_mps=self.evaluation_settings.speeding_tolerance_mps,
            cache_static_geometry=True,
        )
        before = self.world.get_snapshot()
        # Prime temporal rule checks before the initialization tick; otherwise
        # the first recorded frame can never have signal-crossing coverage.
        self.rules.observe(before.frame, 0.0)
        # Actors and attached periodic sensors need one initialization tick.
        self.world.tick()
        snapshot = self.world.get_snapshot()
        self._origin_time = snapshot.timestamp.elapsed_seconds
        self._rule_time_offset = snapshot.timestamp.elapsed_seconds - before.timestamp.elapsed_seconds
        frame = self._frame(snapshot, ControlInput())
        position_spectator(self.world, self.carla, frame, {actor.id for actor in scenario.actors})
        return frame

    def _frame(self, snapshot, control: ControlInput) -> TelemetryFrame:
        states = self.actor_manager.states(snapshot)
        by_id = {state.actor_id: state for state in states}
        if self.ego_id not in by_id:
            raise SimulationError("Ego actor is absent from the world snapshot")
        time_s = snapshot.timestamp.elapsed_seconds - self._origin_time
        collisions = self.sensors.collect(snapshot.frame)
        violations, checks = self.rules.observe(snapshot.frame, time_s + self._rule_time_offset)
        return TelemetryFrame(
            frame=snapshot.frame, time_s=max(0.0, time_s), ego=by_id[self.ego_id],
            actors=[state for state in states if state.actor_id != self.ego_id],
            control=control, collisions=collisions, traffic_violations=violations, rule_checks=checks,
        )

    def observation(self, frame: TelemetryFrame, target_speed_mps: float) -> DrivingObservation:
        heading = None
        light_red = None
        try:
            if frame.ego.transform is not None:
                velocity = frame.ego.velocity_mps
                speed = 0.0 if velocity is None else hypot(velocity.x, velocity.y, velocity.z)
                heading = self.actor_manager.route_heading(self.ego_id, frame.ego.transform, max(2.0, speed * 0.5))
            if heading is None:
                waypoint = self.world.get_map().get_waypoint(
                    self.ego.get_location(), project_to_road=False, lane_type=self.carla.LaneType.Driving,
                )
                if waypoint is None:
                    target_speed_mps = 0.0
                else:
                    current_yaw = frame.ego.transform.rotation.yaw_deg if frame.ego.transform else 0.0
                    velocity = frame.ego.velocity_mps
                    speed = 0.0 if velocity is None else hypot(velocity.x, velocity.y, velocity.z)
                    choices = list(waypoint.next(max(2.0, speed * 0.5)))
                    if choices:
                        def choice_key(candidate):
                            yaw = candidate.transform.rotation.yaw
                            return (abs((yaw - current_yaw + 180) % 360 - 180), candidate.road_id, candidate.lane_id)
                        chosen = min(choices, key=choice_key)
                        origin = self.ego.get_location()
                        point = chosen.transform.location
                        heading = degrees(atan2(point.y - origin.y, point.x - origin.x))
                    else:
                        target_speed_mps = 0.0
                        heading = waypoint.transform.rotation.yaw
        except (AttributeError, RuntimeError):
            target_speed_mps = 0.0
        try:
            if self.ego.is_at_traffic_light():
                light_red = self.ego.get_traffic_light_state() == self.carla.TrafficLightState.Red
            else:
                light_red = False
        except (AttributeError, RuntimeError):
            pass
        return DrivingObservation(
            frame.ego, tuple(frame.actors or []), self.scenario.fixed_delta_seconds,
            target_speed_mps, heading, light_red,
        )

    def step(self, control: ControlInput, commands: Mapping[str, ActorCommand]) -> TelemetryFrame:
        self.actor_manager.control_npcs(commands, self.scenario.fixed_delta_seconds)
        self.ego.apply_control(self.carla.VehicleControl(
            throttle=control.throttle, steer=control.steer, brake=control.brake,
            hand_brake=control.hand_brake, reverse=control.reverse,
        ))
        expected_frame = self.world.tick()
        snapshot = self.world.get_snapshot()
        if snapshot.frame != expected_frame:
            raise SimulationError("Another client advanced the CARLA world; use a dedicated synchronous server")
        return self._frame(snapshot, control)

    def agent_context(self, frame: TelemetryFrame):
        """Optional privileged features; no client connection or world tick."""
        if self.world is None or self.actor_manager is None or not self._opened:
            raise SimulationError("CARLA simulation is not open")
        if self._reference_scene is None:
            from .reference_scene import CarlaReferenceScene
            self._reference_scene = CarlaReferenceScene(self.world, self.carla, self.actor_manager, self.ego_id)
        return self._reference_scene.context(frame)

    def finalize(self, frames: list[TelemetryFrame]) -> list[TelemetryFrame]:
        return self.sensors.finalize(frames) if self.sensors is not None else frames

    def sensor_samples(self) -> tuple[SensorSample, ...]:
        return () if self.sensors is None else self.sensors.samples()

    def runtime_info(self) -> dict[str, Any]:
        if self.scenario is None or self.world is None:
            return {}
        return dict(
            adapter=self.mode, **self._versions, map_name=self.world.get_map().name,
            fixed_delta_seconds=self.scenario.fixed_delta_seconds, seed=self.scenario.seed,
            resolved_spawns={} if self.actor_manager is None else self.actor_manager.resolved_spawns,
        )

    def close(self) -> None:
        errors = []
        for name, resource in (("sensors", self.sensors), ("actors", self.actor_manager), ("world", self.world_manager)):
            if resource is not None:
                try:
                    resource.close()
                except Exception as error:
                    errors.append(f"{name}: {error}")
        self._opened = False
        self._reference_scene = None
        if errors:
            raise SimulationError("CARLA cleanup failed: " + "; ".join(errors))
