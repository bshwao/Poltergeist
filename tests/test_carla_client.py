"""Connection configuration can be checked without installing or running CARLA."""

import sys
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from backend.config import Settings, get_settings
from simulator.carla_client import CarlaConnectionSettings, connect


def install_fake_carla(monkeypatch):
    calls = []
    client = SimpleNamespace(set_timeout=lambda timeout: calls.append(("timeout", timeout)))

    def create_client(host, port):
        calls.append(("client", host, port))
        return client

    monkeypatch.setitem(sys.modules, "carla", SimpleNamespace(Client=create_client))
    return client, calls


def test_explicit_connection_settings_override_environment(monkeypatch):
    monkeypatch.setenv("CARLA_HOST", "unused.invalid")
    monkeypatch.setenv("CARLA_PORT", "9999")
    client, calls = install_fake_carla(monkeypatch)

    result = connect(CarlaConnectionSettings(host="localhost", port=2010, timeout=5))

    assert result is client
    assert calls == [("client", "localhost", 2010), ("timeout", 5.0)]


def test_default_connection_uses_environment_settings(monkeypatch):
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("CARLA_HOST", "carla-server")
    monkeypatch.setenv("CARLA_PORT", "2100")
    monkeypatch.setenv("CARLA_TIMEOUT", "12.5")
    client, calls = install_fake_carla(monkeypatch)
    get_settings.cache_clear()

    try:
        result = connect()
    finally:
        get_settings.cache_clear()

    assert result is client
    assert calls == [("client", "carla-server", 2100), ("timeout", 12.5)]


def test_missing_carla_api_has_an_actionable_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "carla", None)

    with pytest.raises(RuntimeError, match="CARLA Python API is required"):
        connect(CarlaConnectionSettings())


@pytest.mark.parametrize(
    "values",
    [
        {"port": 0},
        {"port": 65536},
        {"timeout": 0},
        {"timeout": float("inf")},
        {"host": " "},
        {"host": "https://localhost"},
    ],
)
def test_explicit_connection_values_are_validated(values):
    with pytest.raises(ValidationError):
        CarlaConnectionSettings(**values)


def test_connection_settings_can_be_built_from_application_settings():
    settings = Settings(
        _env_file=None,
        carla_host="simulator",
        carla_port=2011,
        carla_timeout=4.5,
    )

    connection = CarlaConnectionSettings.from_settings(settings)

    assert (connection.host, connection.port, connection.timeout) == (
        "simulator",
        2011,
        4.5,
    )
