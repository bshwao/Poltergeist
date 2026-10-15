"""Simulation lifecycle, timed events, cancellation, evaluation, and local CLI."""

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from copy import deepcopy
from datetime import UTC, datetime
from math import ceil, isclose, nextafter
from pathlib import Path
from threading import Event, Lock

from evaluation import EvaluationSettings, evaluate_safety, validate_frames
from scenarios import load_scenario, load_template, validate_scenario
from shared.models import ControlInput, Scenario, Simulation, SimulationResult, SimulationRuntime, SimulationStatus, TelemetryFrame

from .agents import BaseAgent, BaselineAgent
from .errors import SimulationCancelled, SimulationError
from .protocols import ActorCommand, SensorSample, SimulationAdapter, SimulationOptions


@dataclass(frozen=True)
class RunOutput:
    result: SimulationResult
    telemetry: tuple[TelemetryFrame, ...]


class SimulationRunner:
    """One run per instance; callbacks receive snapshots and cancellation is cooperative."""

    def __init__(self, adapter: SimulationAdapter | None = None, *, options=None, evaluation_settings=None):
        self.adapter = adapter
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
        agent: BaseAgent | None = None, cancellation: Event | None = None,
        on_progress: Callable | None = None, on_frame: Callable | None = None,
        on_sensor: Callable[[SensorSample], None] | None = None,
    ) -> RunOutput:
        if not self._lock.acquire(blocking=False):
            raise SimulationError("This runner is already executing")
        try:
            if self._used:
                raise SimulationError("Create a new SimulationRunner for each run")
            self._used = True
            return self._run(scenario, mode, agent, cancellation, on_progress, on_frame, on_sensor)
        finally:
            self._lock.release()

    def _run(self, source, mode, agent, cancellation, on_progress, on_frame, on_sensor):
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
        if agent is not None and not isinstance(agent, BaseAgent):
            raise SimulationError("Driving agent must implement BaseAgent")
        driver = BaselineAgent() if agent is None else agent
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
        if adapter is None:
            if simulation.mode == "test":
                from .test_adapter import TestSimulationAdapter
                adapter = TestSimulationAdapter(options=self.options)
            else:
                from .carla_adapter import CarlaSimulationAdapter
                adapter = CarlaSimulationAdapter(options=self.options, evaluation_settings=self.evaluation_settings)
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
            open_attempted = True
            current = accept(adapter.open(validated.model_copy(deep=True)))
            update(status=SimulationStatus.RUNNING, started_at=datetime.now(UTC))
            progress()
            commands = {actor.id: ActorCommand(actor.speed_mps) for actor in validated.actors}
            events = sorted(enumerate(validated.events), key=lambda pair: (pair[1].trigger_time_s, pair[0]))
            next_event = 0
            for _ in range(ticks):
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
                control = driver.run_step(deepcopy(adapter.observation(current.model_copy(deep=True), ego_command.target_speed_mps)))
                control = ControlInput.model_validate(control.model_dump() if isinstance(control, ControlInput) else control)
                if ego_command.deceleration_mps2 is not None:
                    control = ControlInput.model_validate(control.model_dump() | {
                        "throttle": 0.0, "brake": min(1.0, ego_command.deceleration_mps2 / 8.0),
                    })
                current = accept(adapter.step(control, commands), current)
                update(progress=min(nextafter(1.0, 0.0), current.time_s / validated.duration_s))
                progress()
            self._check_cancelled(cancellation)
        except (SimulationCancelled, KeyboardInterrupt):
            status = SimulationStatus.CANCELLED
        except Exception as error:
            status = SimulationStatus.FAILED
            error_message = f"{phase}: {type(error).__name__}: {error}"
        finally:
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
        return RunOutput(result, tuple(trace))


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
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        scenario = load_template(args.template) if args.template else load_scenario(args.scenario)
        output = run_simulation(scenario, mode=args.mode, options=SimulationOptions(
            allow_map_reload=args.allow_map_reload, camera=args.camera, lidar=args.lidar,
        ))
        text = output.result.model_dump_json(indent=2) + "\n"
        if args.output is not None:
            args.output.write_text(text, encoding="utf-8")
        else:
            print(text, end="")
        return 0 if output.result.simulation.status == "completed" else (130 if output.result.simulation.status == "cancelled" else 1)
    except (ValueError, OSError, SimulationError) as error:
        parser.exit(1, f"Simulation failed: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
