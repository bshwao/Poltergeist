"""Configuration validation and path stability without external services."""

import pytest
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from backend.config import REPO_ROOT, Settings, get_settings


@pytest.fixture(autouse=True)
def clean_settings_environment(monkeypatch):
    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
        monkeypatch.delenv(field.lower(), raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_defaults_are_valid_and_paths_are_anchored():
    settings = Settings(_env_file=None)

    assert settings.carla_host == "localhost"
    assert settings.carla_port == 2000
    assert settings.carla_timeout == 10.0
    assert settings.api_host == "0.0.0.0"
    assert settings.api_port == 8000
    assert settings.cors_origins == [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    assert settings.storage_dir == REPO_ROOT / "storage"
    assert make_url(settings.database_url).database == (
        REPO_ROOT / "storage/database/poltergeist.db"
    ).as_posix()
    assert Settings.model_config["env_file"] == REPO_ROOT / ".env"


def test_dotenv_is_utf8_and_environment_takes_precedence(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "CARLA_HOST=simulator\n"
        "API_PORT=8100\n"
        "STORAGE_DIR=storage/café\n"
        'CORS_ORIGINS=["https://dashboard.example", "http://localhost:3000/"]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("API_PORT", "8200")

    settings = Settings(_env_file=dotenv)

    assert settings.carla_host == "simulator"
    assert settings.api_port == 8200
    assert settings.storage_dir == REPO_ROOT / "storage/café"
    assert settings.cors_origins == [
        "https://dashboard.example",
        "http://localhost:3000",
    ]


def test_paths_and_default_env_file_do_not_follow_working_directory(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text("CARLA_PORT=2001\n", encoding="utf-8")
    working_dir = tmp_path / "working-dir"
    working_dir.mkdir()
    (working_dir / ".env").write_text("CARLA_PORT=9999\n", encoding="utf-8")
    monkeypatch.setitem(Settings.model_config, "env_file", dotenv)
    monkeypatch.chdir(working_dir)
    relative_storage = f"artifacts/{tmp_path.name}/runs"
    relative_database = f"artifacts/{tmp_path.name}/results.db"

    settings = Settings(
        storage_dir=relative_storage,
        database_url=f"sqlite:///{relative_database}",
    )

    assert settings.carla_port == 2001
    assert settings.storage_dir == REPO_ROOT / relative_storage
    assert make_url(settings.database_url).database == (REPO_ROOT / relative_database).as_posix()
    assert not settings.storage_dir.exists()
    assert not (REPO_ROOT / relative_database).exists()


@pytest.mark.parametrize(
    "overrides",
    [
        {"carla_port": 0},
        {"carla_port": 65536},
        {"api_port": -1},
        {"carla_timeout": 0},
        {"carla_timeout": float("nan")},
        {"carla_timeout": float("inf")},
        {"carla_host": " "},
        {"api_host": "http://localhost"},
        {"database_url": "not-a-database-url"},
        {"database_url": "sqlite://localhost/not-supported.db"},
        {"storage_dir": ""},
        {"storage_dir": None},
        {"cors_origins": ["https://example.com/path"]},
        {"cors_origins": ["https://user:password@example.com"]},
        {"cors_origins": ["ftp://example.com"]},
        {"cors_origins": ["http://localhost:99999"]},
    ],
)
def test_invalid_settings_are_rejected(overrides):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **overrides)


def test_memory_database_and_external_urls_are_preserved():
    assert Settings(_env_file=None, database_url="sqlite:///:memory:").database_url == (
        "sqlite:///:memory:"
    )
    external = "postgresql+psycopg://user:password@database:5432/poltergeist"
    assert Settings(_env_file=None, database_url=external).database_url == external


def test_settings_are_cached_until_explicitly_cleared(monkeypatch):
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    first = get_settings()
    monkeypatch.setenv("API_PORT", "8123")

    assert get_settings() is first
    assert get_settings().api_port == 8000

    get_settings.cache_clear()
    assert get_settings().api_port == 8123
