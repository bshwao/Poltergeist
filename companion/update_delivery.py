"""Explicit HTTPS update checks and bounded downloads; no background traffic."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
import shutil
import time
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx

from .profile import profile_path
from .signatures import verify_signature
from .updates import (_plain, load_policy, ReleaseManifest, UpdateNotEligible,
                      verify_manifest, verify_release)

CHUNK_BYTES = 65536
FREE_RESERVE = 512 * 1024**2
CHECK_LIMIT = 20


class DownloadCancelled(ValueError):
    pass


@dataclass(frozen=True)
class UpdateOffer:
    manifest: Path
    release: ReleaseManifest


def delivery_origin(url):
    """Only credential-free HTTPS locations from the installed policy."""
    try:
        parts = urlsplit(url)
        if (parts.scheme != "https" or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.query or parts.fragment
                or any(ord(char) <= 32 or ord(char) >= 127 for char in url) or "\\" in url):
            raise ValueError()
        return parts.hostname.lower(), parts.port or 443
    except (TypeError, ValueError):
        raise ValueError("Update delivery requires a credential-free HTTPS URL without query or fragment") from None


def _delivery_policy(policy):
    if policy.get("schema_version") != "poltergeist-release-policy-v1" or not policy.get("publishers_sha256"):
        raise ValueError("Signed updates are disabled until a release publisher is configured in the installed app")
    if not policy.get("feed_url"):
        raise ValueError("This installed build has no update feed configured")
    delivery_origin(policy["feed_url"])
    return policy


def _downloads(profile):
    profile = _plain(profile_path(_plain(profile)))
    if not profile.is_dir():
        raise ValueError("Open the Companion once before checking for updates")
    directory = _plain(profile / "updates" / "downloads")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _cancelled(cancel):
    if cancel is not None and cancel.is_set():
        raise DownloadCancelled("Update transfer cancelled")


def _fetch(client, url, target, *, maximum, expected=None, cancel=None, progress=None, clock=time.monotonic):
    delivery_origin(url)
    target = _plain(target)
    _cancelled(cancel)
    if expected is not None and shutil.disk_usage(target.parent).free < expected + FREE_RESERVE:
        raise ValueError("Insufficient disk space for the update download and free-space reserve")
    deadline = clock() + 600
    created = False
    try:
        # Redirects, environment proxies and compressed bodies are not accepted.
        with client.stream("GET", url, headers={"Accept-Encoding": "identity"}, follow_redirects=False) as response:
            response.raise_for_status()
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise ValueError("Compressed update downloads are not accepted")
            length = response.headers.get("content-length")
            if length is not None and (not re.fullmatch(r"[0-9]{1,12}", length)
                    or int(length) > maximum or expected is not None and int(length) != expected):
                raise ValueError("Update response length differs from its signed size or transfer limit")
            received = 0
            digest = sha256()
            with target.open("xb") as stream:
                created = True
                # Raw transport reads let the deadline run on every received
                # network chunk, including a peer sending a slow partial body.
                for chunk in response.iter_raw():
                    _cancelled(cancel)
                    if clock() >= deadline:
                        raise ValueError("Update transfer exceeded its ten-minute deadline")
                    if len(chunk) > CHUNK_BYTES or received + len(chunk) > maximum:
                        raise ValueError("Update response exceeds its bounded transfer limit")
                    if shutil.disk_usage(target.parent).free < FREE_RESERVE + len(chunk):
                        raise ValueError("Update transfer would use the free-space reserve")
                    stream.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    if progress is not None:
                        progress(received, expected)
            _cancelled(cancel)
            if clock() >= deadline or expected is not None and received != expected:
                raise ValueError("Update transfer was incomplete or exceeded its deadline")
            return digest.hexdigest()
    except BaseException:
        if created:
            _plain(target).unlink(missing_ok=True)
        raise


def _cohort(profile):
    path = _plain(_downloads(profile).parent / "cohort-id")
    try:
        with path.open("x", encoding="ascii") as stream:
            stream.write(str(uuid4()))
    except FileExistsError:
        pass
    with path.open("rb") as stream:
        value = stream.read(37)
    return str(UUID(value.decode("ascii")))


def rollout_eligible(release, cohort):
    # The anonymous random ID stays on this computer; no cohort header or
    # account/device identifier is sent to the update service.
    bucket = int.from_bytes(sha256(f"{cohort}:{release.version}:{release.rollout_salt}".encode()).digest()[:8], "big") % 10000
    return bucket < release.rollout_percent * 100


def _client():
    return httpx.Client(timeout=httpx.Timeout(10, connect=5), follow_redirects=False, trust_env=False,
                        limits=httpx.Limits(max_connections=1, max_keepalive_connections=1))


def check_update(profile, *, current_version, cancel=None, _policy=None, _verify=verify_signature, _client_factory=_client):
    policy = _delivery_policy(load_policy() if _policy is None else _policy)
    root = _downloads(profile)
    if sum(1 for _ in root.iterdir()) >= CHECK_LIMIT:
        raise ValueError("Remove an old downloaded candidate before checking again (20-candidate cache limit)")
    directory = _plain(root / uuid4().hex)
    directory.mkdir()
    manifest = directory / "release.ps1"
    keep = False
    try:
        with _client_factory() as client:
            _fetch(client, policy["feed_url"], manifest, maximum=65536, cancel=cancel)
        try:
            release = verify_manifest(manifest, policy, current_version=current_version, _verify=_verify)
        except UpdateNotEligible:
            return None
        if release.installer_url is None or delivery_origin(release.installer_url) != delivery_origin(policy["feed_url"]):
            raise ValueError("Signed installer location must use the installed update feed's HTTPS origin")
        if not rollout_eligible(release, _cohort(profile)):
            return None
        keep = True
        return UpdateOffer(manifest, release)
    finally:
        if not keep:
            _plain(manifest).unlink(missing_ok=True)
            directory.rmdir()


def download_update(profile, manifest, *, current_version, cancel=None, progress=None,
                    _policy=None, _verify=verify_signature, _client_factory=_client):
    policy = _delivery_policy(load_policy() if _policy is None else _policy)
    manifest = _plain(manifest)
    root = _downloads(profile)
    if (manifest.name != "release.ps1" or manifest.parent.parent != root
            or re.fullmatch(r"[0-9a-f]{32}", manifest.parent.name) is None):
        raise ValueError("Select a signed candidate checked in this Companion profile")
    release = verify_manifest(manifest, policy, current_version=current_version, _verify=_verify)
    if release.installer_url is None or delivery_origin(release.installer_url) != delivery_origin(policy["feed_url"]):
        raise ValueError("Signed installer location must use the installed update feed's HTTPS origin")
    if not rollout_eligible(release, _cohort(profile)):
        raise ValueError("This candidate is outside this computer's staged rollout")
    installer = _plain(manifest.parent / "Setup.exe")
    if not installer.exists():
        partial = _plain(manifest.parent / "Setup.download.exe")
        downloaded = False
        try:
            with _client_factory() as client:
                digest = _fetch(client, release.installer_url, partial, maximum=release.installer_bytes,
                               expected=release.installer_bytes, cancel=cancel, progress=progress)
            downloaded = True
            if digest != release.installer_sha256:
                raise ValueError("Downloaded installer checksum differs from the signed release")
            _verify(partial, policy["publishers_sha256"])
            # Copying or renaming never constitutes an installation.
            partial.replace(installer)
        finally:
            if downloaded:
                _plain(partial).unlink(missing_ok=True)
    verify_release(installer, manifest, policy, current_version=current_version, _verify=_verify)
    return installer


def remove_download(profile, manifest):
    """Remove only an explicitly selected download, never update backups."""
    root, manifest = _downloads(profile), _plain(manifest)
    directory = manifest.parent
    if (manifest.name != "release.ps1" or directory.parent != root
            or re.fullmatch(r"[0-9a-f]{32}", directory.name) is None):
        raise ValueError("Only a checked download in this profile can be removed")
    paths = list(directory.iterdir())
    for path in paths:
        if path.name not in {"release.ps1", "Setup.exe", "Setup.download.exe"} or not _plain(path).is_file():
            raise ValueError("Candidate contains unexpected files; preserve it for local review")
    for path in paths:
        path.unlink()
    directory.rmdir()
