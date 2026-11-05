"""Trained RGB/LiDAR fusion with explicit assisted inputs and no fallback policy."""
from io import BytesIO
import math
from time import perf_counter

from shared.models import AgentDiagnostics, AgentStepResult, ControlInput
from simulator.agent_sensors import resolve_sensor_specs
from simulator.agents.sdk import AutonomousAgent, AgentCompatibilityError, AgentLifecycleError
from .checkpoint import checkpoint_bytes, CHECKPOINT_SHA256, SOURCE_REVISION
from .config import inference_config
from .preprocessing import camera_rgb, lidar_histogram, RouteTarget, world_waypoints
from .rig import manifest


class TransFuserAgent(AutonomousAgent):
    def __init__(self):
        self._model = self._torch = self._route = self._last = None

    def load(self, configuration):
        if self._model is not None:
            raise AgentLifecycleError("TransFuser is already loaded")
        allowed = {"checkpoint_path", "device", "torch_threads", "inference_budget_s"}
        if not isinstance(configuration, dict) or configuration.keys() - allowed:
            raise ValueError("Unsupported TransFuser configuration")
        path = configuration.get("checkpoint_path")
        if not isinstance(path, str) or not path.strip():
            raise ValueError("Prepare the pinned TransFuser checkpoint first")
        if configuration.get("device", "cpu") != "cpu":
            raise ValueError("The supported TransFuser pack uses CPU inference")
        threads, budget = configuration.get("torch_threads", 2), configuration.get("inference_budget_s", 2.)
        if type(threads) is not int or not 1 <= threads <= 16:
            raise ValueError("TransFuser torch_threads must be a whole number from 1 to 16")
        if type(budget) not in {int, float} or not math.isfinite(budget) or not .05 <= budget <= 30.:
            raise ValueError("TransFuser policy step budget must be between 0.05 and 30 seconds")
        data = checkpoint_bytes(path)
        try:
            import torch
            from .network import TransFuser
        except ImportError as error:
            raise AgentCompatibilityError("TransFuser needs the isolated Torch/torchvision CPU environment") from error
        torch.set_num_threads(threads)
        model = TransFuser(inference_config(), "cpu")
        state = torch.load(BytesIO(data), map_location="cpu", weights_only=True)
        model.load_state_dict(state, strict=True)
        del state, data
        model.eval()
        self._torch, self._model, self._budget = torch, model, float(budget)
        self._parameters = sum(p.numel() for p in model.parameters())

    def reset(self, context):
        self._route = self._last = self._predicted = None
        if self._model is None:
            raise AgentLifecycleError("Load TransFuser before reset")
        if (context.protocol_version != "1.3" or context.driving_task is None
                or context.driving_task.route_owner != "framework"
                or not math.isclose(context.fixed_delta_seconds, .05, abs_tol=1e-9)):
            raise AgentCompatibilityError("TransFuser requires SDK 1.3, a framework route and 20 Hz simulation")
        expected = resolve_sensor_specs(manifest("unused")['sensors'])
        if sorted(context.sensors, key=lambda s: s.id) != sorted(expected, key=lambda s: s.id):
            raise AgentCompatibilityError("TransFuser camera/LiDAR calibration differs from the released rig")
        self._route = RouteTarget(context.route)
        self._step = 0
        for controller in (self._model.turn_controller, self._model.speed_controller):
            controller._window.clear()
            controller._window.extend([0.] * controller._window.maxlen)
            controller._max = controller._min = 0.

    def step(self, observation):
        if self._model is None or self._route is None:
            raise AgentLifecycleError("Reset TransFuser before requesting controls")
        if (observation.observation_mode != "sensors" or observation.ego_state_source != "simulated_ego_kinematics"
                or observation.sensor_source not in {"carla_sensors", "deterministic_fixture"}
                or observation.actors is not None or observation.scene is not None or observation.traffic_light_red is not None
                or observation.ego.transform is None or observation.ego.velocity_mps is None):
            raise AgentCompatibilityError("TransFuser requires declared sensors and ideal ego kinematics without actor ground truth")
        samples = {s.sensor_id: s for s in observation.sensors}
        if len(observation.sensors) != 2 or set(samples) != {"front-camera", "lidar"}:
            raise AgentCompatibilityError("TransFuser requires exactly its camera and LiDAR samples")
        if (not math.isclose(observation.dt_s, .05, abs_tol=1e-9)
                or any(s.frame != observation.frame or not math.isclose(s.timestamp_s, observation.time_s, abs_tol=1e-6)
                       for s in samples.values())
                or (self._last is not None and (observation.frame != self._last[0] + 1
                    or not math.isclose(observation.time_s - self._last[1], .05, rel_tol=1e-5, abs_tol=1e-6)))):
            raise AgentCompatibilityError("TransFuser sensor timeline is repeated, missing or out of sync")
        started = perf_counter()
        # Validate both current inputs even on a held-prediction frame.
        image, lidar = camera_rgb(samples["front-camera"]), lidar_histogram(samples["lidar"])
        velocity = observation.ego.velocity_mps
        speed = math.hypot(velocity.x, velocity.y, velocity.z)
        if not math.isfinite(speed):
            raise AgentCompatibilityError("TransFuser ego speed exceeds the supported finite range")
        target = self._route.next(observation.ego.transform)
        infer = self._step > 0 and (self._step <= 4 or self._step % 2 == 0)
        inference_time = 0.
        if self._step == 0:
            # Match the released one-sample initialization; this is not a
            # substituted driving policy. It is explicitly reported below.
            control = ControlInput()
        else:
            torch = self._torch
            with torch.inference_mode():
                if infer:
                    inference_started = perf_counter()
                    predicted = self._model([torch.from_numpy(image).unsqueeze(0)],
                        [torch.from_numpy(lidar).unsqueeze(0)], torch.tensor([target], dtype=torch.float32),
                        torch.tensor([speed], dtype=torch.float32))
                    if tuple(predicted.shape) != (1, 4, 2) or not torch.isfinite(predicted).all().item():
                        raise AgentCompatibilityError("TransFuser produced invalid learned waypoints")
                    self._predicted = predicted
                    self._values = predicted[0].tolist()
                    self._trajectory = world_waypoints(self._values, observation.ego.transform)
                    self._prediction_inputs = {"frame": observation.frame, "time_s": observation.time_s,
                        "camera_sha256": samples["front-camera"].sha256, "lidar_sha256": samples["lidar"].sha256}
                    inference_time = perf_counter() - inference_started
                steer, throttle, brake, tracked = self._model.control_pid(self._predicted.clone(),
                    torch.tensor([speed], dtype=torch.float32))
            control = ControlInput(steer=float(steer), throttle=float(throttle), brake=float(brake))
        elapsed = perf_counter() - started
        if elapsed > self._budget:
            raise AgentCompatibilityError("TransFuser exceeded its policy step budget; no fallback control was substituted")
        self._last = observation.frame, observation.time_s
        self._step += 1
        return AgentStepResult(protocol_version="1.3", frame=observation.frame, control=control,
            diagnostics=AgentDiagnostics(assistance=["ideal_ego_kinematics", "framework_route"],
                maneuver="fusion_warmup" if self._step == 1 else "learned_rgb_lidar_waypoints",
                target_speed_mps=None if self._step == 1 else tracked["desired_speed"],
                planned_trajectory=[] if self._step == 1 else self._trajectory,
                metadata={"checkpoint_sha256": CHECKPOINT_SHA256, "source_revision": SOURCE_REVISION,
                    "parameter_count": self._parameters, "device": "cpu", "input_source": observation.sensor_source,
                    "camera_frame": samples["front-camera"].frame, "lidar_frame": samples["lidar"].frame,
                    "camera_sha256": samples["front-camera"].sha256, "lidar_sha256": samples["lidar"].sha256,
                    "model_inference_performed": infer,
                    "prediction_inputs": None if self._step == 1 else self._prediction_inputs,
                    "model_waypoints": [] if self._step == 1 else self._values,
                    "inference_wall_time_s": inference_time, "policy_step_wall_time_s": elapsed,
                    "policy_step_budget_s": self._budget}))

    def close(self):
        self._model = self._torch = self._route = self._last = self._predicted = None
