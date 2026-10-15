"""Shortest 3D separation between the surfaces of oriented bounding boxes."""

from shared.models import ActorState

from .geometry import actor_box, box_clearance
from .validation import validate_actor


def geometry_available(actor: ActorState) -> bool:
    """Whether the actor has a measured box pose and positive half-extents."""
    return actor_box(validate_actor(actor)) is not None


def bounding_box_clearance(ego: ActorState, other: ActorState) -> float | None:
    """Return clearance in metres; overlap/touch is zero, missing data is None.

    Extents are half-lengths along the box's local axes. An explicit world-space
    ``bounding_box_transform`` takes precedence over the actor pose. Without
    it, the box is assumed centered on and aligned with the actor transform.
    """
    a, b = actor_box(validate_actor(ego)), actor_box(validate_actor(other))
    if a is None or b is None:
        return None
    return box_clearance(a, b)
