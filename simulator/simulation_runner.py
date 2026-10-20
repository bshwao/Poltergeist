"""Simulation lifecycle, timed events, cancellation, evaluation, and local CLI."""

import argparse
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import asdict, dataclass, replace
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
from shared.models import ActorState, AgentInputInfo, AgentObservation, AgentReset, AgentSceneContext, ControlAudit, ControlInput, ControlIntervention, DrivingTask, Scenario, Simulation, SimulationResult, SimulationRuntime, SimulationStatus, TelemetryFrame

from .agents import BaseAgent, BaselineAgent
from .agents.process import AgentProcessOptions, ProcessAgent
from .agents.sdk import AgentSDKError
from .errors import SimulationCancelled, SimulationError
from .protocols import ActorCommand, DrivingObservation, SensorSample, SimulationAdapter, SimulationOptions
from .navigation import TaskMonitor
from .timing import high_resolution_timer


@dataclass(frozen=True)
class RunOutput:
    result: SimulationResult
    telemetry: tuple[TelemetryFrame, ...]
    setup_wall_time_s: float = 0.0
    execution_wall_time_s: float = 0.0
    timing_environment: dict | None = None
    step_wall_times_s: tuple[float, ...] = ()


class SimulationRunner:
    """One run per instance; callbacks receive snapshots and cancellation is cooperative."""

    def __init__(self, adapter: SimulationAdapter | None = None, *, options=None, evaluation_settings=None, connection_settings=None, carla_launcher=None):
        self.adapter = adapter
        self.connection_settings = connection_settings
        self.carla_launcher = carla_launcher
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
        agent_control_mode: str | None = None, on_decision: Callable | None = None,
        on_observation: Callable | None = None, on_agent_reset: Callable | None = None,
    ) -> RunOutput:
        if not self._lock.acquire(blocking=False):
            raise SimulationError("This runner is already executing")
        try:
            if self._used:
                raise SimulationError("Create a new SimulationRunner for each run")
            self._used = True
            with high_resolution_timer() as timing_environment:
                output = self._run(scenario, mode, agent, cancellation, on_progress, on_frame, on_sensor, agent_control_mode, on_decision, on_observation, on_agent_reset)
            return replace(output, timing_environment=dict(timing_environment))
        finally:
            self._lock.release()

    def _run(self, source, mode, agent, cancellation, on_progress, on_frame, on_sensor, agent_control_mode, on_decision, on_observation, on_agent_reset):
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
        if simulation.agent_snapshot is not None:
            registered = simulation.agent_snapshot
            canonical = lambda value: json.dumps(value.model_dump(mode="json"), sort_keys=True, allow_nan=False)
            if (not isinstance(driver, ProcessAgent) or driver.snapshot is None
                    or canonical(driver.snapshot) != canonical(registered)
                    or canonical(driver.manifest) != canonical(registered.manifest)):
                raise SimulationError("Injected agent must match the immutable registered agent snapshot")
            if agent_control_mode in (None, "auto"):
                agent_control_mode = registered.agent_control_mode
            elif agent_control_mode != registered.agent_control_mode:
                raise SimulationError("Agent control mode must match the immutable registered agent snapshot")
        task = validated.driving_task
        sdk_protocol = driver.manifest.protocol_version if isinstance(driver, ProcessAgent) else None
        sensor_agent = isinstance(driver, ProcessAgent) and driver.manifest.observation_mode == "sensors"
        agent_specs = driver.manifest.sensors if sensor_agent else []
        if agent_control_mode in (None, "auto"):
            agent_control_mode = ("authoritative" if isinstance(driver, ProcessAgent)
                                  and (task is not None or sdk_protocol in {"1.2", "1.3"}) else "scripted")
        if agent_control_mode not in ("scripted", "authoritative"):
            raise SimulationError("agent_control_mode must be scripted or authoritative")
        if sensor_agent and (task is None or agent_control_mode != "authoritative"):
            raise SimulationError("Sensor agents require an explicit driving task and authoritative control mode")
        if task is not None:
            if isinstance(driver, ProcessAgent):
                if sdk_protocol not in {"1.2", "1.3"}:
                    raise SimulationError("Explicit driving tasks require an SDK 1.2 or 1.3 agent")
                if task.route_owner == "agent" and driver.manifest.navigation_mode != "agent_planner":
                    raise SimulationError("Agent-owned routes require an agent_planner manifest")
            elif task.route_owner == "agent" or task.target_speed_mps is None:
                raise SimulationError("Legacy/baseline task controllers require a framework route and explicit target_speed_mps")
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
        step_wall_times_s = []
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
        task_monitor = None
        task_descriptor = None
        prepared_reset = None
        current_sensor_frames = []
        resolved_route = []
        intervention_records = []
        ego_speed_script_active = False
        controller_kind = ("sdk_agent" if isinstance(driver, ProcessAgent) else
                           "baseline" if isinstance(driver, BaselineAgent) else "legacy_custom")
        needs_scene = isinstance(driver, ProcessAgent) and "structured_scene" in driver.manifest.required_features
        if adapter is None:
            if simulation.mode == "test":
                from .test_adapter import TestSimulationAdapter
                adapter = TestSimulationAdapter(options=self.options)
            else:
                from .carla_adapter import CarlaSimulationAdapter
                adapter = CarlaSimulationAdapter(options=self.options, evaluation_settings=self.evaluation_settings, connection_settings=self.connection_settings, launcher=self.carla_launcher)
        self.adapter = adapter

        def update(**changes):
            self._simulation = Simulation.model_validate(self._simulation.model_dump() | changes)

        def progress():
            if on_progress is not None:
                on_progress(self.simulation)

        def capture(frame=None):
            nonlocal sensor_count, current_sensor_frames
            for sample in adapter.sensor_samples():
                sensor_count += 1
                if on_sensor is not None:
                    on_sensor(sample)
            if sensor_agent and frame is not None:
                from .agent_sensors import sensor_frames
                samples = tuple(adapter.agent_sensor_samples(frame.model_copy(deep=True)))
                current_sensor_frames = sensor_frames(agent_specs, samples, frame,
                    time_origin_s=adapter.sensor_time_origin_s(),
                    expected_source="carla_sensors" if simulation.mode == "carla" else "deterministic_fixture")
                for sample in samples:
                    sensor_count += 1
                    if on_sensor is not None:
                        on_sensor(sample)

        def accept(raw, previous=None, applied_interventions=()):
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
            # An intervention is applied evidence only after a valid measured
            # transition, including when a subsequent recording callback fails.
            intervention_records.extend(applied_interventions)
            if on_frame is not None:
                on_frame(frame.model_copy(deep=True))
            capture(frame)
            return frame

        try:
            progress()
            self._check_cancelled(cancellation)
            setter = getattr(adapter, "set_cancellation", None)
            if callable(setter):
                setter(lambda: self._cancel.is_set() or (cancellation is not None and cancellation.is_set()))
            if sensor_agent:
                configure = getattr(adapter, "configure_agent_sensors", None)
                if not callable(configure) or not callable(getattr(adapter, "agent_sensor_samples", None)) or not callable(
                        getattr(adapter, "sensor_time_origin_s", None)):
                    raise SimulationError("Adapter cannot deliver declared agent sensor inputs")
                configure(agent_specs)
            if isinstance(driver, ProcessAgent):
                phase = "agent load"
                driver.set_cancellation(lambda: self._cancel.is_set() or (cancellation is not None and cancellation.is_set()))
                driver.load(driver.manifest.configuration)
                self._check_cancelled(cancellation)
            phase = "initialization"
            open_attempted = True
            current = accept(adapter.open(validated.model_copy(deep=True)))
            if task is not None:
                # Keep a failure outcome even if planning fails after spawning.
                task_monitor = TaskMonitor(task, [], current, agent_control_mode)
                planner = getattr(adapter, "prepare_driving_task", None)
                if not callable(planner):
                    raise SimulationError("Adapter cannot prepare explicit driving tasks")
                raw_route = planner(task.model_copy(deep=True), current.model_copy(deep=True))
                # The full immutable route is sent once in reset.route. Each
                # observation carries the stable goal without duplicating it.
                task_descriptor = DrivingTask.model_validate(task.model_dump() | {"route": []})
                # Validate adapter output even when a baseline controller is
                # used. A malformed route must fail inside initialization.
                prepared_reset = AgentReset(
                    protocol_version=sdk_protocol or "1.2", seed=validated.seed, map_name=validated.map,
                    ego_actor_id=current.ego.actor_id, fixed_delta_seconds=validated.fixed_delta_seconds,
                    route=raw_route, destination=task.destination, driving_task=task_descriptor, sensors=agent_specs,
                )
                resolved_route = prepared_reset.route
                task_monitor = TaskMonitor(task, resolved_route, current, agent_control_mode)
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
                reset_context = prepared_reset if prepared_reset is not None else AgentReset(
                    protocol_version=sdk_protocol,
                    seed=validated.seed, map_name=validated.map,
                    ego_actor_id=current.ego.actor_id,
                    fixed_delta_seconds=validated.fixed_delta_seconds,
                    route=(resolved_route if task is not None else
                           [] if initial_scene is None or initial_scene.route is None else initial_scene.route),
                    destination=None if task is None else task.destination,
                    driving_task=task_descriptor,
                )
                driver.reset(reset_context)
                if on_agent_reset is not None:
                    on_agent_reset(reset_context.model_copy(deep=True))
            update(status=SimulationStatus.RUNNING, started_at=datetime.now(UTC))
            progress()
            setup_wall_time_s = perf_counter() - setup_started
            execution_started = perf_counter()
            commands = {actor.id: ActorCommand(actor.speed_mps) for actor in validated.actors}
            events = sorted(enumerate(validated.events), key=lambda pair: (pair[1].trigger_time_s, pair[0]))
            next_event = 0
            for _ in range(ticks):
                if task_monitor is not None and task.stop_on_completion and task_monitor.reached:
                    break
                step_started = perf_counter()
                phase = "execution"
                self._check_cancelled(cancellation)
                while next_event < len(events) and events[next_event][1].trigger_time_s <= current.time_s + 1e-9:
                    event = events[next_event][1]
                    if event.action == "set_speed":
                        commands[event.actor_id] = ActorCommand(event.speed_mps)
                        if event.actor_id == current.ego.actor_id:
                            ego_speed_script_active = True
                    else:
                        commands[event.actor_id] = ActorCommand(0.0, event.deceleration_mps2)
                        if event.actor_id == current.ego.actor_id:
                            ego_speed_script_active = False
                    next_event += 1
                ego_command = commands[current.ego.actor_id]
                if task_monitor is None:
                    observation = deepcopy(adapter.observation(current.model_copy(deep=True), ego_command.target_speed_mps))
                else:
                    target = (ego_command.target_speed_mps if ego_speed_script_active else task.target_speed_mps)
                    signal_provider = getattr(adapter, "traffic_light_observation", None)
                    observation = DrivingObservation(
                        ego=(ActorState(actor_id=current.ego.actor_id, transform=current.ego.transform,
                             velocity_mps=current.ego.velocity_mps, acceleration_mps2=current.ego.acceleration_mps2)
                             if sensor_agent else current.ego.model_copy(deep=True)),
                        actors=() if sensor_agent else tuple(item.model_copy(deep=True) for item in current.actors or []),
                        dt_s=validated.fixed_delta_seconds, target_speed_mps=target,
                        route_heading_deg=(task_monitor.guidance_heading(current)
                                           if task.route_owner == "framework" else None),
                        traffic_light_red=signal_provider() if not sensor_agent and callable(signal_provider) else None,
                    )
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
                        protocol_version=sdk_protocol,
                        frame=current.frame, time_s=current.time_s,
                        ego=observation.ego, actors=None if sensor_agent else current.actors,
                        dt_s=observation.dt_s,
                        target_speed_mps=observation.target_speed_mps,
                        route_heading_deg=observation.route_heading_deg,
                        traffic_light_red=observation.traffic_light_red,
                        scene=scene,
                        driving_task=task_descriptor,
                        observation_mode="sensors" if sensor_agent else "simulator_state",
                        sensors=current_sensor_frames,
                        ego_state_source="simulated_ego_kinematics" if sensor_agent else "simulator_state",
                        sensor_source=("carla_sensors" if simulation.mode == "carla" else "deterministic_fixture") if sensor_agent else None,
                    )
                    if on_observation is not None:
                        on_observation(sdk_observation.model_copy(deep=True), agent_control_mode)
                    decision = driver.step(sdk_observation)
                    control = decision.control
                    self._check_cancelled(cancellation)
                else:
                    if on_observation is not None:
                        sdk_observation = AgentObservation(
                            protocol_version="1.2" if task is not None else "1.0",
                            frame=current.frame, time_s=current.time_s, dt_s=observation.dt_s,
                            ego=observation.ego, actors=current.actors,
                            target_speed_mps=observation.target_speed_mps,
                            route_heading_deg=observation.route_heading_deg,
                            traffic_light_red=observation.traffic_light_red,
                            driving_task=task_descriptor,
                        )
                        on_observation(sdk_observation.model_copy(deep=True), agent_control_mode)
                    control = driver.run_step(observation)
                control = ControlInput.model_validate(control.model_dump() if isinstance(control, ControlInput) else control)
                proposed_control = control.model_copy(deep=True)
                interventions = []
                if ego_speed_script_active:
                    interventions.append("scripted_ego_speed_target")
                if ego_command.deceleration_mps2 is not None:
                    interventions.append("scripted_ego_braking")
                    control = ControlInput.model_validate(control.model_dump() | {
                        "throttle": 0.0, "brake": min(1.0, ego_command.deceleration_mps2 / 8.0),
                    })
                applied_interventions = []
                for intervention in interventions:
                    applied_interventions.append(ControlIntervention(
                        frame=current.frame, time_s=current.time_s, kind=intervention,
                        proposed_control=proposed_control, applied_control=control,
                        target_speed_mps=ego_command.target_speed_mps,
                    ))
                previous = current
                current = accept(adapter.step(control, commands), current, applied_interventions)
                if task_monitor is not None:
                    task_monitor.update(current)
                # The internal progress is bounded arithmetic over validated
                # telemetry; the unchanged recipe need not be rebuilt per tick.
                self._simulation = self._simulation.model_copy(update={
                    "progress": min(nextafter(1.0, 0.0), current.time_s / validated.duration_s),
                })
                progress()
                full_step_wall_time_s = perf_counter() - step_started
                if on_decision is not None:
                    if sdk_observation is None:
                        sdk_observation = AgentObservation(
                            protocol_version="1.2" if task is not None else "1.0",
                            frame=previous.frame, time_s=previous.time_s, dt_s=observation.dt_s,
                            ego=observation.ego, actors=previous.actors,
                            target_speed_mps=observation.target_speed_mps,
                            route_heading_deg=observation.route_heading_deg,
                            traffic_light_red=observation.traffic_light_red,
                            driving_task=task_descriptor,
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
                        "control_mode": agent_control_mode,
                    })
                # Include serialization, validation, writing/flushing evidence
                # and any user callback in the actual control-loop cadence.
                step_wall_times_s.append(perf_counter() - step_started)
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
        final_status = self._simulation.status
        driving_outcome = None
        if task is not None and trace:
            # Finalized telemetry is the persisted evidence. Replaying also
            # covers failures in callbacks after accept appended a valid frame.
            task_monitor = TaskMonitor(task, resolved_route, trace[0], agent_control_mode)
            for frame in trace[1:]:
                task_monitor.update(frame)
        if task_monitor is not None:
            reason = ("cancelled" if final_status == SimulationStatus.CANCELLED else
                      "execution_failed" if final_status == SimulationStatus.FAILED else
                      "goal_reached" if task.stop_on_completion and task_monitor.reached else "duration_elapsed")
            driving_outcome = task_monitor.outcome(final_status.value, intervention_records, reason)
        result = SimulationResult(
            simulation=self.simulation, metrics=metrics, telemetry_frames=len(trace),
            measured_duration_s=0.0 if not trace else trace[-1].time_s - trace[0].time_s,
            runtime=runtime,
            driving_outcome=driving_outcome,
            agent_inputs=AgentInputInfo(
                observation_mode="sensors" if sensor_agent else "simulator_state",
                source=("carla_sensors" if simulation.mode == "carla" else "deterministic_fixture") if sensor_agent else (
                    "carla_ground_truth" if simulation.mode == "carla" else "synthetic_state"),
                ego_state_source="simulated_ego_kinematics" if sensor_agent else "simulator_state",
                sensors=agent_specs, ground_truth_actor_inputs=not sensor_agent,
                assistance=(["ideal_ego_kinematics"] if sensor_agent else []) + (
                    ["framework_navigation"] if task is not None and task.route_owner == "framework" else []),
            ),
            control_audit=ControlAudit(
                mode=agent_control_mode, controller=controller_kind,
                navigation=("legacy_assisted" if task is None else
                            "framework_route" if task.route_owner == "framework" else "agent_planner"),
                interventions=intervention_records,
            ),
        )
        return RunOutput(result, tuple(trace), setup_wall_time_s, execution_wall_time_s,
                         step_wall_times_s=tuple(step_wall_times_s))


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
    rendering = parser.add_mutually_exclusive_group()
    rendering.add_argument("--no-rendering", action="store_true", help="Disable world rendering during this run and restore afterward")
    rendering.add_argument("--rendering", action="store_true", help="Enable rendering for RGB sensors during this run and restore afterward")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--agent-package", type=Path, help="Trusted local agent package directory containing agent.json")
    parser.add_argument("--agent-python", type=Path, help="Python executable in the agent's dependency environment")
    parser.add_argument("--agent-load-timeout", type=float, help="Agent load deadline in wall-clock seconds (default: 30)")
    parser.add_argument("--agent-step-timeout", type=float, help="Per-step agent deadline in wall-clock seconds (default: 2)")
    parser.add_argument("--agent-close-timeout", type=float, help="Agent cleanup deadline in wall-clock seconds (default: 2)")
    parser.add_argument("--agent-control-mode", choices=["auto", "scripted", "authoritative"], default="auto")
    parser.add_argument("--driving-task", type=Path, help="JSON driving task: destination/route, desired speed, ownership and goal criteria")
    parser.add_argument("--decision-trace", type=Path, help="New JSONL file with exact agent inputs, decisions and resulting motion")
    parser.add_argument("--sensor-timeout", type=float, default=2.0, help="Deadline in wall seconds for each synchronized sensor bundle")
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
        if args.driving_task is not None:
            task = DrivingTask.model_validate_json(args.driving_task.read_text(encoding="utf-8"))
            scenario = validate_scenario(scenario.model_dump() | {"driving_task": task.model_dump()})
        if args.output is not None and args.decision_trace is not None and args.output.resolve() == args.decision_trace.resolve():
            raise ValueError("Result output and decision trace must be separate files")
        with ExitStack() as stack:
            trace_writer = None
            if args.decision_trace is not None:
                from .reference_evidence import DecisionTrace
                trace_writer = stack.enter_context(DecisionTrace(args.decision_trace))
            output = run_simulation(scenario, mode=args.mode, options=SimulationOptions(
                allow_map_reload=args.allow_map_reload, camera=args.camera, lidar=args.lidar,
                no_rendering_mode=True if args.no_rendering else False if args.rendering else None,
                sensor_timeout_s=args.sensor_timeout,
            ), agent=agent, agent_control_mode=args.agent_control_mode, on_decision=trace_writer)
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
