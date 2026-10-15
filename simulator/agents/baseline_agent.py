"""A small deterministic driving controller without a CARLA dependency.

The controller follows a route heading and regulates speed. Emergency braking
uses observed oriented boxes and constant-velocity TTC; it is a development
baseline, with no lane planning, perception, or intersection negotiation.
"""

import math

from evaluation.distance_metrics import bounding_box_clearance
from evaluation.ttc_calculator import time_to_collision
from shared.models import ControlInput
from simulator.protocols import DrivingObservation

from .base_agent import BaseAgent


class BaselineAgent(BaseAgent):
    def __init__(
        self,
        *,
        target_speed_mps: float | None = None,
        emergency_ttc_s: float = 1.5,
        minimum_clearance_m: float = 2.0,
    ) -> None:
        self.target_speed_mps = (
            None if target_speed_mps is None
            else _finite_number(target_speed_mps, "target_speed_mps", minimum=0.0)
        )
        self.emergency_ttc_s = _finite_number(emergency_ttc_s, "emergency_ttc_s", minimum=0.0)
        self.minimum_clearance_m = _finite_number(minimum_clearance_m, "minimum_clearance_m", minimum=0.0)

    def run_step(self, observation: DrivingObservation) -> ControlInput:
        if not isinstance(observation, DrivingObservation):
            raise TypeError("observation must be a DrivingObservation")
        _finite_number(observation.dt_s, "dt_s", minimum=0.0, exclusive=True)
        requested_speed = _finite_number(observation.target_speed_mps, "target_speed_mps", minimum=0.0)
        target = self.target_speed_mps if self.target_speed_mps is not None else requested_speed
        ego = observation.ego
        if ego.transform is None or ego.velocity_mps is None:
            return ControlInput(brake=1.0)
        # Revalidate telemetry at this public boundary, including mutated models.
        ego = type(ego).model_validate(ego.model_dump())
        speed = math.hypot(ego.velocity_mps.x, ego.velocity_mps.y, ego.velocity_mps.z)
        yaw = ego.transform.rotation.yaw_deg
        heading = yaw if observation.route_heading_deg is None else _finite_number(
            observation.route_heading_deg, "route_heading_deg"
        )
        error_deg = (heading - yaw + 180.0) % 360.0 - 180.0
        steer = max(-1.0, min(1.0, error_deg / 45.0))
        if observation.traffic_light_red is True:
            return ControlInput(steer=steer, brake=1.0)

        forward = (math.cos(math.radians(yaw)), math.sin(math.radians(yaw)))
        for actor in observation.actors:
            ttc = time_to_collision(ego, actor, horizon_s=max(self.emergency_ttc_s, 0.01))
            if ttc is not None and ttc <= self.emergency_ttc_s:
                return ControlInput(steer=steer, brake=1.0)
            clearance = bounding_box_clearance(ego, actor)
            if clearance is None or clearance > self.minimum_clearance_m:
                continue
            other_pose = actor.bounding_box_transform or actor.transform
            if other_pose is None:
                continue
            displacement = (
                other_pose.position.x - ego.transform.position.x,
                other_pose.position.y - ego.transform.position.y,
            )
            ahead = sum(component * direction for component, direction in zip(displacement, forward)) >= 0.0
            if clearance == 0.0 or ahead:
                return ControlInput(steer=steer, brake=1.0)

        speed_error = target - speed
        if target == 0.0 and speed < 0.05:
            return ControlInput(steer=steer, brake=1.0)
        return ControlInput(
            steer=steer,
            throttle=max(0.0, min(1.0, speed_error / 3.0)),
            brake=max(0.0, min(1.0, -speed_error / 8.0)),
        )


def _finite_number(
    value: float, name: str, *, minimum: float | None = None, exclusive: bool = False
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result) or (minimum is not None and (
        result < minimum or (exclusive and result == minimum)
    )):
        raise ValueError(f"{name} must be a finite number within its allowed range")
    return result
