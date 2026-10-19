"""Short Windows waits use a scoped, balanced resolution request."""

import ctypes
import gc

import pytest

from simulator import timing


class Operation:
    def __init__(self, calls, name, result=0):
        self.calls, self.name, self.result = calls, name, result

    def __call__(self, period):
        self.calls.append((self.name, period))
        return self.result


def library(monkeypatch, *, result=0):
    calls = []
    fake = type("Library", (), {})()
    fake.timeBeginPeriod = Operation(calls, "begin", result)
    fake.timeEndPeriod = Operation(calls, "end")
    monkeypatch.setattr(timing.sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: fake, raising=False)
    return calls


def test_timer_request_is_balanced_even_on_failure(monkeypatch):
    calls = library(monkeypatch)
    with pytest.raises(ValueError, match="run failed"):
        with timing.high_resolution_timer() as info:
            assert info == {"clock": "perf_counter", "windows_timer_period_ms": 1,
                            "windows_timer_enabled": True}
            assert calls == [("begin", 1)]
            raise ValueError("run failed")
    assert calls == [("begin", 1), ("end", 1)]


def test_rejected_timer_request_needs_no_release(monkeypatch):
    calls = library(monkeypatch, result=97)
    with timing.high_resolution_timer() as info:
        assert info["windows_timer_enabled"] is False
        assert info["windows_timer_period_ms"] is None
    assert calls == [("begin", 1)]


def test_metadata_mutation_cannot_suppress_release(monkeypatch):
    calls = library(monkeypatch)
    with timing.high_resolution_timer() as info:
        info["windows_timer_enabled"] = False
    assert calls == [("begin", 1), ("end", 1)]


def test_nested_runs_balance_individual_requests(monkeypatch):
    calls = library(monkeypatch)
    with timing.high_resolution_timer():
        with timing.high_resolution_timer():
            pass
    assert calls == [("begin", 1), ("begin", 1), ("end", 1), ("end", 1)]


def test_unavailable_library_keeps_native_timers(monkeypatch):
    monkeypatch.setattr(timing.sys, "platform", "win32")
    def missing(*a, **k):
        raise OSError("unavailable")
    monkeypatch.setattr(ctypes, "WinDLL", missing, raising=False)
    with timing.high_resolution_timer() as info:
        assert info["windows_timer_enabled"] is False


def test_non_windows_never_loads_timer_library(monkeypatch):
    monkeypatch.setattr(timing.sys, "platform", "linux")
    def forbidden(*a, **k):
        pytest.fail("Windows timer library loaded on another platform")
    monkeypatch.setattr(ctypes, "WinDLL", forbidden, raising=False)
    with timing.high_resolution_timer() as info:
        assert info["windows_timer_enabled"] is False


@pytest.fixture
def gc_settings():
    """Each test restores the real process settings, including failed tests."""
    original_thresholds, originally_enabled = gc.get_threshold(), gc.isenabled()
    try:
        yield
    finally:
        gc.set_threshold(*original_thresholds)
        if originally_enabled:
            gc.enable()
        else:
            gc.disable()


def test_gc_latency_is_opt_in_and_records_unchanged_settings(gc_settings):
    gc.set_threshold(700, 11, 12)
    previous = gc.get_threshold()  # Python 3.14 ignores threshold2.
    with timing.latency_gc() as info:
        assert gc.get_threshold() == previous
        assert info == {"python_gc_latency_mode": False, "python_gc_enabled": gc.isenabled(),
                        "python_gc_thresholds_before": list(previous),
                        "python_gc_thresholds_active": list(previous)}
    assert gc.get_threshold() == previous


def test_gc_latency_raises_only_young_generation_threshold_and_restores(gc_settings):
    gc.set_threshold(700, 11, 12)
    previous = gc.get_threshold()
    active = (50_000, *previous[1:])
    gc.enable()
    with timing.latency_gc(enabled=True) as info:
        assert gc.get_threshold() == active
        assert gc.isenabled() and info["python_gc_enabled"] is True
        assert info["python_gc_thresholds_before"] == list(previous)
        assert info["python_gc_thresholds_active"] == list(active)
    assert gc.get_threshold() == previous
    assert gc.isenabled()


def test_gc_latency_restores_exact_thresholds_after_error(gc_settings):
    gc.set_threshold(2_001, 13, 0)
    with pytest.raises(RuntimeError, match="run failed"):
        with timing.latency_gc(enabled=True):
            assert gc.get_threshold() == (50_000, 13, 0)
            raise RuntimeError("run failed")
    assert gc.get_threshold() == (2_001, 13, 0)


def test_nested_gc_latency_contexts_restore_their_own_entry_settings(gc_settings):
    gc.set_threshold(700, 10, 10)
    previous = gc.get_threshold()
    active = (50_000, *previous[1:])
    with timing.latency_gc(enabled=True):
        assert gc.get_threshold() == active
        with timing.latency_gc(enabled=True) as inner:
            assert inner["python_gc_thresholds_before"] == list(active)
            assert gc.get_threshold() == active
        assert gc.get_threshold() == active
        with timing.latency_gc(enabled=False):
            assert gc.get_threshold() == active
        assert gc.get_threshold() == active
    assert gc.get_threshold() == previous


def test_gc_latency_preserves_already_disabled_collector_and_high_custom_threshold(gc_settings):
    gc.set_threshold(80_001, 24, 7)
    previous = gc.get_threshold()
    gc.disable()
    with timing.latency_gc(enabled=True) as info:
        assert not gc.isenabled() and info["python_gc_enabled"] is False
        assert gc.get_threshold() == previous
    assert not gc.isenabled()
    assert gc.get_threshold() == previous


def test_gc_latency_cleanup_ignores_mutation_of_returned_metadata(gc_settings):
    gc.set_threshold(700, 10, 10)
    previous = gc.get_threshold()
    with timing.latency_gc(enabled=True) as info:
        info["python_gc_latency_mode"] = False
        info["python_gc_thresholds_before"][:] = [1, 1, 1]
    assert gc.get_threshold() == previous


@pytest.mark.parametrize("enabled", [None, 0, 1, "yes"])
def test_gc_latency_option_requires_bool(enabled):
    with pytest.raises(ValueError, match="boolean"):
        with timing.latency_gc(enabled=enabled):
            pass
