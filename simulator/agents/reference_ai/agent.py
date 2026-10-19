"""Public SDK adapter for the real pretrained PlanT 2.0 reference pilot."""

import math
from pathlib import Path
from time import perf_counter

from shared.models import AgentDiagnostics, AgentObservation, AgentReset, AgentStepResult, Transform, Vector3
from simulator.agents.sdk import AgentCompatibilityError, AgentLifecycleError, AutonomousAgent

from .checkpoint import DEFAULT_CHECKPOINT, UPSTREAM_REVISION, verify_checkpoint
from .features import PlanTFeatureBuilder, tensor_batch


class ReferenceAIAgent(AutonomousAgent):
    """A learned local planner plus the model authors' tracking controllers.

    It receives ground truth and a map-derived route, not RGB perception. The
    learned network chooses the local path and speed. No baseline controller,
    hazard brake rule, or scenario event is substituted for model inference.
    """

    def __init__(self):
        self._model = self._torch = self._features = self._controller = None
        self._ready = False
        self._device = None
        self._checkpoint_hash = None
        self._configuration = None
        self._parameter_count = None

    def load(self, configuration):
        if self._model is not None:
            raise AgentLifecycleError("Reference AI is already loaded")
        allowed = {"checkpoint_path", "device", "torch_threads"}
        if not isinstance(configuration, dict) or configuration.keys() - allowed:
            raise ValueError("Reference AI configuration supports checkpoint_path, device, and torch_threads only")
        selected = configuration.get("device", "auto")
        if selected not in ("cpu", "cuda", "auto"):
            raise ValueError("Reference AI device must be cpu, cuda, or auto")
        threads = configuration.get("torch_threads", 2)
        if type(threads) is not int or not 1 <= threads <= 16:
            raise ValueError("torch_threads must be a whole number from 1 to 16")
        checkpoint = configuration.get("checkpoint_path", str(DEFAULT_CHECKPOINT))
        if not isinstance(checkpoint, str) or not checkpoint.strip():
            raise ValueError("checkpoint_path must be a nonempty local file path")
        checkpoint_path = Path(checkpoint).expanduser().resolve()
        self._checkpoint_hash = verify_checkpoint(checkpoint_path)
        try:
            import torch
            from omegaconf import OmegaConf
            from .controller import PlanTController
            from .network import PlanT2Network
        except ImportError as error:
            raise AgentCompatibilityError(
                "Reference AI dependencies are missing. Prepare a Python 3.12 environment "
                "with examples/agents/reference_ai/requirements.txt and PyTorch; "
                f"missing dependency: {error.name}"
            ) from error
        torch.set_num_threads(threads)
        if selected == "cuda" and not torch.cuda.is_available():
            raise AgentCompatibilityError("Reference AI requested CUDA but this PyTorch environment has no available CUDA device")
        selected = ("cuda" if torch.cuda.is_available() else "cpu") if selected == "auto" else selected
        self._torch = torch
        self._device = torch.device(selected)
        # This upstream Lightning release embeds OmegaConf in its pickle. It
        # is deserialized only after verifying the exact published SHA-256.
        checkpoint_data = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
        cfg = checkpoint_data["hyper_parameters"]["cfg"]
        if OmegaConf.is_config(cfg):
            cfg = OmegaConf.to_container(cfg, resolve=False)
        model = PlanT2Network(cfg)
        state = checkpoint_data["state_dict"]
        unexpected = [key for key in state if not key.startswith("model.")]
        if unexpected:
            raise ValueError(f"Unexpected non-model parameters in the pinned release: {unexpected}")
        model.load_state_dict({key.removeprefix("model."): value for key, value in state.items()}, strict=True)
        model.eval()
        model.to(self._device)
        if selected == "cuda":
            torch.cuda.synchronize(self._device)
        training = cfg["model"]["training"]
        self._features = PlanTFeatureBuilder(
            input_bev=model.input_bev, range_m=float(training["range"]),
            range_factor_front=float(training["range_factor_front"]),
        )
        self._controller = PlanTController()
        self._configuration = cfg
        self._parameter_count = sum(value.numel() for value in model.parameters())
        self._model = model

    def reset(self, context: AgentReset):
        if self._model is None:
            raise AgentLifecycleError("Load Reference AI before resetting it")
        if not math.isclose(context.fixed_delta_seconds, 0.05, abs_tol=1e-9):
            raise AgentCompatibilityError("The released PlanT pilot uses 20 Hz (fixed_delta_seconds=0.05)")
        self._features.reset()
        self._controller.reset()
        self._ready = True

    def step(self, observation: AgentObservation) -> AgentStepResult:
        if not self._ready or self._model is None:
            raise AgentLifecycleError("Load and reset Reference AI before stepping it")
        features = self._features.build(observation)
        batch = tensor_batch(features, self._torch, self._device)
        if self._device.type == "cuda":
            self._torch.cuda.synchronize(self._device)
        started = perf_counter()
        with self._torch.inference_mode():
            path_tensor, waypoint_tensor = self._model(batch)
        # Copying to host also synchronizes CUDA and includes actual inference
        # completion, rather than timing only asynchronous kernel submission.
        path = path_tensor[0].detach().cpu().numpy()
        waypoints = waypoint_tensor[0].detach().cpu().numpy()
        inference_wall_time = perf_counter() - started
        control, desired_speed = self._controller.control(path, waypoints, features.ego_speed_mps)
        ego = observation.ego.transform
        yaw = math.radians(ego.rotation.yaw_deg)
        world_path = [Transform(position=Vector3(
            x=float(ego.position.x + math.cos(yaw) * x - math.sin(yaw) * y),
            y=float(ego.position.y + math.sin(yaw) * x + math.cos(yaw) * y),
            z=float(ego.position.z),
        )) for x, y in path]
        return AgentStepResult(
            protocol_version=observation.protocol_version,
            frame=observation.frame,
            control=control,
            diagnostics=AgentDiagnostics(
                target_speed_mps=max(0., float(desired_speed)),
                planned_trajectory=world_path,
                metadata={
                    "model": "PlanT 2.0", "upstream_revision": UPSTREAM_REVISION,
                    "checkpoint_sha256": self._checkpoint_hash,
                    "device": str(self._device),
                    "torch_version": str(self._torch.__version__),
                    "cuda_version": self._torch.version.cuda,
                    "dtype": str(next(self._model.parameters()).dtype),
                    "inference_wall_time_s": inference_wall_time,
                    "input_source": observation.scene.source,
                    "ground_truth_objects": True,
                    "input_object_count": len(features.objects),
                    "omitted_object_count": features.omitted_objects,
                    "speed_category": features.speed_category,
                    "speed_category_kmh": (50, 80, 100, 120)[features.speed_category],
                    "map_speed_limit_mps": float(observation.scene.speed_limit_mps),
                    "requires_road_raster": self._features.input_bev,
                    "learned_waypoints_ego": waypoints.tolist(),
                    "parameter_count": self._parameter_count,
                },
            ),
        )

    def close(self):
        self._ready = False
        self._model = self._features = self._controller = self._configuration = None
        self._parameter_count = None
        if self._torch is not None and self._device is not None and self._device.type == "cuda":
            self._torch.cuda.empty_cache()
        self._torch = self._device = None
