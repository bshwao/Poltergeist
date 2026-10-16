"""Validated application settings, independent of the current directory.

Importing this module only defines configuration. Settings do not create storage
directories or connect to CARLA or the database.
"""

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


REPO_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    """Environment variables override the optional repository-root ``.env``.

    ``CORS_ORIGINS`` uses a JSON array of HTTP(S) origins. Relative storage and
    SQLite database paths are resolved against the repository root.
    """

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    carla_host: str = "localhost"
    carla_port: int = Field(default=2000, ge=1, le=65535)
    carla_timeout: float = Field(default=10.0, gt=0, allow_inf_nan=False)
    database_url: str = "sqlite:///./storage/database/poltergeist.db"
    api_host: str = "0.0.0.0"
    api_port: int = Field(default=8000, ge=1, le=65535)
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    )
    storage_dir: Path = Path("storage")
    simulation_queue_size: int = Field(default=100, ge=1, le=1000)
    simulation_progress_interval_s: float = Field(default=0.25, gt=0, le=60, allow_inf_nan=False)
    simulation_shutdown_timeout_s: float = Field(default=15.0, gt=0, le=60, allow_inf_nan=False)

    @field_validator("carla_host", "api_host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        value = value.strip()
        if not value or any(character.isspace() for character in value):
            raise ValueError("host must be a nonempty hostname or IP address")
        if any(character in value for character in "/\\?#@"):
            raise ValueError("host must not include a URL scheme or path")
        return value

    @field_validator("storage_dir", mode="before")
    @classmethod
    def resolve_storage_dir(cls, value: str | Path) -> Path:
        if not isinstance(value, (str, Path)):
            raise ValueError("storage directory must be a filesystem path")
        if isinstance(value, str) and not value.strip():
            raise ValueError("storage directory must not be empty")
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = REPO_ROOT / path
        return path.resolve()

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        value = value.strip()
        try:
            database = make_url(value)
        except ArgumentError as error:
            raise ValueError("database URL must be a valid SQLAlchemy URL") from error
        if database.get_backend_name() == "sqlite" and any(
            part is not None
            for part in (database.host, database.port, database.username, database.password)
        ):
            raise ValueError("SQLite database URLs must not include a host or credentials")
        if database.get_backend_name() == "sqlite" and database.database not in (
            None,
            "",
            ":memory:",
        ):
            path = Path(database.database).expanduser()
            if not path.is_absolute():
                path = REPO_ROOT / path
            database = database.set(database=path.resolve().as_posix())
            return database.render_as_string(hide_password=False)
        return value

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, values: list[str]) -> list[str]:
        origins: list[str] = []
        for value in values:
            value = value.strip()
            try:
                parsed = urlsplit(value)
                # Accessing port also validates malformed and out-of-range ports.
                parsed.port
            except ValueError as error:
                raise ValueError("CORS origins must be valid HTTP(S) origins") from error
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.hostname
                or any(character.isspace() for character in value)
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in ("", "/")
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("CORS origins must contain only scheme, host, and port")
            origin = value.rstrip("/")
            if origin not in origins:
                origins.append(origin)
        return origins


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once; call ``cache_clear`` after changing the environment."""

    return Settings()
