"""Bounded discovery and truthful setup checks, with no implicit native startup."""

import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from companion import onboarding
from companion.profile import CompanionConfig


def installation(root, *, complete=True):
    root.mkdir(parents=True)
    launcher = root / "CarlaUE4.exe"
    launcher.write_bytes(b"test fixture, not executable")
    if complete:
        (root / "Engine" / "Content" / "Internationalization").mkdir(parents=True)
        shipping = root / "CarlaUE4" / "Binaries" / "Win64" / "CarlaUE4-Win64-Shipping.exe"
        shipping.parent.mkdir(parents=True)
        shipping.write_bytes(b"test fixture, not executable")
    return launcher


def test_discovery_finds_nested_complete_folder_and_rejects_incomplete_copy(tmp_path):
    complete = installation(tmp_path / "Downloads" / "poltergiest" / "CARLA_0.9.16")
    incomplete = installation(tmp_path / "Downloads" / "CARLA_0.9.16", complete=False)
    # Many ordinary files must not consume the directory traversal limit.
    for index in range(150):
        (tmp_path / "Downloads" / f"download-{index}.txt").write_text("unrelated")
    before = {str(p): p.stat().st_mtime_ns for p in tmp_path.rglob("*") if p.is_file()}
    report = onboarding.detect_carla([tmp_path / "Downloads"])
    assert report["installations"] == [{"executable": str(complete), "version_verified": False}]
    assert report["rejected"][0]["path"] == str(incomplete)
    assert not report["version_verified"] and not report["search_limited"]
    assert before == {str(p): p.stat().st_mtime_ns for p in tmp_path.rglob("*") if p.is_file()}


def test_discovery_reports_bounded_search_instead_of_claiming_nothing_is_installed(tmp_path, monkeypatch):
    for index in range(5):
        (tmp_path / str(index)).mkdir()
    monkeypatch.setattr(onboarding, "MAX_ENTRIES", 2)
    report = onboarding.detect_carla([tmp_path])
    assert report["search_limited"] and not report["installations"]


def test_discovery_deduplicates_roots_and_does_not_descend_without_bound(tmp_path):
    launcher = installation(tmp_path / "CARLA")
    report = onboarding.detect_carla([tmp_path, tmp_path])
    assert len(report["installations"]) == 1
    assert report["installations"][0]["executable"] == str(launcher)
    assert report["directories_scanned"] <= onboarding.MAX_DIRECTORIES


def test_system_inspection_is_read_only_and_does_not_certify_performance(tmp_path):
    target = tmp_path / "not-created" / "profile"
    report = onboarding.inspect_system(target)
    assert report["disk_free_bytes"] > 0
    assert report["logical_cpus"] > 0
    assert "not a CARLA" in report["scope"]
    assert not target.exists()


def test_connection_probe_is_bounded_and_preserves_versions_and_synchronous_state(monkeypatch):
    observed = []
    data = {"client_version": "0.9.16", "server_version": "0.9.16", "map": "/Game/Carla/Maps/Town10HD_Opt",
            "available_maps": ["/Game/Carla/Maps/Town10HD_Opt"], "synchronous_mode": True}
    def run(arguments, **kwargs):
        observed.append((arguments, kwargs))
        return SimpleNamespace(returncode=0, stdout="native info\nPOLTERGEIST_SETUP=" + json.dumps(data) + "\n", stderr="")
    monkeypatch.setattr(onboarding.subprocess, "run", run)
    assert onboarding.check_connection(CompanionConfig(carla_port=2100)) == data
    arguments, options = observed[0]
    assert arguments[:3] == [sys.executable, "-I", "-B"]
    assert arguments[-1] == "2100" and options["timeout"] == 15
    assert "world.tick" not in arguments[4] and "load_world" not in arguments[4]


def test_native_version_failure_is_reported_without_inventing_compatibility(monkeypatch):
    monkeypatch.setattr(onboarding.subprocess, "run", lambda *a, **k:
        SimpleNamespace(returncode=1, stdout="", stderr="RuntimeError: CARLA requires matching 0.9.16 client/server"))
    with pytest.raises(RuntimeError, match="matching 0.9.16"):
        onboarding.check_connection(CompanionConfig())


def test_explicit_start_uses_existing_launcher_with_low_graphics_and_cancellation(tmp_path, monkeypatch):
    executable = installation(tmp_path / "CARLA")
    calls = []
    client = SimpleNamespace(set_timeout=lambda value: None)
    monkeypatch.setitem(sys.modules, "carla", SimpleNamespace(Client=lambda *args: client))
    class Launcher:
        def __init__(self, settings):
            assert settings.carla_auto_start and settings.carla_quality == "Low"
        def ensure_running(self, value, map_name, **kwargs):
            calls.append((value, map_name, kwargs["cancellation"]()))
    monkeypatch.setattr("simulator.carla_launcher.LocalCarlaLauncher", Launcher)
    monkeypatch.setattr(onboarding, "check_connection", lambda config: {"server_version": "0.9.16"})
    config = CompanionConfig(carla_executable=str(executable), carla_auto_start=False)
    result = onboarding.start_carla(tmp_path / "profile", config, "Town10HD_Opt", cancellation=lambda: True)
    assert calls == [(client, "Town10HD_Opt", True)]
    assert result["server_version"] == "0.9.16" and not config.carla_auto_start
    assert not (tmp_path / "profile").exists()


def test_start_rejects_map_flags_before_any_carla_import(tmp_path):
    with pytest.raises(ValueError, match="map name"):
        onboarding.start_carla(tmp_path, CompanionConfig(), "Town03 -malicious-flag")
