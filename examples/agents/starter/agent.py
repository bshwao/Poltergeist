"""Editable SDK starter: a rule-based example, not a trained driving model."""

from shared.models import AgentObservation, AgentStepResult
from simulator.agents.sdk_reference import BaselineSDKAgent


class StarterAgent(BaselineSDKAgent):
    """Begin with the reference controller, then replace it with your agent.

    For a learned model, override ``load`` to initialize weights and
    preprocessing, ``reset`` to clear episode state, ``step`` to run inference,
    and ``close`` to release resources. Return controls tagged with the input
    frame. AgentSession validates the returned controls.
    """

    def step(self, observation: AgentObservation) -> AgentStepResult:
        # Replace this call with preprocessing -> model inference -> controls.
        # Only attach diagnostics that your own agent actually produces.
        return super().step(observation)
