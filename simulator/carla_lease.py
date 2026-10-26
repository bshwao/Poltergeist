"""Per-user, cross-process CARLA ownership, independent of a companion profile.

OS locks have no age-based expiry. A suspended owner keeps its lease. A durable
active marker survives process loss and fences the unfinished CARLA episode.
Files are never unlinked: replacing a locked inode would create a second owner.
"""

from hashlib import sha256
import ipaddress
import json
import os
from pathlib import Path
import socket
import stat
from threading import Lock

from .errors import SimulationError

_mutex = Lock()
_owners = set()
_BUSY = "Another Poltergeist execution owns this CARLA endpoint, or its lease is unavailable"
_RECOVERY = "Previous CARLA cleanup is unverified. Restart CARLA before retrying this endpoint"
_UNOBSERVED = "CARLA has no current asynchronous snapshot; recovery was not verified"


def _directory():
    # Do not use TEMP, a profile path, or an environment override: independent
    # installations/profiles belonging to this OS user must meet at one lock.
    if os.name == "nt":
        import ctypes
        value = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x28, None, 0, value) != 0 or not value.value:
            raise SimulationError(_BUSY)
        return Path(value.value) / "Poltergeist" / "CarlaEndpointLeases"
    import pwd
    return Path(pwd.getpwuid(os.getuid()).pw_dir) / ".cache" / "poltergeist" / "carla-endpoints"


def _address(value):
    address = ipaddress.ip_address(value.split("%", 1)[0])
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return "loopback" if address.is_loopback or address.is_unspecified else address.compressed


def endpoint_keys(host, port):
    host = host.rstrip(".").lower()
    try:
        addresses = {_address(host)}
        names = set()
    except ValueError:
        if host == "localhost":
            addresses, names = {"loopback"}, set()
        else:
            # Reserve every current resolved address as well as the configured
            # name, covering aliases and overlapping multi-address DNS answers.
            names = {"name:" + host.encode("idna").decode("ascii")}
            try:
                addresses = {_address(item[4][0]) for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
            except (OSError, ValueError, UnicodeError):
                raise SimulationError("The CARLA endpoint could not be resolved for execution ownership") from None
            if not addresses:
                raise SimulationError(_BUSY)
    return sorted(f"{key}:{port}" for key in names | {"address:" + value for value in addresses})


def _ordinary(path):
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
            raise SimulationError(_BUSY)


class CarlaEndpointLease:
    def __init__(self, settings, *, _root=None):
        self.settings = settings
        self.root = Path(_root) if _root is not None else _directory()
        if not self.root.is_absolute():
            raise ValueError("CARLA lease directory must be absolute")
        self._files = []
        self._dirty = False
        self._reserved = []

    def acquire(self):
        if self._files or self._reserved:
            raise SimulationError("This CARLA endpoint lease is already owned")
        try:
            _ordinary(self.root)
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            _ordinary(self.root)
            for key in endpoint_keys(self.settings.host, self.settings.port):
                path = self.root / (sha256(key.encode("utf-8")).hexdigest() + ".lock")
                _ordinary(path)
                registry = os.path.normcase(str(path))
                with _mutex:
                    if registry in _owners:
                        raise SimulationError(_BUSY)
                    _owners.add(registry)
                    self._reserved.append(registry)
                descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
                stream = os.fdopen(descriptor, "r+b", buffering=0)
                try:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        raise SimulationError(_BUSY)
                    if os.fstat(stream.fileno()).st_size == 0:
                        stream.write(b"L")
                    stream.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BaseException:
                    stream.close()
                    raise
                self._files.append(stream)
        except BaseException as error:
            self.release()
            if isinstance(error, (OSError, UnicodeError)):
                raise SimulationError(_BUSY) from None
            raise

    def _records(self):
        if not self._files:
            raise SimulationError("Acquire CARLA ownership before preparing its world")
        records = []
        for stream in self._files:
            stream.seek(0)
            raw = stream.read(1025)
            if raw == b"L":
                records.append(None)
                continue
            try:
                value = json.loads(raw[1:])
                if (not raw.startswith(b"L") or len(raw) > 1024 or not isinstance(value, dict)
                        or set(value) != {"schema", "state", "episode"} or type(value["schema"]) is not int
                        or value["schema"] != 1 or value["state"] not in ("active", "clean")
                        or (value["episode"] is not None and (type(value["episode"]) is not int
                            or not 0 <= value["episode"] < 2**64))):
                    raise ValueError()
            except (ValueError, TypeError, UnicodeError):
                raise SimulationError("CARLA ownership metadata is invalid; inspect the endpoint before recovery") from None
            records.append(value)
        return records

    def _write(self, state, episode):
        raw = b"L" + json.dumps({"schema": 1, "state": state, "episode": episode}, separators=(",", ":")).encode("ascii")
        for stream in self._files:
            stream.seek(0)
            stream.write(raw)
            stream.truncate()
            os.fsync(stream.fileno())

    def begin(self, world, *, map_reload=False):
        records = self._records()
        episode = world.id
        if type(episode) is not int or not 0 <= episode < 2**64:
            raise SimulationError("CARLA did not provide a valid episode identity")
        unfinished = [record for record in records if record and record["state"] == "active"]
        if unfinished:
            # A different, empty asynchronous episode is evidence of a reset.
            # A map reload in the interrupted run may itself change episodes,
            # so its unknown marker requires deliberate inspected recovery.
            if any(record["episode"] is None or record["episode"] == episode for record in unfinished):
                raise SimulationError(_RECOVERY)
            self._verify_empty_async(world, _RECOVERY)
        # Write intent before the first mutation, including load_world which can
        # change an episode and raise before returning its new identity.
        self._dirty = True
        self._write("active", None if map_reload else episode)

    def mark_clean(self):
        if self._dirty:
            self._write("clean", None)
            self._dirty = False

    def recover_inspected(self, world):
        """Explicit operator recovery after restarting an ambiguous episode."""
        self._records()  # Refuse corrupt records instead of silently resetting.
        self._verify_empty_async(world, "Recovery requires an empty asynchronous CARLA world after a server restart")
        self._write("clean", None)

    def _verify_empty_async(self, world, refusal):
        if world.get_settings().synchronous_mode:
            raise SimulationError(refusal)
        # Actor enumeration uses the client's episode snapshot. A newly attached
        # client of a paused synchronous world can have frame zero and no actors
        # even when the server owns vehicles. Wait for a natural asynchronous
        # update before treating an actor list as evidence of an empty world.
        try:
            snapshot = world.wait_for_tick(self.settings.timeout)
            if (type(snapshot.frame) is not int or snapshot.frame <= 0
                    or type(snapshot.id) is not int or snapshot.id != world.id):
                raise SimulationError(_UNOBSERVED)
        except (RuntimeError, AttributeError, TypeError):
            raise SimulationError(_UNOBSERVED) from None
        if (world.get_settings().synchronous_mode
                or any(actor.type_id.startswith(("vehicle.", "walker.", "sensor.", "controller."))
                       for actor in world.get_actors())):
            raise SimulationError(refusal)

    def release(self):
        # Do not clear the active marker here. Only verified resource/world
        # cleanup can do that; OS release after process loss isn't cleanup.
        try:
            for stream in reversed(self._files):
                try:
                    stream.close()  # Closing the descriptor releases its OS lock.
                except OSError:
                    pass
            self._files.clear()
        finally:
            with _mutex:
                for key in self._reserved:
                    _owners.discard(key)
            self._reserved.clear()
            self._dirty = False

    def __del__(self):
        try:
            self.release()
        except Exception:
            pass
