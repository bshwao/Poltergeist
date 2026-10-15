"""Validated callable injection for user-supplied driving controllers."""

from collections.abc import Callable, Mapping
from typing import Any

from shared.models import ControlInput
from simulator.protocols import DrivingObservation

from .base_agent import BaseAgent


class CustomAgent(BaseAgent):
    def __init__(
        self,
        controller: Callable[[DrivingObservation], ControlInput | Mapping[str, Any]],
    ) -> None:
        if not callable(controller):
            raise TypeError("controller must be callable")
        self.controller = controller

    def run_step(self, observation: DrivingObservation) -> ControlInput:
        if not isinstance(observation, DrivingObservation):
            raise TypeError("observation must be a DrivingObservation")
        result = self.controller(observation)
        if isinstance(result, ControlInput):
            result = result.model_dump()
        if not isinstance(result, Mapping):
            raise TypeError("controller must return ControlInput or a control mapping")
        return ControlInput.model_validate(dict(result))
