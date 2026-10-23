"""Validate deployment boundaries with Compose's parser, without a Docker engine."""

import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def compose_config(*, carla=False, overrides=None):
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker CLI is not installed; container configuration check skipped")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("POLTERGEIST_", "COMPOSE_"))
        and key not in ("CARLA_HOST", "CARLA_PORT", "CARLA_TIMEOUT", "SIMULATION_QUEUE_SIZE", "SIMULATION_PROGRESS_INTERVAL_S")
    }
    environment.update(overrides or {})
    arguments = [docker, "compose", "--env-file", os.devnull, "--project-name", "poltergeist-config-test"]
    if carla:
        arguments += ["--profile", "carla"]
    arguments += ["config", "--format", "json"]
    # The first cold invocation exceeded 90s on a shared Windows runner while
    # all five later invocations passed. Keep parsing/assertions on both OSes;
    # this test does not measure the installed CLI's cold-start performance.
    timeout = 180 if os.name == "nt" else 90
    try:
        completed = subprocess.run(arguments, cwd=ROOT, env=environment, text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        pytest.fail(f"Docker Compose configuration exceeded its {timeout}s startup deadline; the parser check did not complete")
    if completed.returncode and ("is not a docker command" in completed.stderr or "unknown command" in completed.stderr):
        pytest.skip("Docker Compose plugin is not installed")
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_core_stack_is_independent_of_a_simulator_and_dotenv():
    config = compose_config()
    assert set(config["services"]) == {"backend", "dashboard"}
    backend = config["services"]["backend"]
    assert not backend.get("depends_on")
    assert backend["build"]["args"]["INSTALL_SIMULATION"] == "false"
    assert backend["environment"]["CARLA_HOST"] == "carla"
    assert all("env_file" not in service for service in config["services"].values())


def test_saved_database_and_artifacts_share_one_durable_volume():
    config = compose_config()
    backend = config["services"]["backend"]
    environment = backend["environment"]
    storage = PurePosixPath(environment["STORAGE_DIR"])
    database = PurePosixPath(environment["DATABASE_URL"].removeprefix("sqlite:///"))
    assert storage.is_absolute() and database.is_relative_to(storage)
    mounts = [mount for mount in backend["volumes"] if mount["target"] == str(storage)]
    assert len(mounts) == 1
    assert mounts[0]["type"] == "volume"
    assert mounts[0]["source"] in config["volumes"]
    assert not mounts[0].get("read_only", False)


def test_single_backend_retains_signal_handling_and_bounded_shutdown():
    backend = compose_config()["services"]["backend"]
    assert backend["init"] is True
    assert backend.get("command") is None
    assert backend.get("entrypoint") is None
    assert backend["restart"] == "unless-stopped"
    # Two shutdown waits default to 15s each; Docker must allow both to finish.
    assert backend["environment"]["SIMULATION_SHUTDOWN_TIMEOUT_S"] == "15"
    assert backend["stop_grace_period"] == "1m15s"
    assert 'CMD ["python", "-m", "backend"]' in (ROOT / "Dockerfile").read_text()


def test_dashboard_uses_same_origin_proxy_and_waits_for_api_health():
    services = compose_config()["services"]
    dashboard = services["dashboard"]
    assert dashboard["build"]["args"]["VITE_API_URL"] == "/api"
    assert dashboard["depends_on"]["backend"]["condition"] == "service_healthy"
    for service in services.values():
        assert service["read_only"] is True
        assert all(port["host_ip"] == "127.0.0.1" for port in service["ports"])


def test_carla_is_an_explicit_gpu_profile_with_matching_client_build_option():
    config = compose_config(carla=True, overrides={"POLTERGEIST_INSTALL_SIMULATION": "true"})
    assert set(config["services"]) == {"backend", "dashboard", "carla"}
    carla = config["services"]["carla"]
    assert carla["profiles"] == ["carla"]
    assert carla["image"] == "carlasim/carla:0.9.16"
    assert carla["command"][:2] == ["bash", "CarlaUE4.sh"]
    assert "-RenderOffScreen" in carla["command"]
    assert not carla.get("privileged", False)
    devices = carla["deploy"]["resources"]["reservations"]["devices"]
    assert any(device["driver"] == "nvidia" and "gpu" in device["capabilities"] for device in devices)
    assert config["services"]["backend"]["build"]["args"]["INSTALL_SIMULATION"] == "true"


def test_docker_overrides_do_not_reuse_host_localhost_setting():
    config = compose_config(overrides={"CARLA_HOST": "localhost", "POLTERGEIST_CARLA_HOST": "host.docker.internal", "POLTERGEIST_API_PORT": "18000", "POLTERGEIST_DASHBOARD_PORT": "18080"})
    services = config["services"]
    assert services["backend"]["environment"]["CARLA_HOST"] == "host.docker.internal"
    assert services["backend"]["ports"][0]["published"] == "18000"
    assert services["dashboard"]["ports"][0]["published"] == "18080"
