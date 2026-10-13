"""Common interface for driving agents."""

from abc import ABC, abstractmethod


class BaseAgent(ABC):
    """Base class implemented by all driving agents."""

    @abstractmethod
    def run_step(self, observation):
        """Return the next control action for an observation."""
