"""Exercise actual external-agent processes without CARLA or a model runtime."""

from dataclasses import FrozenInstanceError
import ctypes
import json
import os
from pathlib import Path
import sys
from threading import Event, Timer
import time

import pytest

from shared.models import ActorState, AgentObservation, AgentReset
from simulator.agents.process import (
    AgentProcessCancelled,
    AgentProcessError,
    AgentProcessOptions,
    AgentTimeoutError,
    ProcessAgent,
)
from simulator.agents.sdk import AgentLifecycleError, AgentProtocolError, AgentSDKError
from simulator.errors import SimulationCancelled


SOURCE = '''import os
from pathlib import Path
import subprocess
import sys
import time

from simulator.agents.sdk import AutonomousAgent

class Driver(AutonomousAgent):
    def __init__(self):
        self.configuration = {}

    def operation(self, name):
        if self.configuration.get("pid_file"):
            Path(self.configuration["pid_file"]).write_text(str(os.getpid()))
        if self.configuration.get("hang") == name:
            while True:
                time.sleep(1)
        if self.configuration.get("fail") == name:
            raise RuntimeError("deliberate " + name + " failure")
        if self.configuration.get("exit") == name:
            os._exit(37)

    def load(self, configuration):
        self.configuration = configuration
        self.operation("load")
        if configuration.get("descendant_file"):
            subprocess.Popen([
                sys.executable, "-c",
                "import os,sys,time; from pathlib import Path; "
                "p=Path(sys.argv[1] + '.tmp'); p.write_text(str(os.getpid())); "
                "p.replace(sys.argv[1]); time.sleep(120)",
                configuration["descendant_file"],
            ])
            # Record the actual interpreter PID, including on Windows where
            # the virtual-environment executable can itself be a redirector.
            deadline = time.monotonic() + 5.0
            while not Path(configuration["descendant_file"]).exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError("descendant did not start")
                time.sleep(0.01)
        if configuration.get("logs"):
            print("agent stdout diagnostic", flush=True)
            print("original stdout diagnostic", file=sys.__stdout__, flush=True)
            sys.stderr.write("x" * 250_000 + "\\nAGENT_LOG_TAIL\\n")
            sys.stderr.flush()

    def reset(self, context):
        self.operation("reset")

    def step(self, observation):
        self.operation("step")
        if self.configuration.get("noise"):
            os.write(1, (self.configuration["noise"] + "\\n").encode())
        if self.configuration.get("delay"):
            time.sleep(self.configuration["delay"])
        control = {"throttle": 0.4, "steer": -0.2}
        action = self.configuration.get("action")
        if action == "invalid":
            control["throttle"] = 1.5
        if action == "nan":
            control["steer"] = float("nan")
        return {
            "frame": observation.frame + (1 if action == "wrong_frame" else 0),
            "control": control,
            "diagnostics": {
                "maneuver": "proceed", "reason_codes": ["clear_lane"],
                "metadata": {"worker_pid": os.getpid(), "python": sys.executable,
                             "python_prefix": sys.prefix,
                             "virtual_env": os.environ.get("VIRTUAL_ENV"),
                             "python_home": os.environ.get("PYTHONHOME"),
                             "python_path": os.environ.get("PYTHONPATH"),
                             "configuration": self.configuration,
                             "padding": "z" * self.configuration.get("response_bytes", 0)},
            },
        }

    def close(self):
        self.operation("close")
        if self.configuration.get("closed_file"):
            Path(self.configuration["closed_file"]).write_text("closed")
'''


def write_package(directory, **configuration):
    directory.mkdir()
    manifest = {
        "agent_id": "process-test", "name": "Process test", "version": "1.0",
        "entry_point": "agent:Driver", "configuration": configuration,
    }
    (directory / "agent.json").write_text(json.dumps(manifest), encoding="utf-8")
    (directory / "agent.py").write_text(SOURCE, encoding="utf-8")
    return directory


def context():
    return AgentReset(seed=0, map_name="Town03", ego_actor_id="ego", fixed_delta_seconds=0.05)


def observation(frame=410, time_s=0.0):
    return AgentObservation(
        frame=frame, time_s=time_s, dt_s=0.05, ego=ActorState(actor_id="ego"),
        actors=[], target_speed_mps=12.0,
    )


def options(**changes):
    return AgentProcessOptions(**({
        "load_timeout_s": 10.0, "reset_timeout_s": 5.0,
        "step_timeout_s": 2.0, "close_timeout_s": 1.0, "terminate_timeout_s": 1.0,
    } | changes))


def load(agent):
    agent.load(agent.manifest.configuration)
    agent.reset(context())
    return agent


def is_alive(pid):
    if os.name == "nt":
        # os.kill(pid, 0) is not a safe liveness probe on Windows.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            status = ctypes.c_ulong()
            assert kernel32.GetExitCodeProcess(handle, ctypes.byref(status))
            return status.value == 259
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        # Terminated grandchildren can briefly remain zombies on Linux.
        stat = Path(f"/proc/{pid}/stat")
        return not stat.exists() or stat.read_text().split(") ", 1)[1][0] != "Z"
    except ProcessLookupError:
        return False


def assert_stopped(pid):
    deadline = time.monotonic() + 3.0
    while is_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not is_alive(pid), f"Agent process {pid} survived cleanup"


def test_process_controls_diagnostics_identity_and_timing_are_from_the_worker(tmp_path):
    package = write_package(tmp_path / "agent", delay=0.02)
    agent = ProcessAgent(package, python_executable=sys.executable, options=options())
    assert not agent.is_running
    try:
        load(agent)
        child_pid = agent.pid
        assert child_pid != os.getpid()
        assert agent.is_running
        first = agent.step(observation())
        second = agent.step(observation(411, 0.05))
        assert first.frame == 410 and second.frame == 411
        assert first.control.throttle == 0.4
        assert first.control.steer == -0.2
        assert first.diagnostics.maneuver == "proceed"
        assert first.diagnostics.metadata["worker_pid"] == child_pid
        assert Path(first.diagnostics.metadata["python"]).resolve() == Path(sys.executable).resolve()
        stats = agent.stats
        assert stats.steps == 2
        assert stats.worker_pid == child_pid
        assert stats.python_version
        assert 0 < stats.worker_step_max_s <= stats.worker_step_total_s
        assert 0 < stats.round_trip_max_s <= stats.round_trip_total_s
        assert stats.worker_step_total_s >= 0.03
        with pytest.raises((FrozenInstanceError, AttributeError)):
            stats.steps = 50
        assert agent.stats.steps == 2
        agent.reset(context())
        assert agent.step(observation(900, 0.0)).frame == 900
        assert agent.stats.steps == 3
        assert stats.steps == 2  # Reading stats must return an immutable snapshot.
        assert not any(getattr(module, "__file__", None) == str(package / "agent.py")
                       for module in tuple(sys.modules.values()) if module is not None)
    finally:
        agent.close()
    assert not agent.is_running
    assert_stopped(child_pid)
    agent.close()


def test_manifest_is_a_copy_and_worker_configuration_is_independent(tmp_path):
    package = write_package(tmp_path / "agent", nested={"value": 7})
    agent = ProcessAgent(package, options=options())
    exposed = agent.manifest
    exposed.configuration["nested"]["value"] = 999
    try:
        load(agent)
        value = agent.step(observation()).diagnostics.metadata["configuration"]["nested"]["value"]
        assert value == 7
    finally:
        agent.close()


def test_load_accepts_equivalent_configuration_with_reordered_nested_keys(tmp_path):
    configuration = {
        "enabled": True, "target_speed": 12,
        "planner": {"policy": "cautious", "margin": 2.0},
    }
    reordered = {
        "planner": {"margin": 2.0, "policy": "cautious"},
        "target_speed": 12, "enabled": True,
    }
    agent = ProcessAgent(write_package(tmp_path / "agent", **configuration), options=options())
    try:
        agent.load(reordered)
        agent.reset(context())
        assert agent.step(observation()).diagnostics.metadata["configuration"] == configuration
    finally:
        agent.close()


@pytest.mark.parametrize(("field", "changed_value"), [("enabled", 1), ("attempts", True)])
def test_load_rejects_bool_integer_configuration_changes_before_starting_worker(tmp_path, field, changed_value):
    pid_file = tmp_path / "pid.txt"
    agent = ProcessAgent(write_package(
        tmp_path / "agent", enabled=True, attempts=1, pid_file=str(pid_file),
    ), options=options())
    changed = agent.manifest.configuration | {field: changed_value}
    try:
        with pytest.raises(AgentProtocolError, match="configuration"):
            agent.load(changed)
        assert agent.pid is None
        assert not agent.is_running
        assert not pid_file.exists()
    finally:
        agent.close()


def test_selected_interpreter_does_not_inherit_another_python_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONHOME", str(tmp_path / "wrong-home"))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "wrong-imports"))
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "wrong-venv"))
    agent = ProcessAgent(write_package(tmp_path / "agent"), python_executable=sys.executable,
                         options=options())
    try:
        load(agent)
        details = agent.step(observation()).diagnostics.metadata
        assert Path(details["python_prefix"]).resolve() == Path(sys.prefix).resolve()
        assert details["python_home"] is None
        assert details["python_path"] is None
        if (Path(sys.prefix) / "pyvenv.cfg").is_file():
            assert Path(details["virtual_env"]).resolve() == Path(sys.prefix).resolve()
        else:
            assert details["virtual_env"] is None
    finally:
        agent.close()


def test_agent_python_prints_and_large_stderr_do_not_corrupt_or_block_protocol(tmp_path):
    agent = ProcessAgent(write_package(tmp_path / "agent", logs=True), options=options())
    try:
        load(agent)
        assert agent.step(observation()).control.throttle == 0.4
    finally:
        agent.close()
    assert "AGENT_LOG_TAIL" in agent.stderr_tail
    assert len(agent.stderr_tail) < 250_000


@pytest.mark.parametrize("operation", ["load", "reset", "step"])
def test_worker_exceptions_fail_the_session_without_a_fallback(tmp_path, operation):
    closed_file = tmp_path / "closed.txt"
    agent = ProcessAgent(write_package(tmp_path / "agent", fail=operation, closed_file=str(closed_file)),
                         options=options())
    try:
        with pytest.raises(AgentProcessError, match="deliberate " + operation + " failure"):
            agent.load(agent.manifest.configuration)
            agent.reset(context())
            agent.step(observation())
        assert not agent.is_running
        assert agent.stats.steps == 0
        with pytest.raises((AgentLifecycleError, AgentProcessError)):
            agent.load(agent.manifest.configuration)
        with pytest.raises((AgentLifecycleError, AgentProcessError)):
            agent.step(observation())
    finally:
        agent.close()
    assert closed_file.read_text() == "closed"


@pytest.mark.parametrize("action", ["invalid", "nan", "wrong_frame"])
def test_invalid_agent_controls_and_frame_identity_are_rejected(tmp_path, action):
    agent = ProcessAgent(write_package(tmp_path / "agent", action=action), options=options())
    try:
        load(agent)
        pid = agent.pid
        with pytest.raises(AgentProcessError):
            agent.step(observation())
        assert agent.stats.steps == 0
        assert not agent.is_running
    finally:
        agent.close()
    assert_stopped(pid)


@pytest.mark.parametrize("operation", ["load", "reset", "step"])
def test_abrupt_worker_exit_is_detected_without_waiting_for_normal_response(tmp_path, operation):
    agent = ProcessAgent(write_package(tmp_path / "agent", exit=operation), options=options())
    try:
        with pytest.raises(AgentProcessError):
            agent.load(agent.manifest.configuration)
            agent.reset(context())
            agent.step(observation())
        assert not agent.is_running
    finally:
        agent.close()


@pytest.mark.parametrize("operation", ["load", "reset", "step", "close"])
def test_hung_lifecycle_operations_have_deadlines_and_terminate_worker(tmp_path, operation):
    pid_file = tmp_path / "pid.txt"
    # Load includes a cold interpreter and framework imports. Use the same
    # startup budget as the other lifecycle cases so this fixture reaches its
    # intended hung user operation and PID marker under concurrent native tests.
    # Reset/step/close still exercise their short operation-specific deadlines.
    timeouts = {} if operation == "load" else {operation + "_timeout_s": 0.3}
    agent = ProcessAgent(write_package(tmp_path / "agent", hang=operation, pid_file=str(pid_file)),
                         options=options(**timeouts))
    try:
        with pytest.raises(AgentTimeoutError):
            load(agent)
            if operation == "close":
                agent.close()
            else:
                agent.step(observation())
        assert not agent.is_running
    finally:
        agent.close()
    assert_stopped(int(pid_file.read_text()))


def test_cancellation_interrupts_a_hung_step_without_a_control_or_new_frame(tmp_path):
    agent = ProcessAgent(write_package(tmp_path / "agent", hang="step"),
                         options=options(step_timeout_s=10.0))
    cancelled = Event()
    timer = Timer(0.2, cancelled.set)
    try:
        load(agent)
        pid = agent.pid
        agent.set_cancellation(cancelled.is_set)
        timer.start()
        started = time.monotonic()
        with pytest.raises(AgentProcessCancelled):
            agent.step(observation())
        assert time.monotonic() - started < 5.0
        assert agent.stats.steps == 0
        assert not agent.is_running
    finally:
        timer.cancel()
        agent.close()
    assert_stopped(pid)


def test_load_deadline_also_covers_agent_module_import(tmp_path):
    pid_file = tmp_path / "import-pid.txt"
    package = write_package(tmp_path / "agent")
    (package / "agent.py").write_text(
        "import os, time\nfrom pathlib import Path\n"
        f"Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
        "while True:\n    time.sleep(1)\n", encoding="utf-8",
    )
    # Reach the deliberate module-import hang before asserting deadline cleanup;
    # interpreter/framework startup shares the existing ten-second fixture budget.
    agent = ProcessAgent(package, options=options())
    try:
        with pytest.raises(AgentTimeoutError):
            agent.load(agent.manifest.configuration)
        assert not agent.is_running
    finally:
        agent.close()
    assert_stopped(int(pid_file.read_text()))


def test_already_cancelled_load_does_not_run_user_code(tmp_path):
    pid_file = tmp_path / "pid.txt"
    agent = ProcessAgent(write_package(tmp_path / "agent", pid_file=str(pid_file)), options=options())
    agent.set_cancellation(lambda: True)
    try:
        with pytest.raises(AgentProcessCancelled):
            agent.load(agent.manifest.configuration)
        assert not pid_file.exists()
        assert not agent.is_running
    finally:
        agent.close()


@pytest.mark.parametrize("noise", ["not a JSON response\n", "\u00ff\n",
                                      '{"protocol_version":"1.0","request_id":0,"operation":"step","ok":true}\n'])
def test_corrupt_or_unmatched_protocol_output_fails_closed(tmp_path, noise):
    agent = ProcessAgent(write_package(tmp_path / "agent", noise=noise), options=options())
    try:
        load(agent)
        with pytest.raises(AgentProcessError):
            agent.step(observation())
        assert not agent.is_running
        assert agent.stats.steps == 0
    finally:
        agent.close()


def test_oversized_worker_response_is_bounded_and_fails_without_fallback(tmp_path):
    agent = ProcessAgent(write_package(tmp_path / "agent", response_bytes=30_000),
                         options=options(max_message_bytes=4096))
    try:
        load(agent)
        with pytest.raises(AgentProcessError):
            agent.step(observation())
        assert not agent.is_running
        assert agent.stats.steps == 0
    finally:
        agent.close()


def test_close_failure_is_reported_and_worker_is_still_reaped(tmp_path):
    agent = ProcessAgent(write_package(tmp_path / "agent", fail="close"), options=options())
    load(agent)
    pid = agent.pid
    with pytest.raises(AgentProcessError, match="deliberate close failure"):
        agent.close()
    assert not agent.is_running
    assert_stopped(pid)
    agent.close()


def test_worker_descendants_are_terminated_when_step_times_out(tmp_path):
    descendant_file = tmp_path / "descendant.txt"
    agent = ProcessAgent(write_package(tmp_path / "agent", hang="step", descendant_file=str(descendant_file)),
                         options=options(step_timeout_s=0.3))
    try:
        load(agent)
        descendant_pid = int(descendant_file.read_text())
        with pytest.raises(AgentTimeoutError):
            agent.step(observation())
    finally:
        agent.close()
    assert_stopped(descendant_pid)


def test_normal_close_also_cleans_up_remaining_worker_descendants(tmp_path):
    descendant_file = tmp_path / "descendant.txt"
    agent = ProcessAgent(write_package(tmp_path / "agent", descendant_file=str(descendant_file)),
                         options=options())
    try:
        load(agent)
        descendant_pid = int(descendant_file.read_text())
        assert agent.step(observation()).control.throttle == 0.4
    finally:
        agent.close()
    assert_stopped(descendant_pid)


def test_close_before_load_does_not_start_a_worker_or_execute_user_code(tmp_path):
    pid_file = tmp_path / "pid.txt"
    agent = ProcessAgent(write_package(tmp_path / "agent", pid_file=str(pid_file)), options=options())
    agent.close()
    agent.close()
    assert not agent.is_running
    assert not pid_file.exists()
    with pytest.raises((AgentLifecycleError, AgentProcessError)):
        agent.load(agent.manifest.configuration)


def inject_group_cleanup_failure(agent, monkeypatch):
    original = agent._group.terminate
    attempts = []

    def terminate():
        attempts.append(None)
        # Terminate the real tree first so failure-injection tests cannot leak
        # a worker, even when the caller's cleanup error handling is broken.
        original()
        if len(attempts) == 1:
            raise OSError("injected group cleanup failure")

    monkeypatch.setattr(agent._group, "terminate", terminate)
    return attempts


@pytest.mark.parametrize("failure", ["timeout", "agent_error"])
def test_cleanup_error_preserves_primary_failure_and_still_reaps_worker(tmp_path, monkeypatch, failure):
    configuration = {"hang": "step"} if failure == "timeout" else {"fail": "step"}
    agent = ProcessAgent(write_package(tmp_path / "agent", **configuration),
                         options=options(step_timeout_s=0.3))
    attempts = []
    try:
        load(agent)
        pid = agent.pid
        attempts = inject_group_cleanup_failure(agent, monkeypatch)
        error_type = AgentTimeoutError if failure == "timeout" else AgentProcessError
        message = "step exceeded" if failure == "timeout" else "deliberate step failure"
        with pytest.raises(error_type, match=message) as caught:
            agent.step(observation())
        assert any("injected group cleanup failure" in note
                   for note in getattr(caught.value, "__notes__", ()))
        # Check returncode before poll/is_running, which could itself reap it.
        assert agent._process.returncode is not None
        assert not agent.is_running
        assert_stopped(pid)
    finally:
        agent.close()
    assert len(attempts) >= 2


def test_close_reaps_root_on_group_cleanup_error_and_retries_after_closed_state(tmp_path, monkeypatch):
    agent = ProcessAgent(write_package(tmp_path / "agent"), options=options())
    attempts = []
    try:
        load(agent)
        pid = agent.pid
        attempts = inject_group_cleanup_failure(agent, monkeypatch)
        with pytest.raises(AgentProcessError, match="injected group cleanup failure"):
            agent.close()
        assert agent.state == "closed"
        assert agent._process.returncode is not None
        assert not agent.is_running
        assert_stopped(pid)
    finally:
        agent.close()
    assert len(attempts) >= 2
    agent.close()


@pytest.mark.parametrize("field", ["load_timeout_s", "reset_timeout_s", "step_timeout_s",
                                   "close_timeout_s", "terminate_timeout_s"])
@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), True])
def test_deadlines_must_be_finite_positive_numbers(field, value):
    with pytest.raises((TypeError, ValueError)):
        AgentProcessOptions(**{field: value})


@pytest.mark.parametrize("value", [0, -1, 2.5, True])
def test_message_limit_must_be_a_positive_integer(value):
    with pytest.raises((TypeError, ValueError)):
        AgentProcessOptions(max_message_bytes=value)


def test_error_categories_preserve_sdk_and_simulation_cancellation_contracts():
    assert issubclass(AgentProcessError, AgentSDKError)
    assert issubclass(AgentTimeoutError, AgentProcessError)
    assert issubclass(AgentProcessCancelled, SimulationCancelled)
