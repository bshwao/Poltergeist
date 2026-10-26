"""Native isolated enrollment workers retain ownership through pipe/tree cleanup."""

import json
import os
from pathlib import Path
import subprocess
from threading import Event, Thread
from time import monotonic, sleep

import pytest

from companion import enrollment_process as transport


def worker(tmp_path, source):
    path = tmp_path / "owned_worker.py"
    path.write_text(source, encoding="utf-8")
    return path


def capture_processes(monkeypatch):
    original, processes, arguments = subprocess.Popen, [], []

    def start(command, **values):
        arguments.append(command)
        process = original(command, **values)
        processes.append(process)
        return process

    monkeypatch.setattr(transport.subprocess, "Popen", start)
    return processes, arguments


def assert_closed(owner, processes):
    assert not owner.owned
    assert all(not thread.is_alive() for thread in owner._threads)
    assert all(process.poll() is not None for process in processes)


def test_isolated_worker_gets_large_input_only_over_its_pipe(tmp_path, monkeypatch):
    path = worker(tmp_path, "import json,sys; data=sys.stdin.buffer.read(); sys.stdout.write(json.dumps({'size':len(data),'isolated':sys.flags.isolated}))")
    processes, arguments = capture_processes(monkeypatch)
    owner = transport.EnrollmentProcess(path)
    payload = b"private_enrollment_process_sentinel" * 10000
    try:
        answer = json.loads(owner.exchange(payload))
        assert answer == {"size": len(payload), "isolated": 1}
        assert all("private_enrollment_process_sentinel" not in item for item in arguments[0])
        assert arguments[0][1:3] == ["-I", "-B"]
        assert Path(arguments[0][3]) == path
        assert_closed(owner, processes)
        with pytest.raises(transport.EnrollmentProcessError, match="no longer available"):
            owner.exchange(b"second request")
        assert len(processes) == 1
    finally:
        assert owner.close()


def test_blocked_input_write_has_a_total_deadline_and_reaps_its_worker(tmp_path, monkeypatch):
    path = worker(tmp_path, "import time; time.sleep(60)")
    processes, _ = capture_processes(monkeypatch)
    owner = transport.EnrollmentProcess(path, timeout_s=.4)
    started = monotonic()
    try:
        with pytest.raises(transport.EnrollmentTimeoutError, match="deadline expired"):
            owner.exchange(b"x" * transport.MAX_REQUEST_BYTES)
        assert monotonic() - started < 4
        assert_closed(owner, processes)
    finally:
        assert owner.close()


def test_response_does_not_finish_until_the_actual_process_exits(tmp_path, monkeypatch):
    path = worker(tmp_path, "import sys,time; sys.stdin.buffer.read(); sys.stdout.buffer.write(b'not yet complete'); sys.stdout.buffer.flush(); time.sleep(60)")
    processes, _ = capture_processes(monkeypatch)
    owner = transport.EnrollmentProcess(path, timeout_s=.4)
    try:
        with pytest.raises(transport.EnrollmentTimeoutError):
            owner.exchange(b"request")
        assert_closed(owner, processes)
    finally:
        assert owner.close()


def test_parent_caps_output_before_eof_and_discards_excess(tmp_path, monkeypatch):
    path = worker(tmp_path, "import sys,time; sys.stdin.buffer.read(); sys.stdout.buffer.write(b'x'*1048576); sys.stdout.buffer.flush(); time.sleep(60)")
    processes, _ = capture_processes(monkeypatch)
    owner = transport.EnrollmentProcess(path, max_response_bytes=4096)
    started = monotonic()
    try:
        with pytest.raises(transport.EnrollmentOutputError, match="pipe limit"):
            owner.exchange(b"request")
        assert len(owner._response) == 4097
        assert monotonic() - started < 10
        assert_closed(owner, processes)
    finally:
        assert owner.close()


def test_child_failure_and_stderr_never_expose_private_input(tmp_path, monkeypatch, capsys):
    path = worker(tmp_path, "import sys; sys.stdin.buffer.read(); sys.stderr.write('private_enrollment_stderr_sentinel'); raise SystemExit(7)")
    processes, _ = capture_processes(monkeypatch)
    owner = transport.EnrollmentProcess(path)
    try:
        with pytest.raises(transport.EnrollmentProcessError) as caught:
            owner.exchange(b"private_enrollment_input_sentinel")
        assert "sentinel" not in str(caught.value)
        captured = capsys.readouterr()
        assert "sentinel" not in captured.out + captured.err
        assert_closed(owner, processes)
    finally:
        assert owner.close()


def test_unconfirmed_tree_cleanup_retains_handles_for_retry(tmp_path, monkeypatch):
    path = worker(tmp_path, "import sys; sys.stdin.buffer.read(); sys.stdout.buffer.write(b'answer')")
    processes, _ = capture_processes(monkeypatch)
    owner = transport.EnrollmentProcess(path)
    finish = transport._ProcessTree.finish
    monkeypatch.setattr(transport._ProcessTree, "finish", lambda self, deadline: False)
    try:
        with pytest.raises(transport.EnrollmentProcessError, match="cleanup could not be confirmed"):
            owner.exchange(b"request")
        assert owner.owned and owner._process is processes[0] and owner._tree is not None
        assert owner.close(timeout_s=.1) is False
        assert owner.owned
        with pytest.raises(transport.EnrollmentProcessError, match="no longer available"):
            owner.exchange(b"another request")
        assert len(processes) == 1
    finally:
        monkeypatch.setattr(transport._ProcessTree, "finish", finish)
        assert owner.close()
    assert_closed(owner, processes)


def test_spawn_failure_has_a_fixed_error_and_leaves_no_owner(tmp_path, monkeypatch):
    path = worker(tmp_path, "raise AssertionError('must not start')")

    def fail(*args, **kwargs):
        raise OSError("private_enrollment_spawn_sentinel")

    monkeypatch.setattr(transport.subprocess, "Popen", fail)
    owner = transport.EnrollmentProcess(path)
    with pytest.raises(transport.EnrollmentProcessError) as caught:
        owner.exchange(b"private_enrollment_input_sentinel")
    assert str(caught.value) == "Enrollment process could not be started"
    assert not owner.owned and owner.close()


def test_absolute_deadline_survives_a_gap_between_construction_and_exchange(tmp_path, monkeypatch):
    path = worker(tmp_path, "raise AssertionError('expired request must not start')")
    calls = []

    def start(*args, **kwargs):
        calls.append(True)
        raise AssertionError("Expired request started a process")

    monkeypatch.setattr(transport.subprocess, "Popen", start)
    owner = transport.EnrollmentProcess(path, deadline=monotonic() + .05)
    sleep(.1)
    with pytest.raises(transport.EnrollmentTimeoutError):
        owner.exchange(b"private_enrollment_expired_input_sentinel")
    assert calls == [] and not owner.owned and owner.close()


def test_assignment_failure_reaps_the_unreleased_process_before_input(tmp_path, monkeypatch):
    marker = tmp_path / "must-not-execute.txt"
    path = worker(tmp_path, "import sys; from pathlib import Path; sys.stdin.buffer.read(); "
        f"Path({str(marker)!r}).write_text('input was released')")
    processes, _ = capture_processes(monkeypatch)

    def fail(*args, **kwargs):
        raise OSError("private_enrollment_assignment_sentinel")

    monkeypatch.setattr(transport, "_ProcessTree", fail)
    owner = transport.EnrollmentProcess(path)
    try:
        with pytest.raises(transport.EnrollmentProcessError, match="could not be started"):
            owner.exchange(b"must not leave the parent")
        assert not marker.exists()
        assert_closed(owner, processes)
    finally:
        assert owner.close()


@pytest.mark.parametrize("phase", ["launch", "assignment", "resume"])
def test_expired_native_startup_never_offers_private_input(tmp_path, monkeypatch, phase):
    marker = tmp_path / "private-input-received.txt"
    path = worker(tmp_path, "import sys\nfrom pathlib import Path\ndata=sys.stdin.buffer.read()\n"
        f"if data: Path({str(marker)!r}).write_text('private input was received')\n")
    processes, _ = capture_processes(monkeypatch)
    if phase == "launch":
        start = transport.subprocess.Popen

        def delayed(*args, **kwargs):
            process = start(*args, **kwargs)
            sleep(.2)
            return process

        monkeypatch.setattr(transport.subprocess, "Popen", delayed)
    else:
        operation = transport._ProcessTree.__init__ if phase == "assignment" else transport._ProcessTree.resume

        def delayed(*args, **kwargs):
            operation(*args, **kwargs)
            sleep(.2)

        monkeypatch.setattr(transport._ProcessTree, "__init__" if phase == "assignment" else "resume", delayed)
    owner = transport.EnrollmentProcess(path, timeout_s=.05)
    offers = []
    write = owner._write

    def observed(*args, **kwargs):
        offers.append(True)
        return write(*args, **kwargs)

    monkeypatch.setattr(owner, "_write", observed)
    try:
        with pytest.raises(transport.EnrollmentTimeoutError):
            owner.exchange(b"private_enrollment_late_start_sentinel")
        assert offers == [] and not marker.exists()
        assert_closed(owner, processes)
    finally:
        assert owner.close()


def test_cleanup_budget_retains_a_live_pipe_thread_for_later_close(tmp_path, monkeypatch):
    path = worker(tmp_path, "import time; time.sleep(60)")
    processes, _ = capture_processes(monkeypatch)
    owner = transport.EnrollmentProcess(path)
    entered, release = Event(), Event()
    read = owner._read

    def held_read(stream):
        entered.set()
        assert release.wait(5)
        read(stream)

    monkeypatch.setattr(owner, "_read", held_read)
    try:
        with owner._lock:
            owner._start(b"request", monotonic() + 10)
        assert entered.wait(2)
        started = monotonic()
        assert owner.close(timeout_s=.15) is False
        assert monotonic() - started < .6
        assert owner.owned and any(thread.is_alive() for thread in owner._threads)
        assert all(process.poll() is not None for process in processes)
    finally:
        release.set()
        assert owner.close()
    assert_closed(owner, processes)


def test_close_can_interrupt_an_exchange_without_abandoning_pipe_threads(tmp_path, monkeypatch):
    path = worker(tmp_path, "import time; time.sleep(60)")
    processes, _ = capture_processes(monkeypatch)
    owner = transport.EnrollmentProcess(path)
    errors = []

    def exchange():
        try:
            owner.exchange(b"x" * transport.MAX_REQUEST_BYTES)
        except transport.EnrollmentProcessError:
            errors.append("closed")

    caller = Thread(target=exchange, daemon=True)
    caller.start()
    try:
        deadline = monotonic() + 3
        while not owner.owned and monotonic() < deadline:
            sleep(.01)
        assert owner.owned
        assert owner.close()
        caller.join(3)
        assert not caller.is_alive() and errors == ["closed"]
        assert_closed(owner, processes)
    finally:
        assert owner.close()
        caller.join(3)


def test_close_budget_does_not_wait_on_the_startup_lock_or_release_private_input(tmp_path, monkeypatch):
    marker = tmp_path / "late-input.txt"
    path = worker(tmp_path, "import sys\nfrom pathlib import Path\ndata=sys.stdin.buffer.read()\n"
        f"if data: Path({str(marker)!r}).write_text('private input was received')\n")
    processes, _ = capture_processes(monkeypatch)
    started, release = Event(), Event()
    popen = transport.subprocess.Popen
    def held_start(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return popen(*args, **kwargs)
    monkeypatch.setattr(transport.subprocess, "Popen", held_start)
    owner = transport.EnrollmentProcess(path)
    errors = []
    def exchange():
        try:
            owner.exchange(b"private_enrollment_start_lock_sentinel")
        except transport.EnrollmentProcessError:
            errors.append("closed")
    caller = Thread(target=exchange, daemon=True)
    caller.start()
    try:
        assert started.wait(2)
        before = monotonic()
        assert owner.close(timeout_s=.15) is False
        assert monotonic() - before < .6
        assert owner._closing.is_set()
        release.set()
        caller.join(5)
        assert not caller.is_alive() and errors == ["closed"]
        assert not marker.exists()
        assert_closed(owner, processes)
    finally:
        release.set()
        caller.join(5)
        assert owner.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows suspended venv launcher and job descendant ownership")
def test_windows_timeout_reaps_the_actual_child_and_early_descendants(tmp_path, monkeypatch):
    import ctypes
    from ctypes import wintypes

    marker = tmp_path / "descendant.txt"
    path = worker(tmp_path, "import subprocess,sys,time; from pathlib import Path; "
        "child=subprocess.Popen([sys.executable,'-I','-B','-c','import time; time.sleep(60)'],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
        f"Path({str(marker)!r}).write_text(str(child.pid)); time.sleep(60)")
    processes, _ = capture_processes(monkeypatch)
    owner = transport.EnrollmentProcess(path)
    errors = []
    captured_before_stop = set()
    capture = transport._ProcessTree._capture_members
    def observed_capture(tree):
        capture(tree)
        if not tree._stop_requested:
            captured_before_stop.update(tree._members)
    monkeypatch.setattr(transport._ProcessTree, "_capture_members", observed_capture)

    def exchange():
        try:
            owner.exchange(b"request")
        except transport.EnrollmentTimeoutError:
            errors.append("timeout")
        except transport.EnrollmentProcessError:
            errors.append("closed")

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    kernel.IsProcessInJob.restype = wintypes.BOOL
    handle = None
    caller = Thread(target=exchange, daemon=True)
    caller.start()
    try:
        deadline = monotonic() + 8
        while not marker.exists() and monotonic() < deadline:
            sleep(.01)
        assert marker.exists()
        child_pid = int(marker.read_text())
        handle = kernel.OpenProcess(0x00100000 | 0x1000, False, child_pid)  # SYNCHRONIZE | QUERY_LIMITED_INFORMATION
        assert handle and kernel.WaitForSingleObject(handle, 0) == 0x102
        in_job = wintypes.BOOL()
        assert kernel.IsProcessInJob(handle, owner._tree._job, ctypes.byref(in_job)) and in_job.value
        caller.join(12)
        assert not caller.is_alive() and errors == ["timeout"]
        assert child_pid in captured_before_stop
        assert kernel.WaitForSingleObject(handle, 0) == 0
        assert_closed(owner, processes)
    finally:
        assert owner.close()
        caller.join(3)
        if handle:
            assert kernel.CloseHandle(handle)


@pytest.mark.parametrize("timeout", [0, -1, True, float("nan"), float("inf")])
def test_invalid_deadline_is_rejected_without_starting_a_worker(tmp_path, timeout):
    with pytest.raises(ValueError, match="timeout"):
        transport.EnrollmentProcess(tmp_path / "unused.py", timeout_s=timeout)
