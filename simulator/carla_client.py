"""Connection helpers for a CARLA server."""

from typing import TYPE_CHECKING

from pydantic import ConfigDict, Field, field_validator
from pydantic.dataclasses import dataclass

if TYPE_CHECKING:
    from backend.config import Settings


@dataclass(
    frozen=True,
    config=ConfigDict(validate_default=True, str_strip_whitespace=True, extra="forbid"),
)
class CarlaConnectionSettings:
    """Immutable, validated connection values; construction does not connect."""

    host: str = "localhost"
    port: int = Field(default=2000, ge=1, le=65535)
    timeout: float = Field(default=10.0, gt=0, allow_inf_nan=False)

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        if not value or any(character.isspace() for character in value):
            raise ValueError("host must be a nonempty hostname or IP address")
        if any(character in value for character in "/\\?#@"):
            raise ValueError("host must not include a URL scheme or path")
        return value

    @classmethod
    def from_settings(cls, settings: "Settings") -> "CarlaConnectionSettings":
        return cls(
            host=settings.carla_host,
            port=settings.carla_port,
            timeout=settings.carla_timeout,
        )


def connect(settings: CarlaConnectionSettings | None = None):
    """Connect using explicit values or lazily loaded application settings.

    The optional CARLA Python API is imported only when this function is called.
    """

    if settings is None:
        from backend.config import get_settings

        settings = CarlaConnectionSettings.from_settings(get_settings())

    try:
        import carla
    except ImportError as exc:
        raise RuntimeError("The CARLA Python API is required to connect") from exc

    client = carla.Client(settings.host, settings.port)
    client.set_timeout(settings.timeout)
    return client
