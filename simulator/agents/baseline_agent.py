"""Initial CARLA driving agent."""

from .base_agent import BaseAgent


class BaselineAgent(BaseAgent):
    def run_step(self, observation):
        raise NotImplementedError
