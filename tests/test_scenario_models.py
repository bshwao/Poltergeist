"""Scenario placement dependencies and timed behavior contract boundaries."""

import pytest
from pydantic import ValidationError

from shared.models import ActorSpec, BrakingEvent, RelativeSpawn, Scenario, SpeedChangeEvent, Vector3


def scenario_with(actors, events=()):
    return Scenario.model_validate({"name": "custom", "duration_s": 10, "actors": actors, "events": list(events)})


def actor(actor_id, anchor=None):
    data = {"id": actor_id, "type": "ego_vehicle" if actor_id == "ego" else "vehicle"}
    if anchor is not None:
        data["relative_spawn"] = {"anchor_actor_id": anchor, "offset_m": {"x": 5}}
    return data


@pytest.mark.parametrize(
    "placement",
    [
        {},
        {"spawn": {"position": {"x": 1, "y": 2, "z": 3}}},
        {"spawn_point_index": 0},
        {"relative_spawn": {"anchor_actor_id": "ego"}},
    ],
)
def test_actor_accepts_one_placement_mode_or_adapter_default(placement):
    result = ActorSpec.model_validate({"type": "vehicle"} | placement)
    assert ActorSpec.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize(
    "placement",
    [
        {"spawn": {}, "spawn_point_index": 0},
        {"spawn": {}, "relative_spawn": {"anchor_actor_id": "ego"}},
        {"spawn_point_index": 0, "relative_spawn": {"anchor_actor_id": "ego"}},
        {"spawn": {}, "spawn_point_index": 0, "relative_spawn": {"anchor_actor_id": "ego"}},
    ],
)
def test_ambiguous_actor_placement_is_rejected(placement):
    with pytest.raises(ValidationError, match="placement must use only one"):
        ActorSpec.model_validate({"type": "vehicle"} | placement)


@pytest.mark.parametrize("index", [-1, True, "0", 1.2])
def test_spawn_point_index_requires_nonnegative_integer(index):
    with pytest.raises(ValidationError):
        ActorSpec(type="vehicle", spawn_point_index=index)


@pytest.mark.parametrize("value", [True, "90", float("inf"), float("nan")])
def test_relative_yaw_rejects_nonfinite_and_coerced_numbers(value):
    with pytest.raises(ValidationError):
        RelativeSpawn(anchor_actor_id="ego", yaw_offset_deg=value)


def test_relative_placement_defaults_are_independent():
    first = RelativeSpawn(anchor_actor_id="ego")
    second = RelativeSpawn(anchor_actor_id="ego")
    first.offset_m.x = 5

    assert second.offset_m == Vector3()
    assert second.yaw_offset_deg == 0


def test_relative_spawn_chain_is_valid_regardless_of_actor_order():
    result = scenario_with([actor("rear", "lead"), actor("lead", "ego"), actor("ego")])
    assert result.actors[0].relative_spawn.anchor_actor_id == "lead"
    assert Scenario.model_validate_json(result.model_dump_json()) == result


def test_anonymous_actor_can_reference_an_explicit_anchor():
    result = scenario_with([actor("ego"), {"type": "vehicle", "relative_spawn": {"anchor_actor_id": "ego"}}])
    assert result.actors[1].id is None


def test_unreferenced_programmatic_actor_ids_remain_optional():
    result = scenario_with([{"type": "ego_vehicle"}, {"type": "pedestrian"}])
    assert [entry.id for entry in result.actors] == [None, None]


def test_relative_spawn_cannot_reference_an_implicit_actor_id():
    with pytest.raises(ValidationError, match="unknown actor ID: ego"):
        scenario_with([{"type": "ego_vehicle"}, actor("lead", "ego")])


@pytest.mark.parametrize(
    ("actors", "error"),
    [
        ([actor("ego"), actor("lead", "missing")], "unknown actor ID: missing"),
        ([actor("ego"), actor("lead", "lead")], "cannot spawn relative to itself"),
        ([actor("ego", "lead"), actor("lead", "ego")], "must not contain cycles"),
        ([actor("ego"), actor("a", "b"), actor("b", "c"), actor("c", "a")], "must not contain cycles"),
    ],
)
def test_invalid_spawn_dependencies_are_rejected(actors, error):
    with pytest.raises(ValidationError, match=error):
        scenario_with(actors)


def test_timed_events_are_discriminated_and_round_trip():
    result = scenario_with(
        [actor("ego"), actor("lead", "ego")],
        [
            {"action": "set_speed", "actor_id": "lead", "trigger_time_s": 0, "speed_mps": 12},
            {"action": "brake", "actor_id": "lead", "trigger_time_s": 2, "deceleration_mps2": 6},
        ],
    )

    assert isinstance(result.events[0], SpeedChangeEvent)
    assert isinstance(result.events[1], BrakingEvent)
    assert Scenario.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("trigger", [10, 10.1])
def test_events_must_trigger_before_scenario_ends(trigger):
    with pytest.raises(ValidationError, match="less than duration_s"):
        scenario_with(
            [actor("ego")],
            [{"action": "set_speed", "actor_id": "ego", "trigger_time_s": trigger, "speed_mps": 0}],
        )


def test_event_cannot_reference_missing_actor():
    with pytest.raises(ValidationError, match="unknown actor ID: missing"):
        scenario_with(
            [actor("ego")],
            [{"action": "brake", "actor_id": "missing", "trigger_time_s": 0, "deceleration_mps2": 6}],
        )


@pytest.mark.parametrize(
    "event",
    [
        {"action": "teleport", "actor_id": "ego", "trigger_time_s": 0},
        {"action": "set_speed", "actor_id": "ego", "trigger_time_s": -1, "speed_mps": 1},
        {"action": "set_speed", "actor_id": "ego", "trigger_time_s": True, "speed_mps": 1},
        {"action": "set_speed", "actor_id": "ego", "trigger_time_s": 0, "speed_mps": -1},
        {"action": "set_speed", "actor_id": "ego", "trigger_time_s": 0, "speed_mps": float("nan")},
        {"action": "set_speed", "actor_id": "ego", "trigger_time_s": 0, "speed_mps": "1"},
        {"action": "set_speed", "actor_id": "ego", "trigger_time_s": 0, "speed_mps": 1, "deceleration_mps2": 6},
        {"action": "brake", "actor_id": "ego", "trigger_time_s": 0, "deceleration_mps2": 0},
        {"action": "brake", "actor_id": "ego", "trigger_time_s": 0, "deceleration_mps2": float("inf")},
    ],
)
def test_invalid_event_actions_and_physical_values_are_rejected(event):
    with pytest.raises(ValidationError):
        scenario_with([actor("ego")], [event])


def test_event_defaults_are_independent_between_scenarios():
    first = scenario_with([actor("ego")])
    second = scenario_with([actor("ego")])
    first.events.append(SpeedChangeEvent(actor_id="ego", trigger_time_s=0, speed_mps=0))

    assert second.events == []
