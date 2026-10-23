"""Private S1 command surface. This is not the S2 companion or a public API."""

import argparse
from datetime import UTC, datetime
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path, PurePosixPath
import sys
import tempfile
from uuid import uuid4


def bundle_root():
    root = Path(sys.executable).resolve().parent.parent
    marker = root / "bundle.json"
    if not marker.is_file() or json.loads(marker.read_text(encoding="utf-8")).get("schema_version") != "poltergeist-portable-v1":
        raise ValueError("Run this command with the portable bundle's Poltergeist.cmd and private interpreter.")
    return root


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_bundle(root):
    """Detect damage/extra files. An unsigned manifest is not authentication."""
    root = Path(root).resolve()
    manifest = json.loads((root / "bundle.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "poltergeist-portable-v1" or not isinstance(manifest.get("files"), dict):
        raise ValueError("Unsupported or invalid portable manifest")
    for relative, digest in manifest["files"].items():
        lexical = PurePosixPath(relative)
        if lexical.is_absolute() or ".." in lexical.parts or "\\" in relative or ":" in relative:
            raise ValueError("Unsafe path in portable manifest")
        path = root / relative
        if not path.resolve().is_relative_to(root) or path.is_symlink() or not path.is_file() or file_sha256(path) != digest:
            raise ValueError(f"Portable integrity check failed: {relative}")
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    if actual != set(manifest["files"]) | {"bundle.json"}:
        raise ValueError("Portable bundle contains unexpected or missing files")
    return {"schema_version": "poltergeist-portable-integrity-v1", "passed": True, "files": len(manifest["files"]),
            "scope": "File integrity against the bundled unsigned manifest; not publisher authentication."}


def profile_path(value, root):
    if value is None:
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            raise ValueError("LOCALAPPDATA is unavailable; pass --data-dir with an absolute writable directory.")
        value = Path(local) / "Poltergeist" / "S1Prototype"
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("--data-dir must be an absolute path")
    path = path.resolve()
    if path.is_relative_to(Path(root).resolve()):
        raise ValueError("User data must be outside the installed portable bundle")
    return path


def local_settings(profile):
    from backend.config import Settings
    # Do not inherit developer environment configuration or the repository .env.
    for name in list(os.environ):
        if name.lower() in Settings.model_fields:
            os.environ.pop(name)
    config_path = profile / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    if not isinstance(config, dict) or set(config) - {"carla_executable", "carla_port"}:
        raise ValueError("config.json accepts only carla_executable and carla_port")
    if config.get("carla_executable") and not Path(config["carla_executable"]).is_absolute():
        raise ValueError("Configured CARLA executable must be an absolute path")
    return Settings(_env_file=None, storage_dir=profile / "storage",
        database_url=f"sqlite:///{(profile / 'database' / 'poltergeist.db').as_posix()}",
        api_host="127.0.0.1", carla_host="127.0.0.1", carla_auto_start=False, carla_quality="Low", **config)


def prepare_writes(profile):
    logs = profile / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    temporary = profile / "tmp"
    temporary.mkdir(exist_ok=True)
    os.environ["TEMP"] = os.environ["TMP"] = str(temporary)
    tempfile.tempdir = str(temporary)
    handler = RotatingFileHandler(logs / "portable.log", maxBytes=2 * 1024 * 1024, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger = logging.getLogger("poltergeist.portable")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return logger


def worker_smoke(root):
    from shared.models import ActorState, AgentObservation, AgentReset, Transform, Vector3
    from simulator.agents.process import ProcessAgent
    agent = ProcessAgent(root / "examples" / "agents" / "portable_probe", python_executable=sys.executable)
    results = []
    try:
        agent.load({})
        agent.reset(AgentReset(protocol_version="1.3", seed=7, map_name="PortableSDKProbe", ego_actor_id="ego", fixed_delta_seconds=.05))
        for frame in (0, 1):
            result = agent.step(AgentObservation(protocol_version="1.3", frame=frame, time_s=frame * .05, dt_s=.05,
                ego=ActorState(actor_id="ego", transform=Transform(), velocity_mps=Vector3(), acceleration_mps2=Vector3()),
                actors=[], target_speed_mps=2., route_heading_deg=0.))
            metadata = result.diagnostics.metadata
            if Path(metadata["python_executable"]).resolve() != Path(sys.executable).resolve():
                raise RuntimeError("SDK worker did not use the packaged real interpreter")
            if any(not Path(path).resolve().is_relative_to(root) for path in metadata["module_paths"]):
                raise RuntimeError("SDK worker imported first-party code outside the portable bundle")
            results.append(result.model_dump(mode="json"))
    finally:
        agent.close()
    return {"schema_version": "poltergeist-portable-worker-v1", "passed": True,
            "parent_executable": sys.executable, "worker_pid": agent.stats.worker_pid,
            "worker_python_version": agent.stats.python_version, "results": results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, help="Separate absolute profile; default LOCALAPPDATA/Poltergeist/S1Prototype")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("verify", "doctor", "capabilities", "worker-smoke"):
        commands.add_parser(name)
    configure = commands.add_parser("configure")
    configure.add_argument("--carla-executable", type=Path, required=True)
    configure.add_argument("--carla-port", type=int, default=2000)
    validate = commands.add_parser("validate")
    validate.add_argument("--mode", choices=("test", "carla"), default="test")
    validate.add_argument("--sensors", action="store_true")
    validate.add_argument("--duration", type=float, default=.15)
    validate.add_argument("--seeds", type=int, nargs="+", default=[7])
    args = parser.parse_args(argv)
    logger = None
    try:
        root = bundle_root()
        if args.command == "verify":
            print(json.dumps(verify_bundle(root), indent=2))
            return 0
        profile = profile_path(args.data_dir, root)
        settings = local_settings(profile)
        if args.command == "configure":
            executable = args.carla_executable.expanduser().resolve(strict=True)
            if not executable.is_file() or executable.suffix.lower() != ".exe" or not 1 <= args.carla_port <= 65535:
                raise ValueError("Select an existing CARLA .exe and a port from 1 to 65535")
            profile.mkdir(parents=True, exist_ok=True)
            config_path = profile / "config.json"
            with config_path.open("x", encoding="utf-8") as stream:
                json.dump({"carla_executable": str(executable), "carla_port": args.carla_port}, stream, indent=2)
            print(f"Created {config_path}. File presence does not verify simulator version; CARLA was not started.")
            return 0
        if args.command == "doctor":
            from backend.diagnostics import collect
            report = collect(settings=settings, development_tools=False)
            print(json.dumps(report, indent=2))
            return 0 if report["passed"] else 1
        if args.command == "capabilities":
            from backend.services.readiness_service import collect_readiness
            from shared.models.runner import RunnerCapabilities
            from importlib.metadata import version
            readiness = collect_readiness(settings)
            capabilities = RunnerCapabilities(checked_at=readiness.checked_at, implementation_version=version("poltergeist"),
                synthetic_status=readiness.synthetic.status, carla_status=readiness.carla.status)
            print(capabilities.model_dump_json(indent=2))
            return 0
        logger = prepare_writes(profile)
        # CARLA's native client can download map/navigation cache files relative
        # to the working directory. Keep those writes in the private profile too.
        os.chdir(profile)
        logger.info("Starting %s with private interpreter %s", args.command, sys.executable)
        evidence = profile / "evidence"
        evidence.mkdir(exist_ok=True)
        if args.command == "worker-smoke":
            report = worker_smoke(root)
            path = evidence / f"worker-{uuid4()}.json"
            path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"passed": True, "report": str(path)}, indent=2))
            return 0
        if args.mode == "carla":
            from backend.diagnostics import carla_probe
            before = carla_probe(sys.executable, settings.carla_host, settings.carla_port, timeout=3., include_map=False)
            if before["synchronous_mode"]:
                raise ValueError("CARLA is already synchronous; use an idle dedicated world without another tick owner.")
        # Existing validator checks actors, preserves accepted IDs, exports and
        # reopens evidence, and restores the existing map/settings after live runs.
        from scripts.validate_installation import main as validate_installation
        output = evidence / f"{args.mode}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
        code = validate_installation(["--mode", args.mode, "--examples-dir", str(root / "examples"),
            "--output", str(output), "--duration", str(args.duration), "--seeds", *map(str, args.seeds),
            "--carla-host", settings.carla_host, "--carla-port", str(settings.carla_port),
            *(["--sensors"] if args.sensors else [])])
        logger.info("Validation exit=%s evidence=%s", code, output)
        return code
    except Exception as error:
        if logger:
            logger.exception("Portable command failed")
        print(f"Portable command failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
