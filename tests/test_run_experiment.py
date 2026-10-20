"""Experiment CLI lifecycle, provenance, uncertain acceptance, and file boundaries."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from io import BytesIO
import json
from urllib.error import HTTPError, URLError
from uuid import uuid4

import pytest

from scenarios import build_scenario, validate_scenario
from scripts import run_experiment as cli
from shared.models import Simulation, SimulationResult
from shared.models.api import JobInfo, SimulationRecord
from shared.models.telemetry import SafetyMetrics


def recipe(seed=17):
    return validate_scenario({"name": "experiment", "duration_s": 0.1, "seed": seed,
                              "actors": [{"type": "ego_vehicle", "speed_mps": 2.0}]}).model_dump(mode="json")


class FakeApi:
    base_url = "http://127.0.0.1:8000"

    def __init__(self, statuses=("completed",), *, failure=None):
        self.calls = []
        self.records = {}
        self.statuses = statuses
        self.failure = failure
        self.launches = []

    def request(self, method, path, payload=None, **kwargs):
        self.calls.append((method, path, deepcopy(payload), kwargs))
        if self.failure:
            result = self.failure(method, path, payload)
            if result is not None:
                return result
        if path == "/scenarios/generate":
            return build_scenario(payload["template"], seed=payload["seed"]).model_dump(mode="json")
        if path == "/scenarios/validate":
            return validate_scenario(payload).model_dump(mode="json")
        if path == "/simulations":
            self.launches.append(deepcopy(payload))
            now = datetime.now(UTC)
            simulation = Simulation(scenario=payload["scenario"], mode=payload["mode"], created_at=now)
            record = SimulationRecord(simulation=simulation, result=None, scenario_id=None, revision=1,
                                      created_at=now, updated_at=now,
                                      job=JobInfo(cancel_requested=False, queue_position=1, options=payload["options"],
                                                  evaluation_settings=payload["evaluation_settings"]))
            self.records[str(simulation.id)] = (record, len(self.launches) - 1)
            return record.model_dump(mode="json")
        simulation_id = path.split("/")[2]
        record, index = self.records[simulation_id]
        if path.endswith("/cancel"):
            record.job.cancel_requested = True
            return record.model_dump(mode="json")
        status = self.statuses[min(index, len(self.statuses) - 1)]
        simulation = record.simulation.model_dump(mode="python") | {
            "status": status, "progress": 1.0 if status == "completed" else 0.25,
            "started_at": record.created_at + timedelta(seconds=1),
            "finished_at": None if status == "running" else record.created_at + timedelta(seconds=2),
            "error": "adapter disconnected" if status == "failed" else None,
        }
        if path.endswith("/progress"):
            return {"simulation_id": simulation_id, "status": status, "progress": simulation["progress"],
                    "revision": 2, "started_at": simulation["started_at"], "finished_at": simulation["finished_at"],
                    "cancel_requested": False, "queue_position": None, "error": simulation["error"]}
        if path.endswith("/result"):
            result = SimulationResult(simulation=simulation, metrics=SafetyMetrics(
                collision_count=0, severity=None, evaluation_settings=record.job.evaluation_settings),
                telemetry_frames=1, measured_duration_s=0.05,
                runtime={"adapter": record.simulation.mode, "map_name": "Town03", "fixed_delta_seconds": 0.05,
                         "seed": record.simulation.scenario.seed})
            return result.model_dump(mode="json")
        raise AssertionError(f"Unexpected call {method} {path}")


@pytest.fixture
def scenario_file(tmp_path):
    path = tmp_path / "recipe.json"
    path.write_text(json.dumps(recipe()), encoding="utf-8")
    return path


def install(monkeypatch, api=None):
    api = api or FakeApi()
    monkeypatch.setattr(cli, "ApiClient", lambda *args: api)
    return api


def read_stdout(capsys):
    return json.loads(capsys.readouterr().out)


def test_batch_uses_api_sequentially_and_preserves_complete_null_provenance(monkeypatch, scenario_file, capsys):
    api = install(monkeypatch)
    assert cli.main(["--scenario", str(scenario_file), "--mode", "test", "--count", "2"]) == 0
    batch = read_stdout(capsys)
    assert batch["format"] == "poltergeist-experiment-v1"
    assert batch["status"] == "completed"
    assert [run["launch"]["scenario"]["seed"] for run in batch["runs"]] == [17, 18]
    assert [call[1] for call in api.calls[:2]] == ["/scenarios/validate"] * 2
    assert [path.rsplit("/", 1)[-1] for _, path, _, _ in api.calls[2:]] == [
        "simulations", "progress", "result", "simulations", "progress", "result"]
    for run in batch["runs"]:
        result = run["result"]
        assert run["submission"] == "accepted"
        assert result["simulation"]["mode"] == result["runtime"]["adapter"] == "test"
        assert result["metrics"]["severity"] is None
        assert result["metrics"]["min_ttc_s"] is None
        assert result["metrics"]["collision_count"] == 0
        assert result["metrics"]["coverage"]["total_frames"] == 0
        assert result["metrics"]["evaluation_settings"] == run["launch"]["evaluation_settings"]
        assert result["simulation"]["scenario"] == run["launch"]["scenario"]


def test_template_seed_generation_is_completed_before_submissions(monkeypatch, capsys):
    api = install(monkeypatch)
    assert cli.main(["--template", "sudden_braking", "--mode", "test", "--seed", "3", "--count", "2"]) == 0
    batch = read_stdout(capsys)
    assert [run["result"]["runtime"]["seed"] for run in batch["runs"]] == [3, 4]
    assert [call[1] for call in api.calls[:4]] == ["/scenarios/generate", "/scenarios/validate"] * 2


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_terminal_failure_is_preserved_and_remaining_batch_runs(monkeypatch, scenario_file, capsys, status):
    api = install(monkeypatch, FakeApi((status, "completed")))
    assert cli.main(["--scenario", str(scenario_file), "--mode", "carla", "--count", "2"]) == 1
    batch = read_stdout(capsys)
    assert batch["status"] == "finished_with_errors"
    assert batch["runs"][0]["result"]["simulation"]["status"] == status
    assert batch["runs"][1]["result"]["simulation"]["status"] == "completed"
    assert len(api.launches) == 2


def test_options_and_evaluation_settings_are_snapshotted(monkeypatch, scenario_file, tmp_path, capsys):
    install(monkeypatch)
    options = tmp_path / "options.json"
    options.write_text('{"max_ticks": 50, "allow_map_reload": true}', encoding="utf-8")
    evaluation = tmp_path / "evaluation.json"
    evaluation.write_text('{"speeding_tolerance_mps": 0.9}', encoding="utf-8")
    assert cli.main(["--scenario", str(scenario_file), "--mode", "test", "--options", str(options),
                     "--evaluation-settings", str(evaluation)]) == 0
    run = read_stdout(capsys)["runs"][0]
    assert run["launch"]["options"]["max_ticks"] == 50
    assert run["launch"]["options"]["allow_map_reload"] is True
    assert run["result"]["metrics"]["evaluation_settings"]["speeding_tolerance_mps"] == 0.9


@pytest.mark.parametrize("arguments", [
    ["--count", "0"], ["--count", "101"], ["--seed", "-1"],
    ["--seed", "2147483647", "--count", "2"], ["--request-timeout", "nan"],
    ["--wait-timeout", "0"], ["--poll-interval", "inf"], ["--force"],
])
def test_invalid_configuration_makes_no_api_calls(monkeypatch, scenario_file, arguments, capsys):
    api = install(monkeypatch)
    assert cli.main(["--scenario", str(scenario_file), "--mode", "test", *arguments]) == 2
    assert api.calls == []
    assert capsys.readouterr().out == ""


def test_explicit_mode_is_required(monkeypatch, scenario_file, capsys):
    api = install(monkeypatch)
    assert cli.main(["--scenario", str(scenario_file)]) == 2
    assert api.calls == []
    assert "--mode" in capsys.readouterr().err


@pytest.mark.parametrize("settings", ['{"camera":true}', '{"max_ticks":true}', '{"max_ticks":1,"max_ticks":2}', '{"sensor_timeout_s":NaN}'])
def test_invalid_settings_and_test_captures_are_rejected_before_requests(monkeypatch, scenario_file, tmp_path, settings):
    api = install(monkeypatch)
    options = tmp_path / "options.json"
    options.write_text(settings, encoding="utf-8")
    assert cli.main(["--scenario", str(scenario_file), "--mode", "test", "--options", str(options)]) == 2
    assert api.calls == []


def test_invalid_later_recipe_stops_whole_batch_before_submission(monkeypatch, scenario_file, capsys):
    calls = 0

    def failure(method, path, payload):
        nonlocal calls
        if path == "/scenarios/validate":
            calls += 1
            return payload if calls == 1 else {}

    api = install(monkeypatch, FakeApi(failure=failure))
    assert cli.main(["--scenario", str(scenario_file), "--mode", "test", "--count", "2"]) == 2
    assert api.launches == []
    assert "invalid validated scenario" in capsys.readouterr().err


def test_uncertain_acceptance_is_not_retried_or_cancelled_without_known_id(monkeypatch, scenario_file, capsys):
    def failure(method, path, payload):
        if path == "/simulations":
            raise cli.ApiError("network timeout", uncertain=True)

    api = install(monkeypatch, FakeApi(failure=failure))
    assert cli.main(["--scenario", str(scenario_file), "--mode", "test", "--count", "2"]) == 1
    output = capsys.readouterr()
    batch = json.loads(output.out)
    assert len(batch["runs"]) == 1
    assert batch["runs"][0]["submission"] == "uncertain"
    assert batch["runs"][0]["simulation_id"] is None
    assert "check run history before retrying" in output.err
    assert sum(path == "/simulations" for _, path, _, _ in api.calls) == 1
    assert not any(path.endswith("/cancel") for _, path, _, _ in api.calls)


def test_invalid_launch_receipt_is_uncertain_and_not_retried(monkeypatch, scenario_file, capsys):
    api = install(monkeypatch, FakeApi(failure=lambda method, path, payload: {} if path == "/simulations" else None))
    assert cli.main(["--scenario", str(scenario_file), "--mode", "test"]) == 1
    assert read_stdout(capsys)["runs"][0]["submission"] == "uncertain"
    assert sum(path == "/simulations" for _, path, _, _ in api.calls) == 1


def test_rejected_submission_is_distinct_from_uncertain(monkeypatch, scenario_file, capsys):
    def failure(method, path, payload):
        if path == "/simulations":
            raise cli.ApiError("HTTP 429; queue full")

    install(monkeypatch, FakeApi(failure=failure))
    assert cli.main(["--scenario", str(scenario_file), "--mode", "test"]) == 1
    assert read_stdout(capsys)["runs"][0]["submission"] == "rejected"


def test_poll_failure_preserves_known_run_and_does_not_resubmit(monkeypatch, scenario_file, capsys):
    def failure(method, path, payload):
        if path.endswith("/progress"):
            raise cli.ApiError("network unavailable")

    api = install(monkeypatch, FakeApi(failure=failure))
    assert cli.main(["--scenario", str(scenario_file), "--mode", "test", "--count", "2"]) == 1
    run = read_stdout(capsys)["runs"][0]
    assert run["submission"] == "accepted"
    assert run["simulation_id"] in api.records
    assert run["accepted_record"] is not None and run["result"] is None
    assert len(api.launches) == 1


def test_interrupt_requests_cancellation_only_for_own_known_job(monkeypatch, scenario_file, capsys):
    def failure(method, path, payload):
        if path.endswith("/progress"):
            raise KeyboardInterrupt

    api = install(monkeypatch, FakeApi(failure=failure))
    assert cli.main(["--scenario", str(scenario_file), "--mode", "test", "--count", "2"]) == 130
    batch = read_stdout(capsys)
    run = batch["runs"][0]
    assert batch["status"] == "interrupted"
    assert len(api.launches) == 1
    assert run["cancellation"]["job"]["cancel_requested"]
    assert [path for _, path, _, _ in api.calls if path.endswith("/cancel")] == [f"/simulations/{run['simulation_id']}/cancel"]


def test_interrupt_during_submission_does_not_guess_which_job_to_cancel(monkeypatch, scenario_file, capsys):
    def failure(method, path, payload):
        if path == "/simulations":
            raise KeyboardInterrupt

    api = install(monkeypatch, FakeApi(failure=failure))
    assert cli.main(["--scenario", str(scenario_file), "--mode", "test"]) == 130
    assert read_stdout(capsys)["runs"][0]["submission"] == "uncertain"
    assert not any(path.endswith("/cancel") for _, path, _, _ in api.calls)


def test_overall_wait_timeout_bounds_requests_and_leaves_job_in_history(monkeypatch, scenario_file, capsys):
    api = install(monkeypatch, FakeApi(("running",)))
    clock = [0.0]
    monkeypatch.setattr(cli.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(cli.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    assert cli.main(["--scenario", str(scenario_file), "--mode", "test", "--wait-timeout", "0.2", "--poll-interval", "0.1"]) == 1
    batch = read_stdout(capsys)
    assert "wait timeout" in batch["error"]
    assert batch["runs"][0]["submission"] == "accepted"
    assert batch["runs"][0]["result"] is None
    assert all(call[3]["timeout"] <= 0.2 for call in api.calls if "timeout" in call[3])
    assert not any(path.endswith("/cancel") for _, path, _, _ in api.calls)


def test_cross_run_progress_is_rejected(monkeypatch, scenario_file, capsys):
    api = FakeApi()
    original = api.request

    def request(method, path, payload=None, **kwargs):
        result = original(method, path, payload, **kwargs)
        if path.endswith("/progress"):
            result["simulation_id"] = str(uuid4())
        return result

    api.request = request
    install(monkeypatch, api)
    assert cli.main(["--scenario", str(scenario_file), "--mode", "test"]) == 1
    assert "different run" in read_stdout(capsys)["error"]


def test_output_new_file_is_atomic_and_existing_file_needs_force(monkeypatch, scenario_file, tmp_path, capsys):
    api = install(monkeypatch)
    output = tmp_path / "batch.json"
    args = ["--scenario", str(scenario_file), "--mode", "test", "--output", str(output)]
    assert cli.main(args) == 0
    first = output.read_text(encoding="utf-8")
    assert json.loads(first)["status"] == "completed"
    assert capsys.readouterr().out == ""
    count = len(api.calls)
    assert cli.main(args) == 2
    assert len(api.calls) == count and output.read_text(encoding="utf-8") == first
    assert cli.main([*args, "--force"]) == 0
    assert output.read_text(encoding="utf-8") != first
    assert not list(tmp_path.glob(".poltergeist-experiment-*.tmp"))


def test_output_never_overwrites_input_even_with_force(monkeypatch, scenario_file):
    api = install(monkeypatch)
    before = scenario_file.read_bytes()
    assert cli.main(["--scenario", str(scenario_file), "--mode", "test", "--output", str(scenario_file), "--force"]) == 2
    assert scenario_file.read_bytes() == before
    assert api.calls == []


def test_racing_output_writer_is_not_overwritten(tmp_path):
    output = tmp_path / "receipt.json"
    output.write_text("other writer", encoding="utf-8")
    with pytest.raises(FileExistsError):
        cli._write_output({"status": "completed"}, output, False)
    assert output.read_text(encoding="utf-8") == "other writer"
    assert not list(tmp_path.glob(".poltergeist-experiment-*.tmp"))


@pytest.mark.parametrize("url", ["file:///tmp/api", "http://user:password@localhost", "http://localhost?token=a",
                                  "http://localhost/#fragment", "http://localhost:99999", "http://localhost/ bad"])
def test_api_url_rejects_unsafe_or_invalid_values(url):
    with pytest.raises(cli.ExperimentError):
        cli.ApiClient(url, 1)


class Response:
    def __init__(self, data, *, status=200, headers=None):
        self.data = data
        self.status = status
        self.headers = {"Content-Type": "application/json"} if headers is None else headers

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, limit):
        return self.data[:limit]


class Opener:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def open(self, request, **kwargs):
        self.calls.append((request, kwargs))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_http_transport_bounds_timeout_and_keeps_api_path_prefix():
    client = cli.ApiClient("http://localhost:8000/api/", 3)
    client.opener = Opener(Response(b'{"ok":true}'))
    assert client.request("POST", "/scenarios/validate", {"hello": "world"}, timeout=0.2) == {"ok": True}
    request, options = client.opener.calls[0]
    assert request.full_url == "http://localhost:8000/api/scenarios/validate"
    assert json.loads(request.data) == {"hello": "world"}
    assert options["timeout"] == 0.2


@pytest.mark.parametrize("response", [Response(b"<html>server traceback secret</html>", status=202),
                                      Response(b'{"a":1,"a":2}', status=202),
                                      Response(b'{"a":NaN}', status=202), URLError("secret host diagnostic")])
def test_bad_transport_response_is_uncertain_for_launch_and_not_retried(response):
    client = cli.ApiClient("http://localhost:8000", 1)
    client.opener = Opener(response)
    with pytest.raises(cli.ApiError) as caught:
        client.request("POST", "/simulations", {}, expected=202)
    assert caught.value.uncertain
    assert "secret" not in str(caught.value)
    assert len(client.opener.calls) == 1


@pytest.mark.parametrize("method,path", [("POST", "/optimizations"), ("POST", "/agents"),
    ("PUT", "/scenarios/example"), ("PATCH", "/failures/example/reproduction"),
    ("DELETE", "/scenarios/example")])
def test_lost_mutation_responses_remain_uncertain_for_all_api_resources(method, path):
    client = cli.ApiClient("http://localhost:8000", 1)
    client.opener = Opener(URLError("connection lost after sending request"))
    with pytest.raises(cli.ApiError) as caught:
        client.request(method, path, {})
    assert caught.value.uncertain
    assert len(client.opener.calls) == 1


@pytest.mark.parametrize("status,uncertain", [(422, False), (429, False), (500, True), (503, True), (307, True)])
def test_http_error_status_distinguishes_rejection_and_unknown_acceptance(status, uncertain):
    client = cli.ApiClient("http://localhost:8000", 1)
    error = HTTPError("http://localhost:8000/simulations", status, "secret error", {}, BytesIO(b"secret body"))
    client.opener = Opener(error)
    with pytest.raises(cli.ApiError) as caught:
        client.request("POST", "/simulations", {}, expected=202)
    assert caught.value.uncertain is uncertain
    assert "secret" not in str(caught.value)
    assert error.closed
    assert error._closer.close_called


def test_http_error_closes_its_wrapper_without_reading_error_body():
    class UnreadBody(BytesIO):
        def read(self, *args):
            raise AssertionError("Error response bodies must not be read")

    client = cli.ApiClient("http://localhost:8000", 1)
    error = HTTPError("http://localhost:8000/simulations", 503, "unavailable", {}, UnreadBody(b"ignored"))
    client.opener = Opener(error)
    with pytest.raises(cli.ApiError, match="HTTP 503"):
        client.request("POST", "/simulations", {}, expected=202)
    assert error.closed
    assert error._closer.close_called


def test_transport_enforces_response_size_and_strict_status(monkeypatch):
    client = cli.ApiClient("http://localhost:8000", 1)
    monkeypatch.setattr(cli, "MAX_RESPONSE_BYTES", 4)
    client.opener = Opener(Response(b'{"long":true}'))
    with pytest.raises(cli.ApiError, match="exceeded"):
        client.request("GET", "/health")
    client.opener = Opener(Response(b"{}"))
    with pytest.raises(cli.ApiError, match="expected HTTP 202"):
        client.request("POST", "/simulations", {}, expected=202)


@pytest.mark.parametrize("headers,pattern", [
    ({"Content-Type": "text/html"}, "application/json"),
    ({}, "application/json"),
    ({"Content-Type": "application/json", "Content-Length": "4"}, "incomplete"),
    ({"Content-Type": "application/json", "Content-Length": "-1"}, "Content-Length"),
    ({"Content-Type": "application/json", "Content-Length": "secret"}, "Content-Length"),
    ({"Content-Type": "application/json", "Content-Length": "9" * 5000}, "Content-Length"),
    ({"Content-Type": "application/json", "Content-Length": str(cli.MAX_RESPONSE_BYTES + 1)}, "exceeded"),
])
def test_transport_rejects_misrouted_or_incomplete_bodies(headers, pattern):
    client = cli.ApiClient("http://localhost:8000", 1)
    client.opener = Opener(Response(b"{}", status=202, headers=headers))
    with pytest.raises(cli.ApiError, match=pattern) as caught:
        client.request("POST", "/simulations", {}, expected=202)
    assert caught.value.uncertain
    assert len(client.opener.calls) == 1


def test_transport_accepts_exact_content_length_and_json_charset():
    client = cli.ApiClient("http://localhost:8000", 1)
    client.opener = Opener(Response(b"{}", headers={"Content-Type": "application/json; charset=utf-8", "Content-Length": "2"}))
    assert client.request("GET", "/health") == {}
