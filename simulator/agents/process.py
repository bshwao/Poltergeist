"""Deadline-enforced external execution of a trusted local SDK agent.

Only the parent simulation runner owns CARLA. This process boundary isolates
dependencies and failures; it is not a sandbox for untrusted Python code.
"""

from dataclasses import dataclass, replace
import json
import math
import os
from pathlib import Path
from queue import Empty, Full, Queue
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Lock, Thread
from time import perf_counter as monotonic
from typing import Callable

from shared.models import AgentManifest
from simulator.errors import SimulationCancelled

from .package import inspect_agent_package
from .interpreter import worker_interpreter
from .process_group import AgentProcessGroup
from .process_protocol import DEFAULT_MAX_MESSAGE_BYTES, decode_response, encode_request
from .sdk import AgentLifecycleError, AgentProtocolError, AgentSDKError, AgentSession, AutonomousAgent


class AgentProcessError(AgentSDKError):
    """A worker exited, rejected a request, or violated the process protocol."""


class AgentTimeoutError(AgentProcessError):
    """An agent operation exceeded its wall-clock deadline."""


class AgentProcessCancelled(SimulationCancelled):
    """The run was cancelled while waiting for an external agent."""


@dataclass(frozen=True)
class AgentProcessOptions:
    load_timeout_s: float = 30.0
    reset_timeout_s: float = 10.0
    step_timeout_s: float = 2.0
    close_timeout_s: float = 2.0
    terminate_timeout_s: float = 1.0
    max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES

    def __post_init__(self):
        for name in ("load_timeout_s", "reset_timeout_s", "step_timeout_s", "close_timeout_s", "terminate_timeout_s"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a finite positive number")
        if type(self.max_message_bytes) is not int or self.max_message_bytes < 1:
            raise ValueError("max_message_bytes must be a positive integer")


@dataclass(frozen=True)
class AgentStepTiming:
    frame: int
    worker_step_wall_time_s: float
    round_trip_wall_time_s: float


@dataclass(frozen=True)
class AgentProcessStats:
    steps: int = 0
    worker_step_total_s: float = 0.0
    worker_step_max_s: float = 0.0
    round_trip_total_s: float = 0.0
    round_trip_max_s: float = 0.0
    worker_pid: int | None = None
    python_version: str | None = None


class _RemoteAgent(AutonomousAgent):
    def __init__(self, owner):
        self.owner = owner

    def load(self, configuration):
        owner = self.owner
        started = monotonic()
        owner._start()
        deadline = started + owner.options.load_timeout_s
        identity, _ = owner._request("hello", {}, deadline=deadline)
        owner._accept_identity(identity)
        request = {"manifest": owner.package.manifest.model_dump(mode="json")}
        if owner.snapshot is not None:
            request["snapshot"] = owner.snapshot.model_dump(mode="json")
        payload, _ = owner._request("load", request, deadline=deadline)
        if payload != identity:
            raise AgentProcessError("Worker identity changed during load")

    def reset(self, context):
        payload, _ = self.owner._request("reset", context.model_dump(mode="json"))
        if payload:
            raise AgentProcessError("Reset response payload must be empty")

    def step(self, observation):
        payload, elapsed = self.owner._request("step", observation.model_dump(mode="json"))
        if set(payload) != {"result", "worker_step_wall_time_s"}:
            raise AgentProcessError("Invalid step response payload")
        worker_time = payload["worker_step_wall_time_s"]
        if type(worker_time) not in (int, float) or not math.isfinite(worker_time) or worker_time < 0:
            raise AgentProcessError("Invalid worker step wall time")
        self.owner._pending_timing = AgentStepTiming(observation.frame, worker_time, elapsed)
        # AgentSession validates and copies the response, including its frame.
        return payload["result"]

    def close(self):
        owner = self.owner
        primary_error = None
        try:
            if owner.is_running and not owner._faulted:
                deadline = monotonic() + owner.options.close_timeout_s
                payload, _ = owner._request("close", {}, allow_cancel=False, deadline=deadline)
                if payload:
                    raise AgentProcessError("Close response payload must be empty")
                try:
                    owner._process.wait(timeout=max(0, deadline - monotonic()))
                except subprocess.TimeoutExpired as error:
                    raise AgentTimeoutError("Agent process did not exit after close") from error
                if owner._process.returncode != 0:
                    raise AgentProcessError(f"Agent process exited with code {owner._process.returncode} during close")
        except BaseException as error:
            primary_error = error
            raise
        finally:
            owner._stop_preserving(primary_error)


class ProcessAgent(AutonomousAgent):
    """One worker per instance; explicitly choose its already-installed Python.

    Package inspection in the constructor does not execute agent code. Loading
    starts a worker. Every response must match its request and observation frame.
    Failed or cancelled workers are terminated and cannot be reused.
    """

    STDERR_LIMIT_BYTES = 64 * 1024

    def __init__(self, package, *, python_executable=None, options=None, snapshot=None):
        self.package = inspect_agent_package(package)
        self.snapshot = None
        if snapshot is not None:
            from shared.models.registry import AgentSnapshot
            from .fingerprints import verify_agent_snapshot
            self.snapshot = AgentSnapshot.model_validate(snapshot.model_dump(mode="json"))
            verify_agent_snapshot(self.snapshot)
            if str(self.package.directory) != self.snapshot.package_path:
                raise AgentProcessError("Agent package path must match the registered snapshot")
        self.options = AgentProcessOptions() if options is None else options
        if not isinstance(self.options, AgentProcessOptions):
            raise TypeError("options must be AgentProcessOptions")
        # Preserve virtual-environment symlinks on POSIX rather than resolving
        # bin/python to the base interpreter and losing the selected environment.
        try:
            self.python_executable = worker_interpreter(python_executable)
        except ValueError as error:
            raise AgentProcessError(str(error)) from error
        if not self.python_executable.is_file():
            raise AgentProcessError(f"Agent Python executable is unavailable: {self.python_executable}")
        if self.snapshot is not None and (
            str(self.python_executable) != self.snapshot.python_executable
            or self.options != AgentProcessOptions(**self.snapshot.process_options.model_dump())
        ):
            raise AgentProcessError("Agent environment and process options must match the registered snapshot")
        self._process = None
        self._group = None
        self._bytecode_cache = None
        self._runtime_use = None
        self._threads = []
        self._responses = Queue(maxsize=2)
        self._writes = Queue(maxsize=1)
        self._reader_error = None
        self._stderr = bytearray()
        self._stderr_lock = Lock()
        self._lock = Lock()
        self._cancellation = lambda: False
        self._request_id = 0
        self._faulted = False
        self._stats = AgentProcessStats()
        self._last_timing = self._pending_timing = None
        self._session = AgentSession(_RemoteAgent(self), self.package.manifest if self.snapshot is None else self.snapshot.manifest)

    @property
    def manifest(self):
        return self._session.manifest

    @property
    def state(self):
        return self._session.state

    @property
    def pid(self):
        # Windows venvs may use a launcher whose PID differs from the worker.
        return self._stats.worker_pid or (None if self._process is None else self._process.pid)

    @property
    def is_running(self):
        return self._process is not None and self._process.poll() is None

    @property
    def stats(self):
        return replace(self._stats)

    @property
    def last_timing(self):
        return self._last_timing

    @property
    def stderr_tail(self):
        with self._stderr_lock:
            return self._stderr.decode("utf-8", errors="replace")

    def set_cancellation(self, check: Callable[[], bool]):
        if not callable(check):
            raise TypeError("Cancellation check must be callable")
        self._cancellation = check

    def _cancel_check(self):
        if self._cancellation():
            raise AgentProcessCancelled("Simulation cancelled while waiting for agent")

    def _accept_identity(self, payload):
        if set(payload) != {"worker_pid", "python_version"} or type(payload["worker_pid"]) is not int or (
            payload["worker_pid"] <= 0 or payload["worker_pid"] == os.getpid()
            or type(payload["python_version"]) is not str or not payload["python_version"]
        ):
            raise AgentProcessError("Invalid worker identity in startup response")
        self._stats = replace(self._stats, worker_pid=payload["worker_pid"], python_version=payload["python_version"])

    def _start(self):
        self._cancel_check()
        environment = os.environ.copy()
        environment.pop("PYTHONHOME", None)
        environment.pop("PYTHONPATH", None)
        environment["PYTHONUNBUFFERED"] = "1"
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PATH"] = str(self.python_executable.parent) + os.pathsep + environment.get("PATH", "")
        potential_env = self.python_executable.parent.parent
        if (potential_env / "pyvenv.cfg").is_file():
            environment["VIRTUAL_ENV"] = str(potential_env)
        else:
            environment.pop("VIRTUAL_ENV", None)
        try:
            if self.snapshot is not None and self.snapshot.isolated_runtime is not None:
                from .runtime_lease import runtime_use_lease
                from .fingerprints import verify_agent_snapshot
                self._runtime_use = runtime_use_lease(self.snapshot.isolated_runtime.prefix)
                self._runtime_use.acquire()
                verify_agent_snapshot(self.snapshot)
                self._cancel_check()
            # Timestamp/size based .pyc files can contain different code from
            # the source bytes checked by a registered snapshot. A private,
            # initially empty prefix prevents reading old caches for package,
            # runtime and dependency imports; -B also prevents writing new ones.
            # -B alone would still read an existing stale cache.
            self._bytecode_cache = TemporaryDirectory(prefix="poltergeist-agent-cache-")
            isolated = self.snapshot is not None and self.snapshot.isolated_runtime is not None
            if isolated:
                environment.pop("TCL_LIBRARY", None)
                environment.pop("TK_LIBRARY", None)
                environment.update(HF_HOME=str(Path(self._bytecode_cache.name) / "hf"),
                    TORCH_HOME=str(Path(self._bytecode_cache.name) / "torch"),
                    HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
            arguments = [str(self.python_executable), *(["-I"] if isolated else []), "-u", "-B", "-X",
                         f"pycache_prefix={self._bytecode_cache.name}", "-m", "simulator.agents.worker",
                         "--package", str(self.package.directory), "--max-message-bytes", str(self.options.max_message_bytes)]
            self._process = subprocess.Popen(
                arguments, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=Path(__file__).resolve().parents[2], env=environment, shell=False,
                # Suspend before the venv launcher can spawn an untracked child.
                creationflags=(subprocess.CREATE_NO_WINDOW | 0x00000004) if os.name == "nt" else 0,
                start_new_session=os.name != "nt",
            )
            self._group = AgentProcessGroup(self._process)
            self._group.resume()
            for name, target in (("stdout", self._read_output), ("stderr", self._read_stderr), ("stdin", self._write_input)):
                thread = Thread(target=target, name=f"agent-{self.pid}-{name}", daemon=True)
                self._threads.append(thread)
                thread.start()
        except BaseException as error:
            self._faulted = True
            self._stop_preserving(error)
            raise

    def _offer(self, kind, value):
        try:
            self._responses.put_nowait((kind, value))
        except Full:
            self._reader_error = "Agent sent unsolicited or excessive protocol responses"

    def _read_output(self):
        try:
            while True:
                line = self._process.stdout.readline(self.options.max_message_bytes + 1)
                if not line:
                    self._offer("error", "Agent process closed its response stream")
                    return
                if len(line) > self.options.max_message_bytes or not line.endswith(b"\n"):
                    self._offer("error", "Agent response is truncated or exceeds the message limit")
                    return
                self._offer("response", line)
        except (OSError, ValueError) as error:
            self._offer("error", f"Agent response pipe failed: {error}")

    def _read_stderr(self):
        try:
            while True:
                chunk = self._process.stderr.read1(4096)
                if not chunk:
                    return
                with self._stderr_lock:
                    self._stderr.extend(chunk)
                    if len(self._stderr) > self.STDERR_LIMIT_BYTES:
                        del self._stderr[:-self.STDERR_LIMIT_BYTES]
        except (OSError, ValueError):
            return

    def _write_input(self):
        try:
            while True:
                data = self._writes.get()
                if data is None:
                    return
                self._process.stdin.write(data)
                self._process.stdin.flush()
        except (OSError, ValueError) as error:
            self._offer("error", f"Agent request pipe failed: {error}")

    def _request(self, operation, payload, *, allow_cancel=True, deadline=None):
        started = monotonic()
        timeout = getattr(self.options, f"{'load' if operation == 'hello' else operation}_timeout_s")
        if deadline is None:
            deadline = started + timeout
        if allow_cancel:
            self._cancel_check()
        self._request_id += 1
        request_id = self._request_id
        encoded = encode_request(request_id, operation, payload, self.options.max_message_bytes)
        try:
            self._writes.put_nowait(encoded)
        except Full as error:
            raise AgentProcessError("Agent request writer is blocked") from error
        while True:
            if allow_cancel:
                self._cancel_check()
            if self._reader_error is not None:
                raise AgentProcessError(self._reader_error)
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise AgentTimeoutError(f"Agent {operation} exceeded {timeout:g} seconds")
            try:
                kind, value = self._responses.get(timeout=min(0.02, remaining))
            except Empty:
                continue
            if kind == "error":
                detail = self.stderr_tail[-2000:].strip()
                raise AgentProcessError(value + (f"; worker stderr: {detail}" if detail else ""))
            try:
                response = decode_response(value, self.options.max_message_bytes)
            except (ValueError, TypeError, UnicodeError) as error:
                raise AgentProcessError(f"Invalid agent process response: {error}") from error
            if response.request_id != request_id or response.operation != operation:
                raise AgentProcessError("Agent response request ID or operation does not match")
            if not response.ok:
                raise AgentProcessError(f"Agent {operation} failed: {response.error.type}: {response.error.message}")
            if allow_cancel:
                self._cancel_check()
            return response.payload, monotonic() - started

    def _stop(self):
        process = self._process
        if process is None:
            if self._bytecode_cache is not None:
                self._bytecode_cache.cleanup()
                self._bytecode_cache = None
            if self._runtime_use is not None:
                self._runtime_use.release()
                self._runtime_use = None
            return
        deadline = monotonic() + self.options.terminate_timeout_s
        errors = []
        if self._group is not None:
            try:
                self._group.terminate()
                self._group = None
            except Exception as error:
                # A group failure must not skip killing/reaping the root.
                errors.append(error)
        try:
            if process.poll() is None:
                process.kill()
        except OSError as error:
            errors.append(error)
        try:
            process.wait(timeout=max(0, deadline - monotonic()))
        except (subprocess.TimeoutExpired, OSError) as error:
            errors.append(error)
        try:
            self._writes.put_nowait(None)
        except Full:
            pass
        for thread in self._threads:
            thread.join(timeout=max(0, deadline - monotonic()))
        if any(thread.is_alive() for thread in self._threads):
            # Closing a buffered pipe while its reader/writer owns the lock
            # could block forever. Preserve handles for a later cleanup retry.
            errors.append(RuntimeError("Agent pipe threads did not exit within the cleanup deadline"))
        else:
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe is not None:
                    try:
                        pipe.close()
                    except OSError as error:
                        errors.append(error)
        if errors:
            error = AgentProcessError("Agent process cleanup failed: " + "; ".join(str(item) for item in errors))
            raise error from errors[0]
        # Keep the directory if termination or pipe cleanup failed, so a later
        # close can retry after the complete process group has exited.
        if self._bytecode_cache is not None:
            self._bytecode_cache.cleanup()
            self._bytecode_cache = None
        if self._runtime_use is not None:
            self._runtime_use.release()
            self._runtime_use = None

    def _stop_preserving(self, primary_error):
        try:
            self._stop()
        except BaseException as cleanup_error:
            if primary_error is None:
                raise
            primary_error.add_note(f"Agent process cleanup also failed: {type(cleanup_error).__name__}: {cleanup_error}")

    def _failed(self, error):
        self._faulted = True
        self._stop_preserving(error)

    def load(self, configuration):
        with self._lock:
            # A process is tied to its inspected or registered effective manifest.
            supplied = AgentManifest.model_validate(self.manifest.model_dump() | {"configuration": configuration})
            if json.dumps(supplied.model_dump(mode="json"), sort_keys=True, allow_nan=False) != json.dumps(
                self.manifest.model_dump(mode="json"), sort_keys=True, allow_nan=False,
            ):
                raise AgentProtocolError("Agent configuration must match its inspected manifest")
            try:
                self._session.load()
            except BaseException as error:
                if not isinstance(error, AgentLifecycleError):
                    self._failed(error)
                raise

    def reset(self, context):
        with self._lock:
            try:
                self._session.reset(context)
            except BaseException as error:
                if not isinstance(error, AgentLifecycleError):
                    self._failed(error)
                raise

    def step(self, observation):
        with self._lock:
            try:
                result = self._session.step(observation)
                timing = self._pending_timing
                self._last_timing = timing
                self._stats = replace(
                    self._stats, steps=self._stats.steps + 1,
                    worker_step_total_s=self._stats.worker_step_total_s + timing.worker_step_wall_time_s,
                    worker_step_max_s=max(self._stats.worker_step_max_s, timing.worker_step_wall_time_s),
                    round_trip_total_s=self._stats.round_trip_total_s + timing.round_trip_wall_time_s,
                    round_trip_max_s=max(self._stats.round_trip_max_s, timing.round_trip_wall_time_s),
                )
                return result
            except BaseException as error:
                if not isinstance(error, AgentLifecycleError):
                    self._failed(error)
                raise

    def close(self):
        with self._lock:
            if self._session.state == "closed":
                self._stop()
                return
            self._session.close()
