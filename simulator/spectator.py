"""Place CARLA's existing spectator over the scenario without adding sensors."""

import logging
from math import atan2, cos, degrees, hypot, radians, sin
from typing import Any

from shared.models import Rotation, TelemetryFrame, Transform, Vector3

from .transforms import to_carla


logger = logging.getLogger(__name__)


def position_spectator(world: Any, carla_module: Any, frame: TelemetryFrame,
                       scenario_actor_ids: set[str]) -> None:
    """Frame owned actors once, leaving subsequent manual camera movement free.

    Use the first initialized snapshot: CARLA's actor handles may still report
    zero transforms immediately after spawning. Foreign traffic must not move
    the view away from the scenario. A display/RPC failure is nonfatal because
    the spectator is independent of simulation measurements and sensor capture.
    """
    states = [state for state in (frame.ego, *(frame.actors or []))
              if state.actor_id in scenario_actor_ids and state.transform is not None]
    if not states or frame.ego.transform is None:
        return
    origin = frame.ego.transform.position
    points = [state.transform.position for state in states]
    farthest = max(points, key=lambda point: hypot(point.x - origin.x, point.y - origin.y))
    # Put the long approach across the wide side of a typical 16:9 window.
    angle = (atan2(farthest.y - origin.y, farthest.x - origin.x)
             if hypot(farthest.x - origin.x, farthest.y - origin.y) > 0.01
             else radians(frame.ego.transform.rotation.yaw_deg))
    c, s = cos(angle), sin(angle)
    along = [(point.x - origin.x) * c + (point.y - origin.y) * s for point in points]
    across = [-(point.x - origin.x) * s + (point.y - origin.y) * c for point in points]
    mid_along = (min(along) + max(along)) / 2
    mid_across = (min(across) + max(across)) / 2
    # Allow for vehicle bodies and some road context. The spectator normally has
    # a 90-degree horizontal field of view; narrower windows may need adjustment.
    height = max(20.0, (max(along) - min(along)) / 2 + 5.0,
                 ((max(across) - min(across)) / 2 + 5.0) * 16 / 9)
    view = Transform(
        position=Vector3(x=origin.x + mid_along * c - mid_across * s,
                         y=origin.y + mid_along * s + mid_across * c,
                         z=max(point.z for point in points) + height),
        # CARLA's interactive spectator clamps pitch just short of vertical.
        rotation=Rotation(pitch_deg=-89.0, yaw_deg=degrees(angle) - 90.0),
    )
    try:
        world.get_spectator().set_transform(to_carla(view, carla_module))
    except Exception:
        logger.warning("Could not position the CARLA spectator; the simulation will continue",
                       exc_info=True)
