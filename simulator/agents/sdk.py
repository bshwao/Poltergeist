"""Versioned, CARLA-free lifecycle for user-supplied driving agents.

This is an in-process SDK boundary. Sensor samples are lossless, calibrated,
frame-associated inputs; dashboard registration is a separate integration layer.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from copy import deepcopy
from math import isclose
from typing import Any

from shared.models import AgentManifest, AgentObservation, AgentReset, AgentStepResult


class AgentSDKError(RuntimeError):
    """Base error for the autonomous-agent SDK boundary."""


class AgentLifecycleError(AgentSDKError):
    """The requested operation is invalid in the current lifecycle state."""


class AgentCompatibilityError(AgentSDKError):
    """The package requires capabilities this SDK runtime cannot provide."""


class AgentProtocolError(AgentSDKError):
    """An observation or response violates the versioned agent contract."""


class AutonomousAgent(ABC):
    """Implement all four operations; the caller owns the simulation clock.

    Agents receive copied data and return requested controls. They must not tick
    CARLA or apply vehicle controls themselves. ``reset`` must clear episode
    state, including recurrent-model history. Configuration is JSON-compatible.
    """

    @abstractmethod
    def load(self, configuration: dict[str, Any]) -> None:
        """Load the model and dependencies once for this session."""

    @abstractmethod
    def reset(self, context: AgentReset) -> None:
        """Initialize a fresh episode with its seed, map, ego, and timing."""

    @abstractmethod
    def step(self, observation: AgentObservation) -> AgentStepResult | Mapping[str, Any]:
        """Return controls associated with this exact observation frame."""

    @abstractmethod
    def close(self) -> None:
        """Release resources, including those acquired by a failed load."""


def _validated(model, value, label: str):
    # Revalidate model instances too: callers can construct or mutate Pydantic
    # models without passing validation, including their nested control values.
    try:
        if isinstance(value, model):
            # Dumping a model recursively materializes its nested models and
            # containers. Validation builds a new owned object graph from that
            # snapshot; another deepcopy would traverse the entire scene again.
            return model.model_validate(value.model_dump(mode="python"))
        # Mappings can themselves contain already-created model instances.
        # Pydantic may retain those instances, so keep their defensive copy.
        return model.model_validate(value).model_copy(deep=True)
    except (ValueError, TypeError, AttributeError) as error:
        raise AgentProtocolError(f"Invalid {label}: {error}") from error


def check_agent_capabilities(manifest: AgentManifest | Mapping[str, Any]) -> AgentManifest:
    """Check supported declarations without importing or executing agent code."""
    validated = _validated(AgentManifest, manifest, "agent manifest")
    if validated.observation_mode == "sensors":
        if validated.protocol_version != "1.3":
            raise AgentCompatibilityError("Sensor-based driving requires SDK protocol 1.3")
        from simulator.agent_sensors import resolve_sensor_specs

        try:
            sensors = resolve_sensor_specs(validated.sensors)
        except (ValueError, TypeError) as error:
            raise AgentCompatibilityError(f"Unsupported agent sensors: {error}") from error
        validated = AgentManifest.model_validate(validated.model_dump() | {"sensors": sensors})
    return validated


class AgentSession:
    """Validate lifecycle, isolate data, and associate controls with frames.

    Entering a context does not load the model. Call ``load`` explicitly, then
    ``reset`` and ``step``. After a validation or agent failure, close the session
    and create a new one. No fallback controller is substituted.
    """

    def __init__(self, agent: AutonomousAgent, manifest: AgentManifest | Mapping[str, Any]):
        if not isinstance(agent, AutonomousAgent):
            raise TypeError("agent must implement AutonomousAgent")
        self._manifest = check_agent_capabilities(manifest)
        self._agent = agent
        self._state = "new"
        self._context: AgentReset | None = None
        self._last_frame: int | None = None
        self._last_time: float | None = None
        self._sensor_source: str | None = None

    @property
    def state(self) -> str:
        return self._state

    @property
    def manifest(self) -> AgentManifest:
        return self._manifest.model_copy(deep=True)

    def _require(self, *states: str) -> None:
        if self._state not in states:
            raise AgentLifecycleError(
                f"Operation requires state {' or '.join(states)}; session is {self._state}"
            )

    def load(self) -> None:
        self._require("new")
        try:
            self._agent.load(deepcopy(self._manifest.configuration))
        except BaseException:
            self._state = "failed"
            raise
        self._state = "loaded"

    def reset(self, context: AgentReset | Mapping[str, Any]) -> None:
        self._require("loaded", "ready")
        try:
            validated = _validated(AgentReset, context, "episode reset")
            if validated.protocol_version != self._manifest.protocol_version:
                raise AgentProtocolError("Episode reset protocol does not match the agent manifest")
            if (validated.driving_task is not None and validated.driving_task.route_owner == "agent"
                    and self._manifest.navigation_mode != "agent_planner"):
                raise AgentProtocolError("Agent-owned navigation requires an agent_planner manifest")
            if self._manifest.observation_mode == "sensors":
                from simulator.agent_sensors import resolve_sensor_specs

                if validated.driving_task is None:
                    raise AgentProtocolError("Sensor driving requires an explicit driving task")
                try:
                    resolved = resolve_sensor_specs(validated.sensors)
                except (ValueError, TypeError) as error:
                    raise AgentProtocolError(f"Invalid reset sensor calibrations: {error}") from error
                if resolved != self._manifest.sensors:
                    raise AgentProtocolError("Reset sensors must match the manifest's resolved calibration")
                validated = AgentReset.model_validate(validated.model_dump() | {"sensors": resolved})
            elif validated.sensors:
                raise AgentProtocolError("Simulator-state agents must not receive sensor calibrations")
            self._agent.reset(validated.model_copy(deep=True))
        except BaseException:
            self._state = "failed"
            raise
        self._context = validated
        self._last_frame = self._last_time = None
        self._sensor_source = None
        self._state = "ready"

    def step(self, observation: AgentObservation | Mapping[str, Any]) -> AgentStepResult:
        self._require("ready")
        try:
            validated = _validated(AgentObservation, observation, "agent observation")
            if validated.protocol_version != self._manifest.protocol_version:
                raise AgentProtocolError("Observation protocol does not match the agent manifest")
            if validated.observation_mode != self._manifest.observation_mode:
                raise AgentProtocolError("Observation mode does not match the agent manifest")
            if "structured_scene" in self._manifest.required_features and validated.scene is None:
                raise AgentProtocolError("Agent requires structured scene context")
            context = self._context
            if self._manifest.observation_mode == "sensors":
                if self._sensor_source is not None and validated.sensor_source != self._sensor_source:
                    raise AgentProtocolError("Sensor observation source must remain fixed during an episode")
                samples = {sample.sensor_id: sample for sample in validated.sensors}
                if set(samples) != {spec.id for spec in context.sensors}:
                    raise AgentProtocolError("Observation sensors must exactly match the reset sensor IDs")
                for spec in context.sensors:
                    sample = samples[spec.id]
                    if sample.type != spec.type:
                        raise AgentProtocolError("Sensor sample type must match its declared calibration")
                    if spec.type == "sensor.camera.rgb" and (
                        sample.width != spec.attributes["image_size_x"]
                        or sample.height != spec.attributes["image_size_y"]
                        or not isclose(sample.fov_deg, spec.attributes["fov"], rel_tol=0, abs_tol=1e-6)
                    ):
                        raise AgentProtocolError("Camera sample dimensions and FOV must match reset calibration")
                    if spec.type == "sensor.lidar.ray_cast" and sample.channels != spec.attributes["channels"]:
                        raise AgentProtocolError("LiDAR sample channels must match reset calibration")
            if validated.driving_task != context.driving_task:
                raise AgentProtocolError("Observation driving task must match the episode reset")
            if validated.ego.actor_id != context.ego_actor_id:
                raise AgentProtocolError("Observation ego_actor_id does not match the episode reset")
            if not isclose(validated.dt_s, context.fixed_delta_seconds, rel_tol=1e-9, abs_tol=1e-9):
                raise AgentProtocolError("Observation dt_s does not match fixed_delta_seconds")
            if self._last_frame is None:
                if not isclose(validated.time_s, 0.0, rel_tol=0, abs_tol=1e-9):
                    raise AgentProtocolError("The first observation must start at episode time zero")
            elif validated.frame != self._last_frame + 1 or not isclose(
                # Native CARLA timestamps can reflect a float32 timestep.
                # Match the runner's tolerance on each interval, not on total
                # episode time, while keeping frame association exact.
                validated.time_s - self._last_time, context.fixed_delta_seconds,
                rel_tol=1e-5, abs_tol=1e-6,
            ):
                raise AgentProtocolError("Observations must have consecutive frames and fixed-step times")
            # Validation has already isolated caller data. Preserve only the
            # scalar clock identity needed after user code, rather than making
            # a second full scene copy. The agent may mutate its owned input.
            frame, time_s, sensor_source = validated.frame, validated.time_s, validated.sensor_source
            response = self._agent.step(validated)
            result = _validated(AgentStepResult, response, "agent step result")
            if result.protocol_version != self._manifest.protocol_version:
                raise AgentProtocolError("Agent response protocol does not match the manifest")
            if result.frame != frame:
                raise AgentProtocolError("Agent response frame does not match the observation frame")
        except BaseException:
            self._state = "failed"
            raise
        self._last_frame, self._last_time = frame, time_s
        self._sensor_source = sensor_source
        return result

    def close(self) -> None:
        if self._state == "closed":
            return
        # Mark closed before invoking user code, even if cleanup itself fails.
        self._state = "closed"
        self._agent.close()

    def __enter__(self):
        self._require("new", "loaded", "ready")
        return self

    def __exit__(self, error_type, error, traceback):
        try:
            self.close()
        except BaseException as cleanup_error:
            if error is None:
                raise
            error.add_note(f"Agent cleanup also failed: {type(cleanup_error).__name__}: {cleanup_error}")
        return False
