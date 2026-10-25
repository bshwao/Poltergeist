"""Explicit constructor-only configuration; never read the local backend .env."""

from dataclasses import dataclass
from pathlib import Path

from shared.origins import service_origin


@dataclass(frozen=True)
class CoordinatorSettings:
    database_path: Path
    public_origin: str
    test_allow_insecure_loopback: bool = False
    max_http_body_bytes: int = 32768
    pairing_ttl_s: int = 300
    hosted_assets_dir: Path | None = None

    def __post_init__(self):
        path = Path(self.database_path)
        if not path.is_absolute():
            raise ValueError("Coordinator database path must be absolute")
        object.__setattr__(self, "database_path", path)
        object.__setattr__(self, "public_origin", service_origin(self.public_origin,
            allow_insecure_loopback=self.test_allow_insecure_loopback))
        if not 1024 <= self.max_http_body_bytes <= 32768 or not 30 <= self.pairing_ttl_s <= 300:
            raise ValueError("Coordinator request/expiry limits exceed private prototype bounds")
        if self.hosted_assets_dir is not None:
            directory = Path(self.hosted_assets_dir)
            if not directory.is_absolute():
                raise ValueError("Hosted build path must be absolute")
            object.__setattr__(self, "hosted_assets_dir", directory)
