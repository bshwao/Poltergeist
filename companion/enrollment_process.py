"""Own a disposable enrollment process and its bounded anonymous-pipe I/O.

The request wait covers launch, input writes and child DNS/TLS/HTTP operations.
Kernel process creation/termination cannot have a portable hard time guarantee.
Cleanup has a separate bounded wait. Unconfirmed cleanup retains the owner so
the caller can keep its profile locked, reject new requests and retry close.
"""

import math
import os
from pathlib import Path
import signal
import subprocess
import sys
from threading import Event, Lock, RLock, Thread
from time import monotonic, sleep

from simulator.agents.process_group import AgentProcessGroup


MAX_REQUEST_BYTES = 512 * 1024


class EnrollmentProcessError(RuntimeError):
    pass


class EnrollmentTimeoutError(EnrollmentProcessError):
    pass


class EnrollmentOutputError(EnrollmentProcessError):
    pass


def _positive_timeout(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError("Enrollment timeout must be a finite positive number")
    return float(value)


class _ProcessTree(AgentProcessGroup):
    """Keep the Windows job and verified member handles through actual exit.

    The production worker is builtin-only and creates no subprocesses; Windows'
    venv launcher may create its actual Python child. This is not an atomic
    snapshot/termination protocol for arbitrary code continuously spawning trees.
    """

    def __init__(self, process):
        self._stop_requested = False
        self._finished = False
        self._members = {}
        self._capture_uncertain = False
        super().__init__(process)

    def _capture_members(self):
        """Open synchronization handles only after verifying owned-job membership."""
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes

        class ProcessIds(ctypes.Structure):
            _fields_ = [("NumberOfAssignedProcesses", wintypes.DWORD),
                        ("NumberOfProcessIdsInList", wintypes.DWORD),
                        ("ProcessIdList", ctypes.c_size_t * 256)]

        kernel = self._kernel
        kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
            ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        kernel.QueryInformationJobObject.restype = wintypes.BOOL
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        kernel.IsProcessInJob.restype = wintypes.BOOL
        members = ProcessIds()
        if (not kernel.QueryInformationJobObject(self._job, 3, ctypes.byref(members), ctypes.sizeof(members), None)
                or members.NumberOfAssignedProcesses > members.NumberOfProcessIdsInList
                or members.NumberOfProcessIdsInList > 256):
            raise OSError("Enrollment process-tree members could not be inspected")
        for pid in members.ProcessIdList[:members.NumberOfProcessIdsInList]:
            # Popen already owns the exact launch-process handle and close waits
            # on it separately. Never reopen a departed launch PID by number.
            if pid == self.process.pid or pid in self._members:
                continue
            handle = kernel.OpenProcess(0x00100000 | 0x1000, False, pid)  # SYNCHRONIZE | QUERY_LIMITED_INFORMATION
            if not handle:
                raise OSError("Enrollment process-tree member handle is unavailable")
            in_job = wintypes.BOOL()
            if not kernel.IsProcessInJob(handle, self._job, ctypes.byref(in_job)) or not in_job.value:
                kernel.CloseHandle(handle)
                raise OSError("Enrollment process-tree member ownership could not be confirmed")
            self._members[pid] = handle

    def _members_exited(self):
        if os.name != "nt":
            return True
        from ctypes import wintypes
        self._kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self._kernel.WaitForSingleObject.restype = wintypes.DWORD
        for handle in self._members.values():
            state = self._kernel.WaitForSingleObject(handle, 0)
            if state == 0x102:  # WAIT_TIMEOUT: the actual process is not signaled.
                return False
            if state != 0:
                raise OSError("Enrollment process-tree member exit could not be confirmed")
        return True

    def request_stop(self):
        if self._finished:
            return
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            # Accounting can reach zero before an actual process synchronization
            # handle signals. Capture identities before requesting termination.
            capture_error = None
            try:
                self._capture_members()
            except Exception as failure:
                self._capture_uncertain = True
                capture_error = failure
            self._kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            self._kernel.TerminateJobObject.restype = wintypes.BOOL
            if not self._kernel.TerminateJobObject(self._job, 1):
                raise OSError("Enrollment process-tree termination failed")
            if capture_error is not None:
                raise OSError("Enrollment process-tree cleanup could not be confirmed") from None
        elif not self._stop_requested:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self._stop_requested = True

    def _empty(self):
        if os.name != "nt":
            try:
                os.killpg(self.process.pid, 0)
                return False
            except ProcessLookupError:
                return True
        import ctypes
        from ctypes import wintypes

        class Accounting(ctypes.Structure):
            _fields_ = [(name, ctypes.c_longlong) for name in (
                "TotalUserTime", "TotalKernelTime", "ThisPeriodTotalUserTime", "ThisPeriodTotalKernelTime")]
            _fields_ += [(name, wintypes.DWORD) for name in (
                "TotalPageFaultCount", "TotalProcesses", "ActiveProcesses", "TotalTerminatedProcesses")]

        self._kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
            ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        self._kernel.QueryInformationJobObject.restype = wintypes.BOOL
        accounting = Accounting()
        if not self._kernel.QueryInformationJobObject(self._job, 1, ctypes.byref(accounting),
                ctypes.sizeof(accounting), None):
            raise OSError("Enrollment process-tree cleanup could not be inspected")
        return accounting.ActiveProcesses == 0

    def finish(self, deadline):
        if self._finished:
            return True
        if self._capture_uncertain:
            return False
        while True:
            # A second membership read also captures any ordinary launcher child
            # born between the first snapshot and the termination request.
            try:
                self._capture_members()
            except Exception:
                self._capture_uncertain = True
                raise
            if self._empty() and self._members_exited():
                break
            remaining = deadline - monotonic()
            if remaining <= 0:
                return False
            sleep(min(.01, remaining))
        if os.name == "nt":
            for pid, handle in list(self._members.items()):
                if not self._kernel.CloseHandle(handle):
                    return False
                del self._members[pid]
            if not self._kernel.CloseHandle(self._job):
                return False
            self._job = None
        self._finished = True
        return True


class EnrollmentProcess:
    """Single-use request owner; callers must retain it while ``owned``."""

    def __init__(self, worker_path: Path, *, timeout_s: float = 10, max_response_bytes: int = 131072,
            deadline: float | None = None):
        self.worker_path = Path(worker_path).absolute()
        self.timeout_s = _positive_timeout(timeout_s)
        if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline)):
            raise ValueError("Enrollment deadline must be a finite monotonic timestamp")
        self._deadline = deadline
        if type(max_response_bytes) is not int or not 1 <= max_response_bytes <= 1024 * 1024:
            raise ValueError("Enrollment response limit must be a bounded positive integer")
        self.max_response_bytes = max_response_bytes
        self._lock, self._exchange_lock = RLock(), Lock()
        self._process = self._tree = None
        self._threads = []
        self._used = False
        self._closing, self._changed = Event(), Event()
        self._reader_done, self._writer_done = Event(), Event()
        self._overflow, self._pipe_failed = Event(), Event()
        self._response = bytearray()

    @property
    def owned(self):
        with self._lock:
            return self._process is not None or self._tree is not None or any(thread.is_alive() for thread in self._threads)

    def _write(self, payload, stream, deadline):
        try:
            remaining = memoryview(payload)
            while remaining:
                if self._closing.is_set() or monotonic() >= deadline:
                    return
                written = stream.write(remaining)
                if not written:
                    raise OSError("Enrollment input pipe closed")
                remaining = remaining[written:]
        except (OSError, ValueError):
            self._pipe_failed.set()
        finally:
            try:
                stream.close()
            except (OSError, ValueError):
                self._pipe_failed.set()
            self._writer_done.set()
            self._changed.set()

    def _read(self, stream):
        try:
            while True:
                chunk = stream.read(min(65536, self.max_response_bytes + 1 - len(self._response)))
                if not chunk:
                    break
                self._response.extend(chunk)
                if len(self._response) > self.max_response_bytes:
                    self._overflow.set()
                    break
        except (OSError, ValueError):
            self._pipe_failed.set()
        finally:
            self._reader_done.set()
            self._changed.set()

    def _start(self, payload, deadline):
        if monotonic() >= deadline:
            raise EnrollmentTimeoutError("Enrollment request deadline expired")
        executable = Path(sys.executable)
        if executable.name.lower() == "pythonw.exe" and executable.with_name("python.exe").is_file():
            executable = executable.with_name("python.exe")
        environment = os.environ.copy()
        environment.pop("PYTHONHOME", None)
        environment.pop("PYTHONPATH", None)
        self._process = subprocess.Popen([str(executable), "-I", "-B", str(self.worker_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
            shell=False, env=environment,
            creationflags=(subprocess.CREATE_NO_WINDOW | 0x00000004) if os.name == "nt" else 0,
            start_new_session=os.name != "nt")
        self._tree = _ProcessTree(self._process)
        if monotonic() >= deadline:
            raise EnrollmentTimeoutError("Enrollment request deadline expired")
        if self._closing.is_set():
            return
        self._tree.resume()
        if monotonic() >= deadline:
            raise EnrollmentTimeoutError("Enrollment request deadline expired")
        # Establish the bounded reader before releasing credential-bearing input.
        for target, values in ((self._read, (self._process.stdout,)),
                (self._write, (payload, self._process.stdin, deadline))):
            if monotonic() >= deadline:
                raise EnrollmentTimeoutError("Enrollment request deadline expired")
            thread = Thread(target=target, args=values, name="poltergeist-enrollment-pipe", daemon=True)
            thread.start()
            self._threads.append(thread)

    def exchange(self, payload: bytes) -> bytes:
        deadline = monotonic() + self.timeout_s
        if self._deadline is not None:
            deadline = min(deadline, self._deadline)
        if type(payload) is not bytes or len(payload) > MAX_REQUEST_BYTES:
            raise EnrollmentProcessError("Enrollment request exceeds its pipe limit")
        if not self._exchange_lock.acquire(blocking=False):
            raise EnrollmentProcessError("Enrollment process is already in use")
        try:
            with self._lock:
                if self._used or self._closing.is_set():
                    raise EnrollmentProcessError("Enrollment process is no longer available")
                self._used = True
                try:
                    if monotonic() >= deadline:
                        raise EnrollmentTimeoutError("Enrollment request deadline expired")
                    self._start(payload, deadline)
                except BaseException as failure:
                    self.close()
                    if isinstance(failure, EnrollmentTimeoutError) or not isinstance(failure, Exception):
                        raise
                    raise EnrollmentProcessError("Enrollment process could not be started") from None
            try:
                while True:
                    if self._closing.is_set():
                        raise EnrollmentProcessError("Enrollment request was closed")
                    if self._overflow.is_set():
                        raise EnrollmentOutputError("Enrollment response exceeds its pipe limit")
                    if self._pipe_failed.is_set():
                        raise EnrollmentProcessError("Enrollment request pipe failed")
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        raise EnrollmentTimeoutError("Enrollment request deadline expired")
                    with self._lock:
                        process = self._process
                        if process is None:
                            raise EnrollmentProcessError("Enrollment request was closed")
                        code = process.poll()
                    if code is not None and code != 0:
                        raise EnrollmentProcessError("Enrollment process did not complete")
                    if code == 0 and self._reader_done.is_set() and self._writer_done.is_set():
                        result = bytes(self._response)
                        break
                    self._changed.wait(min(.02, remaining))
                    self._changed.clear()
            except BaseException:
                self.close()
                raise
            if not self.close():
                raise EnrollmentProcessError("Enrollment process cleanup could not be confirmed")
            if monotonic() >= deadline:
                raise EnrollmentTimeoutError("Enrollment request deadline expired")
            return result
        finally:
            self._exchange_lock.release()

    def close(self, timeout_s: float = 3) -> bool:
        deadline = monotonic() + _positive_timeout(timeout_s)
        self._closing.set()
        self._changed.set()
        if not self._lock.acquire(timeout=max(0, deadline - monotonic())):
            return False
        try:
            process, tree = self._process, self._tree
            if process is None and tree is None:
                return not any(thread.is_alive() for thread in self._threads)
            clean = True
            if tree is not None:
                try:
                    tree.request_stop()
                except Exception:
                    clean = False
            if process is not None:
                try:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=max(0, deadline - monotonic()))
                except (OSError, subprocess.SubprocessError):
                    clean = False
            if tree is not None:
                try:
                    if tree.finish(deadline):
                        self._tree = None
                    else:
                        clean = False
                except Exception:
                    clean = False
            for thread in self._threads:
                thread.join(timeout=max(0, deadline - monotonic()))
            if any(thread.is_alive() for thread in self._threads):
                return False
            if process is not None:
                for pipe in (process.stdin, process.stdout):
                    try:
                        if pipe is not None:
                            pipe.close()
                    except (OSError, ValueError):
                        if not pipe.closed:
                            clean = False
            if clean:
                self._process = None
            return clean and self._tree is None
        finally:
            self._lock.release()
