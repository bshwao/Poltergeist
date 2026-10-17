"""Errors raised by simulation adapters and resource managers."""


class SimulationError(RuntimeError):
    pass


class SimulationCancelled(SimulationError):
    pass


class ActorPlacementError(SimulationError):
    """A candidate layout is unsuitable; another mapped layout may be tried."""
