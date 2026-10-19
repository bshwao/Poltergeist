"""Run the maintained learned policy and save local, reproducible pilot evidence."""

import argparse
from dataclasses import asdict
from datetime import UTC, datetime
import json
import math
import os
from pathlib import Path
import sys
from time import monotonic

from scenarios import load_scenario, load_template, validate_scenario
from shared.models import AgentDiagnostics, AgentStepResult, ControlInput, DrivingTask, SimulationOptions
from simulator.agents import AgentSDKError
from simulator.agents.package import inspect_agent_package
from simulator.agents.reference_ai.checkpoint import CHECKPOINT_SHA256, DEFAULT_CHECKPOINT, UPSTREAM_REVISION
from simulator.agents.process import AgentProcessOptions, ProcessAgent
from simulator.errors import SimulationError
from simulator.reference_evidence import (
    DecisionTrace, compare_runs, file_sha256, gpu_snapshot, hardware_info, package_fingerprint,
    process_peak_memory_bytes, summarize_run,
)
from simulator.simulation_runner import SimulationRunner
from simulator.timing import latency_gc


REFERENCE_PACKAGE = Path(__file__).resolve().parents[1] / "examples" / "agents" / "reference_ai"
TRIALS = ("road_following", "sudden_braking", "pedestrian_crossing", "intersection")


class ControlAblationAgent(ProcessAgent):
    """Deliberately replace neural controls in a separately labelled experiment.

    The actual policy still executes. Its proposed control and diagnostics are
    retained so this intervention cannot masquerade as learned braking.
    """

    def step(self, observation):
        predicted = super().step(observation)
        diagnostics = predicted.diagnostics or AgentDiagnostics()
        metadata = dict(diagnostics.metadata)
        metadata["control_ablation"] = {"kind": "full_braking_replacement", "learned_control": predicted.control.model_dump(mode="json")}
        return AgentStepResult(protocol_version=predicted.protocol_version, frame=predicted.frame, control=ControlInput(brake=1.0),
                               diagnostics=AgentDiagnostics.model_validate(diagnostics.model_dump() | {"metadata": metadata}))


def _write_json(path, payload):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, indent=2, allow_nan=False)
        stream.write("\n")


def _positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return number


def _seed(value):
    number = int(value)
    if not 0 <= number <= 2**31 - 1:
        raise argparse.ArgumentTypeError("must be between 0 and 2,147,483,647")
    return number


def _recipe(template, scenario_path, duration, seed):
    if scenario_path is not None:
        source = load_scenario(scenario_path)
    elif template == "road_following":
        source = load_template("sudden_braking")
        # Keep a valid spawn selection while removing the hazard entirely.
        source = validate_scenario(source.model_dump() | {
            "name": "reference_ai_road_following", "template": None,
            "description": "Bounded road following with the maintained learned reference policy.",
            "actors": [actor.model_dump() for actor in source.actors if actor.type == "ego_vehicle"],
            "events": [], "event": None, "parameters": {},
        })
    else:
        source = load_template(template)
    changes = {"seed": seed}
    if duration is not None:
        if any(event.trigger_time_s >= duration for event in source.events):
            raise ValueError("--duration would remove a scheduled scenario event; choose a longer duration or an explicit scenario file")
        changes["duration_s"] = duration
    return validate_scenario(source.model_dump() | changes)


def _run_trial(scenario, *, label, directory, args, options, process_options):
    directory.mkdir()
    _write_json(directory / "recipe.json", scenario.model_dump(mode="json"))
    agent = None
    if label != "baseline":
        agent_class = ControlAblationAgent if label == "control_ablation" else ProcessAgent
        agent = agent_class(REFERENCE_PACKAGE, python_executable=args.agent_python, options=process_options)
    worker_peak = None
    parent_peak = None
    with DecisionTrace(directory / "decisions.jsonl") as trace:
        def record(payload):
            nonlocal worker_peak, parent_peak
            # Label the deliberate override in both top-level trial metadata and
            # every affected step. It is not an autonomous model decision.
            if label == "control_ablation":
                payload = dict(payload) | {"interventions": [*payload["interventions"], "diagnostic_full_braking_control_ablation"]}
            trace(payload)
            peak = process_peak_memory_bytes(None if agent is None else agent.pid)
            if peak is not None:
                worker_peak = max(worker_peak or 0, peak)
            peak = process_peak_memory_bytes(os.getpid())
            if peak is not None:
                parent_peak = max(parent_peak or 0, peak)

        started = monotonic()
        with latency_gc(enabled=args.performance) as gc_settings:
            output = SimulationRunner(options=options).run(
                scenario, mode=args.mode, agent=agent,
                agent_control_mode="scripted" if agent is None else "authoritative",
                on_decision=record,
            )
        total_wall = monotonic() - started
        summary = summarize_run(output, trace.records, total_wall_time_s=total_wall, variant=label,
                                process_stats=None if agent is None else agent.stats,
                                memory={"parent_process_peak_working_set_bytes": parent_peak,
                                        "worker_process_peak_working_set_bytes": worker_peak,
                                        "gpu_peak_memory_bytes": None,
                                        "method": "OS high-water marks read while processes are alive; parent includes earlier activity. GPU peaks are not measured."})
        summary["timing"]["environment"] = {**(summary["timing"]["environment"] or {}),
                                               "garbage_collection": dict(gc_settings)}
    _write_json(directory / "result.json", output.result.model_dump(mode="json"))
    with (directory / "telemetry.jsonl").open("x", encoding="utf-8", newline="\n") as stream:
        for frame in output.telemetry:
            stream.write(frame.model_dump_json() + "\n")
    _write_json(directory / "summary.json", summary)
    return output, summary


def _markdown(report):
    lines = ["# Poltergeist reference AI pilot", "", f"Generated: {report['created_at']}", "",
             f"Mode: **{report['mode']}**. {report['verification']['interpretation']}", "",
             "| Trial | Variant | Status | Inference p95 | Full cycle p95 | Budget misses | RTF |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for item in report["runs"]:
        timing = item["timing"]
        def ms(value):
            return "unavailable" if value is None else f"{value*1000:.2f} ms"
        rtf = timing["real_time_factor"]
        lines.append(f"| {item['scenario_name']} / seed {item['seed']} | {item['variant']} | {item['status']} | {ms(timing['inference']['p95_s'])} | {ms(timing['cycle']['p95_s'])} | {timing['cycle']['budget_misses']}/{timing['cycle']['samples']} | {'unavailable' if rtf is None else f'{rtf:.3f}'} |")
    lines.extend(["", "The report records simulator-state inputs, a supplied route, and tracking-controller assistance. Completion is not a safety score or evidence of independent perception/global pathfinding.",
                  "", "Real-time throughput requires completed live CARLA trials, RTF >= 1, and at least 95% complete steps within the fixed-step budget over repeated trials. These are measurements, not a hard real-time guarantee.",
                  "", "Lane departures and GPU peak memory are unavailable. Device-wide GPU snapshots and OS process-memory measurements are retained in report.json. See each trial's decisions.jsonl, telemetry.jsonl, recipe.json, result.json, and summary.json for the underlying evidence.", ""])
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--template", choices=(*TRIALS, "occluded_pedestrian"))
    source.add_argument("--scenario", type=Path)
    source.add_argument("--suite", action="store_true", help="Road following, braking, pedestrian and crossing-traffic trials")
    parser.add_argument("--mode", choices=("test", "carla"), default="carla")
    parser.add_argument("--duration", type=_positive, help="Simulated seconds; scheduled events must remain inside the trial")
    parser.add_argument("--driving-task", type=Path, help="JSON driving task with a real map-valid destination and optional framework route")
    parser.add_argument("--seed", type=_seed, action="append", help="Repeat for additional trial seeds (default: 0)")
    parser.add_argument("--output", type=Path, required=True, help="A new directory for the report and raw evidence; never overwritten")
    parser.add_argument("--agent-python", type=Path, help="Existing Python environment containing the reference agent dependencies")
    parser.add_argument("--agent-load-timeout", type=_positive, default=60.0)
    parser.add_argument("--agent-step-timeout", type=_positive, default=2.0)
    parser.add_argument("--compare-baseline", action="store_true", help="Run the existing rule-based baseline with identical recipes")
    parser.add_argument("--ablate", action="store_true", help="Run a separate labelled full-braking control replacement to verify influence on motion")
    parser.add_argument("--allow-map-reload", action="store_true")
    parser.add_argument("--no-rendering", action="store_true", help="Disable CARLA world rendering for this state-based pilot, restoring it afterward")
    parser.add_argument("--performance", action="store_true", help="Use no rendering, a 1 ms sensor-settle window, and scoped GC tuning for the state-based throughput benchmark")
    parser.add_argument("--require-real-time", action="store_true", help="Exit unsuccessfully unless repeated live reference trials pass the 20 Hz gate; retain all evidence")
    parser.add_argument("--sensor-settle", type=_positive, help="Explicit positive sensor-settle wall seconds per tick (default: 0.02; --performance: 0.001)")
    args = parser.parse_args(argv)
    output_dir = args.output.expanduser().absolute()
    created_output = False
    try:
        seeds = args.seed or ([load_scenario(args.scenario).seed] if args.scenario else [0])
        if len(seeds) != len(set(seeds)):
            raise ValueError("Each --seed must be unique")
        trial_names = list(TRIALS) if args.suite else [args.template or ("scenario" if args.scenario else "road_following")]
        recipes = [_recipe(name, args.scenario, args.duration, seed) for name in trial_names for seed in seeds]
        if args.driving_task is not None:
            task = DrivingTask.model_validate_json(args.driving_task.read_text(encoding="utf-8"))
            recipes = [validate_scenario(recipe.model_dump() | {"driving_task": task.model_dump()}) for recipe in recipes]
        package = inspect_agent_package(REFERENCE_PACKAGE)
        fingerprint = package_fingerprint(package)
        checkpoint_path = Path(package.manifest.configuration.get("checkpoint_path", DEFAULT_CHECKPOINT)).expanduser().absolute()
        # The supported checkpoint is intentionally outside the SDK package so
        # large weights remain in ignored local storage. Preserve both its
        # pinned identity and the bytes actually present, without deserializing.
        if package.manifest.agent_id == "poltergeist-reference-ai":
            fingerprint["checkpoint"] = {"path": str(checkpoint_path), "expected_sha256": CHECKPOINT_SHA256,
                                         "actual_sha256": file_sha256(checkpoint_path), "upstream_revision": UPSTREAM_REVISION,
                                         "size_bytes": checkpoint_path.stat().st_size}
            if fingerprint["checkpoint"]["actual_sha256"] != CHECKPOINT_SHA256:
                raise ValueError("Reference AI checkpoint does not match the pinned published release; run scripts.setup_reference_ai")
            repository = Path(__file__).resolve().parents[1]
            sources = [*(repository / "simulator" / "agents" / "reference_ai").rglob("*.py"),
                       *(repository / name for name in (
                           "simulator/reference_scene.py", "simulator/simulation_runner.py", "simulator/navigation.py",
                           "simulator/reference_evidence.py", "simulator/carla_adapter.py",
                           "simulator/world_manager.py", "simulator/agents/sdk.py",
                           "simulator/agents/process.py", "simulator/agents/process_protocol.py",
                           "simulator/agents/worker.py", "simulator/sensor_manager.py",
                           "simulator/rule_monitor.py", "simulator/timing.py", "shared/models/agent.py",
                           "shared/models/runtime.py", "shared/models/scenario.py", "shared/models/simulation.py",
                           "scripts/run_reference_ai.py"))]
            fingerprint["integration_source"] = {path.relative_to(repository).as_posix(): file_sha256(path) for path in sorted(sources)}
        settle = args.sensor_settle if args.sensor_settle is not None else (0.001 if args.performance else 0.02)
        options = SimulationOptions(allow_map_reload=args.allow_map_reload, sensor_settle_s=settle,
                                    no_rendering_mode=True if args.no_rendering or args.performance else None)
        process_options = AgentProcessOptions(load_timeout_s=args.agent_load_timeout, step_timeout_s=args.agent_step_timeout)
        output_dir.mkdir(parents=True, exist_ok=False)
        created_output = True
        report = {"schema_version": "reference-ai-pilot-1.0", "created_at": datetime.now(UTC).isoformat(),
                  "mode": args.mode, "model": fingerprint, "hardware": hardware_info(),
                  "options": options.model_dump(mode="json"), "process_options": asdict(process_options),
                  "command": list(argv) if argv is not None else sys.argv[1:], "runs": [], "comparisons": []}
        report["requested_run_count"] = len(recipes) * (1 + int(args.compare_baseline) + int(args.ablate))
        # Preserve reproduction data before starting CARLA, including failures.
        _write_json(output_dir / "manifest.json", {key: value for key, value in report.items() if key not in {"runs", "comparisons"}})
        cancelled = False
        for index, scenario in enumerate(recipes):
            pilot = None
            variants = ["reference_ai", *(["baseline"] if args.compare_baseline else []), *(["control_ablation"] if args.ablate else [])]
            for variant in variants:
                folder = output_dir / f"{index + 1:03d}-{variant}"
                print(f"Running {scenario.name}, seed {scenario.seed}, {variant} ({args.mode})", file=sys.stderr, flush=True)
                output, summary = _run_trial(scenario, label=variant, directory=folder, args=args, options=options, process_options=process_options)
                summary["evidence_directory"] = folder.name
                report["runs"].append(summary)
                if variant == "reference_ai":
                    pilot = output
                else:
                    report["comparisons"].append({"trial": index + 1, "variant": variant, **compare_runs(pilot, output)})
                if output.result.simulation.status == "cancelled":
                    cancelled = True
                    break
            if cancelled:
                break
        report["executed_run_count"] = len(report["runs"])
        report["skipped_run_count"] = report["requested_run_count"] - report["executed_run_count"]
        report["hardware"]["gpu_after"] = gpu_snapshot()
        pilots = [item for item in report["runs"] if item["variant"] == "reference_ai"]
        all_live = bool(pilots) and all(item["control_evidence"]["live_carla_execution"] and item["control_evidence"]["complete_trace"] for item in pilots)
        declared_hashes = {fingerprint["files"][name]["sha256"] for name in package.manifest.assets}
        if "checkpoint" in fingerprint:
            declared_hashes.add(fingerprint["checkpoint"]["expected_sha256"])
        reported_hashes = {sha for item in pilots for sha in item["control_evidence"]["reported_checkpoint_sha256"]}
        report["verification"] = {
            "live_carla_trials_completed": all_live,
            "all_requested_runs_completed": report["skipped_run_count"] == 0 and all(item["status"] == "completed" for item in report["runs"]),
            "model_inference_observed_checkpoint_hashes": sorted(reported_hashes),
            "checkpoint_identity_matches_declared_assets": bool(reported_hashes) and reported_hashes <= declared_hashes,
            "repeated_live_throughput_gate": all_live and len(pilots) >= 2 and all(item["timing"]["single_trial_throughput_gate"] for item in pilots),
            "interpretation": ("Live CARLA execution was recorded. Review checkpoint identity, control evidence, response tests, and driving metrics before claiming learned driving or competence."
                               if all_live else "Live CARLA control is not verified by this report. Synthetic mode and failed trials cannot satisfy that checkpoint."),
        }
        _write_json(output_dir / "report.json", report)
        with (output_dir / "report.md").open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(_markdown(report))
        print(json.dumps({"report": str(output_dir / "report.json"), "verification": report["verification"]}, allow_nan=False))
        statuses = {item["status"] for item in report["runs"]}
        unmet_performance = args.require_real_time and not report["verification"]["repeated_live_throughput_gate"]
        return 130 if "cancelled" in statuses else (1 if "failed" in statuses or unmet_performance else 0)
    except (OSError, ValueError, SimulationError, AgentSDKError) as error:
        if created_output and not (output_dir / "failure.json").exists():
            try:
                _write_json(output_dir / "failure.json", {"error_type": type(error).__name__, "error": str(error)})
            except OSError:
                pass
        print(f"Reference AI pilot failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
