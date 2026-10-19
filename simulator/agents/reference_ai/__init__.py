"""Maintained integration of the pretrained PlanT 2.0 planning model.

Importing this package does not import PyTorch, download weights, or connect to
CARLA. Neural inference dependencies are loaded explicitly by the agent.
"""

__all__ = ["ReferenceAIAgent"]


def __getattr__(name):
    if name == "ReferenceAIAgent":
        from .agent import ReferenceAIAgent

        return ReferenceAIAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
