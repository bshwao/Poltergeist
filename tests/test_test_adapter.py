"""Deterministic kinematics, placements, contacts, and adapter lifecycle."""

import math
import random

import pytest

from evaluation import summarize_traffic_rules
from scenarios.scenario_loader import list_templates, load_template, validate_scenario
from shared.models import ControlInput, TrafficRule
from simulator.agents import BaselineAgent
from simulator.errors import SimulationError
from simulator.protocols import ActorCommand
from simulator.test_adapter import TestSimulationAdapter


def scenario(*, speed=0.0, actors=None, **changes):
    return validate_scenario({
        "name": "kinematics", "seed": 11, "fixed_delta_seconds": 0.1,
        "actors": actors or [{"id": "ego", "type": "ego_vehicle", "spawn_point_index": 0, "speed_mps": speed}],
        **changes,
    })


def test_initial_frame_has_stable_zero_clock_and_full_known_telemetry():
    adapter = TestSimulationAdapter()
    frame = adapter.open(scenario(speed=5.0))
    assert frame.frame == frame.time_s == 0
    assert frame.ego.actor_id == "ego"
    assert frame.ego.velocity_mps.x == 5.0
    assert frame.ego.acceleration_mps2.x == 0.0
    assert frame.ego.bounding_box_transform.position.z == frame.ego.bounding_box_extent.z
    assert frame.actors == frame.collisions == frame.traffic_violations == []
    assert set(frame.rule_checks) == set(TrafficRule)
    assert adapter.sensor_samples() == ()
    adapter.close()


def test_exact_constant_acceleration_and_braking_distance():
    adapter = TestSimulationAdapter()
    adapter.open(scenario())
    for _ in range(10):
        frame = adapter.step(ControlInput(throttle=1.0), {})
    assert frame.frame == 10
    assert frame.time_s == 1.0
    assert frame.ego.transform.position.x == pytest.approx(1.5)
    assert frame.ego.velocity_mps.x == pytest.approx(3.0)
    assert frame.ego.acceleration_mps2.x == pytest.approx(3.0)
    for _ in range(4):
        frame = adapter.step(ControlInput(brake=1.0), {})
    # 3 m/s stops in 0.375 s after travelling v²/(2a).
    assert frame.ego.transform.position.x == pytest.approx(1.5 + 9.0 / 16.0)
    assert frame.ego.velocity_mps.x == 0.0
    position = frame.ego.transform.position.x
    assert adapter.step(ControlInput(brake=1.0), {}).ego.transform.position.x == position
    adapter.close()


def test_reverse_and_bicycle_steering_are_world_space():
    adapter = TestSimulationAdapter()
    adapter.open(scenario())
    frame = adapter.step(ControlInput(throttle=1.0, reverse=True, steer=1.0), {})
    assert frame.ego.transform.position.x < 0.0
    assert frame.ego.transform.rotation.yaw_deg < 0.0
    assert frame.ego.velocity_mps.x < 0.0
    adapter.close()
    adapter.open(scenario(speed=10.0))
    frame = adapter.step(ControlInput(steer=1.0), {})
    assert frame.ego.transform.position.y > 0.0
    assert 0.0 < frame.ego.transform.rotation.yaw_deg <= 9.0
    assert frame.ego.velocity_mps.y > 0.0
    adapter.close()


def test_npc_commands_persist_and_requested_deceleration_is_physical():
    value = scenario(actors=[
        {"id": "ego", "type": "ego_vehicle", "spawn_point_index": 0},
        {"id": "lead", "type": "lead_vehicle", "spawn_point_index": 4, "speed_mps": 2.0},
    ])
    adapter = TestSimulationAdapter()
    adapter.open(value)
    frame = adapter.step(ControlInput(), {"lead": ActorCommand(0.0, deceleration_mps2=6.0)})
    assert frame.actors[0].velocity_mps.x == pytest.approx(1.4)
    assert frame.actors[0].acceleration_mps2.x == pytest.approx(-6.0)
    for _ in range(4):
        frame = adapter.step(ControlInput(), {})
    assert frame.actors[0].velocity_mps.x == 0.0
    assert frame.actors[0].transform.position.x == pytest.approx(80.0 + 4.0 / 12.0)
    frame = adapter.step(ControlInput(), {"lead": ActorCommand(1.0)})
    assert frame.actors[0].velocity_mps.x == pytest.approx(0.3)
    adapter.close()


def test_contact_is_real_overlap_and_stops_both_actors():
    adapter = TestSimulationAdapter()
    initial = adapter.open(scenario(speed=10.0, actors=[
        {"id": "ego", "type": "ego_vehicle", "spawn_point_index": 0, "speed_mps": 10.0},
        {"id": "obstacle", "type": "vehicle", "spawn": {"position": {"x": 5.0}}},
    ]))
    assert initial.collisions == []
    frame = adapter.step(ControlInput(), {})
    assert [event.other_actor_id for event in frame.collisions] == ["obstacle"]
    assert frame.collisions[0].event_id == "test-contact:1:obstacle"
    assert frame.collisions[0].impulse_n_s == 0.0
    assert frame.ego.velocity_mps.x == frame.actors[0].velocity_mps.x == 0.0
    assert frame.ego.acceleration_mps2.x == -100.0
    adapter.close()


def test_nearby_parallel_and_vertical_actors_do_not_invent_contacts():
    adapter = TestSimulationAdapter()
    adapter.open(scenario(actors=[
        {"id": "ego", "type": "ego_vehicle", "spawn_point_index": 0},
        {"id": "parallel", "type": "vehicle", "spawn": {"position": {"y": 3.0}}},
        {"id": "bridge", "type": "vehicle", "spawn": {"position": {"z": 4.0}}},
    ]))
    assert adapter.step(ControlInput(), {}).collisions == []
    adapter.close()


def test_relative_spawn_hierarchy_resolves_in_rotated_anchor_frame():
    adapter = TestSimulationAdapter()
    value = scenario(actors=[
        {"id": "ped", "type": "pedestrian", "relative_spawn": {
            "anchor_actor_id": "lead", "offset_m": {"x": 2.0}, "yaw_offset_deg": -90.0,
        }},
        {"id": "lead", "type": "lead_vehicle", "relative_spawn": {
            "anchor_actor_id": "ego", "offset_m": {"x": 10.0, "y": 3.0},
        }},
        {"id": "ego", "type": "ego_vehicle", "spawn": {
            "position": {"x": 4.0, "y": 5.0}, "rotation": {"yaw_deg": 90.0},
        }},
    ])
    adapter.open(value)
    poses = adapter.runtime_info()["resolved_spawns"]
    assert poses["lead"].position.x == pytest.approx(1.0)
    assert poses["lead"].position.y == pytest.approx(15.0)
    assert poses["ped"].position.x == pytest.approx(1.0)
    assert poses["ped"].position.y == pytest.approx(17.0)
    assert poses["ped"].rotation.yaw_deg == 0.0
    adapter.close()


def test_default_placements_are_seeded_and_do_not_mutate_global_random():
    value = scenario(actors=[{"type": "ego_vehicle"}, {"type": "vehicle"}, {"type": "pedestrian"}])
    before = random.getstate()
    adapters = [TestSimulationAdapter(), TestSimulationAdapter()]
    frames = [adapter.open(value) for adapter in adapters]
    assert random.getstate() == before
    assert frames[0] == frames[1]
    assert set(adapters[0].runtime_info()["resolved_spawns"]) == {"ego", "vehicle", "pedestrian"}
    for adapter in adapters:
        adapter.close()
    third = TestSimulationAdapter()
    changed = third.open(value.model_copy(update={"seed": 12}))
    assert changed.ego.transform != frames[0].ego.transform
    third.close()


def test_relative_yaw_rotates_about_a_pitched_anchor_local_vertical():
    adapter = TestSimulationAdapter()
    initial = adapter.open(scenario(actors=[
        {"id": "ego", "type": "ego_vehicle", "spawn": {
            "rotation": {"pitch_deg": 30.0},
        }},
        {"id": "crossing", "type": "crossing_vehicle", "speed_mps": 2.0,
         "relative_spawn": {"anchor_actor_id": "ego", "offset_m": {"x": 10.0}, "yaw_offset_deg": 90.0}},
    ]))
    crossing = initial.actors[0]
    assert crossing.transform.position.x == pytest.approx(10.0 * math.cos(math.radians(30.0)))
    assert crossing.transform.position.z == pytest.approx(5.0)
    # Local yaw 90 makes child forward equal the parent's right axis.
    assert crossing.velocity_mps.x == pytest.approx(0.0, abs=1e-12)
    assert crossing.velocity_mps.y == pytest.approx(2.0)
    assert crossing.velocity_mps.z == pytest.approx(0.0, abs=1e-12)
    assert crossing.transform.rotation.roll_deg == pytest.approx(30.0)
    adapter.close()


@pytest.mark.parametrize("template", list_templates())
def test_all_templates_run_with_repeatable_motion_and_events(template):
    value = load_template(template)
    value.duration_s = 3.0
    traces = []
    for _ in range(2):
        adapter = TestSimulationAdapter()
        frame = adapter.open(value)
        agent = BaselineAgent()
        trace = [frame]
        applied = set()
        for _ in range(60):
            commands = {}
            for index, event in enumerate(value.events):
                if index not in applied and event.trigger_time_s <= frame.time_s + 1e-9:
                    commands[event.actor_id] = (
                        ActorCommand(event.speed_mps) if event.action == "set_speed"
                        else ActorCommand(0.0, event.deceleration_mps2)
                    )
                    applied.add(index)
            frame = adapter.step(agent.run_step(adapter.observation(frame, 12.0)), commands)
            trace.append(frame)
        assert adapter.finalize(trace) == trace
        assert frame.frame == 60 and frame.time_s == 3.0
        assert all(math.isfinite(item.ego.transform.position.x) for item in trace)
        traces.append(trace)
        adapter.close()
    assert traces[0] == traces[1]


def test_speeding_is_observed_and_runtime_metadata_survives_close():
    adapter = TestSimulationAdapter()
    frame = adapter.open(scenario(speed=15.0))
    summary = summarize_traffic_rules([frame])
    assert [event.rule for event in summary.episodes] == [TrafficRule.SPEEDING]
    assert summarize_traffic_rules([frame], speeding_tolerance_mps=2.0).count == 0
    info = adapter.runtime_info()
    assert info["map_name"] == "test:Town03"
    assert info["server_version"] is info["client_version"] is None
    adapter.close()
    assert adapter.runtime_info() == info
    info["resolved_spawns"]["ego"].position.x = 500.0
    assert adapter.runtime_info()["resolved_spawns"]["ego"].position.x == 0.0


def test_lifecycle_guards_and_idempotent_cleanup():
    adapter = TestSimulationAdapter()
    assert adapter.runtime_info() == {}
    adapter.close()
    assert adapter.runtime_info() == {}
    with pytest.raises(SimulationError, match="not open"):
        adapter.step(ControlInput(), {})
    adapter.open(scenario())
    with pytest.raises(SimulationError, match="already open"):
        adapter.open(scenario())
    adapter.close()
    adapter.close()
    with pytest.raises(SimulationError, match="not open"):
        adapter.finalize([])
    assert adapter.open(scenario()).frame == 0
    adapter.close()


@pytest.mark.parametrize("command", [ActorCommand(-1.0), ActorCommand(float("nan")), ActorCommand(0.0, 0.0)])
def test_invalid_commands_rejected_before_motion(command):
    adapter = TestSimulationAdapter()
    adapter.open(scenario())
    with pytest.raises(ValueError):
        adapter.step(ControlInput(), {"ego": command})
    assert adapter.step(ControlInput(), {}).frame == 1
    adapter.close()
