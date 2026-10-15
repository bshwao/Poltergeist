"""Observed braking, data gaps, and measured stopping trajectories."""

import pytest

from evaluation.braking_metrics import summarize_braking
from shared.models import ActorState, ControlInput, TelemetryFrame, Vector3


def frame(index, speed=None, acceleration=None, *, time=None, brake=0, hand_brake=False):
    velocity = speed if isinstance(speed, Vector3) else None if speed is None else Vector3(x=speed)
    accel = acceleration if isinstance(acceleration, Vector3) else (
        None if acceleration is None else Vector3(x=acceleration)
    )
    return TelemetryFrame(
        frame=index,
        time_s=index * 0.25 if time is None else time,
        ego=ActorState(actor_id="ego", velocity_mps=velocity, acceleration_mps2=accel),
        control=ControlInput(brake=brake, hand_brake=hand_brake),
    )


@pytest.mark.parametrize(
    ("speed", "acceleration", "expected"),
    [(10, -5, 5), (-10, 5, 5), (10, 5, 0), (-10, -5, 0), (0, -9, 0), (0.1, -9, 0)],
)
def test_signed_acceleration_is_projected_onto_direction_of_motion(speed, acceleration, expected):
    result = summarize_braking([frame(0, speed, acceleration)])
    assert result.max_deceleration_mps2 == expected
    assert result.observed_frames == 1
    assert result.hard_braking_count == int(expected >= 4)


def test_perpendicular_acceleration_is_not_braking():
    result = summarize_braking([frame(0, Vector3(x=6, y=8), Vector3(x=-8, y=6))])
    assert result.max_deceleration_mps2 == pytest.approx(0)
    assert result.hard_braking_count == 0


def test_vertical_motion_is_included_in_acceleration_projection():
    result = summarize_braking([frame(0, Vector3(z=10), Vector3(z=-6))])
    assert result.max_deceleration_mps2 == 6


@pytest.mark.parametrize("samples", [[], [frame(0)], [frame(0, 10)], [frame(0, acceleration=-5)]])
def test_missing_data_is_distinct_from_measured_no_braking(samples):
    result = summarize_braking(samples)
    assert result.max_deceleration_mps2 is None
    assert result.hard_braking_count is None
    assert result.observed_frames == 0
    assert result.stopping_episodes == ()


def test_speed_differences_supply_braking_only_when_acceleration_is_absent():
    result = summarize_braking([frame(0, -10), frame(1, -8), frame(2, -6, 0)])
    assert result.max_deceleration_mps2 == 8
    assert result.observed_frames == 2
    assert result.hard_braking_count == 1


def test_unchanged_speed_with_changed_direction_is_not_inferred_braking():
    result = summarize_braking([frame(0, 10), frame(1, Vector3(y=10))])
    assert result.max_deceleration_mps2 == 0
    assert result.hard_braking_count == 0


def test_hard_braking_counts_episodes_including_exact_threshold():
    samples = [frame(index, 10, acceleration) for index, acceleration in enumerate([-4, -6, -3, -4, -4])]
    result = summarize_braking(samples)
    assert result.max_deceleration_mps2 == 6
    assert result.hard_braking_count == 2


@pytest.mark.parametrize(
    "samples",
    [
        [frame(0, 10, -5), frame(2, 10, -5)],
        [frame(0, 10, -5), frame(1, 10, -5, time=1)],
        [frame(0, 10, -5), frame(1), frame(2, 10, -5)],
    ],
)
def test_gaps_split_hard_braking_episodes(samples):
    assert summarize_braking(samples).hard_braking_count == 2


@pytest.mark.parametrize(
    "samples",
    [[frame(0, 10), frame(2, 5)], [frame(0, 10), frame(1, 5, time=1)]],
)
def test_no_deceleration_is_inferred_across_skipped_frames_or_excessive_time_gaps(samples):
    assert summarize_braking(samples).max_deceleration_mps2 is None


def test_completed_stop_uses_measured_times_and_trapezoidal_speed_integral():
    samples = [frame(0, 6, -8, brake=1), frame(1, 4, -8), frame(2, 2, -8), frame(3, 0, 0)]
    result = summarize_braking(samples)
    stop, = result.stopping_episodes
    assert stop.start_time_s == 0
    assert stop.end_time_s == 0.75
    assert stop.duration_s == 0.75
    assert stop.distance_m == pytest.approx(2.25)


def test_direct_acceleration_starts_stop_at_first_observed_braking_sample():
    samples = [frame(0, 6, 0), frame(1, 4, -8), frame(2, 2, -8), frame(3, 0, 0)]
    stop, = summarize_braking(samples).stopping_episodes
    assert stop.start_time_s == 0.25
    assert stop.duration_s == 0.5
    assert stop.distance_m == 1


def test_inferred_braking_starts_at_previous_sample_and_works_for_reverse():
    samples = [frame(0, -6), frame(1, -4), frame(2, -2), frame(3, 0)]
    stop, = summarize_braking(samples).stopping_episodes
    assert stop.start_time_s == 0
    assert stop.end_time_s == 0.75
    assert stop.distance_m == 2.25


def test_single_observed_braking_interval_can_be_a_complete_stop():
    stop, = summarize_braking([frame(0, 2), frame(1, 0)]).stopping_episodes
    assert stop.duration_s == 0.25
    assert stop.distance_m == 0.25


def test_brake_command_starts_episode_even_without_calculable_deceleration():
    stop, = summarize_braking([frame(0, 4, brake=0.5), frame(1, 0, 0)]).stopping_episodes
    assert stop.duration_s == 0.25
    assert stop.distance_m == 0.5


def test_handbrake_command_starts_episode():
    stop, = summarize_braking([frame(0, 4, 0, hand_brake=True), frame(1, 0, 0)]).stopping_episodes
    assert stop.start_time_s == 0
    assert stop.distance_m == 0.5


def test_current_brake_command_does_not_backdate_stop_when_inferred_deceleration_is_zero():
    samples = [frame(0, 4), frame(1, 4, brake=1), frame(2, 0)]
    stop, = summarize_braking(samples).stopping_episodes
    assert stop.start_time_s == 0.25
    assert stop.distance_m == 0.5


def test_interrupted_braking_discards_original_stopping_start():
    samples = [frame(0, 6, -8), frame(1, 4, -8), frame(2, 4, 0), frame(3, 2, -8), frame(4, 0, 0)]
    stop, = summarize_braking(samples).stopping_episodes
    assert stop.start_time_s == 0.75
    assert stop.duration_s == 0.25
    assert stop.distance_m == 0.25


def test_brake_command_keeps_pending_stop_active_during_zero_deceleration():
    samples = [frame(0, 4, 0, brake=1), frame(1, 4, 0, brake=1), frame(2, 0, 0)]
    stop, = summarize_braking(samples).stopping_episodes
    assert stop.start_time_s == 0
    assert stop.duration_s == 0.5
    assert stop.distance_m == 1.5


def test_stopped_threshold_completes_episode_and_does_not_start_when_already_stopped():
    result = summarize_braking([frame(0, 2, brake=1), frame(1, 0.1), frame(2, 0, brake=1)])
    stop, = result.stopping_episodes
    assert stop.end_time_s == 0.25
    assert stop.distance_m == pytest.approx(0.2625)


def test_independent_completed_stops_are_retained_in_order():
    samples = [frame(0, 2), frame(1, 0), frame(2, 2), frame(3, 0)]
    result = summarize_braking(samples)
    assert [stop.start_time_s for stop in result.stopping_episodes] == [0, 0.5]
    assert [stop.distance_m for stop in result.stopping_episodes] == [0.25, 0.25]


@pytest.mark.parametrize(
    "samples",
    [
        [frame(0, 4, brake=1), frame(1, 2)],
        [frame(0, 4, brake=1), frame(2, 0, 0)],
        [frame(0, 4, brake=1), frame(1, 0, 0, time=1)],
        [frame(0, 4, brake=1), frame(1), frame(2, 0, 0)],
    ],
)
def test_incomplete_stops_and_discontinuous_trajectories_have_no_stopping_estimate(samples):
    assert summarize_braking(samples).stopping_episodes == ()


@pytest.mark.parametrize("name", ["hard_braking_threshold_mps2", "stopped_speed_mps", "max_sample_gap_s"])
@pytest.mark.parametrize("value", [True, "4", None, -1, float("nan"), float("inf")])
def test_invalid_thresholds_are_rejected(name, value):
    with pytest.raises(ValueError, match=name):
        summarize_braking([], **{name: value})


@pytest.mark.parametrize("name", ["hard_braking_threshold_mps2", "max_sample_gap_s"])
def test_positive_thresholds_cannot_be_zero(name):
    with pytest.raises(ValueError, match=name):
        summarize_braking([], **{name: 0})


def test_zero_stopped_threshold_requires_zero_speed():
    result = summarize_braking([frame(0, 2, brake=1), frame(1, 0.1)], stopped_speed_mps=0)
    assert result.stopping_episodes == ()


@pytest.mark.parametrize(
    "samples",
    [
        [frame(0, Vector3(x=1.7e308, y=1.7e308), 0)],
        [frame(0, Vector3(x=1, y=1), Vector3(x=-1.7e308, y=-1.7e308))],
        [frame(0, 1e308), frame(1, 0)],
    ],
)
def test_finite_inputs_cannot_silently_produce_infinite_metrics(samples):
    with pytest.raises(ValueError, match="finite range"):
        summarize_braking(samples)
