"""Exercise registration, matched runs, decisions, analysis and exports in isolated storage.

No running Poltergeist API is needed. The local API binds an ephemeral loopback
port. CARLA is contacted only with explicit --mode carla; no map is reloaded.
"""

import argparse
from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import socket
import sys
from threading import Thread
from time import monotonic, sleep

import uvicorn

from backend.config import Settings
from backend.main import create_app
from scenarios import load_scenario, validate_scenario
from scripts.doctor import carla_probe
from scripts.export_report import download_report
from scripts.run_experiment import ApiClient
from shared.models.comparison import ComparisonReport
from simulator.reference_evidence import hardware_info, latency_summary


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


@contextmanager
def isolated_api(settings):
    """Bind before starting, avoiding a free-port discovery/startup race."""
    server = None
    thread = None
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        server = uvicorn.Server(uvicorn.Config(create_app(settings), log_level="warning", access_log=False,
            host="127.0.0.1", port=listener.getsockname()[1], lifespan="on"))
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = monotonic() + 30
            while not server.started:
                if not thread.is_alive() or monotonic() >= deadline:
                    raise RuntimeError("Isolated API did not start; inspect stderr. No application storage was changed.")
                sleep(.05)
            yield ApiClient(f"http://127.0.0.1:{listener.getsockname()[1]}", timeout=30)
        finally:
            server.should_exit = True
            thread.join(20)
            if thread.is_alive():
                raise RuntimeError("Isolated API did not stop cleanly; validation storage was retained for investigation.")


def live_recipe(host, port, duration):
    """Read a short forward road route on the loaded map; never tick/load worlds."""
    import carla
    client = carla.Client(host, port)
    client.set_timeout(10)
    if client.get_client_version() != "0.9.16" or client.get_server_version() != "0.9.16":
        raise ValueError("Live validation requires a matching CARLA 0.9.16 client/server")
    world = client.get_world()
    existing = [a.id for a in world.get_actors() if a.type_id.startswith(("vehicle.", "walker.", "sensor.", "controller."))]
    if existing:
        raise ValueError("Live validation needs an idle dedicated CARLA world; existing actors were left untouched")
    road = world.get_map()
    spawns = road.get_spawn_points()
    if not spawns:
        raise ValueError("Loaded map has no vehicle spawn points")
    waypoint = road.get_waypoint(spawns[0].location, project_to_road=True, lane_type=carla.LaneType.Driving)
    route = []
    for _ in range(16):
        if waypoint is None:
            break
        transform = waypoint.transform
        route.append({"position": {"x": float(transform.location.x), "y": float(transform.location.y), "z": float(transform.location.z)},
            "rotation": {"yaw_deg": float(transform.rotation.yaw)}})
        choices = waypoint.next(2.)
        waypoint = min(choices, key=lambda w: (w.road_id, w.lane_id, w.s)) if choices else None
    if len(route) < 2:
        raise ValueError("Could not resolve a short route from spawn zero")
    return validate_scenario({"name": "Live installation control check", "map": road.name.split("/")[-1],
        "seed": 7, "duration_s": duration, "fixed_delta_seconds": .05,
        "actors": [{"id": "ego", "type": "ego_vehicle", "blueprint": "vehicle.tesla.model3", "spawn_point_index": 0, "speed_mps": 2.}],
        # Let the normal road-network resolver build the supplied framework
        # route from the measured spawn. Reprojecting every hand-sampled point
        # can select a different junction segment at a lane boundary.
        "driving_task": {"destination": route[-1], "target_speed_mps": 3.}})


def wait_for_operation(client, identifier, timeout):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        view = client.request("GET", f"/optimizations/{identifier}")
        if view["operation"]["status"] not in {"queued", "running"}:
            return view
        sleep(.1)
    client.request("POST", f"/optimizations/{identifier}/cancel", {}, expected=202)
    raise TimeoutError(f"Validation wait exceeded {timeout}s; cancellation requested for {identifier}. Inspect retained storage.")


def validate(args, directory):
    examples = args.examples_dir.resolve()
    if not (examples / "scenarios" / "installation_check.json").is_file():
        raise ValueError("Supported examples not found; pass --examples-dir /absolute/path/to/checkout/examples")
    settings = Settings(_env_file=None, database_url=f"sqlite:///{(directory / 'database' / 'runs.db').as_posix()}",
        storage_dir=directory / "artifacts", api_host="127.0.0.1", carla_host=args.carla_host,
        carla_port=args.carla_port, carla_timeout=30., simulation_shutdown_timeout_s=5.)
    source = load_scenario(examples / "scenarios" / "installation_check.json") if args.mode == "test" else live_recipe(args.carla_host, args.carla_port, args.duration or 2.)
    if args.mode == "test" and args.duration is not None:
        source = validate_scenario(source.model_dump(mode="python") | {"duration_s": args.duration})
    options = {"no_rendering_mode": not args.sensors} if args.mode == "carla" else {}
    before = carla_probe(sys.executable, args.carla_host, args.carla_port) if args.mode == "carla" else None
    report = {"schema_version": "poltergeist-installation-validation-v1", "created_at": datetime.now(UTC).isoformat(),
        "mode": args.mode, "passed": False, "hardware": hardware_info(), "carla_before": before,
        "scenario": source.model_dump(mode="json"), "runs": [], "checks": [],
        "scope": "Installation/control-path integration, not a safety ranking or real-time benchmark. Reported step times exclude some persistence/I/O; use the reference AI performance harness for full-cycle throughput."}
    write_json(directory / "recipe.json", source.model_dump(mode="json"))
    registrations = []
    with isolated_api(settings) as client:
        client.request("GET", "/health")
        selected = [(examples / "agents" / "starter", sys.executable, {}, [])]
        if args.sensors:
            selected.append((examples / "agents" / "sensor_pilot", sys.executable, {}, []))
        if args.agent_package:
            configuration = json.loads(args.configuration.read_text(encoding="utf-8")) if args.configuration else {}
            if not isinstance(configuration, dict):
                raise ValueError("Agent configuration must be a JSON object")
            selected.append((args.agent_package.resolve(), str(args.agent_python or sys.executable), configuration,
                             [str(path.resolve(strict=True)) for path in args.model_file]))
        for package, python, configuration, files in selected:
            deadlines = {"load_timeout_s": args.agent_load_timeout} if args.agent_package and package == args.agent_package.resolve() else {}
            registration = client.request("POST", "/agents", {"package_path": str(package), "python_executable": str(python),
                "configuration": configuration, "model_files": files, "process_options": deadlines,
                "agent_control_mode": "authoritative", "trusted_local_code": True}, expected=201)
            registrations.append(registration)
        write_json(directory / "registrations.json", registrations)
        started = monotonic()
        operation = client.request("POST", "/optimizations", {"kind": "compare", "scenario": source.model_dump(mode="json"),
            "agents": [None, *[a["id"] for a in registrations]], "mode": args.mode, "seeds": args.seeds,
            "repeats": args.repeats, "options": options}, expected=202)
        write_json(directory / "accepted-operation.json", operation)
        view = wait_for_operation(client, operation["id"], args.wait_timeout)
        report["operation_wall_time_s"] = monotonic() - started
        comparison = ComparisonReport.model_validate(client.request("GET", f'/optimizations/{operation["id"]}/comparison'))
        write_json(directory / "comparison.json", comparison.model_dump(mode="json"))
        if view["operation"]["status"] != "completed":
            raise RuntimeError(f'Experiment {operation["id"]} ended {view["operation"]["status"]}: {view["operation"]["error"]}')
        for trial in view["trials"]:
            identifier = trial["simulation_id"]
            record = client.request("GET", f"/simulations/{identifier}")
            if record["simulation"]["status"] != "completed":
                raise RuntimeError(f'Child {identifier} failed: {record["simulation"]["error"]}')
            analysis = client.request("GET", f"/simulations/{identifier}/analysis")
            write_json(directory / f"{identifier}-analysis.json", analysis)
            page = client.request("GET", f"/simulations/{identifier}/decisions?limit=100&offset=0")
            if not page["available"] or page["coverage"] != "complete" or page["applied"] < 1:
                raise RuntimeError(f"Child {identifier} lacks a complete applied decision trace")
            records = page["records"][:]
            for offset in range(100, page["total"], 100):
                records.extend(client.request("GET", f"/simulations/{identifier}/decisions?limit=100&offset={offset}")["records"])
            snapshot = record["simulation"]["agent_snapshot"]
            if snapshot and (any(d["proposed_control"] != d["applied_control"] or d["interventions"] for d in records)
                             or any(d["agent_result"] is None for d in records)):
                raise RuntimeError(f"Child {identifier} did not preserve its authoritative agent responses")
            files = []
            for format in ("json", "csv", "html"):
                path = directory / f"{identifier}-report.{format}"
                download_report(api_url=client.base_url, simulation_id=identifier, format=format, output=path,
                              include_behavior=True, timeout=60.)
                files.append({"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size})
            step_times = [d["worker_step_wall_time_s"] for d in records if d["worker_step_wall_time_s"] is not None]
            metadata = [d["agent_result"]["diagnostics"]["metadata"] for d in records
                        if d["agent_result"] and d["agent_result"].get("diagnostics")]
            checkpoint_hashes = sorted({m["checkpoint_sha256"] for m in metadata if isinstance(m.get("checkpoint_sha256"), str)})
            if snapshot and snapshot["manifest"]["agent_id"] == "poltergeist-reference-ai":
                from simulator.agents.reference_ai.checkpoint import CHECKPOINT_SHA256
                if (checkpoint_hashes != [CHECKPOINT_SHA256] or len(metadata) != len(records)
                        or not all(isinstance(m.get("inference_wall_time_s"), (int, float)) and m["inference_wall_time_s"] > 0 for m in metadata)
                        or not any(f["sha256"] == CHECKPOINT_SHA256 for f in snapshot["model_files"])):
                    raise RuntimeError("Reference run lacks matching declared weights and recorded inference diagnostics")
                report["checks"].append("reference_checkpoint_and_inference_diagnostics")
            report["runs"].append({"simulation_id": identifier, "agent_index": trial["agent_index"], "seed": trial["scenario"]["seed"],
                "status": record["simulation"]["status"], "decisions": page["total"], "agent_inputs": record["result"]["agent_inputs"],
                "runtime": record["result"]["runtime"], "worker_step": latency_summary(step_times, source.fixed_delta_seconds),
                "agent_reported_checkpoint_sha256": checkpoint_hashes, "reports": files})
        report["checks"].extend(["registered_snapshot_comparisons", "completed_children", "complete_decision_traces",
            "authoritative_agent_controls", "behavior_analysis", "json_csv_html_behavior_exports"])
    # Reopen the actual database/artifacts behind a fresh API runtime.
    with isolated_api(settings) as client:
        restored = client.request("GET", f'/optimizations/{operation["id"]}/comparison')
        if restored != comparison.model_dump(mode="json"):
            raise RuntimeError("Comparison changed after persistence reopen")
        for run in report["runs"]:
            record = client.request("GET", f'/reports/simulations/{run["simulation_id"]}?include_behavior=true')
            if record["record"]["simulation"]["id"] != run["simulation_id"]:
                raise RuntimeError("Reopened export returned a different child identity")
    report["checks"].append("persistence_reopen")
    if args.mode == "carla":
        report["carla_after"] = carla_probe(sys.executable, args.carla_host, args.carla_port)
        if report["carla_after"] != before:
            raise RuntimeError("CARLA map/settings/version probe changed after validation; inspect retained evidence")
        report["checks"].append("carla_map_settings_restored")
    report["passed"] = True
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("test", "carla"), default="test")
    parser.add_argument("--output", type=Path, required=True, help="New evidence directory; existing paths are never overwritten")
    parser.add_argument("--examples-dir", type=Path, default=Path(__file__).resolve().parents[1] / "examples")
    parser.add_argument("--sensors", action="store_true", help="Include the bundled camera/LiDAR teaching agent; rendering is required in CARLA")
    parser.add_argument("--agent-package", type=Path, help="Also execute this trusted local agent package")
    parser.add_argument("--agent-python", type=Path)
    parser.add_argument("--agent-load-timeout", type=float, default=30., help="Additional agent's declared load deadline; default 30 seconds")
    parser.add_argument("--configuration", type=Path, help="Additional agent's configuration JSON")
    parser.add_argument("--model-file", type=Path, action="append", default=[])
    parser.add_argument("--seeds", type=int, nargs="+", default=[7, 11])
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--duration", type=float)
    parser.add_argument("--wait-timeout", type=float, default=180.)
    parser.add_argument("--carla-host", default="127.0.0.1")
    parser.add_argument("--carla-port", type=int, default=2000)
    args = parser.parse_args(argv)
    import math
    if not math.isfinite(args.agent_load_timeout) or args.agent_load_timeout <= 0:
        parser.error("Agent load timeout must be finite and positive")
    if not math.isfinite(args.wait_timeout) or args.wait_timeout <= 0 or args.duration is not None and (not math.isfinite(args.duration) or args.duration < .05):
        parser.error("Duration must be finite and at least .05s; wait timeout must be finite and positive")
    if not 1 <= args.repeats <= 10 or not 1 <= len(args.seeds) <= 20 or len(set(args.seeds)) != len(args.seeds) or any(s < 0 or s > 2**31 - 1 for s in args.seeds):
        parser.error("Choose 1–20 distinct valid seeds and 1–10 repetitions")
    if (args.agent_python or args.configuration or args.model_file or args.agent_load_timeout != 30.) and not args.agent_package:
        parser.error("Additional agent options require --agent-package")
    count = 2 + bool(args.sensors) + bool(args.agent_package)
    if count * len(args.seeds) * args.repeats > 200:
        parser.error("Plan exceeds 200 executions")
    directory = args.output.expanduser().absolute()
    try:
        directory.mkdir(exist_ok=False)
    except OSError as error:
        print(f"Choose a new output directory under an existing parent: {error}", file=sys.stderr)
        return 1
    interrupted = False
    try:
        report = validate(args, directory)
    except (Exception, KeyboardInterrupt) as error:
        interrupted = isinstance(error, KeyboardInterrupt)
        report = {"schema_version": "poltergeist-installation-validation-v1", "passed": False,
            "mode": args.mode, "error": f"{type(error).__name__}: {error}",
            "scope": "Failure retained for diagnosis. Inspect accepted-operation.json and isolated database; no submission was automatically retried."}
    write_json(directory / "validation.json", report)
    print(json.dumps({"passed": report["passed"], "report": str(directory / "validation.json"), "error": report.get("error")}, indent=2))
    return 130 if interrupted else 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
