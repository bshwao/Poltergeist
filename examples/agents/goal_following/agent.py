"""Small deterministic SDK 1.2 example, not a pretrained driving model."""

import math

from shared.models import AgentStepResult, ControlInput
from simulator.agents import AutonomousAgent
from simulator.navigation import RouteTracker


class GoalFollowingAgent(AutonomousAgent):
    """Track supplied polylines; direct-goal navigation is synthetic-only.

    This controller has no learned perception, obstacle avoidance, or road
    planner. It demonstrates the explicit ownership and goal interface only.
    """

    def load(self, configuration):
        speed = configuration.get("cruise_speed_mps", 4.0)
        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not math.isfinite(speed) or speed < 0:
            raise ValueError("cruise_speed_mps must be a finite nonnegative number")
        self.cruise_speed_mps = float(speed)
        self.task, self.route, self.route_tracker = None, [], None

    def reset(self, context):
        if context.protocol_version != "1.2" or context.driving_task is None:
            raise ValueError("Goal-following example requires an SDK 1.2 driving task")
        self.task = context.driving_task.model_copy(deep=True)
        self.route = [point.model_copy(deep=True) for point in context.route]
        self.route_tracker = RouteTracker(self.route,
            height_tolerance_m=self.task.completion_height_tolerance_m)
        self.route_length_m = sum(math.hypot(a.position.x - b.position.x, a.position.y - b.position.y)
                                  for a, b in zip(self.route, self.route[1:]))

    def step(self, observation):
        if self.task is None or observation.ego.transform is None or observation.ego.velocity_mps is None:
            raise ValueError("Goal-following example requires a reset task and measured ego pose/velocity")
        if self.task.route_owner == "agent" and (
                observation.scene is None or observation.scene.source != "synthetic"):
            raise ValueError("Direct-goal example supports synthetic test mode only; it has no CARLA road planner")
        pose, goal = observation.ego.transform, self.task.destination
        speed = math.hypot(observation.ego.velocity_mps.x, observation.ego.velocity_mps.y,
                           observation.ego.velocity_mps.z)
        distance = math.hypot(goal.position.x - pose.position.x, goal.position.y - pose.position.y)
        if self.task.route_owner == "framework":
            route = self.route_tracker.local_route(pose)
            if not route:
                raise ValueError("Vehicle has left the supplied finite route")
            target = route[min(len(route) - 1, max(1, int(speed * 0.5 + 2.0)))]
            stopping_distance = max(distance, self.route_length_m - (self.route_tracker.progress_m or 0.0))
        else:
            target = goal
            stopping_distance = distance
        dx, dy = target.position.x - pose.position.x, target.position.y - pose.position.y
        heading = math.degrees(math.atan2(dy, dx)) if math.hypot(dx, dy) > 1e-6 else pose.rotation.yaw_deg
        error = (heading - pose.rotation.yaw_deg + 180.0) % 360.0 - 180.0
        cruise = observation.target_speed_mps if observation.target_speed_mps is not None else self.cruise_speed_mps
        desired = min(cruise, math.sqrt(2.0 * 2.0 * max(0.0, stopping_distance - self.task.completion_radius_m * 0.6)))
        stop = stopping_distance <= self.task.completion_radius_m * 0.7
        delta = desired - speed
        control = ControlInput(
            throttle=0.0 if stop else min(1.0, max(0.0, delta)),
            brake=1.0 if stop else min(1.0, max(0.0, -delta / 2.0)),
            steer=min(1.0, max(-1.0, error / 45.0)),
        )
        return AgentStepResult(protocol_version="1.2", frame=observation.frame, control=control,
            diagnostics={"target_speed_mps": desired, "metadata": {
                "controller": "deterministic_teaching_example", "route_owner": self.task.route_owner,
                "desired_speed_mps": desired, "goal_distance_m": distance,
                "speed_source": "task_advisory" if observation.target_speed_mps is not None else "agent_configuration"}})

    def close(self):
        self.task, self.route, self.route_tracker = None, [], None
