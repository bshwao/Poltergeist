"""Scenario overview framing against the public CARLA transform boundary."""

from types import SimpleNamespace as NS

import pytest

from shared.models import ActorState, TelemetryFrame, Transform, Vector3
from simulator.spectator import position_spectator


@pytest.mark.parametrize("offset", [(60, 0), (0, 60), (36, 48)])
def test_overview_frames_both_approaches_at_translated_and_rotated_locations(offset):
    x, y = offset
    views = []
    carla = NS(Location=NS, Rotation=NS,
               Transform=lambda location, rotation: NS(location=location, rotation=rotation))
    world = NS(get_spectator=lambda: NS(set_transform=views.append))
    frame = TelemetryFrame(
        frame=5, time_s=0,
        ego=ActorState(actor_id="ego", transform=Transform(position=Vector3(x=1000, y=2000, z=6))),
        actors=[ActorState(actor_id="crossing", transform=Transform(position=Vector3(x=1000 + x, y=2000 + y, z=6)))],
    )
    position_spectator(world, carla, frame, {"ego", "crossing"})
    view, = views
    assert view.location.x == pytest.approx(1000 + x / 2)
    assert view.location.y == pytest.approx(2000 + y / 2)
    # A 60 m approach fits the wide axis with 5 m margin at each end,
    # rather than raising the spectator beyond Low quality's drawing range.
    assert view.location.z == pytest.approx(41)
    assert view.rotation.pitch == -89
