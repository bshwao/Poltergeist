"""Explicit per-user desktop configuration; never inherit a developer .env."""

import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.engine import URL

from backend.config import Settings
from backend.worker_lock import WorkerLease
from shared.origins import service_origin


class CompanionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["poltergeist-companion-config-v1"] = "poltergeist-companion-config-v1"
    carla_executable: str | None = None
    carla_port: int = Field(default=2000, ge=1, le=65535)
    carla_auto_start: bool = False
    carla_quality: Literal["Low", "Epic"] = "Low"
    setup_completed: bool = False
    coordinator_origin: str | None = None

    @field_validator("coordinator_origin")
    @classmethod
    def coordinator(cls, value):
        return None if value is None else service_origin(value)

    @field_validator("carla_executable")
    @classmethod
    def executable(cls, value):
        if value is None:
            return None
        path = Path(value)
        if not path.is_absolute():
            raise ValueError("CARLA executable must be an absolute path")
        return str(path.resolve())


class _IsolatedSettings(Settings):
    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        return (init_settings,)


def profile_path(value=None, *, bundle=None):
    if value is None:
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            raise ValueError("LOCALAPPDATA is unavailable; select an absolute --data-dir")
        value = Path(local) / "Poltergeist" / "CompanionPrototype"
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("The companion data directory must be absolute")
    path = path.resolve()
    if bundle is not None and path.is_relative_to(Path(bundle).resolve()):
        raise ValueError("Keep user data outside the installed bundle")
    return path


def load_config(profile):
    path = Path(profile) / "companion.json"
    if not path.exists():
        return CompanionConfig()
    return CompanionConfig.model_validate_json(path.read_bytes())


def validate_windows_carla_root(path):
    path = Path(path)
    if (path.name.lower() != "carlaue4.exe"
            or not (path.parent / "Engine" / "Content" / "Internationalization").is_dir()
            or not (path.parent / "CarlaUE4" / "Binaries" / "Win64" / "CarlaUE4-Win64-Shipping.exe").is_file()):
        raise ValueError("Select CarlaUE4.exe in the complete CARLA folder containing Engine and CarlaUE4. The shipping executable alone is incomplete.")


def validate_installation(config):
    """Reject the known incomplete Windows shipping-executable copy at save time."""
    if config.carla_auto_start and config.carla_executable is None:
        raise ValueError("Select CARLA before enabling automatic startup")
    if config.carla_executable is None:
        return
    path = Path(config.carla_executable)
    if not path.is_file():
        raise ValueError("The selected CARLA executable does not exist")
    if os.name == "nt":
        validate_windows_carla_root(path)


def save_config(profile, config):
    config = CompanionConfig.model_validate(config.model_dump())
    validate_installation(config)
    profile = Path(profile)
    profile.mkdir(parents=True, exist_ok=True)
    temporary = profile / f".companion-{uuid4().hex}.tmp"
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(config.model_dump_json(indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(profile / "companion.json")
    finally:
        temporary.unlink(missing_ok=True)


def local_settings(profile, config):
    profile = Path(profile).resolve()
    return _IsolatedSettings(_env_file=None, storage_dir=profile / "storage",
        database_url=URL.create("sqlite", database=(profile / "database" / "poltergeist.db").as_posix()).render_as_string(hide_password=False),
        api_host="127.0.0.1", cors_origins=[], carla_host="127.0.0.1",
        carla_port=config.carla_port, carla_executable=config.carla_executable,
        carla_quality=config.carla_quality, carla_auto_start=config.carla_auto_start,
        simulation_shutdown_timeout_s=2.)


def instance_lease(profile):
    # Separate from the database worker lease: a stopped desktop still owns its
    # settings and profile. OS locks survive stale lock files and release on exit.
    return WorkerLease(SimpleNamespace(file_path=Path(profile) / "companion-instance"))


def prepare_logs(profile):
    directory = Path(profile) / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(directory / "companion.log", maxBytes=2 * 1024 * 1024,
                                  backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger = logging.getLogger("poltergeist.companion")
    logger.setLevel(logging.INFO)
    logging.getLogger().addHandler(handler)
    return logger, handler


def configure_gui_environment(bundle):
    """Do not load ambient developer/user Tcl scripts when using the bundle."""
    runtime = Path(bundle).resolve() / "runtime" / "tcl"
    os.environ["TCL_LIBRARY"] = str(runtime / "tcl8.6")
    os.environ["TK_LIBRARY"] = str(runtime / "tk8.6")
