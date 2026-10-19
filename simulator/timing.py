"""Scoped Windows timer precision for short simulation waits.

The requested resolution belongs to this process and is released even when a
run fails. It affects wake-up precision, never the simulator's physics clock.
Other platforms retain their native timer behavior.
"""

from contextlib import contextmanager
import gc
import sys


@contextmanager
def high_resolution_timer():
    info = {"clock": "perf_counter", "windows_timer_period_ms": None,
            "windows_timer_enabled": False}
    library = None
    acquired = False
    if sys.platform == "win32":
        try:
            import ctypes
            library = ctypes.WinDLL("winmm", use_last_error=True)
            for name in ("timeBeginPeriod", "timeEndPeriod"):
                operation = getattr(library, name)
                operation.argtypes = [ctypes.c_uint]
                operation.restype = ctypes.c_uint
            if library.timeBeginPeriod(1) == 0:
                acquired = True
                info.update(windows_timer_period_ms=1, windows_timer_enabled=True)
        except (OSError, AttributeError):
            # Precision is an optimization, not a dependency for correctness.
            pass
    try:
        yield info
    finally:
        if acquired:
            library.timeEndPeriod(1)


@contextmanager
def latency_gc(enabled: bool = False):
    """Opt in to less frequent automatic collections during a dedicated run.

    Garbage collection stays enabled or disabled exactly as the caller left it.
    The generation-zero threshold is raised to at least 50,000 allocations;
    older-generation thresholds stay unchanged. This prevents frequent scans
    of a large, mostly immutable map and recorded evidence graph. Collection
    still occurs automatically at the raised threshold, with no forced pause
    inside the driving loop. Object counts do not imply a fixed byte limit.

    Thresholds belong to the process, so this option is intended for the
    dedicated reference-pilot CLI. Lexically nested scopes restore correctly;
    unrelated concurrent code must not modify these process-global settings.
    """
    if type(enabled) is not bool:
        raise ValueError("GC latency option must be a boolean")
    previous = gc.get_threshold()
    active = (max(previous[0], 50_000), *previous[1:]) if enabled else previous
    info = {"python_gc_latency_mode": enabled, "python_gc_enabled": gc.isenabled(),
            "python_gc_thresholds_before": list(previous),
            "python_gc_thresholds_active": list(active)}
    try:
        if enabled:
            gc.set_threshold(*active)
        yield info
    finally:
        if enabled:
            gc.set_threshold(*previous)
