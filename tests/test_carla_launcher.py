"""Local startup is explicit to execution, bounded, cancellable and never duplicated."""

from types import SimpleNamespace

import pytest

from backend.config import Settings
from simulator import carla_launcher as module
from simulator.errors import SimulationCancelled, SimulationError


@pytest.fixture
def launch(monkeypatch, tmp_path):
    executable = tmp_path / ("CarlaUE4.exe" if module.os.name == "nt" else "CarlaUE4.sh")
    executable.write_bytes(b"fixture executable, never executed")
    settings = Settings(_env_file=None, carla_host="127.0.0.1", carla_executable=executable,
                        carla_startup_timeout_s=1, carla_timeout=7)
    clock, open_, calls, timeouts = [0.], [False], [], []
    process = SimpleNamespace(returncode=None)
    process.poll = lambda: process.returncode
    def start(command, **kwargs):
        calls.append((command, kwargs)); open_[0] = True
        return process
    client = SimpleNamespace(set_timeout=timeouts.append, get_world=lambda: object())
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(module, "port_open", lambda *a: open_[0])
    monkeypatch.setattr(module.subprocess, "Popen", start)
    return SimpleNamespace(launcher=module.LocalCarlaLauncher(settings), settings=settings, client=client,
        clock=clock, open=open_, calls=calls, timeouts=timeouts, process=process)


def test_launches_low_graphics_on_the_requested_map_and_reuses_it(launch):
    launch.launcher.ensure_running(launch.client, "Town10HD_Opt")
    command, options = launch.calls[0]
    assert "/Game/Carla/Maps/Town10HD_Opt" in command
    assert "-quality-level=Low" in command and "-carla-rpc-port=2000" in command
    assert "-windowed" in command and "-nosound" in command
    assert options["shell"] is False and options["cwd"] == launch.settings.carla_executable.parent
    assert launch.timeouts[-1] == 7
    launch.launcher.ensure_running(launch.client, "Town03")
    assert len(launch.calls) == 1  # Map switching belongs to scoped WorldManager.


def test_readiness_reconnects_instead_of_reusing_a_client_created_before_startup(launch):
    def stale(): raise AssertionError("Pre-start RPC client must not be reused")
    launch.client.get_world = stale
    timeouts = []
    fresh = SimpleNamespace(get_world=lambda: object(), set_timeout=timeouts.append)
    result = launch.launcher.ensure_running(launch.client, "Town10HD_Opt", reconnect=lambda: fresh)
    assert result is fresh and timeouts[-1] == 7


@pytest.mark.parametrize("configured,selected,expected", [
    ("Low", "Epic", "Epic"), ("Epic", "Low", "Low"), ("Epic", None, "Epic"),
])
def test_dashboard_quality_overrides_only_the_next_launch_default(launch, configured, selected, expected):
    launch.settings.carla_quality = configured
    launch.launcher.ensure_running(launch.client, "Town10HD_Opt", quality=selected)
    assert f"-quality-level={expected}" in launch.calls[0][0]


def test_quality_preference_never_restarts_an_existing_instance(launch):
    launch.open[0] = True
    assert launch.launcher.ensure_running(launch.client, "Town03", quality="Epic") is launch.client
    assert not launch.calls


def test_existing_listener_is_never_replaced_even_when_rpc_times_out(launch):
    launch.open[0] = True
    def unavailable(): raise RuntimeError("server is loading or port belongs to another service")
    launch.client.get_world = unavailable
    with pytest.raises(SimulationError, match="no duplicate instance"):
        launch.launcher.ensure_running(launch.client, "Town03")
    assert not launch.calls and launch.timeouts[-1] == 7


@pytest.mark.parametrize("changes", [{"carla_auto_start": False}, {"carla_host": "192.0.2.10"}])
def test_remote_and_disabled_startup_leave_connection_ownership_to_the_caller(launch, changes):
    settings = launch.settings.model_copy(update=changes)
    module.LocalCarlaLauncher(settings).ensure_running(launch.client, "Town03")
    assert not launch.calls and not launch.timeouts


def test_cancellation_before_launch_never_starts_a_process(launch):
    with pytest.raises(SimulationCancelled):
        launch.launcher.ensure_running(launch.client, "Town03", cancellation=lambda: True)
    assert not launch.calls


def test_cancelled_startup_is_reused_when_it_finishes_later(launch, monkeypatch):
    def start(*args, **kwargs): launch.calls.append(args); return launch.process
    monkeypatch.setattr(module.subprocess, "Popen", start)
    with pytest.raises(SimulationCancelled):
        launch.launcher.ensure_running(launch.client, "Town03", cancellation=lambda: launch.clock[0] >= .5)
    assert len(launch.calls) == 1
    launch.clock[0] = 5.
    launch.open[0] = True
    launch.launcher.ensure_running(launch.client, "Town03")
    assert len(launch.calls) == 1 and launch.timeouts[-1] == 7


def test_a_new_run_does_not_duplicate_an_unconfirmed_startup(launch, monkeypatch):
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **kw: (launch.calls.append(a) or launch.process))
    for _ in range(2):
        with pytest.raises(SimulationError, match="did not become ready"):
            launch.launcher.ensure_running(launch.client, "Town03")
    assert len(launch.calls) == 1


def test_native_startup_crashes_are_actionable_and_do_not_wait_the_entire_deadline(launch, monkeypatch):
    launch.process.returncode = 12
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **kw: launch.process)
    with pytest.raises(SimulationError, match="code 12"):
        launch.launcher.ensure_running(launch.client, "Town03")
    assert launch.clock[0] == 0 and launch.timeouts[-1] == 7


@pytest.mark.parametrize("name", ["-quality-level=Epic", "Town03 -foo", "Town03?listen"])
def test_scenario_map_cannot_inject_unreal_startup_options(launch, name):
    with pytest.raises(SimulationError, match="valid CARLA map"):
        launch.launcher.ensure_running(launch.client, name)
    assert not launch.calls


def test_configured_missing_executable_does_not_silently_use_a_different_install(tmp_path):
    with pytest.raises(SimulationError, match="CARLA_EXECUTABLE"):
        module.find_executable(tmp_path / "missing.exe")


def test_common_windows_or_linux_installation_can_be_found_without_recursive_scanning(tmp_path, monkeypatch):
    monkeypatch.setattr(module.Path, "home", lambda: tmp_path)
    executable = tmp_path / "OneDrive" / "Documents" / "CARLA_0.9.16" / ("CarlaUE4.exe" if module.os.name == "nt" else "CarlaUE4.sh")
    executable.parent.mkdir(parents=True); executable.write_bytes(b"fixture")
    assert module.find_executable() == executable.resolve()
