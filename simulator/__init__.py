"""Simulation adapters; CARLA is imported only when a real run is requested."""

from .protocols import SimulationOptions

__all__ = ["RunOutput", "SimulationOptions", "SimulationRunner", "run_simulation"]


def __getattr__(name):
    if name in {"RunOutput", "SimulationRunner", "run_simulation"}:
        from . import simulation_runner
        return getattr(simulation_runner, name)
    raise AttributeError(name)
