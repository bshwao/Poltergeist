"""Analytic geometry cases independent of CARLA and optional numeric libraries."""

import math

import pytest

from evaluation.distance_metrics import bounding_box_clearance, geometry_available
from evaluation.ttc_calculator import can_compute_ttc, time_to_collision
from shared.models import ActorState, BoundingBoxExtent, Rotation, Transform, Vector3


def actor(
    actor_id="actor", *, position=(0.0, 0.0, 0.0), velocity=(0.0, 0.0, 0.0),
    extent=(1.0, 1.0, 1.0), yaw=0.0, pitch=0.0, roll=0.0,
):
    return ActorState(
        actor_id=actor_id,
        transform=None if position is None else Transform(
            position=Vector3(x=position[0], y=position[1], z=position[2]),
            rotation=Rotation(yaw_deg=yaw, pitch_deg=pitch, roll_deg=roll),
        ),
        velocity_mps=None if velocity is None else Vector3(x=velocity[0], y=velocity[1], z=velocity[2]),
        bounding_box_extent=None if extent is None else BoundingBoxExtent(x=extent[0], y=extent[1], z=extent[2]),
    )


@pytest.mark.parametrize("position, expected", [
    ((0, 0, 0), 0), ((1, 0, 0), 0), ((2, 0, 0), 0),
    ((5, 0, 0), 3), ((5, 6, 7), math.sqrt(50)), ((0, 0, 5), 3),
])
def test_box_clearance_is_surface_distance(position, expected):
    assert bounding_box_clearance(actor(), actor(position=position)) == pytest.approx(expected)


def test_yaw_clearance_uses_oriented_faces():
    assert bounding_box_clearance(actor(yaw=45), actor(position=(5, 0, 0))) == pytest.approx(4 - math.sqrt(2))


@pytest.mark.parametrize("rotation, extent", [
    ({"pitch": 90}, (2, 1, 1)), ({"roll": 90}, (1, 2, 1)),
])
def test_pitch_and_roll_rotate_half_extents_vertically(rotation, extent):
    assert bounding_box_clearance(actor(extent=extent, **rotation), actor(position=(0, 0, 5))) == pytest.approx(2)


def test_positive_pitch_in_carla_points_forward_axis_upward():
    tilted = actor(pitch=45, extent=(5, 0.1, 0.1))
    assert bounding_box_clearance(tilted, actor(position=(3, 0, 3), extent=(0.1, 0.1, 0.1))) == 0
    assert bounding_box_clearance(tilted, actor(position=(3, 0, -3), extent=(0.1, 0.1, 0.1))) > 3


def test_positive_roll_in_carla_points_right_axis_downward():
    tilted = actor(roll=45, extent=(0.1, 5, 0.1))
    assert bounding_box_clearance(tilted, actor(position=(0, 3, -3), extent=(0.1, 0.1, 0.1))) == 0
    assert bounding_box_clearance(tilted, actor(position=(0, 3, 3), extent=(0.1, 0.1, 0.1))) > 3


def test_edge_interiors_can_be_closer_than_every_vertex():
    # Perpendicular rods above one another have nearest points inside their edges.
    horizontal = actor(extent=(3, 0.1, 0.1))
    vertical = actor(position=(0, 0, 2), extent=(3, 0.1, 0.1), yaw=90)
    assert bounding_box_clearance(horizontal, vertical) == pytest.approx(1.8)


def test_world_box_pose_overrides_actor_origin_and_rotation():
    first = actor(position=(100, 100, 100), yaw=45, extent=(2, 1, 1))
    first.bounding_box_transform = Transform(position=Vector3(z=2), rotation=Rotation(pitch_deg=90))
    other = actor(position=(0, 0, 6))
    assert bounding_box_clearance(first, other) == pytest.approx(1)
    first.transform = None
    assert geometry_available(first)
    assert bounding_box_clearance(first, other) == pytest.approx(1)


@pytest.mark.parametrize("missing", [{"position": None}, {"extent": None}])
def test_unknown_geometry_is_unmeasured_not_point_clearance(missing):
    incomplete = actor(**missing)
    assert not geometry_available(incomplete)
    assert bounding_box_clearance(incomplete, actor()) is None
    assert bounding_box_clearance(actor(), incomplete) is None
    assert time_to_collision(incomplete, actor()) is None
    assert not can_compute_ttc(incomplete, actor())


@pytest.mark.parametrize("first, second, expected", [
    (actor(velocity=(5, 0, 0)), actor(position=(22, 0, 0), velocity=(-5, 0, 0)), 2),
    (actor(velocity=(10, 0, 0)), actor(position=(22, 0, 0), velocity=(5, 0, 0)), 4),
    (actor(position=(-10, 0, 0), velocity=(2, 0, 0)), actor(position=(0, -10, 0), velocity=(0, 2, 0)), 4),
    (actor(), actor(position=(0, 0, 10), velocity=(0, 0, -1)), 8),
    (actor(velocity=(1, 0, 0)), actor(position=(4, 2, 0)), 2),
])
def test_constant_velocity_contact(first, second, expected):
    assert time_to_collision(first, second) == pytest.approx(expected)
    assert time_to_collision(second, first) == pytest.approx(expected)
    assert can_compute_ttc(first, second)


@pytest.mark.parametrize("first, second", [
    (actor(), actor(position=(5, 0, 0))),
    (actor(velocity=(10, 0, 0)), actor(position=(20, 0, 0), velocity=(10, 0, 0))),
    (actor(velocity=(-1, 0, 0)), actor(position=(5, 0, 0), velocity=(1, 0, 0))),
    (actor(position=(-10, 0, 0), velocity=(2, 0, 0)), actor(position=(0, -20, 0), velocity=(0, 2, 0))),
    (actor(velocity=(10, 0, 0)), actor(position=(20, 0, 3), velocity=(-10, 0, 0))),
    (actor(velocity=(1, 0, 0)), actor(position=(4, 2.001, 0))),
])
def test_noncollision_motion_is_not_a_finite_ttc(first, second):
    assert time_to_collision(first, second) is None
    assert can_compute_ttc(first, second)


def test_rotated_boxes_change_predicted_contact_time():
    first = actor(velocity=(1, 0, 0), extent=(2, 1, 1), yaw=90)
    other = actor(position=(10, 0, 0), extent=(2, 1, 1), yaw=90)
    assert time_to_collision(first, other) == pytest.approx(8)
    first.transform.rotation.yaw_deg = 0
    other.transform.rotation.yaw_deg = 0
    assert time_to_collision(first, other) == pytest.approx(6)


@pytest.mark.parametrize("yaw", [0, 90, 180, 270, 360])
def test_grazing_contact_does_not_depend_on_equivalent_cube_rotation(yaw):
    # At cardinal rotations, a cube is the same solid. Tiny trig residuals
    # must not turn the zero lateral speed into an early separation time.
    first = actor(velocity=(1, 0, 0), yaw=yaw)
    other = actor(position=(10, 2, 0), yaw=yaw)
    assert time_to_collision(first, other) == pytest.approx(8)


def test_contact_horizon_includes_endpoint_and_excludes_later_contact():
    first, other = actor(velocity=(1, 0, 0)), actor(position=(12, 0, 0))
    assert time_to_collision(first, other, horizon_s=10) == 10
    assert time_to_collision(first, other, horizon_s=9.999) is None
    other.transform.position.x = 13
    assert time_to_collision(first, other) is None


@pytest.mark.parametrize("x", [0, 1, 2])
def test_current_contact_is_zero_ttc_without_velocity(x):
    first, other = actor(velocity=None), actor(position=(x, 0, 0), velocity=None)
    assert time_to_collision(first, other) == 0
    assert can_compute_ttc(first, other)


def test_missing_velocity_is_not_assumed_stationary():
    first, other = actor(velocity=(10, 0, 0)), actor(position=(10, 0, 0), velocity=None)
    assert time_to_collision(first, other) is None
    assert not can_compute_ttc(first, other)
    assert bounding_box_clearance(first, other) == 8


def test_geometry_is_symmetric_and_translation_invariant():
    first = actor(position=(2, 3, 4), yaw=13, pitch=27, roll=-42, velocity=(2, 0, 0))
    second = actor(position=(9, 4, 5), yaw=-27, pitch=33, roll=19, velocity=(-1, 0, 0))
    clearance = bounding_box_clearance(first, second)
    ttc = time_to_collision(first, second)
    assert clearance == pytest.approx(bounding_box_clearance(second, first))
    assert ttc == pytest.approx(time_to_collision(second, first))
    for state in (first, second):
        state.transform.position.x += 1_000_000
        state.transform.position.y -= 1_000_000
        state.transform.position.z += 100
        state.velocity_mps.x += 100
        state.velocity_mps.y -= 50
    assert bounding_box_clearance(first, second) == pytest.approx(clearance)
    assert time_to_collision(first, second) == pytest.approx(ttc)


@pytest.mark.parametrize("horizon", [0, -1, float("nan"), float("inf"), True, "10", 10**1000])
def test_invalid_horizon_rejected_even_when_geometry_missing(horizon):
    with pytest.raises(ValueError):
        time_to_collision(actor(extent=None), actor(), horizon_s=horizon)


@pytest.mark.parametrize("metric", [bounding_box_clearance, time_to_collision, can_compute_ttc])
@pytest.mark.parametrize("field", ["position", "extent", "velocity", "rotation"])
def test_mutated_nonfinite_contract_is_revalidated(metric, field):
    state = actor()
    if field == "position":
        state.transform.position.x = float("nan")
    elif field == "extent":
        state.bounding_box_extent.x = float("inf")
    elif field == "velocity":
        state.velocity_mps.x = float("inf")
    else:
        state.transform.rotation.yaw_deg = float("nan")
    with pytest.raises(ValueError):
        metric(state, actor(position=(10, 0, 0)))


@pytest.mark.parametrize("metric", [bounding_box_clearance, time_to_collision])
def test_overflow_cannot_be_reported_as_safe_or_unavailable(metric):
    with pytest.raises(ValueError, match="finite|overflow"):
        metric(actor(position=(1e308, 0, 0)), actor(position=(-1e308, 0, 0)))


def test_overflowing_relative_velocity_is_rejected():
    with pytest.raises(ValueError, match="finite|overflow"):
        time_to_collision(actor(velocity=(1e308, 0, 0)), actor(position=(10, 0, 0), velocity=(-1e308, 0, 0)))
