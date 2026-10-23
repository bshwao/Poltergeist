"""Canonical single-service origins shared by coordinator and companion."""

import ipaddress
import re
from urllib.parse import urlsplit


def service_origin(value, *, allow_insecure_loopback=False):
    try:
        return _service_origin(value, allow_insecure_loopback=allow_insecure_loopback)
    except ValueError:
        # urllib's port/hostname errors can include rejected input. An address
        # pasted into a settings field may contain credentials; never echo it.
        raise ValueError("Configure a single coordinator HTTPS origin") from None


def _service_origin(value, *, allow_insecure_loopback=False):
    if not isinstance(value, str) or len(value) > 256 or any(character.isspace() for character in value):
        raise ValueError("Configure a single coordinator HTTPS origin")
    parts = urlsplit(value)
    if (parts.username is not None or parts.password is not None or parts.query or parts.fragment
            or parts.path not in ("", "/") or parts.scheme not in ("https", "http") or not parts.hostname):
        raise ValueError("Configure a single coordinator HTTPS origin")
    host = parts.hostname.lower()
    if not host.isascii():
        raise ValueError("Use an ASCII coordinator hostname")
    try:
        address = ipaddress.ip_address(host)
        loopback = address.is_loopback
        host = address.compressed
    except ValueError:
        loopback = host == "localhost"
        if not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split(".")):
            raise ValueError("Invalid coordinator hostname")
    if parts.scheme != "https" and not (allow_insecure_loopback and loopback):
        raise ValueError("The coordinator requires HTTPS")
    port = parts.port
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Invalid coordinator port")
    formatted = f"[{host}]" if ":" in host else host
    if port is not None and port != (443 if parts.scheme == "https" else 80):
        formatted += f":{port}"
    return f"{parts.scheme}://{formatted}"
