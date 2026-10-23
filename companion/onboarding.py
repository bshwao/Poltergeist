"""Bounded, local first-run inspection; detection never starts the simulator."""

from collections import deque
from datetime import UTC, datetime
from itertools import islice
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

from .profile import CompanionConfig, local_settings, validate_installation, validate_windows_carla_root

MAX_DIRECTORIES = 256
MAX_CHILDREN = 128
MAX_ENTRIES = 4096
MAX_DEPTH = 2


def default_search_roots():
    home = Path.home()
    roots = [home / "Downloads", home / "Documents", home / "OneDrive" / "Documents"]
    if os.name == "nt":
        roots.extend([Path("C:/CARLA_0.9.16"), Path("C:/CARLA"), Path("D:/CARLA_0.9.16"), Path("D:/CARLA")])
    return roots


def detect_carla(roots=None):
    """Search a small known-directory tree; Browse handles other locations."""
    pending = deque((Path(root), 0) for root in (default_search_roots() if roots is None else roots))
    seen, accepted, rejected = set(), [], []
    scanned = inaccessible = 0
    limited = False
    while pending and scanned < MAX_DIRECTORIES:
        directory, depth = pending.popleft()
        try:
            if directory.is_symlink() or getattr(directory, "is_junction", lambda: False)():
                continue
            directory = directory.resolve()
            key = os.path.normcase(str(directory))
            if key in seen or not directory.is_dir():
                continue
            seen.add(key)
            scanned += 1
            candidate = directory / "CarlaUE4.exe"
            shipping = directory / "CarlaUE4" / "Binaries" / "Win64" / "CarlaUE4-Win64-Shipping.exe"
            if candidate.is_file():
                try:
                    validate_windows_carla_root(candidate)
                    accepted.append({"executable": str(candidate), "version_verified": False})
                except ValueError as error:
                    rejected.append({"path": str(candidate), "reason": str(error)})
            elif shipping.is_file():
                rejected.append({"path": str(shipping), "reason": "Missing root CarlaUE4.exe; choose a complete installation containing Engine."})
            if depth < MAX_DEPTH:
                with os.scandir(directory) as entries:
                    entries = list(islice(entries, MAX_ENTRIES + 1))
                children = [entry for entry in entries[:MAX_ENTRIES]
                            if not entry.name.startswith(".") and entry.is_dir(follow_symlinks=False)]
                if len(entries) > MAX_ENTRIES or len(children) > MAX_CHILDREN:
                    limited = True
                for child in children[:MAX_CHILDREN]:
                    pending.append((Path(child.path), depth + 1))
        except OSError:
            inaccessible += 1
    return {"schema_version": "poltergeist-carla-detection-v1", "installations": accepted,
            "rejected": rejected, "directories_scanned": scanned, "inaccessible": inaccessible,
            "search_limited": limited or bool(pending), "version_verified": False,
            "scope": "Bounded local file-layout inspection. No version RPC, simulator startup or world changes."}


def _windows_hardware():
    import ctypes
    from ctypes import wintypes
    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("load", wintypes.DWORD),
                    *((name, ctypes.c_ulonglong) for name in ("total", "available", "page_total", "page_available", "virtual_total", "virtual_available", "extended"))]
    memory = MemoryStatus()
    memory.length = ctypes.sizeof(memory)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MemoryStatus)]
    total = int(memory.total) if kernel.GlobalMemoryStatusEx(ctypes.byref(memory)) else None
    class DisplayDevice(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("name", wintypes.WCHAR * 32),
                    ("description", wintypes.WCHAR * 128), ("flags", wintypes.DWORD),
                    ("identifier", wintypes.WCHAR * 128), ("registry_key", wintypes.WCHAR * 128)]
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.EnumDisplayDevicesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(DisplayDevice), wintypes.DWORD]
    adapters = []
    for index in range(16):
        device = DisplayDevice()
        device.size = ctypes.sizeof(device)
        if not user.EnumDisplayDevicesW(None, index, ctypes.byref(device), 0):
            break
        if device.description and device.description not in adapters:
            adapters.append(device.description)
    return total, adapters


def inspect_system(profile):
    path = Path(profile).resolve()
    while not path.exists() and path != path.parent:
        path = path.parent
    disk = shutil.disk_usage(path)
    memory, displays = _windows_hardware() if os.name == "nt" else (None, [])
    return {"schema_version": "poltergeist-setup-system-v1", "checked_at": datetime.now(UTC).isoformat(),
            "platform": platform.platform(), "machine": platform.machine(), "logical_cpus": os.cpu_count(),
            "physical_memory_bytes": memory, "display_adapters": displays,
            "disk_free_bytes": disk.free, "disk_total_bytes": disk.total,
            "scope": "Observed hardware and disk space; not a CARLA or model performance qualification."}


_MAP_PROBE = r'''
import json,sys,carla
client=carla.Client('127.0.0.1',int(sys.argv[1]));client.set_timeout(3.)
client_version=client.get_client_version();server_version=client.get_server_version()
if client_version!='0.9.16' or server_version!='0.9.16':
 raise RuntimeError('CARLA requires matching 0.9.16 client/server; found client '+client_version+' and server '+server_version)
world=client.get_world()
print('POLTERGEIST_SETUP='+json.dumps({'client_version':client_version,'server_version':server_version,
 'map':world.get_map().name,'available_maps':client.get_available_maps(),
 'synchronous_mode':world.get_settings().synchronous_mode}))
'''


def check_connection(config):
    config = CompanionConfig.model_validate(config.model_dump())
    result = subprocess.run([sys.executable, "-I", "-B", "-c", _MAP_PROBE, str(config.carla_port)],
        capture_output=True, text=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        detail = (result.stderr.strip().splitlines() or ["CARLA RPC did not return a result"])[-1]
        raise RuntimeError(f"CARLA connection check failed: {detail[:500]}. Start the matching server or check the local RPC port.")
    for line in reversed(result.stdout.splitlines()):
        if line.startswith("POLTERGEIST_SETUP="):
            return json.loads(line.removeprefix("POLTERGEIST_SETUP="))
    raise RuntimeError("CARLA connection check returned no structured result")


def start_carla(profile, config, map_name, *, cancellation=lambda: False):
    """Explicit startup reuses the existing launcher and leaves an existing map alone."""
    from simulator.carla_launcher import LocalCarlaLauncher
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", map_name):
        raise ValueError("Choose a CARLA map name, for example Town10HD_Opt")
    validate_installation(config)
    if config.carla_executable is None:
        raise ValueError("Browse to the complete CARLA installation first")
    import carla
    settings = local_settings(profile, config).model_copy(update={"carla_auto_start": True})
    client = carla.Client(settings.carla_host, settings.carla_port)
    client.set_timeout(settings.carla_timeout)
    LocalCarlaLauncher(settings).ensure_running(client, map_name, cancellation=cancellation,
        reconnect=lambda: carla.Client(settings.carla_host, settings.carla_port))
    return check_connection(config)
