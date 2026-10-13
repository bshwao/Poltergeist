"""Connection helpers for a CARLA server."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CarlaConnectionSettings:
    host: str = "localhost"
    port: int = 2000
    timeout: float = 10.0


def connect(settings: CarlaConnectionSettings = CarlaConnectionSettings()):
    """Connect to CARLA, importing the optional dependency only when needed."""
    try:
        import carla
    except ImportError as exc:
        raise RuntimeError("The CARLA Python API is required to connect") from exc

    client = carla.Client(settings.host, settings.port)
    client.set_timeout(settings.timeout)
    return client
