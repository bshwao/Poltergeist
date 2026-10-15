"""Common interface for driving agents."""

from abc import ABC, abstractmethod

from shared.models import ControlInput
from simulator.protocols import DrivingObservation


class BaseAgent(ABC):
    """Base class implemented by all driving agents."""

    @abstractmethod
    def run_step(self, observation: DrivingObservation) -> ControlInput:
        """Return the next control action for an observation."""
