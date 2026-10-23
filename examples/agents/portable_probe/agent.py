"""Trusted packaging probe, not a driving policy or performance benchmark."""

import sys

import backend
import shared
import simulator
from shared.models import AgentDiagnostics, AgentStepResult, ControlInput
from simulator.agents import AutonomousAgent


class PortableProbe(AutonomousAgent):
    def load(self, configuration):
        pass

    def reset(self, context):
        pass

    def step(self, observation):
        return AgentStepResult(protocol_version=observation.protocol_version, frame=observation.frame,
            control=ControlInput(throttle=.1), diagnostics=AgentDiagnostics(metadata={
                "python_executable": sys.executable, "python_prefix": sys.prefix,
                "module_paths": [backend.__file__, shared.__file__, simulator.__file__],
            }))

    def close(self):
        pass
