"""Calibrate fixed stress recipes against the unchanged ground-truth baseline.

Runs are serial in a separately started idle CARLA 0.9.16 world. No user database,
server startup, map reload, learned model, or baseline tuning is performed here.
Interrupted trials are retained and never silently retried by --resume.
"""

import argparse
from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
import json
from math import sqrt
import os
from pathlib import Path
import statistics
from time import monotonic

from evaluation import evaluate_safety
from scenarios.stress_presets import CALIBRATION_MAP, CATALOG_VERSION, STRESS_PRESETS, get_stress_preset
from shared.models import EvaluationSettings, SafetyMetrics, Simulation, SimulationOptions, SimulationResult
from simulator.carla_client import CarlaConnectionSettings
from simulator.simulation_runner import SimulationRunner
from simulator.world_manager import _SETTINGS_FIELDS, _WEATHER_FIELDS

ROOT = Path(__file__).resolve().parents[1]


def write_json(path, value):
    """Replace one owned evidence document atomically, without partial JSON."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def source_hashes():
    paths = [ROOT / "scripts" / "benchmark_stress.py"]
    for name in ("scenarios", "simulator", "evaluation", "shared"):
        paths.extend((ROOT / name).rglob("*.py"))
    return {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(paths)}


def world_state(client):
    world = client.get_world()
    settings, weather = world.get_settings(), world.get_weather()
    return {
        "map": world.get_map().name.split("/")[-1],
        "client_version": client.get_client_version(), "server_version": client.get_server_version(),
        "settings": {name: getattr(settings, name) for name in _SETTINGS_FIELDS if hasattr(settings, name)},
        "weather": {name: getattr(weather, name) for name in _WEATHER_FIELDS if hasattr(weather, name)},
        "dynamic_actors": sorted(actor.id for actor in world.get_actors()
                                 if actor.type_id.startswith(("vehicle.", "walker.", "sensor.", "controller."))),
    }


def require_idle(state):
    if state["client_version"] != "0.9.16" or state["server_version"] != "0.9.16":
        raise ValueError("Stress calibration requires a matching CARLA 0.9.16 client and server")
    if state["map"] != CALIBRATION_MAP:
        raise ValueError(f"Start the dedicated server on {CALIBRATION_MAP}; this tool never reloads maps")
    if state["dynamic_actors"] or state["settings"]["synchronous_mode"]:
        raise ValueError("CARLA is not idle/asynchronous. Existing actors and tick owners were left untouched")


def wait_for_signal_flags(world, frozen, *, green=None):
    # RPC completion and the next streamed snapshot can race. Re-read fresh
    # actor handles until the authoritative snapshot reflects the request.
    deadline = monotonic() + 5.
    while True:
        lights = list(world.get_actors().filter("traffic.traffic_light"))
        if all(light.is_frozen() == frozen and (green is None or light.get_state() == green) for light in lights):
            return True
        if monotonic() >= deadline:
            return False
        world.wait_for_tick(1.)


@contextmanager
def signal_protocol(client, carla, mode, directory):
    """Control one dedicated idle world's lights and retain restoration evidence.

    CARLA cannot restore elapsed cycle time; original colors are requested again,
    and the original global frozen flag/durations are preserved. No map reload.
    See https://carla.readthedocs.io/en/latest/python_api/#carlatrafficlight.
    """
    if mode == "natural":
        yield
        return
    world = client.get_world()
    lights = list(world.get_actors().filter("traffic.traffic_light"))
    frozen = {light.is_frozen() for light in lights}
    if len(frozen) > 1:
        raise ValueError("Mixed traffic-light freeze states cannot be restored with CARLA's global freeze API")
    was_frozen = next(iter(frozen), False)
    original = {}
    try:
        world.freeze_all_traffic_lights(True)
        if not wait_for_signal_flags(world, True):
            raise RuntimeError("Traffic lights did not freeze")
        original = {light.id: light.get_state() for light in lights}
        write_json(directory / "signals.json", {"mode": mode, "original_frozen": was_frozen,
                   "original_colors": {str(key): str(value) for key, value in original.items()}, "restored": False,
                   "scope": "Original colors requested and frozen flag restored; elapsed signal cycle time is not restorable."})
        for light in lights:
            light.set_state(carla.TrafficLightState.Green)
        if not wait_for_signal_flags(world, True, green=carla.TrafficLightState.Green):
            raise RuntimeError("Controlled green traffic lights were not applied")
        yield
    finally:
        errors = []
        for light in lights:
            if light.id in original:
                try:
                    light.set_state(original[light.id])
                except Exception as error:
                    errors.append(f"color {light.id}: {error}")
        flags_restored = False
        try:
            world.freeze_all_traffic_lights(was_frozen)
            flags_restored = wait_for_signal_flags(world, was_frozen)
        except Exception as error:
            errors.append(f"frozen status: {error}")
        write_json(directory / "signals.json", {"mode": mode, "original_frozen": was_frozen,
                   "original_colors": {str(key): str(value) for key, value in original.items()},
                   "restored": flags_restored and not errors, "colors_requested": not errors, "errors": errors,
                   "scope": "Original colors requested and frozen flag restored; elapsed signal cycle time is not restorable."})
        if not flags_restored or errors:
            raise RuntimeError("Traffic-light frozen status was not restored")


def wilson_interval(successes, total):
    if not total:
        return None
    z = 1.959963984540054
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    half = z * sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [max(0., center - half), min(1., center + half)]


def collision_coverage_complete(metrics):
    """Require complete physical channels; missing road rules remain unknown."""
    coverage = metrics.coverage
    return (coverage.total_frames > 0 and not coverage.has_gaps
            and coverage.collision_frames == coverage.clearance_frames == coverage.ttc_frames == coverage.total_frames
            and coverage.actor_pairs == coverage.clearance_pairs == coverage.ttc_pairs
            and metrics.collision_count is not None)


def interaction_metrics(output):
    family = output.result.simulation.scenario.template
    target = {"intersection": "crossing", "sudden_braking": "lead"}.get(family, "pedestrian")
    frames = []
    for frame in output.telemetry:
        actors = None if frame.actors is None else [actor for actor in frame.actors if actor.actor_id == target]
        # Every preset declares its target for the entire run. An absent target
        # is unavailable evidence, rather than an observed empty safe scene.
        frames.append(frame.model_copy(update={
            "actors": actors or None,
            "collisions": None if frame.collisions is None else [event for event in frame.collisions if event.other_actor_id == target],
        }))
    return target, evaluate_safety(frames, settings=output.result.metrics.evaluation_settings)


def observed_danger(result, metrics=None):
    """Operational bins, scoped to this controller/map/protocol, not road safety."""
    metrics = result.metrics if metrics is None else metrics
    if result.simulation.status != "completed" or not collision_coverage_complete(metrics):
        return "unclassified"
    if metrics.collision_count is None:
        return "unclassified"
    if metrics.collision_count > 0:
        return "collision"
    if (metrics.min_ttc_s is not None and metrics.min_ttc_s <= 1.5
            or metrics.min_clearance_m is not None and metrics.min_clearance_m <= 1.):
        return "critical_avoidance"
    return "clear"


def summarize(directory):
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    rows = []
    for identifier in manifest["presets"]:
        records = []
        for seed in manifest["seeds"]:
            trial = directory / f"{identifier}--seed-{seed}"
            result_path = trial / "result.json"
            if result_path.exists():
                result = SimulationResult.model_validate_json(result_path.read_text(encoding="utf-8"))
                state = json.loads((trial / "trial.json").read_text(encoding="utf-8"))
                interaction = SafetyMetrics.model_validate(state["interaction_metrics"]) if "interaction_metrics" in state else SafetyMetrics()
                accepted = (state.get("state_restored") is True and result.simulation.status == "completed"
                            and collision_coverage_complete(interaction))
                records.append((result, interaction, accepted))
        valid = [(result, metrics) for result, metrics, accepted in records if accepted]
        collisions = sum(metrics.collision_count > 0 for result, metrics in valid)
        counts = {name: sum(observed_danger(result, metrics) == name for result, metrics in valid)
                  for name in ("clear", "critical_avoidance", "collision", "unclassified")}
        medians = {}
        for name in ("min_clearance_m", "min_ttc_s", "max_deceleration_mps2", "severity"):
            values = [getattr(metrics, name) for result, metrics in valid if getattr(metrics, name) is not None]
            medians[name] = statistics.median(values) if values else None
        intended = manifest.get("preset_metadata", {}).get(identifier, {}).get("intended_stress")
        rows.append({"preset": identifier, "intended_stress": intended,
                     "expected": len(manifest["seeds"]), "terminal": len(records), "valid_completed": len(valid),
                     "invalid_or_noncompleted": len(records) - len(valid), "missing": len(manifest["seeds"]) - len(records),
                     "collision_runs": collisions, "collision_rate": collisions / len(valid) if valid else None,
                     "collision_rate_wilson_95": wilson_interval(collisions, len(valid)), "observed_counts": counts,
                     "all_actor_collision_runs": sum(result.metrics.collision_count > 0 for result, metrics in valid),
                     "medians": medians})
    signal_record = directory / "signals.json"
    signals_restored = (manifest.get("signals") == "natural" or signal_record.is_file()
                        and json.loads(signal_record.read_text(encoding="utf-8")).get("restored") is True)
    report = {"schema_version": "poltergeist-stress-summary-v1", "catalog_version": manifest["catalog_version"],
              "phase": manifest["phase"], "seeds": manifest["seeds"], "presets": rows,
              "scope": "Observed ground-truth baseline outcomes on one CARLA map/protocol; no learned-perception or general road-safety claim.",
              "signals_restored": signals_restored,
              "complete": signals_restored and all(row["valid_completed"] == row["expected"] for row in rows)}
    write_json(directory / "summary.json", report)
    return report


def run(args):
    directory = args.output.expanduser().resolve()
    presets = [get_stress_preset(identifier) for identifier in args.presets] if args.presets else list(STRESS_PRESETS)
    if len(set(args.seeds)) != len(args.seeds) or len({preset.id for preset in presets}) != len(presets):
        raise ValueError("Seeds and preset IDs must be unique")
    # Validate every recipe before creating evidence or connecting.
    recipes = {(preset.id, seed): preset.scenario(seed=seed) for preset in presets for seed in args.seeds}
    options = SimulationOptions(no_rendering_mode=True, allow_map_reload=False)
    evaluation = EvaluationSettings()
    hashes = source_hashes()
    manifest = {"schema_version": "poltergeist-stress-benchmark-v1", "catalog_version": CATALOG_VERSION,
                "phase": args.phase, "presets": [preset.id for preset in presets], "seeds": args.seeds,
                "preset_metadata": {preset.id: {"family": preset.family.value, "intended_stress": preset.intended_stress,
                                               "description": preset.description} for preset in presets},
                "host": args.carla_host, "port": args.carla_port, "source_hashes": hashes,
                "options": options.model_dump(mode="json"), "evaluation": evaluation.model_dump(mode="json"),
                "baseline_defaults": {"target_speed_mps": None, "emergency_ttc_s": 1.5, "minimum_clearance_m": 2.0},
                "signals": args.signals,
                "protocol": "Serial 8 s dry daylight ground-truth baseline; no ego events; no camera/learned perception. Danger bins use target contacts, TTC <= 1.5 s or clearance <= 1 m; braking and all-actor collisions are reported separately."}
    if args.resume:
        if json.loads((directory / "manifest.json").read_text(encoding="utf-8")) != manifest:
            raise ValueError("Resume configuration/source differs. Use a new output directory to preserve the old evidence")
    else:
        directory.mkdir(parents=True, exist_ok=False)
        write_json(directory / "manifest.json", manifest)
    # Keep the native map cache and all trial output in this evidence directory.
    os.chdir(directory)
    import carla
    client = carla.Client(args.carla_host, args.carla_port)
    client.set_timeout(30.)
    require_idle(world_state(client))
    connection = CarlaConnectionSettings(host=args.carla_host, port=args.carla_port, timeout=30.)
    with signal_protocol(client, carla, args.signals, directory):
        run_trials(directory, recipes, hashes, client, connection, options, evaluation)
    return summarize(directory)


def run_trials(directory, recipes, hashes, client, connection, options, evaluation):
    for (identifier, seed), recipe in recipes.items():
        trial = directory / f"{identifier}--seed-{seed}"
        if trial.exists():
            if not (trial / "result.json").exists() or not (trial / "trial.json").exists():
                raise ValueError(f"Uncertain/interrupted trial retained at {trial}; inspect it, do not blindly retry")
            previous = json.loads((trial / "trial.json").read_text(encoding="utf-8"))
            if previous.get("state_restored") is not True:
                raise ValueError(f"Previous trial lacks verified cleanup: {trial}")
            continue
        if source_hashes() != hashes:
            raise ValueError("Source changed during calibration; retained evidence must not mix implementations")
        before = world_state(client)
        require_idle(before)
        trial.mkdir()
        simulation = Simulation(scenario=recipe, agent="baseline", mode="carla")
        record = {"simulation_id": str(simulation.id), "started_at": datetime.now(UTC).isoformat(),
                  "status": "running", "before": before, "state_restored": False}
        write_json(trial / "scenario.json", recipe.model_dump(mode="json"))
        write_json(trial / "trial.json", record)
        print(f"START {identifier} seed={seed} simulation={simulation.id}", flush=True)
        with (trial / "telemetry.partial.jsonl").open("x", encoding="utf-8") as stream:
            def save_frame(frame):
                stream.write(frame.model_dump_json() + "\n")
                stream.flush()
            output = SimulationRunner(options=options, evaluation_settings=evaluation, connection_settings=connection).run(
                simulation, on_frame=save_frame)
        write_json(trial / "result.json", output.result.model_dump(mode="json"))
        # Sensor finalization can reconcile late contacts. The final trace is
        # authoritative; keep the earlier streamed trace separately for failures.
        with (trial / "telemetry.jsonl").open("x", encoding="utf-8") as stream:
            for frame in output.telemetry:
                stream.write(frame.model_dump_json() + "\n")
        write_json(trial / "timing.json", {"setup_wall_time_s": output.setup_wall_time_s,
                    "execution_wall_time_s": output.execution_wall_time_s, "timing_environment": output.timing_environment})
        after = world_state(client)
        target, metrics = interaction_metrics(output)
        record.update(status=output.result.simulation.status, finished_at=datetime.now(UTC).isoformat(),
                      after=after, state_restored=before == after, interaction_target=target,
                      interaction_metrics=metrics.model_dump(mode="json"), observed_danger=observed_danger(output.result, metrics))
        write_json(trial / "trial.json", record)
        summarize(directory)
        print(f"DONE {identifier} seed={seed} status={record['status']} class={record['observed_danger']} "
              f"collisions={metrics.collision_count} clearance={metrics.min_clearance_m} TTC={metrics.min_ttc_s} "
              f"error={output.result.simulation.error}", flush=True)
        if not record["state_restored"]:
            raise RuntimeError(f"CARLA state restoration failed; stopped before another trial: {trial}")
        if record["status"] == "cancelled":
            break
    return summarize(directory)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="New isolated evidence directory (or exact --resume directory)")
    parser.add_argument("--phase", choices=("pilot", "calibration", "benchmark"), default="pilot")
    parser.add_argument("--seeds", type=int, nargs="+", default=[7])
    parser.add_argument("--presets", nargs="+", choices=[preset.id for preset in STRESS_PRESETS])
    parser.add_argument("--carla-host", default="127.0.0.1")
    parser.add_argument("--carla-port", type=int, default=2000)
    parser.add_argument("--signals", choices=("natural", "green"), default="natural")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--summarize", action="store_true", help="Read retained results without contacting CARLA")
    args = parser.parse_args(argv)
    args.output = args.output.expanduser().resolve()
    report = summarize(args.output.resolve()) if args.summarize else run(args)
    print(json.dumps({"complete": report["complete"], "summary": str(args.output.resolve() / "summary.json")}, indent=2))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
