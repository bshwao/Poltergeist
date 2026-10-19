"""Local evidence for the reference AI pilot, separate from durable API reports.

Measurements are deliberately literal: a completed synthetic run does not prove
CARLA control, a file hash does not prove model inference, and a synchronous
simulation can be much slower than real time.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from typing import Any

from shared.models import ActorState, AgentObservation, AgentStepResult, ControlInput, TelemetryFrame
from simulator.agents.package import AgentPackage


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_fingerprint(package: AgentPackage) -> dict[str, Any]:
    """Hash declared files and the inference source without importing agent code."""
    relative_module = package.module_path.relative_to(package.directory).as_posix()
    names = {"agent.json", relative_module, *package.manifest.assets}
    if package.manifest.requirements_file is not None:
        names.add(package.manifest.requirements_file)
    # Include package helpers: changing preprocessing changes the tested stack.
    names.update(path.relative_to(package.directory).as_posix() for path in package.directory.rglob("*.py"))
    files = {}
    for name in sorted(names):
        source = (package.directory / name).resolve(strict=True)
        if not source.is_relative_to(package.directory) or not source.is_file():
            raise ValueError(f"Fingerprint file escaped the agent package: {name}")
        files[name] = {"sha256": file_sha256(source), "size_bytes": source.stat().st_size}
    return {"manifest": package.manifest.model_dump(mode="json"), "files": files}


def latency_summary(values: Sequence[float], budget_s: float) -> dict[str, Any]:
    if type(budget_s) not in (float, int) or not math.isfinite(budget_s) or budget_s <= 0:
        raise ValueError("Latency budget must be a finite positive number")
    samples = list(values)
    if any(type(value) not in (float, int) or not math.isfinite(value) or value < 0 for value in samples):
        raise ValueError("Latency samples must be finite and non-negative")
    samples.sort()

    def percentile(fraction):
        if not samples:
            return None
        index = (len(samples) - 1) * fraction
        low = math.floor(index)
        high = math.ceil(index)
        return samples[low] + (samples[high] - samples[low]) * (index - low)

    misses = sum(value > budget_s for value in samples)
    return {
        "samples": len(samples), "p50_s": percentile(0.50),
        "p95_s": percentile(0.95), "p99_s": percentile(0.99),
        "max_s": max(samples) if samples else None,
        "mean_s": math.fsum(samples) / len(samples) if samples else None,
        "budget_s": budget_s, "budget_misses": misses,
        "budget_miss_fraction": misses / len(samples) if samples else None,
    }


class DecisionTrace:
    """Validate each observation/action pair and flush an exclusive JSONL file."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.records: list[dict[str, Any]] = []
        self._stream = self.path.open("x", encoding="utf-8", newline="\n")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self._stream.close()

    def __call__(self, record: Mapping[str, Any]):
        # Serialize first so we keep a detached primitive snapshot, never mutable
        # references from a runner, agent, or callback.
        encoded = json.dumps(dict(record), allow_nan=False, separators=(",", ":"))
        snapshot = json.loads(encoded)
        observation = AgentObservation.model_validate(snapshot["observation"])
        if (type(snapshot["frame"]) is not int or type(snapshot["time_s"]) not in (float, int)
                or snapshot["frame"] != observation.frame or snapshot["time_s"] != observation.time_s):
            raise ValueError("Decision trace frame/time must match its observation")
        if self.records and observation.frame != self.records[-1]["frame"] + 1:
            raise ValueError("Decision trace must contain consecutive frames")
        proposed = ControlInput.model_validate(snapshot["proposed_control"])
        ControlInput.model_validate(snapshot["applied_control"])
        if snapshot["agent_result"] is not None:
            result = AgentStepResult.model_validate(snapshot["agent_result"])
            if result.protocol_version != observation.protocol_version or result.frame != observation.frame or result.control != proposed:
                raise ValueError("Proposed control must match the frame-associated agent result")
        if type(snapshot["resulting_frame"]) is not int or snapshot["resulting_frame"] != observation.frame + 1:
            raise ValueError("Decision trace must include the next resulting frame")
        resulting_ego = ActorState.model_validate(snapshot["resulting_ego"])
        if resulting_ego.actor_id != observation.ego.actor_id:
            raise ValueError("Resulting ego must match the observed ego actor")
        if not isinstance(snapshot["interventions"], list) or any(type(item) is not str for item in snapshot["interventions"]):
            raise ValueError("Decision interventions must be a list of strings")
        for name in ("worker_step_wall_time_s", "round_trip_wall_time_s", "full_step_wall_time_s"):
            value = snapshot[name]
            if value is not None and (type(value) not in (float, int) or not math.isfinite(value) or value < 0):
                raise ValueError(f"{name} must be a finite non-negative measurement or null")
        # Validation reads the detached snapshot without changing it. Reuse its
        # complete serialization instead of encoding the same scene twice.
        self._stream.write(encoded + "\n")
        self._stream.flush()
        self.records.append(snapshot)


def _position(frame: TelemetryFrame):
    return None if frame.ego.transform is None else frame.ego.transform.position


def motion_summary(frames: Sequence[TelemetryFrame], *, stopped_speed_mps=0.2, stuck_after_s=3.0):
    distance = 0.0
    observed = bool(frames)
    for before, after in zip(frames, frames[1:]):
        first, second = _position(before), _position(after)
        if first is None or second is None:
            observed = False
            continue
        distance += math.dist((first.x, first.y, first.z), (second.x, second.y, second.z))
    speeds = []
    longest_stop = current_stop = 0.0
    for index, frame in enumerate(frames):
        velocity = frame.ego.velocity_mps
        speed = None if velocity is None else math.sqrt(velocity.x**2 + velocity.y**2 + velocity.z**2)
        speeds.append(speed)
        if index and speed is not None and speed < stopped_speed_mps:
            current_stop += frame.time_s - frames[index - 1].time_s
            longest_stop = max(longest_stop, current_stop)
        else:
            current_stop = 0.0
    first, last = (_position(frames[0]), _position(frames[-1])) if frames else (None, None)
    displacement = None if first is None or last is None else math.dist((first.x, first.y, first.z), (last.x, last.y, last.z))
    return {
        "distance_travelled_m": distance if observed else None,
        "displacement_m": displacement,
        "final_speed_mps": speeds[-1] if speeds else None,
        "longest_observed_stop_s": longest_stop if all(speed is not None for speed in speeds) and speeds else None,
        "stopped_speed_threshold_mps": stopped_speed_mps,
        "possible_stuck": (longest_stop >= stuck_after_s) if speeds and all(speed is not None for speed in speeds) else None,
        "possible_stuck_threshold_s": stuck_after_s,
        "stuck_interpretation": "A prolonged stop may be an appropriate hazard response; no driving-failure conclusion is inferred.",
    }


def route_progress(frames, records):
    """Project motion onto the supplied bounded route; never invent one."""
    if not frames or not records:
        return None
    observation = records[0].get("observation", {})
    scene = observation.get("scene") or {}
    raw_route = scene.get("route") or scene.get("route_points")
    if not isinstance(raw_route, list) or len(raw_route) < 2:
        return None
    points = []
    try:
        for item in raw_route:
            position = item.get("position", item.get("transform", {}).get("position"))
            points.append((float(position["x"]), float(position["y"])))
    except (TypeError, KeyError, ValueError, AttributeError):
        return None
    if not all(math.isfinite(value) for point in points for value in point):
        return None
    segments = []
    total = 0.0
    for a, b in zip(points, points[1:]):
        length = math.dist(a, b)
        if length:
            segments.append((a, b, length, total))
            total += length
    if not segments:
        return None

    def projection(frame):
        p = _position(frame)
        if p is None:
            return None
        best = None
        for a, b, length, offset in segments:
            along = min(1.0, max(0.0, ((p.x-a[0])*(b[0]-a[0]) + (p.y-a[1])*(b[1]-a[1])) / length**2))
            cross = math.dist((p.x, p.y), (a[0] + along*(b[0]-a[0]), a[1] + along*(b[1]-a[1])))
            value = (cross, offset + along*length)
            if best is None or value[0] < best[0]:
                best = value
        return best

    start, end = projection(frames[0]), projection(frames[-1])
    if start is None or end is None:
        return None
    return {"method": "nearest_segment_projection_on_supplied_route", "route_length_m": total,
            "initial_along_route_m": start[1], "final_along_route_m": end[1],
            "progress_m": end[1] - start[1], "final_cross_track_m": end[0]}


def summarize_run(output, records, *, total_wall_time_s, variant, process_stats=None, memory=None):
    result = output.result
    budget = result.simulation.scenario.fixed_delta_seconds
    summaries = {
        label: latency_summary([record[key] for record in records if record.get(key) is not None], budget)
        for label, key in (("agent_step", "worker_step_wall_time_s"), ("round_trip", "round_trip_wall_time_s"), ("full_step", "full_step_wall_time_s"))
    }
    setup = getattr(output, "setup_wall_time_s", None)
    execution = getattr(output, "execution_wall_time_s", None)
    rtf = result.measured_duration_s / execution if execution is not None and execution > 0 else None
    completed = result.simulation.status == "completed"
    complete_trace = len(records) == max(0, result.telemetry_frames - 1) and bool(records)
    match = all(record["proposed_control"] == record["applied_control"] for record in records) if records else None
    interventions = Counter(item for record in records for item in record["interventions"])
    inference_metadata = [record["agent_result"].get("diagnostics", {}).get("metadata", {})
                          for record in records if record.get("agent_result") and record["agent_result"].get("diagnostics")]
    network_times = [item["inference_wall_time_s"] for item in inference_metadata
                     if type(item.get("inference_wall_time_s")) in (int, float)
                     and math.isfinite(item["inference_wall_time_s"]) and item["inference_wall_time_s"] >= 0]
    summaries["inference"] = latency_summary(network_times, budget)
    cycle_times = list(getattr(output, "step_wall_times_s", ()))
    summaries["cycle"] = latency_summary(cycle_times, budget)
    checkpoint_hashes = sorted({item["checkpoint_sha256"] for item in inference_metadata if isinstance(item.get("checkpoint_sha256"), str)})
    motion = motion_summary(output.telemetry)
    gate = (completed and complete_trace and rtf is not None and rtf >= 1
            and summaries["full_step"]["samples"] == len(records)
            and summaries["cycle"]["samples"] == len(records)
            and summaries["cycle"]["budget_miss_fraction"] <= 0.05)
    return {
        "variant": variant, "mode": result.simulation.mode, "status": result.simulation.status.value,
        "error": result.simulation.error, "seed": result.simulation.scenario.seed,
        "scenario_name": result.simulation.scenario.name,
        "timing": {**summaries, "cycle_wall_times_s": cycle_times,
                   "environment": getattr(output, "timing_environment", None),
                   "setup_wall_time_s": setup, "execution_wall_time_s": execution,
                   "total_wall_time_s": total_wall_time_s, "simulated_execution_s": result.measured_duration_s,
                   "real_time_factor": rtf, "single_trial_throughput_gate": bool(gate),
                   "measurement_scope": "Inference is the agent-reported synchronized neural forward pass; agent_step includes feature preparation and control. Full step excludes evidence callback I/O. Cycle time, execution wall time and RTF include all evidence callbacks and I/O. Cycle samples align with decision records. Setup includes model warmup; all execution steps are retained.",
                   "gate_definition": "completed, complete trace and cycle measurements, RTF >= 1, at least 95% full cycles including evidence I/O within the fixed-step budget",
                   "real_time_claim": "No hard real-time guarantee; repeated live CARLA trials are required."},
        "control_evidence": {"recorded_steps": len(records), "complete_trace": complete_trace,
                             "proposed_controls_reached_adapter_unchanged": match,
                             "interventions": dict(interventions), "reported_checkpoint_sha256": checkpoint_hashes,
                             "live_carla_execution": completed and result.simulation.mode == "carla",
                             "ego_motion_observed": motion["displacement_m"] is not None and motion["displacement_m"] > 0.01,
                             "scope": "Frame association and applied controls establish the control connection. Checkpoint identity must also match the pinned model; these checks alone do not establish driving competence."},
        "metrics": result.metrics.model_dump(mode="json"), "motion": motion,
        "route_progress": route_progress(output.telemetry, records),
        "lane_departures": None,
        "lane_departures_note": "No lane-invasion stream is collected by this pilot; traffic-rule violations are reported separately.",
        "process": asdict(process_stats) if process_stats is not None else None,
        "memory": memory, "runtime": result.runtime.model_dump(mode="json") if result.runtime is not None else None,
        "limitations": ["Simulator-state input exposes hidden actors; this is not camera perception.",
                        "A supplied route and tracking controller assist driving; this is not independent global pathfinding.",
                        "Test mode uses synthetic kinematics and cannot verify live CARLA or real-world driving."],
    }


def compare_runs(reference, other):
    """Compare actual recorded motion; no causal or safety conclusion from one run."""
    matching_recipe = reference.result.simulation.scenario == other.result.simulation.scenario
    controls_changed = sum(a.control != b.control for a, b in zip(reference.telemetry[1:], other.telemetry[1:]))
    motion_changed = False
    for first, second in zip(reference.telemetry, other.telemetry):
        a, b = _position(first), _position(second)
        if a is not None and b is not None and math.dist((a.x, a.y, a.z), (b.x, b.y, b.z)) > 0.01:
            motion_changed = True
            break
    return {"same_recipe": matching_recipe, "aligned_samples": min(len(reference.telemetry), len(other.telemetry)),
            "changed_applied_control_frames": controls_changed, "motion_changed_over_1cm": motion_changed,
            "both_completed": reference.result.simulation.status == other.result.simulation.status == "completed",
            "interpretation": "A dedicated control ablation can demonstrate control-path influence. CARLA physics and NPC timing may vary across otherwise matched trials; no safety ranking is inferred."}


def gpu_snapshot():
    """An optional device-wide snapshot, including CARLA; never a peak estimate."""
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return {"available": False, "devices": None, "reason": "nvidia-smi is unavailable"}
    try:
        output = subprocess.run([executable, "--query-gpu=name,driver_version,memory.total,memory.used", "--format=csv,noheader,nounits"],
                                capture_output=True, text=True, check=True, timeout=2, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        devices = []
        for line in output.stdout.splitlines():
            name, driver, total, used = [item.strip() for item in line.split(",")]
            devices.append({"name": name, "driver_version": driver, "total_memory_mib": float(total), "used_memory_mib": float(used)})
        return {"available": True, "devices": devices, "measurement": "device-wide point snapshot; includes CARLA and other processes, not a peak"}
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return {"available": False, "devices": None, "reason": f"GPU measurement unavailable: {type(error).__name__}"}


def hardware_info():
    return {"platform": platform.platform(), "machine": platform.machine(),
            "processor": platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER") or None,
            "logical_cpus": os.cpu_count(), "parent_python": sys.version,
            "parent_python_executable": sys.executable, "gpu_before": gpu_snapshot()}


@lru_cache(maxsize=1)
def _windows_memory_reader():
    """Create native bindings once, without caching process handles or values.

    Rebuilding ctypes types and DLL wrappers per decision creates reference
    cycles and periodic collection pauses. Only the immutable bindings persist.
    """
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    *((name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage"))]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]

    def read(pid):
        handle = kernel.OpenProcess(0x0400 | 0x0010, False, pid)
        if not handle:
            return None
        try:
            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            return int(counters.PeakWorkingSetSize) if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb) else None
        finally:
            kernel.CloseHandle(handle)
    return read


def process_peak_memory_bytes(pid):
    """Read an OS high-water mark while the process exists; null when unavailable."""
    if pid is None:
        return None
    if sys.platform == "win32":
        return _windows_memory_reader()(pid)
    if sys.platform.startswith("linux"):
        try:
            for line in Path(f"/proc/{pid}/status").read_text(encoding="ascii").splitlines():
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) * 1024
        except (OSError, ValueError):
            pass
    return None
