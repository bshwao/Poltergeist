"""Simulation lifecycle, timed events, cancellation, evaluation, and local CLI."""

import argparse
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from copy import deepcopy
from datetime import UTC, datetime
from math import ceil, isclose, nextafter
from pathlib import Path
from threading import Event, Lock
from time import perf_counter
import json
import sys

from evaluation import EvaluationSettings, evaluate_safety, validate_frames
from scenarios import load_scenario, load_template, validate_scenario
from shared.models import AgentObservation, AgentReset, AgentSceneContext, ControlInput, Scenario, Simulation, SimulationResult, SimulationRuntime, SimulationStatus, TelemetryFrame

from .agents import BaseAgent, BaselineAgent
from .agents.process import AgentProcessOptions, ProcessAgent
from .agents.sdk import AgentSDKError
from .errors import SimulationCancelled, SimulationError
from .protocols import ActorCommand, SensorSample, SimulationAdapter, SimulationOptions


@dataclass(frozen=True)
class RunOutput:
    result: SimulationResult
    telemetry: tuple[TelemetryFrame, ...]
    setup_wall_time_s: float = 0.0
    execution_wall_time_s: float = 0.0


class SimulationRunner:
    """One run per instance; callbacks receive snapshots and cancellation is cooperative."""

    def __init__(self, adapter: SimulationAdapter | None = None, *, options=None, evaluation_settings=None, connection_settings=None):
        self.adapter = adapter
        self.connection_settings = connection_settings
        self.options = SimulationOptions() if options is None else SimulationOptions.model_validate(options.model_dump())
        self.evaluation_settings = EvaluationSettings() if evaluation_settings is None else EvaluationSettings.model_validate(evaluation_settings.model_dump())
        self._cancel = Event()
        self._lock = Lock()
        self._used = False
        self._simulation = None

    @property
    def simulation(self) -> Simulation | None:
        return None if self._simulation is None else self._simulation.model_copy(deep=True)

    def cancel(self) -> None:
        self._cancel.set()

    def _check_cancelled(self, cancellation) -> None:
        if self._cancel.is_set() or (cancellation is not None and cancellation.is_set()):
            raise SimulationCancelled("Simulation cancelled")

    def run(
        self, scenario: Scenario | Simulation | Mapping, *, mode: str | None = None,
        agent: BaseAgent | ProcessAgent | None = None, cancellation: Event | None = None,
        on_progress: Callable | None = None, on_frame: Callable | None = None,
        on_sensor: Callable[[SensorSample], None] | None = None,
        agent_control_mode: str = "scripted", on_decision: Callable | None = None,
    ) -> RunOutput:
        if not self._lock.acquire(blocking=False):
            raise SimulationError("This runner is already executing")
        try:
            if self._used:
                raise SimulationError("Create a new SimulationRunner for each run")
            self._used = True
            return self._run(scenario, mode, agent, cancellation, on_progress, on_frame, on_sensor, agent_control_mode, on_decision)
        finally:
            self._lock.release()

    def _run(self, source, mode, agent, cancellation, on_progress, on_frame, on_sensor, agent_control_mode, on_decision):
        agent_kind = "baseline" if agent is None or isinstance(agent, BaselineAgent) else "custom"
        if isinstance(source, Simulation):
            simulation = Simulation.model_validate(source.model_dump())
            if simulation.status != SimulationStatus.QUEUED:
                raise SimulationError("Only queued simulations can be run")
            if mode is not None and mode != simulation.mode:
                raise SimulationError("Execution mode must match the queued simulation")
            if agent is not None and simulation.agent != agent_kind:
                raise SimulationError("Injected agent must match the queued simulation agent")
            validated = validate_scenario(simulation.scenario)
            simulation = Simulation.model_validate(simulation.model_dump() | {"scenario": validated})
        else:
            validated = validate_scenario(source)
            simulation = Simulation(scenario=validated, mode=mode or (self.adapter.mode if self.adapter else "carla"), agent=agent_kind)
        if self.adapter is not None and self.adapter.mode != simulation.mode:
            raise SimulationError("Adapter mode must match the simulation record")
        if simulation.agent == "custom" and agent is None:
            raise SimulationError("A custom simulation requires an injected driving agent")
        if agent is not None and not isinstance(agent, (BaseAgent, ProcessAgent)):
            raise SimulationError("Driving agent must implement BaseAgent or use ProcessAgent")
        driver = BaselineAgent() if agent is None else agent
        if agent_control_mode not in ("scripted", "authoritative"):
            raise SimulationError("agent_control_mode must be scripted or authoritative")
        if agent_control_mode == "authoritative":
            if not isinstance(driver, ProcessAgent):
                raise SimulationError("Authoritative control mode requires an external SDK agent")
            ego_id = next(actor.id for actor in validated.actors if actor.type == "ego_vehicle")
            if any(event.actor_id == ego_id for event in validated.events):
                raise SimulationError("Authoritative agent mode rejects scripted ego-control events")
        ticks = ceil(validated.duration_s / validated.fixed_delta_seconds - 1e-9)
        if ticks > self.options.max_ticks:
            raise SimulationError(f"Scenario requires {ticks} ticks; max_ticks={self.options.max_ticks}")
        self._simulation = simulation
        trace = []
        runtime = None
        sensor_count = 0
        phase = "initialization"
        error_message = None
        status = SimulationStatus.COMPLETED
        adapter = self.adapter
        open_attempted = False
        setup_started = perf_counter()
        execution_started = None
        setup_wall_time_s = execution_wall_time_s = 0.0
        initial_scene = None
        needs_scene = isinstance(driver, ProcessAgent) and "structured_scene" in driver.manifest.required_features
        if adapter is None:
            if simulation.mode == "test":
                from .test_adapter import TestSimulationAdapter
                adapter = TestSimulationAdapter(options=self.options)
            else:
                from .carla_adapter import CarlaSimulationAdapter
                adapter = CarlaSimulationAdapter(options=self.options, evaluation_settings=self.evaluation_settings, connection_settings=self.connection_settings)
        self.adapter = adapter

        def update(**changes):
            self._simulation = Simulation.model_validate(self._simulation.model_dump() | changes)

        def progress():
            if on_progress is not None:
                on_progress(self.simulation)

        def capture():
            nonlocal sensor_count
            for sample in adapter.sensor_samples():
                sensor_count += 1
                if on_sensor is not None:
                    on_sensor(sample)

        def accept(raw, previous=None):
            frame = validate_frames([raw])[0]
            ego_id = next(actor.id for actor in validated.actors if actor.type == "ego_vehicle")
            if frame.ego.actor_id != ego_id:
                raise SimulationError("Adapter returned an unexpected ego actor ID")
            if previous is None:
                if not isclose(frame.time_s, 0, abs_tol=1e-6):
                    raise SimulationError("Initial telemetry must begin at elapsed time zero")
            elif frame.frame != previous.frame + 1 or not isclose(
                frame.time_s - previous.time_s, validated.fixed_delta_seconds, rel_tol=1e-5, abs_tol=1e-6,
            ):
                raise SimulationError("Adapter skipped a frame or changed the fixed time step")
            trace.append(frame)
            if on_frame is not None:
                on_frame(frame.model_copy(deep=True))
            capture()
            return frame

        try:
            progress()
            self._check_cancelled(cancellation)
            if isinstance(driver, ProcessAgent):
                phase = "agent load"
                driver.set_cancellation(lambda: self._cancel.is_set() or (cancellation is not None and cancellation.is_set()))
                driver.load(driver.manifest.configuration)
                self._check_cancelled(cancellation)
            phase = "initialization"
            open_attempted = True
            current = accept(adapter.open(validated.model_copy(deep=True)))
            if isinstance(driver, ProcessAgent):
                phase = "agent reset"
                if needs_scene:
                    provider = getattr(adapter, "agent_context", None)
                    if not callable(provider):
                        raise SimulationError("Adapter cannot provide required structured scene context")
                    raw_scene = provider(current.model_copy(deep=True))
                    initial_scene = AgentSceneContext.model_validate(
                        raw_scene.model_dump() if isinstance(raw_scene, AgentSceneContext) else raw_scene,
                    )
                driver.reset(AgentReset(
                    protocol_version=driver.manifest.protocol_version,
                    seed=validated.seed, map_name=validated.map,
                    ego_actor_id=current.ego.actor_id,
                    fixed_delta_seconds=validated.fixed_delta_seconds,
                    route=[] if initial_scene is None or initial_scene.route is None else initial_scene.route,
                    destination=None if initial_scene is None or not initial_scene.route else initial_scene.route[-1],
                ))
            update(status=SimulationStatus.RUNNING, started_at=datetime.now(UTC))
            progress()
            setup_wall_time_s = perf_counter() - setup_started
            execution_started = perf_counter()
            commands = {actor.id: ActorCommand(actor.speed_mps) for actor in validated.actors}
            events = sorted(enumerate(validated.events), key=lambda pair: (pair[1].trigger_time_s, pair[0]))
            next_event = 0
            for _ in range(ticks):
                step_started = perf_counter()
                phase = "execution"
                self._check_cancelled(cancellation)
                while next_event < len(events) and events[next_event][1].trigger_time_s <= current.time_s + 1e-9:
                    event = events[next_event][1]
                    if event.action == "set_speed":
                        commands[event.actor_id] = ActorCommand(event.speed_mps)
                    else:
                        commands[event.actor_id] = ActorCommand(0.0, event.deceleration_mps2)
                    next_event += 1
                ego_command = commands[current.ego.actor_id]
                observation = deepcopy(adapter.observation(current.model_copy(deep=True), ego_command.target_speed_mps))
                scene = None
                if needs_scene:
                    if initial_scene is not None:
                        scene, initial_scene = initial_scene, None
                    else:
                        raw_scene = adapter.agent_context(current.model_copy(deep=True))
                        scene = AgentSceneContext.model_validate(
                            raw_scene.model_dump() if isinstance(raw_scene, AgentSceneContext) else raw_scene,
                        )
                sdk_observation = None
                decision = None
                if isinstance(driver, ProcessAgent):
                    # Preserve unavailable actor data (None) from telemetry;
                    # legacy DrivingObservation represents it as an empty tuple.
                    sdk_observation = AgentObservation(
                        protocol_version=driver.manifest.protocol_version,
                        frame=current.frame, time_s=current.time_s,
                        ego=observation.ego, actors=current.actors,
                        dt_s=observation.dt_s,
                        target_speed_mps=observation.target_speed_mps,
                        route_heading_deg=observation.route_heading_deg,
                        traffic_light_red=observation.traffic_light_red,
                        scene=scene,
                    )
                    decision = driver.step(sdk_observation)
                    control = decision.control
                    self._check_cancelled(cancellation)
                else:
                    control = driver.run_step(observation)
                control = ControlInput.model_validate(control.model_dump() if isinstance(control, ControlInput) else control)
                proposed_control = control.model_copy(deep=True)
                interventions = []
                if ego_command.deceleration_mps2 is not None:
                    interventions.append("scripted_ego_braking")
                    control = ControlInput.model_validate(control.model_dump() | {
                        "throttle": 0.0, "brake": min(1.0, ego_command.deceleration_mps2 / 8.0),
                    })
                previous = current
                current = accept(adapter.step(control, commands), current)
                update(progress=min(nextafter(1.0, 0.0), current.time_s / validated.duration_s))
                progress()
                full_step_wall_time_s = perf_counter() - step_started
                if on_decision is not None:
                    if sdk_observation is None:
                        sdk_observation = AgentObservation(
                            frame=previous.frame, time_s=previous.time_s, dt_s=observation.dt_s,
                            ego=observation.ego, actors=previous.actors,
                            target_speed_mps=observation.target_speed_mps,
                            route_heading_deg=observation.route_heading_deg,
                            traffic_light_red=observation.traffic_light_red,
                        )
                    timing = driver.last_timing if isinstance(driver, ProcessAgent) else None
                    on_decision({
                        "frame": previous.frame, "time_s": previous.time_s,
                        "observation": sdk_observation.model_dump(mode="json"),
                        "agent_result": None if decision is None else decision.model_dump(mode="json"),
                        "proposed_control": proposed_control.model_dump(mode="json"),
                        "applied_control": control.model_dump(mode="json"),
                        "interventions": interventions,
                        "worker_step_wall_time_s": None if timing is None else timing.worker_step_wall_time_s,
                        "round_trip_wall_time_s": None if timing is None else timing.round_trip_wall_time_s,
                        "full_step_wall_time_s": full_step_wall_time_s,
                        "resulting_frame": current.frame, "resulting_ego": current.ego.model_dump(mode="json"),
                    })
            self._check_cancelled(cancellation)
        except (SimulationCancelled, KeyboardInterrupt):
            status = SimulationStatus.CANCELLED
        except Exception as error:
            status = SimulationStatus.FAILED
            error_message = f"{phase}: {type(error).__name__}: {error}"
        finally:
            if execution_started is not None:
                execution_wall_time_s = perf_counter() - execution_started
            else:
                setup_wall_time_s = perf_counter() - setup_started
            if isinstance(driver, ProcessAgent):
                try:
                    driver.close()
                except KeyboardInterrupt:
                    if status != SimulationStatus.FAILED:
                        status = SimulationStatus.CANCELLED
                    try:
                        driver.close()
                    except (Exception, KeyboardInterrupt) as error:
                        status = SimulationStatus.FAILED
                        error_message = (error_message + "; " if error_message else "") + f"agent cleanup interrupted: {error}"
                except Exception as error:
                    status = SimulationStatus.FAILED
                    error_message = (error_message + "; " if error_message else "") + f"agent cleanup: {error}"
            if trace:
                try:
                    timeline = [(frame.frame, frame.time_s, frame.ego.actor_id) for frame in trace]
                    finalized = validate_frames(adapter.finalize([frame.model_copy(deep=True) for frame in trace]))
                    if [(frame.frame, frame.time_s, frame.ego.actor_id) for frame in finalized] != timeline:
                        raise SimulationError("Sensor finalization changed the recorded timeline")
                    trace = finalized
                    capture()
                except (Exception, KeyboardInterrupt) as error:
                    if isinstance(error, KeyboardInterrupt):
                        if status != SimulationStatus.FAILED:
                            status = SimulationStatus.CANCELLED
                    else:
                        status = SimulationStatus.FAILED
                        error_message = (error_message + "; " if error_message else "") + f"sensor finalization: {error}"
            try:
                info = adapter.runtime_info() if open_attempted else {}
                if info:
                    candidate = SimulationRuntime.model_validate(info | {"sensor_samples_count": sensor_count})
                    if candidate.adapter != simulation.mode:
                        raise SimulationError("Runtime metadata adapter does not match the simulation mode")
                    runtime = candidate
            except (Exception, KeyboardInterrupt) as error:
                if isinstance(error, KeyboardInterrupt):
                    if status != SimulationStatus.FAILED:
                        status = SimulationStatus.CANCELLED
                else:
                    status = SimulationStatus.FAILED
                    error_message = (error_message + "; " if error_message else "") + f"runtime metadata: {error}"
            try:
                adapter.close()
            except KeyboardInterrupt:
                if status != SimulationStatus.FAILED:
                    status = SimulationStatus.CANCELLED
                try:
                    adapter.close()
                except (Exception, KeyboardInterrupt) as error:
                    status = SimulationStatus.FAILED
                    error_message = (error_message + "; " if error_message else "") + f"cleanup interrupted: {error}"
            except Exception as error:
                status = SimulationStatus.FAILED
                error_message = (error_message + "; " if error_message else "") + f"cleanup: {error}"
        try:
            metrics = evaluate_safety(trace, settings=self.evaluation_settings)
        except Exception as error:
            status = SimulationStatus.FAILED
            error_message = (error_message + "; " if error_message else "") + f"evaluation: {error}"
            metrics = evaluate_safety([], settings=self.evaluation_settings)
        if status != SimulationStatus.COMPLETED and not metrics.collision_count:
            metrics.severity = None
        update(
            status=status, finished_at=datetime.now(UTC),
            progress=1.0 if status == SimulationStatus.COMPLETED else self._simulation.progress,
            error=error_message if status == SimulationStatus.FAILED else None,
        )
        try:
            progress()
        except KeyboardInterrupt:
            if self._simulation.status != SimulationStatus.FAILED:
                update(status=SimulationStatus.CANCELLED)
            if not metrics.collision_count:
                metrics.severity = None
        except Exception as error:
            earlier = self._simulation.error
            update(status=SimulationStatus.FAILED, error=(earlier + "; " if earlier else "") + f"terminal progress callback: {error}")
            if not metrics.collision_count:
                metrics.severity = None
        result = SimulationResult(
            simulation=self.simulation, metrics=metrics, telemetry_frames=len(trace),
            measured_duration_s=0.0 if not trace else trace[-1].time_s - trace[0].time_s,
            runtime=runtime,
        )
        return RunOutput(result, tuple(trace), setup_wall_time_s, execution_wall_time_s)


def run_simulation(scenario, *, mode=None, options=None, evaluation_settings=None, **kwargs) -> RunOutput:
    return SimulationRunner(options=options, evaluation_settings=evaluation_settings).run(scenario, mode=mode, **kwargs)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run a Poltergeist scenario locally")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--template", choices=["intersection", "pedestrian_crossing", "occluded_pedestrian", "sudden_braking"])
    source.add_argument("--scenario", type=Path)
    parser.add_argument("--mode", choices=["carla", "test"], default="carla")
    parser.add_argument("--allow-map-reload", action="store_true")
    parser.add_argument("--camera", action="store_true")
    parser.add_argument("--lidar", action="store_true")
    parser.add_argument("--no-rendering", action="store_true", help="Disable world rendering during this run and restore afterward")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--agent-package", type=Path, help="Trusted local agent package directory containing agent.json")
    parser.add_argument("--agent-python", type=Path, help="Python executable in the agent's dependency environment")
    parser.add_argument("--agent-load-timeout", type=float, help="Agent load deadline in wall-clock seconds (default: 30)")
    parser.add_argument("--agent-step-timeout", type=float, help="Per-step agent deadline in wall-clock seconds (default: 2)")
    parser.add_argument("--agent-close-timeout", type=float, help="Agent cleanup deadline in wall-clock seconds (default: 2)")
    parser.add_argument("--agent-control-mode", choices=["scripted", "authoritative"], default="scripted")
    args = parser.parse_args(argv)
    dependent = (args.agent_python, args.agent_load_timeout, args.agent_step_timeout, args.agent_close_timeout)
    if args.agent_package is None and any(value is not None for value in dependent):
        parser.error("--agent-python and --agent-*-timeout require --agent-package")
    if args.agent_control_mode == "authoritative" and args.agent_package is None:
        parser.error("--agent-control-mode authoritative requires --agent-package")
    agent = None
    try:
        if args.agent_package is not None:
            timeouts = {
                name: value for name, value in (
                    ("load_timeout_s", args.agent_load_timeout),
                    ("step_timeout_s", args.agent_step_timeout),
                    ("close_timeout_s", args.agent_close_timeout),
                ) if value is not None
            }
            agent = ProcessAgent(args.agent_package, python_executable=args.agent_python, options=AgentProcessOptions(**timeouts))
        scenario = load_template(args.template) if args.template else load_scenario(args.scenario)
        output = run_simulation(scenario, mode=args.mode, options=SimulationOptions(
            allow_map_reload=args.allow_map_reload, camera=args.camera, lidar=args.lidar,
            no_rendering_mode=True if args.no_rendering else None,
        ), agent=agent, agent_control_mode=args.agent_control_mode)
        text = output.result.model_dump_json(indent=2) + "\n"
        if args.output is not None:
            args.output.write_text(text, encoding="utf-8")
        else:
            print(text, end="")
        return 0 if output.result.simulation.status == "completed" else (130 if output.result.simulation.status == "cancelled" else 1)
    except (ValueError, OSError, SimulationError, AgentSDKError) as error:
        parser.exit(1, f"Simulation failed: {error}\n")
    finally:
        if agent is not None:
            print(json.dumps({"agent_process": asdict(agent.stats)}, allow_nan=False), file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
