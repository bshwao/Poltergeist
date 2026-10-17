"""Submit reproducible, sequential experiment batches to the existing API worker."""

import argparse
from datetime import UTC, datetime
from http.client import HTTPException
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import ValidationError

from scenarios import load_scenario, validate_scenario
from shared.models import Scenario, ScenarioTemplate, SimulationResult, SimulationStatus
from shared.models.api import SimulationLaunch, SimulationProgress, SimulationRecord
from shared.models.evaluation import EvaluationSettings
from shared.models.runtime import SimulationOptions


MAX_SEED = 2**31 - 1
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
TERMINAL = {SimulationStatus.COMPLETED, SimulationStatus.FAILED, SimulationStatus.CANCELLED}


class ExperimentError(ValueError):
    """An actionable configuration, API, or lifecycle error."""


class ApiError(ExperimentError):
    def __init__(self, message, *, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ExperimentError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _constant(value):
    raise ExperimentError(f"Non-finite JSON number {value} is not supported")


def _json(text):
    return json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        return None


class ApiClient:
    """Bounded HTTP JSON calls, with no redirects or implicit mutation retries."""

    def __init__(self, api_url, timeout):
        try:
            parts = urlsplit(api_url)
            port = parts.port
        except ValueError as exc:
            raise ExperimentError("--api-url must be a valid HTTP(S) URL") from exc
        if (parts.scheme not in {"http", "https"} or not parts.hostname
                or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment or "\\" in api_url
                or any(character.isspace() or ord(character) < 32 for character in api_url)):
            raise ExperimentError("--api-url must be HTTP(S), without credentials, query, or fragment")
        if port is not None and not 1 <= port <= 65535:
            raise ExperimentError("--api-url has an invalid port")
        self.base_url = api_url.rstrip("/")
        self.timeout = timeout
        self.opener = build_opener(_NoRedirect())

    def request(self, method, path, payload=None, *, expected=200, timeout=None):
        data = None if payload is None else json.dumps(payload, allow_nan=False).encode("utf-8")
        request = Request(self.base_url + path, data=data, method=method,
                          headers={"Accept": "application/json", "Content-Type": "application/json"})
        mutation = method == "POST" and path.startswith("/simulations")
        try:
            with self.opener.open(request, timeout=self.timeout if timeout is None else timeout) as response:
                if response.status != expected:
                    raise ApiError(f"{method} {path}: expected HTTP {expected}, received {response.status}",
                                   uncertain=mutation)
                media_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if media_type != "application/json":
                    raise ApiError(f"{method} {path}: API response must use application/json; check --api-url and proxy configuration",
                                   uncertain=mutation)
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    if not content_length.isascii() or not content_length.isdecimal() or len(content_length) > 20:
                        raise ApiError(f"{method} {path}: API returned an invalid Content-Length", uncertain=mutation)
                    content_length = int(content_length)
                    if content_length > MAX_RESPONSE_BYTES:
                        raise ApiError(f"{method} {path}: response exceeded 16 MiB", uncertain=mutation)
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ApiError(f"{method} {path}: response exceeded 16 MiB", uncertain=mutation)
                if content_length is not None and len(raw) != content_length:
                    raise ApiError(f"{method} {path}: API response body was incomplete", uncertain=mutation)
                try:
                    return _json(raw.decode("utf-8"))
                except (ValueError, UnicodeError) as exc:
                    raise ApiError(f"{method} {path}: API returned invalid JSON", uncertain=mutation) from exc
        except HTTPError as exc:
            try:
                hint = {404: "resource was not found", 409: "resource is not ready or conflicts",
                        422: "request was rejected by validation", 429: "worker queue is full",
                        503: "worker or persistence is unavailable"}.get(exc.code, "request was rejected")
                raise ApiError(f"{method} {path}: HTTP {exc.code}; {hint}",
                               uncertain=mutation and (exc.code >= 500 or 300 <= exc.code < 400)) from exc
            finally:
                # HTTPError owns an open response wrapper even when its body is
                # ignored. Close the wrapper itself, not only its underlying fp.
                exc.close()
        except (URLError, OSError, TimeoutError, HTTPException, UnicodeError) as exc:
            raise ApiError(f"{method} {path}: could not reach API or request timed out; check --api-url and API logs",
                           uncertain=mutation) from exc


def _contract(model, payload, label):
    try:
        return model.model_validate(payload)
    except (ValidationError, TypeError, ValueError) as exc:
        raise ExperimentError(f"API returned an invalid {label}; check API/client versions") from exc


def _settings(path, model):
    if path is None:
        return model()
    source = Path(path)
    try:
        if source.stat().st_size > 1024 * 1024:
            raise ExperimentError(f"{source}: configuration exceeds 1 MiB")
        payload = _json(source.read_text(encoding="utf-8-sig"))
        return model.model_validate(payload)
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        if isinstance(exc, ValidationError):
            fields = ", ".join(".".join(map(str, item["loc"])) or "root" for item in exc.errors())
            raise ExperimentError(f"{source}: invalid {model.__name__} fields: {fields}") from exc
        raise ExperimentError(f"{source}: could not read valid JSON configuration ({type(exc).__name__})") from exc


def _positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return number


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--scenario", help="Validated YAML or JSON scenario file")
    source.add_argument("--template", choices=[item.value for item in ScenarioTemplate], help="Bundled template generated through the API")
    parser.add_argument("--mode", choices=["carla", "test"], required=True, help="Explicit execution mode; test mode is synthetic")
    parser.add_argument("--count", type=int, default=1, help="Sequential runs, 1 to 100 (default: 1)")
    parser.add_argument("--seed", type=int, help="Initial seed; incremented once per run (default: recipe seed or 0)")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--options", help="SimulationOptions JSON file")
    parser.add_argument("--evaluation-settings", help="EvaluationSettings JSON file")
    parser.add_argument("--request-timeout", type=_positive, default=10.0, help="Seconds per HTTP request (default: 10)")
    parser.add_argument("--wait-timeout", type=_positive, default=300.0, help="Overall batch execution wait in seconds (default: 300)")
    parser.add_argument("--poll-interval", type=_positive, default=0.5, help="Seconds between progress checks (default: 0.5)")
    parser.add_argument("--output", help="New JSON receipt/result file; stdout when omitted")
    parser.add_argument("--force", action="store_true", help="Allow replacing an existing --output file")
    return parser


def _output_path(args):
    if args.force and not args.output:
        raise ExperimentError("--force requires --output")
    if not args.output:
        return None
    output = Path(args.output).absolute()
    if not output.parent.is_dir():
        raise ExperimentError(f"Output directory does not exist: {output.parent}")
    if output.exists() and (not args.force or not output.is_file()):
        raise ExperimentError(f"Output already exists or is not a file: {output}; choose a new file or use --force")
    for source in (args.scenario, args.options, args.evaluation_settings):
        if source is not None and output.resolve() == Path(source).resolve():
            raise ExperimentError("Output must differ from scenario and settings input files")
    return output


def _write_output(batch, output, force):
    text = json.dumps(batch, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if output is None:
        sys.stdout.write(text)
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=output.parent,
                                         prefix=".poltergeist-experiment-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if force:
            os.replace(temporary, output)
        elif os.name == "nt":
            # Windows rename refuses an existing destination and is atomic on
            # the same volume. Hard links can be restricted in local sandboxes.
            os.rename(temporary, output)
        else:
            # POSIX rename replaces existing files; link provides no-clobber.
            os.link(temporary, output)
        print(f"Wrote experiment receipt: {output}", file=sys.stderr)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _prepare(args, client):
    if not 1 <= args.count <= 100:
        raise ExperimentError("--count must be between 1 and 100")
    options = _settings(args.options, SimulationOptions)
    evaluation = _settings(args.evaluation_settings, EvaluationSettings)
    if args.mode == "test" and (options.camera or options.lidar):
        raise ExperimentError("Camera and LiDAR capture require --mode carla")
    scenario = load_scenario(args.scenario) if args.scenario else None
    seed = args.seed if args.seed is not None else (scenario.seed if scenario else 0)
    if not 0 <= seed <= MAX_SEED or seed + args.count - 1 > MAX_SEED:
        raise ExperimentError("Seeds must remain between 0 and 2147483647 for the entire batch")
    launches = []
    for index in range(args.count):
        if scenario is None:
            payload = client.request("POST", "/scenarios/generate", {"template": args.template, "seed": seed + index})
            recipe = _contract(Scenario, payload, "generated scenario")
            if recipe.template != args.template or recipe.seed != seed + index:
                raise ExperimentError("API generated a different template or seed than requested")
        else:
            recipe = validate_scenario(scenario.model_dump(mode="python") | {"seed": seed + index})
        launch = SimulationLaunch(scenario=recipe, mode=args.mode, options=options, evaluation_settings=evaluation)
        validated = _contract(Scenario, client.request("POST", "/scenarios/validate", recipe.model_dump(mode="json")), "validated scenario")
        if validated != recipe:
            raise ExperimentError("API normalized the recipe differently; update the client before launching")
        launches.append(launch.model_dump(mode="json", exclude={"scenario_id"}))
    return launches


def _match_simulation(simulation, simulation_id, launch):
    if (simulation.id != simulation_id or simulation.mode != launch["mode"]
            or simulation.scenario.model_dump(mode="json") != launch["scenario"]):
        raise ExperimentError("API returned a different simulation, recipe, or mode; inspect run history")


def _execute(args, client, launches, batch):
    deadline = time.monotonic() + args.wait_timeout
    active_id = None
    active_entry = None

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise ExperimentError("Batch wait timeout reached; accepted runs continue in the API. Check run history using their IDs")
        return min(args.request_timeout, value)

    try:
        for launch in launches:
            timeout = remaining()
            entry = {"launch": launch, "submission": "submitting", "simulation_id": None,
                     "accepted_record": None, "progress": None, "result": None, "error": None}
            batch["runs"].append(entry)
            active_entry = entry
            try:
                payload = client.request("POST", "/simulations", launch, expected=202, timeout=timeout)
                record = _contract(SimulationRecord, payload, "launch receipt")
                active_id = record.simulation.id
                entry["simulation_id"] = str(active_id)
                entry["submission"] = "accepted"
                entry["accepted_record"] = record.model_dump(mode="json")
                _match_simulation(record.simulation, active_id, launch)
                if record.job is None or record.job.options.model_dump(mode="json") != launch["options"] or record.job.evaluation_settings.model_dump(mode="json") != launch["evaluation_settings"]:
                    raise ExperimentError("API launch receipt has different execution/evaluation settings; inspect run history")
            except ApiError as exc:
                entry["submission"] = "uncertain" if exc.uncertain else "rejected"
                if exc.uncertain:
                    raise ExperimentError(f"{exc}. Acceptance is uncertain; check run history before retrying to avoid duplicate runs") from exc
                raise
            except ExperimentError:
                if entry["submission"] == "submitting":
                    entry["submission"] = "uncertain"
                    raise ExperimentError("Launch receipt was invalid. Acceptance is uncertain; check run history before retrying")
                raise
            print(f"Accepted {active_id} ({launch['mode']}, seed {launch['scenario']['seed']})", file=sys.stderr)
            while True:
                progress = _contract(SimulationProgress, client.request("GET", f"/simulations/{active_id}/progress", timeout=remaining()), "progress response")
                if progress.simulation_id != active_id:
                    raise ExperimentError("API progress belongs to a different run")
                entry["progress"] = progress.model_dump(mode="json")
                if progress.status in TERMINAL:
                    result = _contract(SimulationResult, client.request("GET", f"/simulations/{active_id}/result", timeout=remaining()), "simulation result")
                    _match_simulation(result.simulation, active_id, launch)
                    if result.simulation.status != progress.status:
                        raise ExperimentError("API result and progress statuses differ")
                    if result.metrics.evaluation_settings is not None and result.metrics.evaluation_settings.model_dump(mode="json") != launch["evaluation_settings"]:
                        raise ExperimentError("API result uses different evaluation settings")
                    entry["result"] = result.model_dump(mode="json")
                    active_id = None
                    print(f"Run {entry['simulation_id']}: {result.simulation.status}", file=sys.stderr)
                    break
                time.sleep(min(args.poll_interval, max(0, deadline - time.monotonic())))
    except KeyboardInterrupt:
        batch["status"] = "interrupted"
        batch["error"] = "Interrupted; no additional runs were submitted"
        if active_entry is not None and active_entry["submission"] == "submitting":
            active_entry["submission"] = "uncertain"
            active_entry["error"] = "Interrupted while submitting; check run history before retrying"
            print("Interrupted during submission; acceptance is uncertain. Check run history before retrying", file=sys.stderr)
        if active_id is not None:
            try:
                cancelled = _contract(SimulationRecord, client.request("POST", f"/simulations/{active_id}/cancel", {}, expected=202), "cancellation receipt")
                _match_simulation(cancelled.simulation, active_id, active_entry["launch"])
                active_entry["cancellation"] = cancelled.model_dump(mode="json")
                print(f"Cancellation requested for {active_id}; finalization is cooperative. Check run history for its persisted result", file=sys.stderr)
            except (ExperimentError, KeyboardInterrupt) as exc:
                active_entry["cancellation_error"] = str(exc) or "Cancellation interrupted; check run history"
                print(f"Could not confirm cancellation of {active_id}; check run history", file=sys.stderr)
        return 130
    except ExperimentError as exc:
        batch["status"] = "error"
        batch["error"] = str(exc)
        if active_entry is not None:
            active_entry["error"] = str(exc)
        print(f"Experiment error: {exc}", file=sys.stderr)
        return 1
    batch["status"] = "completed" if all(run["result"]["simulation"]["status"] == "completed" for run in batch["runs"]) else "finished_with_errors"
    return 0 if batch["status"] == "completed" else 1


def main(argv=None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    try:
        output = _output_path(args)
        client = ApiClient(args.api_url, args.request_timeout)
        launches = _prepare(args, client)
    except KeyboardInterrupt:
        print("Interrupted before any simulations were submitted", file=sys.stderr)
        return 130
    except ApiError as exc:
        print(f"API error before submission: {exc}", file=sys.stderr)
        return 1
    except (ExperimentError, OSError, ValueError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    batch = {"format": "poltergeist-experiment-v1", "api_url": client.base_url,
             "created_at": datetime.now(UTC).isoformat(), "mode": args.mode,
             "requested_runs": args.count, "status": "running", "error": None, "runs": []}
    code = _execute(args, client, launches, batch)
    batch["finished_at"] = datetime.now(UTC).isoformat()
    try:
        _write_output(batch, output, args.force)
    except (OSError, ValueError) as exc:
        print(f"Could not write experiment receipt ({type(exc).__name__}); accepted run IDs remain available in API history", file=sys.stderr)
        for entry in batch["runs"]:
            if entry["simulation_id"] is not None:
                print(f"Run: {entry['simulation_id']}", file=sys.stderr)
        return 1 if code == 0 else code
    return code


if __name__ == "__main__":
    raise SystemExit(main())
