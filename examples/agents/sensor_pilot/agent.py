"""A rule-based sensor integration example, with no learned perception.

LiDAR returns determine corridor braking; camera brightness gates visibility.
Finite route tracking uses the explicitly supplied framework route and ideal
simulated ego localization. This is not an autonomous road-driving stack: it
does not interpret traffic lights, infer object classes, or predict motion.
"""

import math
import struct

from shared.models import AgentStepResult, ControlInput
from simulator.agents import AutonomousAgent
from simulator.navigation import RouteTracker
from simulator.transforms import rotation_matrix


class SensorPilot(AutonomousAgent):
    """Consume actual sample bytes, never privileged other-actor state."""

    def load(self, configuration):
        speed = configuration.get("cruise_speed_mps", 4.0)
        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not math.isfinite(speed) or not 0 < speed <= 15:
            raise ValueError("cruise_speed_mps must be a finite number above zero and at most 15")
        self.cruise_speed_mps = float(speed)
        self.close()

    def reset(self, context):
        self.close()
        if context.protocol_version != "1.3" or context.driving_task is None:
            raise ValueError("Sensor pilot requires an SDK 1.3 driving task")
        if context.driving_task.route_owner != "framework" or not context.route:
            raise ValueError("Sensor pilot requires a supplied finite framework route")
        cameras = [sensor for sensor in context.sensors if sensor.type == "sensor.camera.rgb"]
        lidars = [sensor for sensor in context.sensors if sensor.type == "sensor.lidar.ray_cast"]
        if len(cameras) != 1 or len(lidars) != 1 or len(context.sensors) != 2:
            raise ValueError("Sensor pilot requires exactly one RGB camera and one LiDAR")
        if float(lidars[0].attributes["rotation_frequency"]) * context.fixed_delta_seconds < 1.0 - 1e-9:
            raise ValueError("Sensor pilot requires a complete LiDAR sweep per step; increase rotation_frequency or fixed_delta_seconds")
        self.camera, self.lidar = (sensor.model_copy(deep=True) for sensor in (cameras[0], lidars[0]))
        self.lidar_rotation = rotation_matrix(self.lidar.mount.rotation)
        self.task = context.driving_task.model_copy(deep=True)
        self.route_tracker = RouteTracker(context.route, height_tolerance_m=self.task.completion_height_tolerance_m)
        self.route_length_m = sum(math.hypot(a.position.x - b.position.x, a.position.y - b.position.y)
                                  for a, b in zip(context.route, context.route[1:]))

    def _visibility(self, sample):
        # Evenly spaced pixels throughout the image; ignore BGRA's alpha byte.
        raw = sample.raw_data()
        stride = max(1, sample.width * sample.height // 2048)
        brightness, dark, count = 0.0, 0, 0
        for index in range(0, sample.width * sample.height, stride):
            offset = index * 4
            value = (raw[offset] + raw[offset + 1] + raw[offset + 2]) / 3.0
            brightness += value
            dark += value < 8.0
            count += 1
        return brightness / count, dark / count

    def _nearest_return(self, sample):
        """Convert calibrated sensor-local points into ego coordinates.

        Configured hull dimensions are conservative teaching assumptions, not
        simulator bounding-box ground truth. Ground and returns above vehicles
        are ignored; no hidden object IDs, boxes, or class labels are consulted.
        """
        raw, nearest, accepted, points = sample.raw_data(), None, 0, 0
        offset, rotation = self.lidar.mount.position, self.lidar_rotation
        for point in struct.iter_unpack("<ffff", raw):
            points += 1
            x, y, z = (getattr(offset, axis) + sum(component * coordinate
                        for component, coordinate in zip(row, point[:3]))
                        for axis, row in zip(("x", "y", "z"), rotation))
            if z <= 0.25 or z > 2.5:
                continue
            if abs(x) <= 2.6 and abs(y) <= 1.2:
                continue
            if x <= 2.6 or abs(y) > 1.4:
                continue
            accepted += 1
            bumper_distance = x - 2.5
            nearest = bumper_distance if nearest is None else min(nearest, bumper_distance)
        return nearest, accepted, points

    def step(self, observation):
        if self.task is None:
            raise ValueError("Reset the sensor pilot before stepping")
        if observation.protocol_version != "1.3" or observation.observation_mode != "sensors":
            raise ValueError("Sensor pilot requires sensor observations")
        if observation.ego.transform is None or observation.ego.velocity_mps is None:
            raise ValueError("Sensor pilot requires declared ideal ego pose and velocity")
        if observation.driving_task != self.task:
            raise ValueError("Driving task changed after reset")
        samples = {sample.sensor_id: sample for sample in observation.sensors}
        if set(samples) != {self.camera.id, self.lidar.id}:
            raise ValueError("Sensor samples must match the reset calibrations")
        camera, lidar = samples[self.camera.id], samples[self.lidar.id]
        if camera.type != self.camera.type or lidar.type != self.lidar.type:
            raise ValueError("Sensor sample types do not match the reset calibrations")
        if (camera.width != int(self.camera.attributes["image_size_x"])
                or camera.height != int(self.camera.attributes["image_size_y"])
                or camera.fov_deg != float(self.camera.attributes["fov"])
                or lidar.channels != int(self.lidar.attributes["channels"])):
            raise ValueError("Sensor sample calibration changed after reset")
        pose, velocity = observation.ego.transform, observation.ego.velocity_mps
        speed = math.hypot(velocity.x, velocity.y, velocity.z)
        route = self.route_tracker.local_route(pose)
        if not route:
            raise ValueError("Vehicle has left the supplied finite route")
        target = route[min(len(route) - 1, max(1, int(speed * 0.5 + 2.0)))]
        dx, dy = target.position.x - pose.position.x, target.position.y - pose.position.y
        heading = math.degrees(math.atan2(dy, dx)) if math.hypot(dx, dy) > 1e-6 else pose.rotation.yaw_deg
        heading_error = (heading - pose.rotation.yaw_deg + 180.0) % 360.0 - 180.0
        goal = self.task.destination.position
        goal_distance = math.hypot(goal.x - pose.position.x, goal.y - pose.position.y)
        remaining = max(goal_distance, self.route_length_m - (self.route_tracker.progress_m or 0.0))
        cruise = self.task.target_speed_mps if self.task.target_speed_mps is not None else self.cruise_speed_mps
        desired = min(cruise, math.sqrt(4.0 * max(0.0, remaining - self.task.completion_radius_m * 0.6)))
        goal_stop = remaining <= self.task.completion_radius_m * 0.7
        nearest, corridor_points, total_points = self._nearest_return(lidar)
        mean_brightness, dark_fraction = self._visibility(camera)
        blind = mean_brightness < 8.0 and dark_fraction > 0.95
        braking_distance = 2.0 + 0.7 * speed + speed * speed / 8.0
        ttc = nearest / speed if nearest is not None and speed > 0.05 else None
        obstacle = nearest is not None and (nearest <= braking_distance or ttc is not None and ttc <= 1.5)
        reasons = (["camera_visibility_insufficient"] if blind else [])
        if not total_points:
            reasons.append("lidar_empty")
        if obstacle:
            reasons.append("lidar_corridor_hazard")
        if goal_stop:
            reasons.append("goal_stop")
        stop = bool(reasons)
        delta = desired - speed
        control = ControlInput(throttle=0.0 if stop else min(1.0, max(0.0, delta)),
            brake=1.0 if stop else min(1.0, max(0.0, -delta / 2.0)),
            steer=min(1.0, max(-1.0, heading_error / 45.0)))
        return AgentStepResult(protocol_version="1.3", frame=observation.frame, control=control,
            diagnostics={"maneuver": "brake" if stop else "follow_route",
                "reason_codes": reasons or ["sensor_corridor_clear"],
                "target_speed_mps": 0.0 if stop else desired,
                "assistance": ["simulated_ego_kinematics", "framework_route", "deterministic_route_tracking",
                    "configured_ego_hull", "goal_speed_controller", "rule_based_lidar_perception", "camera_visibility_guard"],
                "metadata": {"controller": "rule_based_sensor_teaching_example",
                    "nearest_lidar_return_m": nearest, "lidar_ttc_s": ttc,
                    "lidar_corridor_points": corridor_points, "lidar_total_points": total_points,
                    "camera_mean_brightness": mean_brightness, "camera_dark_fraction": dark_fraction,
                    "braking_distance_m": braking_distance, "goal_distance_m": goal_distance,
                    "desired_speed_mps": desired, "camera_frame": camera.frame, "lidar_frame": lidar.frame}})

    def close(self):
        self.task, self.route_tracker, self.camera, self.lidar = None, None, None, None
        self.lidar_rotation, self.route_length_m = None, 0.0
