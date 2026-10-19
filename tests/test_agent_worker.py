"""Exercise the child transport independently of the parent process client."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from shared.models import ActorState, AgentObservation, AgentReset
from simulator.agents.package import inspect_agent_package
from simulator.agents.process_group import AgentProcessGroup
from simulator.agents.process_protocol import (
    AgentProcessRequest, AgentProcessResponse, AgentWorkerError,
    decode_request, decode_response, encode_message,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = '''import os
from pathlib import Path
import sys
from simulator.agents.sdk import AutonomousAgent
print("agent import noise")
print("original stdout noise", file=sys.__stdout__)

class Driver(AutonomousAgent):
    def __init__(self):
        print("agent constructor noise")
        self.configuration = {}

    def check(self, operation):
        print("agent " + operation + " noise")
        if self.configuration.get("fail_on") == operation:
            raise RuntimeError(operation + " failed")

    def load(self, configuration):
        self.configuration = configuration
        self.check("load")

    def reset(self, context):
        self.check("reset")

    def step(self, observation):
        self.check("step")
        return {"frame": observation.frame, "control": {
            "throttle": self.configuration.get("throttle", 0.4)},
            "diagnostics": {"metadata": {"worker_pid": os.getpid()}}}

    def close(self):
        if "cleanup_file" in self.configuration:
            with Path(self.configuration["cleanup_file"]).open("a", encoding="utf-8") as target:
                target.write("closed\\n")
        self.check("close")
        if self.configuration.get("fail_close"):
            raise RuntimeError("cleanup failed")
'''


def package(tmp_path, **configuration):
    directory = tmp_path / "agent"
    directory.mkdir()
    (directory / "agent.json").write_text(json.dumps({
        "agent_id": "worker-test", "name": "Worker test", "version": "1",
        "entry_point": "agent:Driver", "configuration": configuration,
    }), encoding="utf-8")
    (directory / "agent.py").write_text(SOURCE, encoding="utf-8")
    return inspect_agent_package(directory)


def request(request_id, operation, payload=None):
    return AgentProcessRequest(
        protocol_version="1.0", request_id=request_id, operation=operation, payload=payload or {},
    )


def load(package, request_id=0):
    return request(request_id, "load", {"manifest": package.manifest.model_dump(mode="json")})


def reset(request_id=1):
    return request(request_id, "reset", AgentReset(
        seed=0, map_name="Town03", ego_actor_id="ego", fixed_delta_seconds=0.05,
    ).model_dump(mode="json"))


def step(request_id=2, **changes):
    values = {
        "frame": 10, "time_s": 0.0, "dt_s": 0.05,
        "ego": ActorState(actor_id="ego"), "actors": [], "target_speed_mps": 12.0,
    }
    return request(request_id, "step", AgentObservation(**(values | changes)).model_dump(mode="json"))


def run_worker(package, requests=(), *, raw=None, limit=None):
    command = [sys.executable, "-m", "simulator.agents.worker", "--package", str(package.directory)]
    if limit is not None:
        command.extend(["--max-message-bytes", str(limit)])
    completed = subprocess.run(
        command, cwd=ROOT, input=raw if raw is not None else b"".join(map(encode_message, requests)),
        capture_output=True, timeout=15,
    )
    responses = [decode_response(line) for line in completed.stdout.splitlines()]
    return completed, responses


def test_protocol_round_trip_counts_utf8_and_line_terminator():
    message = request(0, "load", {"name": "\u00e9"})
    encoded = encode_message(message)
    assert encoded.endswith(b"\n")
    assert decode_request(encoded) == message
    assert encode_message(message, len(encoded)) == encoded
    with pytest.raises(ValueError, match="exceeds"):
        encode_message(message, len(encoded) - 1)
    with pytest.raises(ValueError, match="exceeds"):
        decode_request(encoded, len(encoded) - 1)


@pytest.mark.parametrize("change", [
    {"protocol_version": "2.0"}, {"protocol_version": None}, {"request_id": True},
    {"request_id": -1}, {"request_id": "0"}, {"operation": "tick"},
    {"payload": []}, {"payload": {"nested": float("inf")}}, {"extra": 1},
])
def test_requests_reject_invalid_envelopes(change):
    payload = request(0, "load").model_dump(mode="json") | change
    with pytest.raises(ValueError):
        decode_request(json.dumps(payload))


@pytest.mark.parametrize("raw", [
    b'{"protocol_version":"1.0","request_id":0,"operation":"close","payload":{},"request_id":1}\n',
    b'{"protocol_version":"1.0","request_id":0,"operation":"load","payload":{"x":1,"x":2}}\n',
    b'{"protocol_version":"1.0","request_id":0,"operation":"load","payload":{"x":NaN}}\n',
    b'{"protocol_version":"1.0","request_id":0,"operation":"load","payload":{"x":1e999}}\n',
    b'{\n"protocol_version":"1.0"}\n', b'{}\n{}\n', b'\xff\n', b'[]\n',
    b'{"request_id":0,"operation":"close","payload":{}}\n',
])
def test_decoders_reject_ambiguous_json(raw):
    with pytest.raises(ValueError):
        decode_request(raw)


@pytest.mark.parametrize("ok,error", [(True, {"type": "Error", "message": "bad"}), (False, None)])
def test_responses_require_consistent_error_status(ok, error):
    with pytest.raises(ValueError):
        AgentProcessResponse(protocol_version="1.0", request_id=0, operation="load", ok=ok, error=error)


def test_encode_revalidates_mutated_payloads():
    message = request(0, "load")
    message.payload["invalid"] = (1, 2)
    with pytest.raises(ValueError):
        encode_message(message)


def test_worker_runs_lifecycle_in_distinct_process_with_clean_stdout_and_no_carla(tmp_path):
    cleanup_file = tmp_path / "closed.txt"
    agent = package(tmp_path, cleanup_file=str(cleanup_file))
    with (agent.directory / "agent.py").open("a", encoding="utf-8") as target:
        target.write("\nassert 'carla' not in sys.modules\n")
    completed, responses = run_worker(agent, [load(agent), reset(), step(), request(3, "close")])
    assert completed.returncode == 0, completed.stderr.decode()
    assert [item.request_id for item in responses] == [0, 1, 2, 3]
    assert all(item.ok for item in responses)
    assert responses[0].payload["worker_pid"] != os.getpid()
    assert responses[0].payload["python_version"]
    result = responses[2].payload
    assert result["result"]["frame"] == 10
    assert result["result"]["control"]["throttle"] == 0.4
    assert result["result"]["diagnostics"]["metadata"]["worker_pid"] == responses[0].payload["worker_pid"]
    assert result["worker_step_wall_time_s"] >= 0
    assert b"agent import noise" in completed.stderr
    assert b"original stdout noise" in completed.stderr
    assert b"agent close noise" in completed.stderr
    assert cleanup_file.read_text(encoding="utf-8") == "closed\n"


def test_worker_defers_agent_import_until_load(tmp_path):
    agent = package(tmp_path)
    (agent.directory / "agent.py").write_text("raise RuntimeError('must not import')\n", encoding="utf-8")
    completed, responses = run_worker(agent)
    assert completed.returncode == 0
    assert responses == []
    assert completed.stderr == b""


def test_hello_identifies_worker_without_importing_agent_code(tmp_path):
    agent = package(tmp_path)
    (agent.directory / "agent.py").write_text("raise RuntimeError('must not import')\n", encoding="utf-8")
    completed, responses = run_worker(agent, [request(0, "hello"), request(1, "close")])
    assert completed.returncode == 0
    assert [response.operation for response in responses] == ["hello", "close"]
    assert all(response.ok for response in responses)
    assert responses[0].payload["worker_pid"] > 0
    assert responses[0].payload["worker_pid"] != os.getpid()
    assert responses[0].payload["python_version"]
    assert completed.stderr == b""


def test_hello_and_load_report_same_worker_identity(tmp_path):
    agent = package(tmp_path)
    completed, responses = run_worker(agent, [request(0, "hello"), load(agent, 1), request(2, "close")])
    assert completed.returncode == 0
    assert responses[0].payload == responses[1].payload


@pytest.mark.parametrize("requests", [
    lambda agent: [request(0, "hello"), request(1, "hello")],
    lambda agent: [load(agent), request(1, "hello")],
    lambda agent: [request(0, "hello", {"unexpected": True})],
])
def test_hello_is_empty_and_allowed_only_once_before_load(tmp_path, requests):
    agent = package(tmp_path)
    completed, responses = run_worker(agent, requests(agent))
    assert completed.returncode == 1
    assert responses[-1].operation == "hello"
    assert not responses[-1].ok
    assert responses[-1].error.type == "AgentProtocolError"


def test_worker_rejects_manifest_changes_before_import(tmp_path):
    agent = package(tmp_path)
    message = load(agent)
    message.payload["manifest"]["name"] = "different"
    completed, responses = run_worker(agent, [message])
    assert completed.returncode == 1
    assert len(responses) == 1
    assert "manifest does not match" in responses[0].error.message
    assert b"agent import noise" not in completed.stderr


def test_manifest_identity_is_type_sensitive_for_json_configuration(tmp_path):
    agent = package(tmp_path, flag=True)
    message = load(agent)
    message.payload["manifest"]["configuration"]["flag"] = 1
    completed, responses = run_worker(agent, [message])
    assert completed.returncode == 1
    assert "manifest does not match" in responses[0].error.message
    assert b"agent import noise" not in completed.stderr


@pytest.mark.parametrize("operation", ["load", "reset", "step"])
def test_agent_failures_are_returned_and_cleanup_runs_once(tmp_path, operation):
    cleanup_file = tmp_path / "closed.txt"
    agent = package(tmp_path, cleanup_file=str(cleanup_file), fail_on=operation)
    requests = [load(agent), reset(), step(), request(3, "close")]
    completed, responses = run_worker(agent, requests)
    assert completed.returncode == 1
    assert not responses[-1].ok
    assert responses[-1].operation == operation
    assert responses[-1].error.type == "RuntimeError"
    assert responses[-1].error.message == operation + " failed"
    assert cleanup_file.read_text(encoding="utf-8") == "closed\n"


def test_cleanup_failure_preserves_primary_failure(tmp_path):
    cleanup_file = tmp_path / "closed.txt"
    agent = package(tmp_path, cleanup_file=str(cleanup_file), fail_on="step", fail_close=True)
    completed, responses = run_worker(agent, [load(agent), reset(), step()])
    assert completed.returncode == 1
    assert responses[-1].error.type == "RuntimeError"
    assert "step failed" in responses[-1].error.message
    assert "Agent cleanup also failed: RuntimeError: cleanup failed" in responses[-1].error.message
    assert cleanup_file.read_text(encoding="utf-8") == "closed\n"


def test_normal_close_failure_is_an_error(tmp_path):
    agent = package(tmp_path, fail_close=True)
    completed, responses = run_worker(agent, [load(agent), request(1, "close")])
    assert completed.returncode == 1
    assert not responses[-1].ok
    assert responses[-1].operation == "close"
    assert responses[-1].error.message == "cleanup failed"


@pytest.mark.parametrize("requests", [
    lambda agent: [reset()],
    lambda agent: [load(agent), step()],
    lambda agent: [load(agent), load(agent, 1)],
    lambda agent: [load(agent), reset(0)],
    lambda agent: [load(agent), reset(), step(), step(3, frame=12, time_s=0.05)],
    lambda agent: [load(agent), reset(), step(2, ego=ActorState(actor_id="wrong"))],
])
def test_worker_enforces_lifecycle_request_ids_and_frame_alignment(tmp_path, requests):
    agent = package(tmp_path)
    completed, responses = run_worker(agent, requests(agent))
    assert completed.returncode == 1
    assert not responses[-1].ok
    assert responses[-1].error.type in {"AgentLifecycleError", "AgentProtocolError"}


def test_invalid_agent_controls_are_rejected(tmp_path):
    agent = package(tmp_path, throttle=3.0)
    completed, responses = run_worker(agent, [load(agent), reset(), step()])
    assert completed.returncode == 1
    assert responses[-1].error.type == "AgentProtocolError"
    assert "throttle" in responses[-1].error.message


def test_input_eof_closes_loaded_agent_once(tmp_path):
    cleanup_file = tmp_path / "closed.txt"
    agent = package(tmp_path, cleanup_file=str(cleanup_file))
    completed, responses = run_worker(agent, [load(agent)])
    assert completed.returncode == 0
    assert len(responses) == 1
    assert cleanup_file.read_text(encoding="utf-8") == "closed\n"


@pytest.mark.parametrize("raw", [b"x" * 201 + b"\n", b"{}", b"{}\n"])
def test_worker_exits_on_malformed_or_oversized_input_without_guessing_identity(tmp_path, raw):
    agent = package(tmp_path)
    completed, responses = run_worker(agent, raw=raw, limit=200)
    assert completed.returncode == 1
    assert responses == []
    assert completed.stderr


def test_response_error_round_trip():
    response = AgentProcessResponse(
        protocol_version="1.0", request_id=4, operation="step", ok=False,
        error=AgentWorkerError(type="RuntimeError", message="model failed"),
    )
    assert decode_response(encode_message(response)) == response


@pytest.mark.skipif(os.name != "nt", reason="Windows suspended launcher and job ownership")
def test_windows_suspended_launch_owns_early_descendants_before_resuming(tmp_path):
    import ctypes
    from ctypes import wintypes

    marker = tmp_path / "descendant.txt"
    script = (
        "import subprocess,sys,time; from pathlib import Path; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)']); "
        f"Path({str(marker)!r}).write_text(str(child.pid)); time.sleep(120)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script], cwd=ROOT,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW | 0x00000004,
    )
    group = None
    descendant_pid = None
    try:
        group = AgentProcessGroup(process)
        assert not marker.exists()
        group.resume()
        with pytest.raises(RuntimeError, match="already been resumed"):
            group.resume()
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists(), "The suspended worker did not resume"
        descendant_pid = int(marker.read_text())
    finally:
        if group is not None:
            group.terminate()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    deadline = time.monotonic() + 3
    while True:
        handle = kernel.OpenProcess(0x1000, False, descendant_pid)
        if not handle:
            break
        try:
            exit_code = wintypes.DWORD()
            assert kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            alive = exit_code.value == 259
        finally:
            kernel.CloseHandle(handle)
        if not alive:
            break
        assert time.monotonic() < deadline, "Early worker descendant survived job cleanup"
        time.sleep(0.02)
