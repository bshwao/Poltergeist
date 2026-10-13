"""Extension point for future ML-based agents."""

from .base_agent import BaseAgent


class CustomAgent(BaseAgent):
    def run_step(self, observation):
        raise NotImplementedError
