"""Errors raised by simulation adapters and resource managers."""


class SimulationError(RuntimeError):
    pass


class SimulationCancelled(SimulationError):
    pass
