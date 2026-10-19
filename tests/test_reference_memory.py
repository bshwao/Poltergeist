"""Memory measurement reuses bindings while reading fresh OS values."""

import ctypes
from types import SimpleNamespace

import pytest

from simulator import reference_evidence as evidence


@pytest.fixture
def native_memory(monkeypatch):
    state = {"loaded": [], "opened": [], "closed": [], "peak": 4096, "fail": False}

    def open_process(access, inherit, pid):
        state["opened"].append(pid)
        return pid if pid > 0 else 0

    def close_handle(handle):
        state["closed"].append(handle)

    def memory_info(handle, pointer, size):
        if state["fail"]:
            return 0
        pointer._obj.PeakWorkingSetSize = state["peak"]
        return 1

    def library(name, **kwargs):
        state["loaded"].append(name)
        return (SimpleNamespace(OpenProcess=open_process, CloseHandle=close_handle)
                if name == "kernel32" else SimpleNamespace(GetProcessMemoryInfo=memory_info))

    evidence._windows_memory_reader.cache_clear()
    monkeypatch.setattr(ctypes, "WinDLL", library, raising=False)
    monkeypatch.setattr(evidence.sys, "platform", "win32")
    yield state
    evidence._windows_memory_reader.cache_clear()


def test_bindings_reused_but_values_and_handles_remain_fresh(native_memory):
    state = native_memory
    assert evidence.process_peak_memory_bytes(10) == 4096
    state["peak"] = 8192
    assert evidence.process_peak_memory_bytes(11) == 8192
    assert state["loaded"] == ["kernel32", "psapi"]
    assert state["opened"] == state["closed"] == [10, 11]


def test_failed_measurements_remain_unknown_and_close_handles(native_memory):
    native_memory["fail"] = True
    assert evidence.process_peak_memory_bytes(10) is None
    assert native_memory["closed"] == [10]
    assert evidence.process_peak_memory_bytes(0) is None
    assert native_memory["closed"] == [10]
    assert evidence.process_peak_memory_bytes(None) is None
