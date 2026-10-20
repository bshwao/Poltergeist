"""Start a configured local CARLA installation only when executing a live run.

The browser supplies a scenario map, never a command or executable path. Existing
listeners are reused even when their RPC is unresponsive; a second server is not
started to compete with them. The game remains open after a run or cancellation.
"""

import ipaddress
import os
from pathlib import Path
import re
import socket
import subprocess
from threading import Lock
from time import monotonic, sleep

from .errors import SimulationCancelled, SimulationError


def local_host(host):
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def find_executable(configured=None):
    if configured is not None:
        path = Path(configured)
        if not path.is_file():
            raise SimulationError(f"CARLA executable was not found: {path}. Correct CARLA_EXECUTABLE and restart the backend.")
        return path
    filename = "CarlaUE4.exe" if os.name == "nt" else "CarlaUE4.sh"
    home = Path.home()
    roots = [home / "Documents", home / "OneDrive" / "Documents", home / "Downloads", home,
             Path("C:/") if os.name == "nt" else Path("/opt")]
    for root in roots:
        for name in ("CARLA_0.9.16", "CARLA", "Carla", "WindowsNoEditor"):
            for suffix in (Path(filename), Path("WindowsNoEditor") / filename, Path("LinuxNoEditor") / filename):
                candidate = root / name / suffix
                if candidate.is_file():
                    return candidate.resolve()
    raise SimulationError("CARLA is closed and its installation was not found. Set CARLA_EXECUTABLE in the backend .env to the full CarlaUE4.exe (or CarlaUE4.sh) path, then restart the backend; alternatively start CARLA manually.")


def port_open(host, port):
    try:
        with socket.create_connection((host, port), timeout=.3):
            return True
    except OSError:
        return False


class LocalCarlaLauncher:
    def __init__(self, settings):
        self.settings = settings
        self._lock = Lock()
        self._process = None
        self._started_at = None
        self._ready = False

    def ensure_running(self, client, map_name, *, quality=None, cancellation=lambda: False, reconnect=None):
        settings = self.settings
        if not settings.carla_auto_start or not local_host(settings.carla_host):
            return client
        with self._lock:
            if cancellation():
                raise SimulationCancelled("Simulation cancelled before CARLA startup")
            listening = port_open(settings.carla_host, settings.carla_port)
            if not listening and (self._process is None or self._ready and self._process.poll() is not None):
                # Restrict the map argument to a package name, never Unreal flags.
                name = map_name.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
                if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
                    raise SimulationError("Choose a valid CARLA map name before starting the simulator")
                executable = find_executable(settings.carla_executable)
                if os.name == "nt" and executable.suffix.lower() != ".exe":
                    raise SimulationError("CARLA_EXECUTABLE must point to CarlaUE4.exe on Windows")
                command = (["bash", str(executable)] if executable.suffix == ".sh" else [str(executable)])
                command += [f"/Game/Carla/Maps/{name}", f"-carla-rpc-port={settings.carla_port}",
                            f"-quality-level={quality or settings.carla_quality}", "-windowed", "-ResX=640", "-ResY=360", "-nosound"]
                try:
                    self._process = subprocess.Popen(command, cwd=executable.parent, stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, shell=False,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), start_new_session=os.name != "nt")
                except OSError as error:
                    raise SimulationError(f"Could not start CARLA: {error}. Check CARLA_EXECUTABLE and the simulator installation.") from error
                self._started_at = monotonic()
                self._ready = False
            deadline = (self._started_at if not listening and not self._ready and self._started_at is not None else monotonic()) + settings.carla_startup_timeout_s
            try:
                while monotonic() < deadline:
                    if cancellation():
                        raise SimulationCancelled("Simulation cancelled while CARLA was starting; the simulator remains open")
                    if port_open(settings.carla_host, settings.carla_port):
                        # CARLA's RPC client can retain a failed connection made
                        # before the game was listening. Retry with a fresh one.
                        if reconnect is not None:
                            client = reconnect()
                        client.set_timeout(min(2., max(.1, deadline - monotonic())))
                        try:
                            client.get_world()
                            self._ready = True
                            return client
                        except RuntimeError:
                            pass
                    if self._process is not None and self._process.poll() not in (None, 0):
                        code = self._process.returncode
                        self._process = None
                        raise SimulationError(f"CARLA exited during startup (code {code}). Inspect its window/logs and graphics settings.")
                    sleep(.25)
            finally:
                client.set_timeout(settings.carla_timeout)
            raise SimulationError(f"CARLA did not become ready within {settings.carla_startup_timeout_s:g} seconds. Check the existing CARLA window and port {settings.carla_port}; no duplicate instance was started. A longer CARLA_STARTUP_TIMEOUT_S can be configured for slow startup.")
