"""SDK wrapper for the existing deterministic simulator-state baseline.

This is a rule-based development controller, not a trained autonomous-driving
model. Its actor inputs are simulator ground truth, including occluded actors.
It does not provide perception, destination planning, or decision explanations.
"""

from copy import deepcopy

from shared.models import AgentManifest, AgentObservation, AgentReset, AgentStepResult
from simulator.protocols import DrivingObservation

from .baseline_agent import BaselineAgent
from .sdk import AutonomousAgent


BASELINE_AGENT_MANIFEST = AgentManifest(
    agent_id="poltergeist-baseline",
    name="Poltergeist rule-based baseline",
    version="1.0.0",
    entry_point="simulator.agents.sdk_reference:BaselineSDKAgent",
    observation_mode="simulator_state",
    configuration={},
)


class BaselineSDKAgent(AutonomousAgent):
    """Expose BaselineAgent through the versioned load/reset/step/close SDK.

    Configuration accepts the existing baseline's ``target_speed_mps``,
    ``emergency_ttc_s``, and ``minimum_clearance_m`` keyword arguments. The
    observation's target speed is used unless configuration overrides it.
    Unknown actors (``None``) are rejected; an empty observed list is valid.
    """

    def __init__(self) -> None:
        self._configuration: dict | None = None
        self._controller: BaselineAgent | None = None

    def load(self, configuration: dict) -> None:
        if not isinstance(configuration, dict):
            raise TypeError("Baseline configuration must be a dictionary")
        candidate = deepcopy(configuration)
        # Validate names and values before replacing a previously loaded setup.
        BaselineAgent(**candidate)
        self._configuration = candidate
        self._controller = None

    def reset(self, context: AgentReset) -> None:
        if self._configuration is None:
            raise RuntimeError("Load the baseline agent before resetting it")
        self._controller = BaselineAgent(**self._configuration)

    def step(self, observation: AgentObservation) -> AgentStepResult:
        if self._controller is None:
            raise RuntimeError("Load and reset the baseline agent before stepping it")
        if observation.actors is None:
            raise ValueError(
                "The rule-based baseline requires observed simulator-state actors; "
                "actors=None means unavailable, not an empty road"
            )
        control = self._controller.run_step(DrivingObservation(
            ego=observation.ego,
            actors=tuple(observation.actors),
            dt_s=observation.dt_s,
            target_speed_mps=observation.target_speed_mps,
            route_heading_deg=observation.route_heading_deg,
            traffic_light_red=observation.traffic_light_red,
        ))
        # BaselineAgent returns controls, not introspective decision evidence.
        return AgentStepResult(frame=observation.frame, control=control)

    def close(self) -> None:
        self._controller = None
        self._configuration = None
