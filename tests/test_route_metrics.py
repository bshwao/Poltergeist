"""Reference-path distance is finite, elevation-aware, and coordinate invariant."""

import math

import pytest

from evaluation.route_metrics import PolylineReference
from evaluation.validation import EvaluationError
from shared.models import Transform


def pose(x=0.0, y=0.0, z=0.0):
    return Transform.model_validate({"position": {"x": x, "y": y, "z": z}})


@pytest.mark.parametrize("point,expected", [
    ((5, 3, 0), 3), ((-4, 3, 0), 5), ((13, 4, 0), 5), ((0, 0, 0), 0), ((10, 0, 0), 0),
])
def test_distance_clamps_to_finite_segment_endpoints(point, expected):
    reference = PolylineReference([pose(), pose(10)])
    assert reference.distance(pose(*point), height_tolerance_m=1) == pytest.approx(expected)


def test_overpasses_select_the_height_compatible_path_not_zero_xy_distance():
    reference = PolylineReference([pose(-10, 0, 10), pose(10, 0, 10), pose(10, 5, 0), pose(-10, 5, 0)])
    assert reference.distance(pose(), height_tolerance_m=1) == 5
    assert reference.distance(pose(0, 0, 10), height_tolerance_m=1) == 0
    assert reference.distance(pose(0, 0, 30), height_tolerance_m=1) is None


def test_sloped_segment_is_clipped_to_the_valid_height_band_before_projection():
    reference = PolylineReference([pose(0, 0, 10), pose(10, 0, 0)])
    # Only x >= 8 lies inside the ego's ±2 m height band.
    assert reference.distance(pose(), height_tolerance_m=2) == 8


def test_empty_point_and_vertical_reference_paths():
    assert PolylineReference([]).distance(pose(), height_tolerance_m=1) is None
    assert PolylineReference([pose(1, 1)]).distance(pose(4, 5), height_tolerance_m=1) == 5
    vertical = PolylineReference([pose(1, 1, -10), pose(1, 1, 10)])
    assert vertical.distance(pose(4, 5), height_tolerance_m=1) == 5


@pytest.mark.parametrize("angle", [0, 30, 90, 173, 270])
def test_distance_is_invariant_under_rigid_rotation_and_translation(angle):
    radians = math.radians(angle)
    def change(point):
        x, y, z = point
        return pose(x * math.cos(radians) - y * math.sin(radians) + 500,
                    x * math.sin(radians) + y * math.cos(radians) - 300, z + 20)
    reference = PolylineReference([change((0, 0, 0)), change((10, 0, 0)), change((10, 10, 0))])
    assert reference.distance(change((6, 3, 0)), height_tolerance_m=1) == pytest.approx(3)


def test_spatial_tree_keeps_close_segment_when_route_order_is_reversed():
    # More than eight segments exercises branching rather than the leaf path.
    route = [pose(index, (index % 2) * 2) for index in range(100)]
    forward, backward = PolylineReference(route), PolylineReference(route[::-1])
    for index in range(0, 100, 7):
        point = pose(index + .2, 8)
        assert forward.distance(point, height_tolerance_m=1) == pytest.approx(backward.distance(point, height_tolerance_m=1))


def test_nonfinite_arithmetic_never_becomes_a_json_distance():
    with pytest.raises(EvaluationError, match="finite"):
        PolylineReference([pose(-1e308), pose(1e308)])
