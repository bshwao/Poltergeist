"""Low-level control for PlanT's learned path and speed.

Adapted from PlanT/PlanT_agent.py and carla_garage controllers/config at
6269e5a43c6f6b984d2bd97e3ec4a025ebefbeef; MIT, see LICENSE.plant2.
These deterministic controllers track model predictions. They do not inspect
hazards, traffic lights, scenario events, or the supplied navigation route.
"""

import math

import numpy as np
from scipy.interpolate import PchipInterpolator

from shared.models import ControlInput


class PlanTController:
    def __init__(self):
        self.error_history: list[float] = []

    def reset(self):
        self.error_history.clear()

    @staticmethod
    def interpolate_path(points):
        path = np.concatenate((np.zeros_like(points[:1]), np.asarray(points)))
        shifted = np.roll(path, 1, axis=0)
        shifted[0] = shifted[1]
        distances = np.cumsum(np.linalg.norm(path - shifted, axis=1))
        distances += np.arange(len(distances)) * 1e-4
        interpolator = PchipInterpolator(distances, path, axis=0)
        samples = interpolator(np.arange(0.1, distances[-1], 0.1))
        return samples if len(samples) else path[None, -1]

    def control(self, path, waypoints, current_speed: float, *, speed_cap_mps: float | None = None) -> tuple[ControlInput, float]:
        """Track predictions; return upstream speed before an explicit task cap.

        An explicit navigation task may supply a separately reported upper
        tracking bound. The upstream standstill startup limit is retained.
        With no task bound this preserves the upstream controller.
        """
        path, waypoints = np.asarray(path), np.asarray(waypoints)
        if (path.shape != (20, 2) or waypoints.shape != (8, 2)
                or not np.isfinite(path).all() or not np.isfinite(waypoints).all()):
            raise ValueError("PlanT returned invalid path/waypoint predictions")
        desired_speed = float(np.linalg.norm(waypoints[2] - waypoints[3]) * 4.0)
        if current_speed < 0.01:
            mean_speed = float(np.linalg.norm(waypoints[:-1] - waypoints[1:], axis=-1).mean() * 4.0)
            desired_speed = min(mean_speed, 0.1)
        if speed_cap_mps is not None and (not math.isfinite(speed_cap_mps) or speed_cap_mps < 0):
            raise ValueError("Tracking speed cap must be finite and nonnegative")
        tracking_speed = desired_speed if speed_cap_mps is None else min(desired_speed, speed_cap_mps)
        throttle, brake = self.longitudinal(tracking_speed, current_speed)
        if current_speed < 0.05 and brake:
            lateral_path = np.asarray([[1., 0.], [2., 0.], [3., 0.], [4., 0.]])
        else:
            lateral_path = self.interpolate_path(path)
        lookahead = np.clip(0.9755321901954155 * current_speed * 3.6 + 1.9152884533402488, 24., 105.)
        index = int(min(lookahead, len(lateral_path) - 1))
        target = lateral_path[index]
        error = math.atan2(float(target[1]), float(target[0])) * 2.0 / math.pi
        self.error_history.append(error)
        self.error_history = self.error_history[-6:]
        derivative = 0. if len(self.error_history) == 1 else self.error_history[-1] - self.error_history[-2]
        integral = sum(self.error_history) / len(self.error_history)
        steer = float(np.clip(3.118357247806046 * error + 1.3782508892109167 * derivative + 0.6406067986034124 * integral, -1., 1.))
        return ControlInput(throttle=float(throttle), steer=steer, brake=float(brake)), desired_speed

    @staticmethod
    def longitudinal(desired_speed: float, current_speed: float) -> tuple[float, bool]:
        if desired_speed < 0.05:
            return 0., True
        desired_speed = max(0.278, desired_speed) * 3.6
        current_speed *= 3.6
        error = desired_speed - current_speed
        if error > 1.89:
            return 1., False
        coefficients = np.asarray([
            1.1990342347353184, -0.8057602384167799, 1.710818710950062,
            0.921890257450335, 1.556497522998393, -0.7013479734904027,
        ])
        if current_speed / desired_speed > 1.031266635497984:
            return 0., True
        clipped_error = max(error, 0.) / 100.
        speed = current_speed / 100.
        features = np.asarray([speed, speed**2, 100. * clipped_error,
                               clipped_error**2, speed * clipped_error, speed**2 * clipped_error])
        return float(np.clip(features @ coefficients, 0., 1.)), False
