"""Native OS ownership across profiles, aliases, crashes and cleanup failures."""

import subprocess
import sys
from types import SimpleNamespace as NS

import pytest

from simulator.carla_client import CarlaConnectionSettings
from simulator.carla_lease import CarlaEndpointLease, endpoint_keys
from simulator.errors import SimulationError


def lease(root, host="localhost", port=2000):
    return CarlaEndpointLease(CarlaConnectionSettings(host=host, port=port), _root=root)


def world(episode=1, *, synchronous=False, actors=()):
    return NS(id=episode, get_settings=lambda: NS(synchronous_mode=synchronous),
        get_actors=lambda: [NS(type_id=name) for name in actors])


def test_profiles_and_loopback_aliases_share_one_owner_but_other_ports_are_independent(tmp_path):
    first = lease(tmp_path)
    first.acquire()
    try:
        for host in ("127.0.0.1", "127.4.3.2", "::1", "::ffff:127.0.0.1", "LOCALHOST."):
            contender = lease(tmp_path, host)
            with pytest.raises(SimulationError, match="Another Poltergeist"):
                contender.acquire()
        other = lease(tmp_path, port=2001)
        other.acquire()
        other.release()
        with pytest.raises(SimulationError, match="already owned"):
            first.acquire()
    finally:
        first.release()
    fresh = lease(tmp_path, "::1")
    fresh.acquire()
    fresh.release()


def test_dns_aliases_reserve_all_addresses_and_release_partial_acquisitions(tmp_path, monkeypatch):
    import simulator.carla_lease as module
    monkeypatch.setattr(module.socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("192.0.2.5", 2000)), (0, 0, 0, "", ("192.0.2.6", 2000))])
    original = lease(tmp_path, "192.0.2.6")
    original.acquire()
    try:
        with pytest.raises(SimulationError):
            lease(tmp_path, "simulator.example").acquire()
        # The alias acquired .5 before encountering the busy .6; rollback must
        # release .5 and its in-process reservation, without touching .6.
        partial = lease(tmp_path, "192.0.2.5")
        partial.acquire()
        partial.release()
        with pytest.raises(SimulationError):
            lease(tmp_path, "192.0.2.6").acquire()
    finally:
        original.release()
    assert endpoint_keys("Simulator.Example.", 2000) == endpoint_keys("simulator.example", 2000)


def test_active_marker_survives_owner_loss_and_requires_a_new_empty_episode(tmp_path):
    original = lease(tmp_path)
    original.acquire()
    original.begin(world(11))
    original.release()  # Mirrors descriptor release after an unclean exit.
    for current in (world(11), world(12, synchronous=True), world(12, actors=["vehicle.other"]),
                    world(12, actors=["sensor.camera.rgb"]), world(12, actors=["controller.ai.walker"])):
        contender = lease(tmp_path)
        contender.acquire()
        try:
            with pytest.raises(SimulationError, match="cleanup is unverified"):
                contender.begin(current)
        finally:
            contender.release()
    recovered = lease(tmp_path)
    recovered.acquire()
    recovered.begin(world(12))
    recovered.mark_clean()
    recovered.release()
    same_episode = lease(tmp_path)
    same_episode.acquire()
    same_episode.begin(world(12))
    same_episode.mark_clean()
    same_episode.release()


def test_possible_map_reload_is_quarantined_until_deliberate_inspected_recovery(tmp_path):
    first = lease(tmp_path)
    first.acquire()
    first.begin(world(10), map_reload=True)
    first.release()
    next_owner = lease(tmp_path)
    next_owner.acquire()
    try:
        with pytest.raises(SimulationError, match="cleanup is unverified"):
            next_owner.begin(world(100))
        with pytest.raises(SimulationError, match="empty asynchronous"):
            next_owner.recover_inspected(world(100, actors=["walker.pedestrian"]))
        next_owner.recover_inspected(world(100))
        next_owner.begin(world(100))
        next_owner.mark_clean()
    finally:
        next_owner.release()


def test_reusing_a_released_object_cannot_clear_an_old_unclean_marker(tmp_path):
    owner = lease(tmp_path)
    owner.acquire()
    owner.begin(world(7))
    owner.release()
    owner.acquire()
    with pytest.raises(SimulationError, match="cleanup is unverified"):
        owner.begin(world(7))
    owner.mark_clean()  # This acquisition never began a verified execution.
    owner.release()
    again = lease(tmp_path)
    again.acquire()
    try:
        with pytest.raises(SimulationError, match="cleanup is unverified"):
            again.begin(world(7))
    finally:
        again.release()


@pytest.mark.parametrize("raw", [b"private_corrupt_marker", b"L{}", b"L" + b"x" * 1025,
    b'L{"schema":true,"state":"clean","episode":null}'])
def test_corrupt_marker_fails_closed_and_is_never_rewritten(tmp_path, raw):
    owner = lease(tmp_path)
    owner.acquire()
    owner.release()
    path = next(tmp_path.glob("*.lock"))
    path.write_bytes(raw)
    contender = lease(tmp_path)
    contender.acquire()
    try:
        with pytest.raises(SimulationError, match="metadata is invalid"):
            contender.begin(world())
        with pytest.raises(SimulationError, match="metadata is invalid"):
            contender.recover_inspected(world())
    finally:
        contender.release()
    assert path.read_bytes() == raw


def test_symlink_lock_files_are_rejected(tmp_path):
    first = lease(tmp_path)
    first.acquire()
    first.release()
    path = next(tmp_path.glob("*.lock"))
    target = tmp_path / "private"
    target.write_bytes(b"private_marker")
    path.unlink()
    try:
        path.symlink_to(target)
    except OSError:
        pytest.skip("Symlink creation is unavailable on this Windows host")
    with pytest.raises(SimulationError):
        lease(tmp_path).acquire()
    assert target.read_bytes() == b"private_marker"


CHILD = r'''
import os,sys
from types import SimpleNamespace as NS
from pathlib import Path
from simulator.carla_client import CarlaConnectionSettings
from simulator.carla_lease import CarlaEndpointLease
from simulator.errors import SimulationError
owner=CarlaEndpointLease(CarlaConnectionSettings(host=sys.argv[2]),_root=Path(sys.argv[1]))
try:
 owner.acquire()
except SimulationError:
 print("busy",flush=True)
 raise SystemExit(3)
if sys.argv[3]=='probe':
 owner.release()
 print("free",flush=True)
else:
 owner.begin(NS(id=91,get_settings=lambda:NS(synchronous_mode=False),get_actors=lambda:[]))
 print("owned",flush=True)
 if sys.stdin.readline().strip()=='crash':
  os._exit(79)
 owner.mark_clean()
 owner.release()
'''


def test_native_process_ownership_crash_release_and_dirty_episode_fence(tmp_path):
    # All children are exclusively created here; no saved or user process PID
    # is ever used for termination. os._exit models abrupt process loss without
    # Python finally/destructors. Killing a Windows venv launcher alone can
    # merely close its child's stdin, accidentally allowing graceful cleanup.
    holder = subprocess.Popen([sys.executable, "-u", "-c", CHILD, str(tmp_path), "127.0.0.1", "hold"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "owned"
        denied = subprocess.run([sys.executable, "-c", CHILD, str(tmp_path), "::1", "probe"],
            capture_output=True, text=True, timeout=20)
        assert denied.returncode == 3 and denied.stdout.strip() == "busy"
        holder.stdin.write("crash\n")
        holder.stdin.flush()
        holder.communicate(timeout=20)
        assert holder.returncode == 79
        fresh = lease(tmp_path, "localhost")
        fresh.acquire()  # The OS lock is gone; the active marker is not.
        try:
            with pytest.raises(SimulationError, match="cleanup is unverified"):
                fresh.begin(world(91))
            fresh.begin(world(92))
            fresh.mark_clean()
        finally:
            fresh.release()
        allowed = subprocess.run([sys.executable, "-c", CHILD, str(tmp_path), "localhost", "probe"],
            capture_output=True, text=True, timeout=20)
        assert allowed.returncode == 0 and allowed.stdout.strip() == "free"
    finally:
        if holder.poll() is None:
            holder.kill()
        holder.communicate(timeout=20)
