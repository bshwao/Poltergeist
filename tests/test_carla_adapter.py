"""Orchestration against injected CARLA boundaries; real server tests stay optional."""

from types import SimpleNamespace as NS

import pytest

from evaluation import EvaluationSettings
from scenarios import validate_scenario
from shared.models import ActorState, ControlInput, TrafficRule, Transform, Vector3
from simulator.carla_adapter import CarlaSimulationAdapter
from simulator.errors import SimulationError
from simulator.protocols import ActorCommand, SimulationOptions
from simulator.simulation_runner import SimulationRunner


@pytest.fixture
def setup_adapter(monkeypatch):
    import simulator.carla_adapter as module

    log = []
    scenario = validate_scenario({
        "name": "carla-boundary", "duration_s": 0.1,
        "actors": [{"id": "driver", "type": "ego_vehicle", "spawn_point_index": 0}],
    })
    class World:
        frame = 50
        actor_states = [ActorState(actor_id="driver", transform=Transform(), velocity_mps=Vector3(), acceleration_mps2=Vector3())]
        def tick(self):
            self.frame += 1
            return self.frame
        def get_snapshot(self):
            return NS(frame=self.frame, timestamp=NS(elapsed_seconds=self.frame * 0.05))
        def get_map(self):
            return NS(name="/Game/Carla/Maps/Town03", get_waypoint=lambda *a, **k: None)
        def get_spectator(self):
            return NS(set_transform=lambda pose: log.append(("spectator", self.frame, pose)))
    world = World()
    ego = NS(id=11, apply_control=lambda control: log.append(("control", control)), get_location=lambda: NS(x=0,y=0,z=0))
    class Worlds:
        def __init__(self, *args, **kwargs): pass
        def open(self, scenario):
            log.append("world-open")
            return world
        def close(self): log.append("world-close")
    class Actors:
        resolved_spawns = {"driver": Transform()}
        actor_ids = {11: "driver", 77: "world:77"}
        def __init__(self, *args): pass
        def spawn(self):
            log.append("actors-spawn")
            return {"driver": ego}
        def states(self, snapshot):
            return world.actor_states
        def control_npcs(self, commands, dt): log.append(("npc", commands, dt))
        def route_heading(self, actor_id, pose, lookahead): return None
        def close(self): log.append("actors-close")
    class Sensors:
        def __init__(self, *args, **kwargs):
            assert kwargs["actor_ids"][77] == "world:77"
            assert kwargs["actor_id_resolver"](77) == "world:77"
        def open(self): log.append("sensors-open")
        def collect(self, frame):
            log.append(("collect", frame))
            return []
        def samples(self): return ()
        def finalize(self, frames): return frames
        def close(self): log.append("sensors-close")
    class Rules:
        def __init__(self, *args, **kwargs):
            assert kwargs["actor_id"] == "driver"
            assert kwargs["speeding_tolerance_mps"] == 1.0
            self.previous = None
        def observe(self, frame, time_s):
            if self.previous is not None:
                assert time_s > self.previous
            self.previous = time_s
            log.append(("rules", frame, time_s))
            return [], list(TrafficRule)
    monkeypatch.setattr(module, "WorldManager", Worlds)
    monkeypatch.setattr(module, "ActorManager", Actors)
    monkeypatch.setattr(module, "SensorManager", Sensors)
    monkeypatch.setattr(module, "RuleMonitor", Rules)
    client = NS(get_server_version=lambda: "0.9.16", get_client_version=lambda: "0.9.16")
    carla = NS(VehicleControl=lambda **kwargs: kwargs, LaneType=NS(Driving="Driving"),
               Location=NS, Rotation=NS, Transform=lambda location, rotation: NS(location=location, rotation=rotation))
    adapter = CarlaSimulationAdapter(client=client, carla_module=carla, evaluation_settings=EvaluationSettings(speeding_tolerance_mps=1))
    return adapter, scenario, log, world, client, module


def test_carla_boundary_runs_native_frames_and_restores_in_resource_order(setup_adapter):
    adapter, scenario, log, world, _, _ = setup_adapter
    output = SimulationRunner(adapter).run(scenario)
    assert output.result.simulation.status == "completed", output.result.simulation.error
    assert [frame.frame for frame in output.telemetry] == [51, 52, 53]
    assert [frame.time_s for frame in output.telemetry] == pytest.approx([0, 0.05, 0.1])
    assert output.result.runtime.server_version == "0.9.16"
    assert output.result.runtime.resolved_spawns["driver"] == Transform()
    assert log[-3:] == ["sensors-close", "actors-close", "world-close"]
    # Priming occurs before initialization tick, with monotonic rule times.
    observed_rules = [item for item in log if isinstance(item, tuple) and item[0] == "rules"]
    assert observed_rules[0] == ("rules", 50, 0)
    assert all(frame.rule_checks == list(TrafficRule) for frame in output.telemetry)


def test_wrong_carla_version_fails_before_world_mutations(setup_adapter):
    adapter, scenario, log, _, client, _ = setup_adapter
    client.get_server_version = lambda: "0.9.15"
    with pytest.raises(SimulationError, match="0.9.16"):
        adapter.open(scenario)
    adapter.close()
    assert log == []


@pytest.mark.parametrize("quality", [None, "Low", "Epic"])
def test_local_launcher_waits_before_versions_world_changes_and_actor_spawns(setup_adapter, monkeypatch, quality):
    adapter, scenario, log, _, client, module = setup_adapter
    adapter.client = None
    adapter.options.carla_quality = quality
    monkeypatch.setattr(module, "connect", lambda _: client)
    adapter.launcher = NS(ensure_running=lambda supplied, map_name, **kw: log.append(("ready", supplied is client, map_name, callable(kw["cancellation"]), kw["quality"])) or supplied)
    output = SimulationRunner(adapter).run(scenario)
    assert output.result.simulation.status == "completed"
    assert log[0] == ("ready", True, scenario.map, True, quality)
    assert log[1] == "world-open"


def test_partial_sensor_setup_failure_still_restores_world(setup_adapter, monkeypatch):
    adapter, scenario, log, _, _, module = setup_adapter
    class FailingSensors:
        def __init__(self, *args, **kwargs): pass
        def open(self): raise RuntimeError("sensor attach failed")
        def close(self): log.append("sensors-close")
    monkeypatch.setattr(module, "SensorManager", FailingSensors)
    output = SimulationRunner(adapter).run(scenario)
    assert output.result.simulation.status == "failed"
    assert "sensor attach failed" in output.result.simulation.error
    assert log[-3:] == ["sensors-close", "actors-close", "world-close"]


def test_cleanup_attempts_every_resource_even_after_sensor_error(setup_adapter):
    adapter, scenario, log, _, _, _ = setup_adapter
    adapter.open(scenario)
    def fail():
        log.append("sensors-close")
        raise RuntimeError("sensor destroy failed")
    adapter.sensors.close = fail
    with pytest.raises(SimulationError, match="sensor destroy failed"):
        adapter.close()
    assert log[-3:] == ["sensors-close", "actors-close", "world-close"]


def test_other_client_tick_is_rejected(setup_adapter):
    adapter, scenario, _, world, _, _ = setup_adapter
    adapter.open(scenario)
    def jumped():
        world.frame += 2
        return world.frame - 1
    world.tick = jumped
    with pytest.raises(SimulationError, match="Another client"):
        adapter.step(ControlInput(), {"driver": ActorCommand(0)})
    adapter.close()


def test_missing_route_produces_hold_target(setup_adapter):
    adapter, scenario, _, _, _, _ = setup_adapter
    frame = adapter.open(scenario)
    assert adapter.observation(frame, 10).target_speed_mps == 0
    adapter.close()


def test_spectator_uses_initialized_ego_pose_ignores_foreign_traffic_and_only_moves_once(setup_adapter):
    adapter, scenario, log, world, _, _ = setup_adapter
    # Spawn bookkeeping still contains the origin; only the initialized state
    # has the real location. An unrelated vehicle must not affect framing.
    world.actor_states = [
        ActorState(actor_id="driver", transform=Transform(position=Vector3(x=100, y=200, z=8))),
        ActorState(actor_id="world:77", transform=Transform(position=Vector3(x=10000, y=-9000))),
    ]
    adapter.open(scenario)
    view = next(entry for entry in log if isinstance(entry, tuple) and entry[0] == "spectator")
    assert view[1] == 51
    assert (view[2].location.x, view[2].location.y, view[2].location.z) == (100, 200, 28)
    assert view[2].rotation.pitch == -89
    assert log.index(view) > log.index(("collect", 51))
    adapter.step(ControlInput(), {"driver": ActorCommand(0)})
    adapter.close()
    assert len([entry for entry in log if isinstance(entry, tuple) and entry[0] == "spectator"]) == 1


def test_spectator_rpc_failure_does_not_fail_simulation(setup_adapter, caplog):
    adapter, scenario, log, world, _, _ = setup_adapter
    def unavailable():
        raise RuntimeError("spectator unavailable")
    world.get_spectator = unavailable
    output = SimulationRunner(adapter).run(scenario)
    assert output.result.simulation.status == "completed", output.result.simulation.error
    assert "Could not position the CARLA spectator" in caplog.text
    assert log[-3:] == ["sensors-close", "actors-close", "world-close"]


def test_planned_junction_route_takes_precedence_over_missing_map_projection(setup_adapter):
    adapter, scenario, _, _, _, _ = setup_adapter
    frame = adapter.open(scenario)
    adapter.actor_manager.route_heading = lambda actor_id, pose, lookahead: 25
    observation = adapter.observation(frame, 12)
    assert observation.target_speed_mps == 12
    assert observation.route_heading_deg == 25
    adapter.close()
