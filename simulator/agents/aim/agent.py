"""Learned RGB-to-waypoint driving, with the released AIM tracking controller."""
from io import BytesIO
import math
from time import perf_counter
from types import SimpleNamespace

from shared.models import AgentDiagnostics, AgentStepResult, ControlInput
from simulator.agents.sdk import AutonomousAgent, AgentCompatibilityError, AgentLifecycleError
from .checkpoint import checkpoint_bytes, CHECKPOINT_SHA256, SOURCE_REVISION
from .preprocessing import camera_rgb, RouteTarget, world_waypoints


class AIMCameraAgent(AutonomousAgent):
    def __init__(self):
        self._model = self._torch = self._route = self._camera = None
        self._last = None

    def load(self, configuration):
        if self._model is not None:
            raise AgentLifecycleError("AIM is already loaded")
        allowed = {"checkpoint_path", "device", "torch_threads", "inference_budget_s"}
        if not isinstance(configuration, dict) or configuration.keys() - allowed:
            raise ValueError("AIM configuration supports checkpoint_path, device, torch_threads and inference_budget_s")
        path = configuration.get("checkpoint_path")
        if not isinstance(path, str) or not path.strip():
            raise ValueError("Prepare the managed AIM checkpoint before enabling the camera policy")
        device = configuration.get("device", "cpu")
        threads = configuration.get("torch_threads", 2)
        budget = configuration.get("inference_budget_s", 1.)
        if not isinstance(device, str) or device not in {"cpu", "cuda", "auto"}:
            raise ValueError("AIM device must be cpu, cuda or auto")
        if type(threads) is not int or not 1 <= threads <= 16:
            raise ValueError("AIM torch_threads must be a whole number from 1 to 16")
        if type(budget) not in {int, float} or not math.isfinite(budget) or not .05 <= budget <= 30.:
            raise ValueError("AIM inference budget must be between 0.05 and 30 seconds")
        data = checkpoint_bytes(path)
        try:
            import torch
            from .network import AIM
        except ImportError as error:
            raise AgentCompatibilityError("Prepare the managed AIM CPU environment with Torch and torchvision first") from error
        torch.set_num_threads(threads)
        if device == "cuda" and not torch.cuda.is_available():
            raise AgentCompatibilityError("AIM requested CUDA but this environment has no available CUDA device")
        device = ("cuda" if torch.cuda.is_available() else "cpu") if device == "auto" else device
        config = SimpleNamespace(pred_len=4, turn_KP=1.25, turn_KI=.75, turn_KD=.3, turn_n=40,
            speed_KP=5., speed_KI=.5, speed_KD=1., speed_n=40, max_throttle=.75,
            brake_speed=.4, brake_ratio=1.1, clip_delta=.25)
        model = AIM(config, device)
        # Restricted deserialization of exactly the verified, bounded bytes.
        state = torch.load(BytesIO(data), map_location="cpu", weights_only=True)
        model.load_state_dict(state, strict=True)
        del state, data
        model.eval()
        model.to(device)
        self._torch, self._model = torch, model
        self._device, self._budget = device, float(budget)
        self._parameters = sum(value.numel() for value in model.parameters())

    def reset(self, context):
        if self._model is None:
            raise AgentLifecycleError("Load AIM before resetting it")
        self._route = self._camera = self._last = None
        if (context.protocol_version != "1.3" or context.driving_task is None
                or context.driving_task.route_owner != "framework"):
            raise AgentCompatibilityError("AIM requires SDK 1.3 and a supplied framework driving route")
        if not math.isclose(context.fixed_delta_seconds, .05, abs_tol=1e-9):
            raise AgentCompatibilityError("The supported AIM release uses 20 Hz simulation steps")
        if len(context.sensors) != 1 or context.sensors[0].type != "sensor.camera.rgb":
            raise AgentCompatibilityError("AIM requires exactly its front RGB camera")
        camera = context.sensors[0]
        for key, expected in (("image_size_x", 400), ("image_size_y", 300), ("fov", 100.), ("sensor_tick", 0.)):
            value = camera.attributes.get(key)
            if type(value) not in {int, float} or not math.isclose(value, expected, abs_tol=1e-4):
                raise AgentCompatibilityError("AIM camera resolution, FOV or sample period differs from the supported rig")
        pose = camera.mount
        actual = (pose.position.x, pose.position.y, pose.position.z,
            pose.rotation.pitch_deg, pose.rotation.yaw_deg, pose.rotation.roll_deg)
        if any(not math.isclose(value, expected, abs_tol=1e-4) for value, expected in zip(actual, (1.3, 0., 2.3, 0., 0., 0.))):
            raise AgentCompatibilityError("AIM camera mount differs from the supported rig")
        self._camera = camera.model_copy(deep=True)
        self._route = RouteTarget(context.route)
        self._last = None
        for controller in (self._model.turn_controller, self._model.speed_controller):
            controller._window.clear()
            controller._window.extend([0.] * controller._window.maxlen)
            controller._max = controller._min = 0.

    def step(self, observation):
        if self._model is None or self._route is None:
            raise AgentLifecycleError("Reset AIM before requesting controls")
        if (observation.observation_mode != "sensors" or observation.ego_state_source != "simulated_ego_kinematics"
                or observation.sensor_source not in {"carla_sensors", "deterministic_fixture"}
                or observation.actors is not None or observation.scene is not None or observation.traffic_light_red is not None):
            raise AgentCompatibilityError("AIM consumes declared camera pixels and ideal ego kinematics, without actor ground truth")
        if observation.ego.transform is None or observation.ego.velocity_mps is None:
            raise AgentCompatibilityError("AIM requires the declared ideal ego pose and speedometer observations")
        if len(observation.sensors) != 1:
            raise AgentCompatibilityError("AIM received an unexpected sensor bundle")
        sample = observation.sensors[0]
        if (sample.sensor_id != self._camera.id or sample.frame != observation.frame
                or not math.isclose(sample.timestamp_s, observation.time_s, abs_tol=1e-6)):
            raise AgentCompatibilityError("AIM camera sample does not match the current native frame/time")
        if not math.isclose(observation.dt_s, .05, abs_tol=1e-9):
            raise AgentCompatibilityError("AIM observation timestep changed")
        if self._last is not None and (observation.frame != self._last[0] + 1
                or not math.isclose(observation.time_s - self._last[1], .05, rel_tol=1e-5, abs_tol=1e-6)):
            raise AgentCompatibilityError("AIM camera timeline is repeated or has missing steps")
        started = perf_counter()
        target = self._route.next(observation.ego.transform)
        image = self._torch.from_numpy(camera_rgb(sample)).unsqueeze(0).to(self._device)
        point = self._torch.tensor([target], dtype=self._torch.float32, device=self._device)
        velocity = observation.ego.velocity_mps
        speed = math.hypot(velocity.x, velocity.y, velocity.z)
        if not math.isfinite(speed):
            raise AgentCompatibilityError("AIM ego speed exceeds the supported finite range")
        inference_started = perf_counter()
        with self._torch.inference_mode():
            embedding = self._model.image_encoder([image])
            predicted = self._model([embedding], point)
            if tuple(predicted.shape) != (1, 4, 2) or not self._torch.isfinite(predicted).all().item():
                raise AgentCompatibilityError("AIM produced invalid learned waypoints")
            values = predicted[0].detach().cpu().tolist()
            inference_time = perf_counter() - inference_started
            steer, throttle, brake, tracked = self._model.control_pid(predicted.clone(),
                self._torch.tensor([speed], dtype=self._torch.float32, device=self._device))
        elapsed = perf_counter() - started
        if elapsed > self._budget:
            raise AgentCompatibilityError("AIM exceeded the configured camera-policy step budget; no fallback control was substituted")
        control = ControlInput(steer=float(steer), throttle=float(throttle), brake=float(brake))
        self._last = (observation.frame, observation.time_s)
        return AgentStepResult(protocol_version="1.3", frame=observation.frame, control=control,
            diagnostics=AgentDiagnostics(assistance=["ideal_ego_kinematics", "framework_route"],
                maneuver="learned_camera_waypoints", target_speed_mps=tracked["desired_speed"],
                planned_trajectory=world_waypoints(values, observation.ego.transform), metadata={
                    "checkpoint_sha256": CHECKPOINT_SHA256, "source_revision": SOURCE_REVISION,
                    "parameter_count": self._parameters, "device": self._device,
                    "input_source": observation.sensor_source, "camera_sha256": sample.sha256,
                    "camera_frame": sample.frame, "camera_time_s": sample.timestamp_s,
                    "model_waypoints": values, "inference_wall_time_s": inference_time,
                    "policy_step_wall_time_s": elapsed, "policy_step_budget_s": self._budget}))

    def close(self):
        self._model = self._torch = self._route = self._camera = None
        self._last = None
