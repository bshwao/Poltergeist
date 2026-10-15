"""Sensor replay, coverage, and episode boundaries for safety event metrics."""

import pytest

from evaluation.collision_detector import summarize_collisions
from evaluation.traffic_rules import summarize_traffic_rules
from shared.models import TelemetryFrame, TrafficRule


def frame(number, *, time=None, collisions=None, violations=None, speed=None, limit=None):
    return {
        "frame": number,
        "time_s": float(number) if time is None else time,
        "ego": {
            "actor_id": "ego",
            "velocity_mps": None if speed is None else {"x": speed, "y": 0.0, "z": 0.0},
            "speed_limit_mps": limit,
        },
        "collisions": collisions,
        "traffic_violations": violations,
    }


def collision(other="car", impulse=10.0, event_id=None):
    return {"other_actor_id": other, "impulse_n_s": impulse, "event_id": event_id}


def violation(rule="red_light", *, actor="ego", source=None, event_id=None, details=""):
    return {
        "rule": rule, "actor_id": actor, "source_id": source,
        "event_id": event_id, "details": details,
    }


@pytest.mark.parametrize("frames", [[], [frame(0)], [frame(0), frame(1)]])
def test_collision_sensor_missing_is_not_zero_collisions(frames):
    summary = summarize_collisions(frames)
    assert summary.count is None
    assert summary.observed_frames == 0
    assert summary.episodes == ()


def test_observed_empty_collision_sensor_has_zero_count():
    summary = summarize_collisions([frame(0), frame(1, collisions=[]), frame(2, collisions=[])])
    assert summary.count == 0
    assert summary.observed_frames == 2


def test_repeated_contact_callbacks_merge_with_maximum_impulse():
    summary = summarize_collisions([
        frame(0, collisions=[collision(impulse=5.0, event_id="a")]),
        frame(1, time=0.2, collisions=[]),
        frame(2, time=0.4, collisions=[collision(impulse=12.0, event_id="b"), collision(impulse=8.0)]),
        frame(3, time=1.4, collisions=[collision(impulse=6.0)]),
        frame(4, time=2.5, collisions=[collision(impulse=3.0)]),
    ])
    assert summary.count == 2
    assert summary.observed_frames == 5
    assert summary.episodes[0].start_time_s == 0.0
    assert summary.episodes[0].end_time_s == 1.4
    assert summary.episodes[0].max_impulse_n_s == 12.0
    assert summary.episodes[1].start_time_s == 2.5


def test_collision_event_id_replay_never_extends_contact_or_creates_new_incident():
    summary = summarize_collisions([
        frame(0, collisions=[collision(event_id="a")]),
        frame(1, time=0.9, collisions=[collision(event_id="a")]),
        frame(2, time=1.5, collisions=[collision(event_id="b")]),
        frame(3, time=10.0, collisions=[collision(event_id="a")]),
    ])
    assert summary.count == 2
    assert [episode.end_time_s for episode in summary.episodes] == [0.0, 1.5]


def test_contacts_with_different_actors_are_distinct_even_in_same_frame():
    summary = summarize_collisions([
        frame(0, collisions=[collision("car"), collision("pedestrian"), collision("car")]),
    ])
    assert summary.count == 2
    assert {episode.other_actor_id for episode in summary.episodes} == {"car", "pedestrian"}


@pytest.mark.parametrize("changed", [collision("other", event_id="same"), collision(impulse=9.0, event_id="same")])
def test_collision_event_id_payload_conflict_is_rejected(changed):
    with pytest.raises(ValueError, match="Collision event_id 'same'.*inconsistent"):
        summarize_collisions([
            frame(0, collisions=[collision(event_id="same")]),
            frame(1, collisions=[changed]),
        ])


def test_collision_gap_zero_keeps_separate_frame_contacts_separate():
    summary = summarize_collisions([
        frame(0, collisions=[collision(), collision()]),
        frame(1, time=0.001, collisions=[collision()]),
    ], episode_gap_s=0.0)
    assert summary.count == 2


def test_collisions_accept_validated_models_and_single_pass_generator():
    summary = summarize_collisions(TelemetryFrame.model_validate(data) for data in [
        frame(0, collisions=[]), frame(1, collisions=[collision()]),
    ])
    assert summary.count == 1


@pytest.mark.parametrize("frames", [[], [frame(0)], [frame(0, speed=20.0)], [frame(0, limit=10.0)]])
def test_unobserved_rule_channel_and_incomplete_speed_data_have_unknown_count(frames):
    summary = summarize_traffic_rules(frames)
    assert summary.count is None
    assert summary.observed_frames == 0
    assert summary.speeding_observed_frames == 0


def test_observed_empty_rule_channel_has_zero_count():
    summary = summarize_traffic_rules([frame(0), frame(1, violations=[])])
    assert summary.count == 0
    assert summary.observed_frames == 1


def test_other_actors_violations_do_not_count_for_ego():
    summary = summarize_traffic_rules([
        frame(0, violations=[violation(actor="other"), violation("speeding", actor="other")]),
    ])
    assert summary.count == 0
    assert summary.episodes == ()


def test_continuous_rule_callbacks_count_as_one_episode():
    summary = summarize_traffic_rules([
        frame(0, violations=[violation(event_id="first"), violation()]),
        frame(1, time=0.4, violations=[violation(event_id="second")]),
        frame(2, time=0.8, violations=[violation()]),
    ])
    assert summary.count == 1
    assert summary.episodes[0].rule == TrafficRule.RED_LIGHT
    assert summary.episodes[0].start_time_s == 0.0
    assert summary.episodes[0].end_time_s == 0.8


@pytest.mark.parametrize("middle, expected", [([], 2), (None, 1), ([violation(actor="other")], 2)])
def test_observed_clear_closes_rule_but_unknown_sample_does_not(middle, expected):
    summary = summarize_traffic_rules([
        frame(0, violations=[violation()]),
        frame(1, time=0.25, violations=middle),
        frame(2, time=0.5, violations=[violation()]),
    ])
    assert summary.count == expected


def test_missing_rule_samples_with_large_gap_split_episodes():
    summary = summarize_traffic_rules([
        frame(0, violations=[violation()]),
        frame(1, time=0.5),
        frame(2, time=1.1, violations=[violation()]),
    ])
    assert summary.count == 2


def test_rule_episode_gap_can_be_configured():
    frames = [frame(0, violations=[violation()]), frame(1, time=1.5, violations=[violation()])]
    assert summarize_traffic_rules(frames).count == 2
    assert summarize_traffic_rules(frames, episode_gap_s=2.0).count == 1


def test_distinct_sources_and_distinct_rules_remain_separate():
    summary = summarize_traffic_rules([
        frame(0, violations=[violation(source="light-a"), violation(source="light-b"), violation("stop_sign", source="light-a")]),
    ])
    assert summary.count == 3
    assert [(episode.rule, episode.source_id) for episode in summary.episodes] == [
        (TrafficRule.RED_LIGHT, "light-a"), (TrafficRule.RED_LIGHT, "light-b"), (TrafficRule.STOP_SIGN, "light-a"),
    ]


def test_rule_event_replay_does_not_extend_or_reopen_episode():
    summary = summarize_traffic_rules([
        frame(0, violations=[violation(event_id="first")]),
        frame(1, time=0.9, violations=[violation(event_id="first")]),
        frame(2, time=1.5, violations=[violation(event_id="second")]),
        frame(3, time=1.6, violations=[]),
        frame(4, time=1.7, violations=[violation(event_id="first")]),
    ])
    assert summary.count == 2
    assert [episode.end_time_s for episode in summary.episodes] == [0.0, 1.5]


@pytest.mark.parametrize("change", [
    {"actor": "other"}, {"rule": "stop_sign"}, {"source": "light-b"}, {"details": "changed"},
])
def test_rule_event_id_payload_conflict_is_rejected(change):
    with pytest.raises(ValueError, match="Traffic event_id 'same'.*inconsistent"):
        summarize_traffic_rules([
            frame(0, violations=[violation(event_id="same")]),
            frame(1, violations=[violation(event_id="same", **change)]),
        ])


@pytest.mark.parametrize("speed, expected", [(9.0, 0), (10.0, 0), (10.5, 0), (10.5001, 1), (-12.0, 1)])
def test_speeding_threshold_is_strict_and_speed_uses_velocity_magnitude(speed, expected):
    summary = summarize_traffic_rules([frame(0, speed=speed, limit=10.0)])
    assert summary.count == expected
    assert summary.observed_frames == 0
    assert summary.speeding_observed_frames == 1


def test_speeding_tolerance_is_configurable():
    frames = [frame(0, speed=10.25, limit=10.0)]
    assert summarize_traffic_rules(frames).count == 0
    assert summarize_traffic_rules(frames, speeding_tolerance_mps=0.0).count == 1


def test_decimal_speed_at_limit_plus_tolerance_is_not_speeding():
    assert summarize_traffic_rules([frame(0, speed=1.3, limit=1.0)], speeding_tolerance_mps=0.3).count == 0


def test_speeding_is_derived_from_three_dimensional_velocity():
    data = frame(0, limit=4.0)
    data["ego"]["velocity_mps"] = {"x": 0.0, "y": 3.0, "z": 4.0}
    assert summarize_traffic_rules([data]).count == 1


@pytest.mark.parametrize("middle_speed, expected", [(None, 1), (10.0, 2)])
def test_speeding_clear_and_unobserved_speed_samples(middle_speed, expected):
    summary = summarize_traffic_rules([
        frame(0, speed=12.0, limit=10.0),
        frame(1, time=0.25, speed=middle_speed, limit=10.0),
        frame(2, time=0.5, speed=12.0, limit=10.0),
    ])
    assert summary.count == expected


def test_explicit_and_derived_speeding_merge_and_keep_source():
    summary = summarize_traffic_rules([
        frame(0, speed=12.0, limit=10.0, violations=[violation("speeding", source="zone-a", event_id="a")]),
        frame(1, time=0.5, speed=12.0, limit=10.0),
        frame(2, time=0.75, speed=12.0, limit=10.0, violations=[violation("speeding", source="zone-a", event_id="a")]),
    ])
    assert summary.count == 1
    assert summary.episodes[0].source_id == "zone-a"
    assert summary.episodes[0].end_time_s == 0.75


def test_explicit_distinct_speed_zones_remain_separate_under_derivation():
    summary = summarize_traffic_rules([
        frame(0, speed=12.0, limit=10.0, violations=[violation("speeding", source="zone-a"), violation("speeding", source="zone-b")]),
    ])
    assert summary.count == 2
    assert {episode.source_id for episode in summary.episodes} == {"zone-a", "zone-b"}


def test_replayed_speed_zone_cannot_replace_current_measured_source():
    summary = summarize_traffic_rules([
        frame(0, speed=12.0, limit=10.0, violations=[violation("speeding", source="zone-a", event_id="a")]),
        frame(1, time=0.1, speed=12.0, limit=10.0, violations=[violation("speeding", source="zone-b", event_id="b")]),
        frame(2, time=0.2, speed=12.0, limit=10.0, violations=[violation("speeding", source="zone-a", event_id="a")]),
        frame(3, time=0.3, speed=12.0, limit=10.0),
    ])
    assert summary.count == 2
    assert [(episode.source_id, episode.start_time_s, episode.end_time_s) for episode in summary.episodes] == [
        ("zone-a", 0.0, 0.0), ("zone-b", 0.1, 0.3),
    ]


def test_replayed_speed_zone_cannot_name_a_new_derived_episode_after_clear():
    summary = summarize_traffic_rules([
        frame(0, speed=12.0, limit=10.0, violations=[violation("speeding", source="zone-a", event_id="a")]),
        frame(1, time=0.1, speed=10.0, limit=10.0, violations=[]),
        frame(2, time=0.2, speed=12.0, limit=10.0, violations=[violation("speeding", source="zone-a", event_id="a")]),
    ])
    assert summary.count == 2
    assert [episode.source_id for episode in summary.episodes] == ["zone-a", None]


@pytest.mark.parametrize("middle_speed", [None, 12.0])
def test_fresh_speed_source_closes_replayed_old_source_before_derived_continuation(middle_speed):
    summary = summarize_traffic_rules([
        frame(0, speed=12.0, limit=10.0, violations=[violation("speeding", source="zone-a", event_id="a")]),
        frame(1, time=0.1, speed=middle_speed, limit=10.0, violations=[
            violation("speeding", source="zone-a", event_id="a"),
            violation("speeding", source="zone-b", event_id="b"),
        ]),
        frame(2, time=0.2, speed=12.0, limit=10.0),
    ])
    assert summary.count == 2
    assert [(episode.source_id, episode.end_time_s) for episode in summary.episodes] == [
        ("zone-a", 0.0), ("zone-b", 0.2),
    ]


@pytest.mark.parametrize("named_speed", [None, 12.0])
def test_first_named_source_promotes_a_continuous_derived_speeding_episode(named_speed):
    summary = summarize_traffic_rules([
        frame(0, speed=12.0, limit=10.0),
        frame(1, time=0.1, speed=named_speed, limit=10.0, violations=[violation("speeding", source="zone-a")]),
    ])
    assert summary.count == 1
    assert summary.episodes[0].source_id == "zone-a"
    assert summary.episodes[0].start_time_s == 0.0
    assert summary.episodes[0].end_time_s == 0.1


@pytest.mark.parametrize("anonymous_speed", [None, 12.0])
def test_fresh_anonymous_callback_continues_a_sole_known_speed_zone(anonymous_speed):
    summary = summarize_traffic_rules([
        frame(0, speed=12.0, limit=10.0, violations=[violation("speeding", source="zone-a")]),
        frame(1, time=0.1, speed=anonymous_speed, limit=10.0, violations=[violation("speeding")]),
    ])
    assert summary.count == 1
    assert summary.episodes[0].source_id == "zone-a"
    assert summary.episodes[0].end_time_s == 0.1


@pytest.mark.parametrize("first_source, second_source", [(None, "zone-a"), ("zone-a", None)])
def test_speed_source_promotion_does_not_bridge_expired_episode(first_source, second_source):
    summary = summarize_traffic_rules([
        frame(0, violations=[violation("speeding", source=first_source)]),
        frame(1, time=1.1, violations=[violation("speeding", source=second_source)]),
    ])
    assert summary.count == 2
    assert [episode.source_id for episode in summary.episodes] == [first_source, second_source]


def test_observed_clear_prevents_anonymous_episode_promotion():
    summary = summarize_traffic_rules([
        frame(0, speed=12.0, limit=10.0),
        frame(1, time=0.1, speed=10.0, limit=10.0, violations=[]),
        frame(2, time=0.2, violations=[violation("speeding", source="zone-a")]),
    ])
    assert summary.count == 2
    assert [episode.source_id for episode in summary.episodes] == [None, "zone-a"]


def test_anonymous_and_single_named_callback_in_same_frame_are_one_zone():
    summary = summarize_traffic_rules([
        frame(0, violations=[violation("speeding"), violation("speeding", source="zone-a")]),
    ])
    assert summary.count == 1
    assert summary.episodes[0].source_id == "zone-a"


def test_ambiguous_anonymous_callback_does_not_choose_between_multiple_known_zones():
    summary = summarize_traffic_rules([
        frame(0, violations=[violation("speeding", source="zone-a"), violation("speeding", source="zone-b")]),
        frame(1, time=0.1, violations=[violation("speeding")]),
    ])
    assert summary.count == 3
    assert [episode.source_id for episode in summary.episodes] == ["zone-a", "zone-b", None]


def test_derived_speeding_rejects_overflowing_velocity_magnitude():
    data = frame(0, limit=10.0)
    data["ego"]["velocity_mps"] = {"x": 1.7e308, "y": 1.7e308, "z": 1.7e308}
    with pytest.raises(ValueError, match="Velocity magnitude.*finite"):
        summarize_traffic_rules([data])


def test_large_finite_speed_and_tolerance_remain_a_clear_observation():
    summary = summarize_traffic_rules([
        frame(0, speed=1.7e308, limit=1.0e308),
    ], speeding_tolerance_mps=1.0e308)
    assert summary.count == 0
    assert summary.speeding_observed_frames == 1


def test_derived_speeding_can_supply_evidence_when_full_sensor_list_is_empty():
    summary = summarize_traffic_rules([
        frame(0, speed=12.0, limit=10.0, violations=[]),
        frame(1, time=0.5, speed=12.0, limit=10.0, violations=[]),
    ])
    assert summary.count == 1
    assert summary.observed_frames == 2
    assert summary.speeding_observed_frames == 2


@pytest.mark.parametrize("name", ["episode_gap_s", "speeding_tolerance_mps"])
@pytest.mark.parametrize("value", [-1.0, float("nan"), float("inf"), True, "1"])
def test_invalid_rule_thresholds_are_rejected(name, value):
    with pytest.raises(ValueError, match=name):
        summarize_traffic_rules([], **{name: value})


@pytest.mark.parametrize("value", [-1.0, float("nan"), float("inf"), True, "1"])
def test_invalid_collision_thresholds_are_rejected(value):
    with pytest.raises(ValueError, match="episode_gap_s"):
        summarize_collisions([], episode_gap_s=value)


@pytest.mark.parametrize("summarize", [summarize_collisions, summarize_traffic_rules])
def test_event_evaluators_reject_non_monotonic_input(summarize):
    with pytest.raises(ValueError):
        summarize([frame(1), frame(0)])
