"""Explicit constructor-only configuration; never read the local backend .env."""

from dataclasses import dataclass, field
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
    max_connections: int = 100
    max_transfers: int = 8
    max_devices_per_account: int = 5
    max_commands_per_account: int = 10000
    monitor_token: str | None = field(default=None, repr=False)

    def __post_init__(self):
        import re
        for value, maximum in ((self.max_connections, 1000), (self.max_transfers, 32),
                               (self.max_devices_per_account, 100), (self.max_commands_per_account, 100000)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("Invalid coordinator capacity limit")
        if self.monitor_token is not None and not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", self.monitor_token):
            raise ValueError("Use a high-entropy monitoring token")
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
