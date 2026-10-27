"""Owned, resumable model bytes. Downloading never imports or executes a model.

An interrupted transfer keeps its partial file. Every publication re-reads and
hashes the entire file, including resumed bytes, against the built-in catalogue.
The marker and OS lease prevent adopting another download directory by accident;
they are not a sandbox against code already running as this Windows user.
"""

from dataclasses import dataclass
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import stat
from types import SimpleNamespace
import urllib.error
import urllib.parse
import urllib.request

from backend.worker_lock import WorkerLease
from backend.persistence_errors import ConflictError
from simulator.agents.reference_ai.checkpoint import (
    CHECKPOINT_BYTES, CHECKPOINT_FILENAME, CHECKPOINT_SHA256, CHECKPOINT_URL,
    MODEL_REVISION,
)

CHUNK = 256 * 1024
NETWORK_TIMEOUT = 5


class AssetError(ValueError):
    pass


class DownloadPaused(Exception):
    pass


@dataclass(frozen=True)
class ModelAsset:
    id: str
    filename: str
    url: str
    size: int
    sha256: str

    def __post_init__(self):
        if not re.fullmatch(r"[a-z0-9-]{1,80}", self.id):
            raise ValueError("Invalid model asset ID")
        if not re.fullmatch(r"[a-zA-Z0-9_.=-]{1,120}", self.filename):
            raise ValueError("Invalid model asset filename")
        if self.filename in {".", "..", "owner.json", "transfer", "transfer.worker.lock", "weights.part"}:
            raise ValueError("Reserved model asset filename")
        _https(self.url)
        if type(self.size) is not int or not 0 < self.size <= 64 * 1024**3:
            raise ValueError("Invalid model asset size")
        if not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ValueError("Invalid model asset digest")


def _https(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise AssetError("Model downloads require an HTTPS address without credentials")


PLANT_WEIGHTS = ModelAsset("plant2-" + MODEL_REVISION[:12], CHECKPOINT_FILENAME,
                           CHECKPOINT_URL, CHECKPOINT_BYTES, CHECKPOINT_SHA256)


class _HTTPSRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        _https(newurl)
        return super().redirect_request(request, response, code, message, headers, newurl)


def _opener():
    # No ambient proxy credentials, cookies, authorization, or insecure TLS mode.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _HTTPSRedirects())


def _plain(path, *, directory=False):
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise AssetError("Linked model storage was refused")
    if path.exists():
        info = path.stat()
        expected = stat.S_ISDIR if directory else stat.S_ISREG
        if not expected(info.st_mode) or (not directory and info.st_nlink != 1):
            raise AssetError("Model storage must contain ordinary, unlinked files")


def _check_pause(cancelled):
    if cancelled():
        raise DownloadPaused()


class AssetStore:
    def __init__(self, profile, asset=PLANT_WEIGHTS, *, _open=None):
        profile = Path(profile)
        if not profile.is_absolute():
            raise ValueError("Model storage requires an absolute profile")
        self.asset = asset
        self.directory = profile / "models" / asset.id
        self.path = self.directory / asset.filename
        self.partial = self.directory / "weights.part"
        self.marker = self.directory / "owner.json"
        self.lock = self.directory / "transfer.worker.lock"
        self._open = _open
        self._lease = WorkerLease(SimpleNamespace(file_path=self.directory / "transfer"))

    def _identity(self):
        return {"schema": "poltergeist-model-asset-v1", "id": self.asset.id,
                "filename": self.asset.filename, "size": self.asset.size, "sha256": self.asset.sha256}

    def _guard(self):
        _plain(self.directory, directory=True)
        for path in (self.marker, self.path, self.partial, self.lock):
            _plain(path)

    def _own(self):
        self._guard()
        if not self.directory.exists():
            self.directory.mkdir(parents=True, exist_ok=False)
            # If creation/marker writing is interrupted, refuse this unowned
            # directory next time rather than silently claiming its contents.
            with self.marker.open("x", encoding="utf-8") as stream:
                json.dump(self._identity(), stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
        if not self.marker.is_file() or self.marker.stat().st_size > 4096:
            raise AssetError("Model folder has no valid ownership record")
        try:
            identity = json.loads(self.marker.read_bytes())
        except (ValueError, OSError) as error:
            raise AssetError("Model folder ownership record is unreadable") from error
        if identity != self._identity():
            raise AssetError("Model folder belongs to another asset")
        allowed = {self.marker.name, self.path.name, self.partial.name, self.lock.name}
        if any(item.name not in allowed for item in self.directory.iterdir()):
            raise AssetError("Model folder contains unrecognized files; no files were changed")
        try:
            self._lease.acquire()
        except ConflictError:
            raise ConflictError("Another model operation owns these weights") from None
        try:
            self._guard()
        except BaseException:
            self._lease.release()
            raise

    def _verified(self, path, cancelled, progress):
        self._guard()
        if path.stat().st_size != self.asset.size:
            raise AssetError("Saved model size does not match the catalogue; use Repair")
        digest, total = hashlib.sha256(), 0
        with path.open("rb") as stream:
            while chunk := stream.read(CHUNK):
                _check_pause(cancelled)
                total += len(chunk)
                if total > self.asset.size:
                    raise AssetError("Saved model exceeds the catalogue size; use Repair")
                digest.update(chunk)
                progress("verifying", total, self.asset.size)
        if total != self.asset.size or digest.hexdigest() != self.asset.sha256:
            raise AssetError("Saved model hash does not match the catalogue; use Repair")
        _check_pause(cancelled)

    def inspect(self, *, cancelled=lambda: False, progress=lambda *args: None):
        """Explicitly verify a saved file. No download is started by inspection."""
        if not self.directory.exists():
            self._guard()
            return "absent", 0
        self._own()
        try:
            if self.path.exists():
                self._verified(self.path, cancelled, progress)
                return "weights_ready", self.asset.size
            size = self.partial.stat().st_size if self.partial.exists() else 0
            if size > self.asset.size:
                raise AssetError("Partial model exceeds the catalogue size; use Repair")
            return ("paused" if size else "absent"), size
        finally:
            self._lease.release()

    def download(self, *, cancelled=lambda: False, progress=lambda *args: None, repair=False):
        self._own()
        try:
            _check_pause(cancelled)
            if self.path.exists():
                try:
                    self._verified(self.path, cancelled, progress)
                    return self.path
                except AssetError:
                    if not repair:
                        raise
                    self._guard()
                    self.path.unlink()
            if repair and self.partial.exists():
                self._guard()
                self.partial.unlink()
            self._transfer(cancelled, progress)
            self._verified(self.partial, cancelled, progress)
            self._guard()
            self.partial.replace(self.path)
            return self.path
        finally:
            self._lease.release()

    def _transfer(self, cancelled, progress):
        offset = self.partial.stat().st_size if self.partial.exists() else 0
        if offset > self.asset.size:
            raise AssetError("Partial model exceeds the catalogue size; use Repair")
        if offset == self.asset.size:
            return  # A previous process may have stopped just before publication.
        required = self.asset.size - offset + CHUNK
        if shutil.disk_usage(self.directory).free < required:
            raise AssetError("There is not enough free space for the remaining model download")
        headers = {"User-Agent": "Poltergeist-models/0.1", "Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(self.asset.url, headers=headers)
        try:
            with (self._open or _opener().open)(request, timeout=NETWORK_TIMEOUT) as response:
                _https(response.geturl())
                _check_pause(cancelled)
                status = response.status
                if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise AssetError("The model server returned an encoded response")
                if status == 206:
                    expected = f"bytes {offset}-{self.asset.size - 1}/{self.asset.size}"
                    if response.headers.get("Content-Range") != expected:
                        raise AssetError("The model server returned an inconsistent resume range")
                elif status == 200:
                    offset = 0  # Range ignored: safely restart, never append a full response.
                    if shutil.disk_usage(self.directory).free < self.asset.size + CHUNK:
                        raise AssetError("There is not enough free space to restart the model download")
                else:
                    raise AssetError("The model server did not accept this download")
                expected_length = self.asset.size - offset
                length = response.headers.get("Content-Length")
                if length is not None and (len(length) > 20 or not length.isascii() or not length.isdecimal() or int(length) != expected_length):
                    raise AssetError("The model server returned an inconsistent download size")
                self._guard()
                with self.partial.open("ab" if offset else "wb") as output:
                    total = offset
                    progress("downloading", total, self.asset.size)
                    try:
                        while True:
                            _check_pause(cancelled)
                            # HTTPResponse.read1 performs at most one underlying
                            # read, so a trickling peer cannot hide Pause while
                            # a large read waits to fill its requested buffer.
                            chunk = response.read1(min(CHUNK, self.asset.size - total + 1))
                            if not chunk:
                                break
                            if total + len(chunk) > self.asset.size:
                                raise AssetError("The model download exceeds the catalogue size")
                            output.write(chunk)
                            total += len(chunk)
                            progress("downloading", total, self.asset.size)
                        if total != self.asset.size:
                            raise AssetError("The model download was interrupted; resume to continue")
                    finally:
                        output.flush()
                        os.fsync(output.fileno())
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            # A signed CDN URL can appear in urllib exceptions. Keep it out of
            # GUI messages/logs; a later attempt can resume the owned partial.
            raise AssetError("The model transfer failed; check the connection and resume") from None

    def remove(self):
        """Remove only this catalogue's owned bytes, never imported model paths."""
        if not self.directory.exists():
            self._guard()
            return
        self._own()
        try:
            self._guard()
            self.partial.unlink(missing_ok=True)
            self.path.unlink(missing_ok=True)
        finally:
            self._lease.release()
