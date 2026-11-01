"""Curated Hub provenance and explicitly configured, origin-scoped credentials.

Remote metadata can confirm a built-in pin; it cannot choose replacement bytes.
Neither the Hub SDK's login cache nor ambient Hugging Face tokens are consulted.
"""

from dataclasses import dataclass
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import re
from time import monotonic
import urllib.error
import urllib.parse
import urllib.request

from .credential_store import CredentialStoreError, WindowsCredentialStore
from .model_assets import AssetError, ModelAsset, PLANT_WEIGHTS, NETWORK_TIMEOUT, _HTTPSRedirects, _check_pause
from simulator.agents.reference_ai.checkpoint import MODEL_REPOSITORY, MODEL_REVISION

MAX_METADATA_BYTES = 2 * 1024**2


@dataclass(frozen=True)
class HubModel:
    id: str
    name: str
    repository: str
    revision: str
    filename: str
    size: int
    sha256: str
    license: str

    def __post_init__(self):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", self.repository):
            raise ValueError("Invalid curated Hugging Face repository")
        if not re.fullmatch(r"[0-9a-f]{40}", self.revision):
            raise ValueError("A curated model requires a full immutable Hub revision")
        path = PurePosixPath(self.filename)
        if (path.is_absolute() or ".." in path.parts or "\\" in self.filename or ":" in self.filename
                or not path.parts or any(part in {"", "."} for part in self.filename.split("/"))):
            raise ValueError("Invalid curated model path")
        if not re.fullmatch(r"[a-z0-9.-]{1,80}", self.license) or not 1 <= len(self.name) <= 128:
            raise ValueError("A curated model needs its reviewed license and display name")
        self.asset()  # Also validate local filename, size and digest bounds.

    @property
    def url(self):
        path = urllib.parse.quote(self.filename, safe="/=+-_.")
        return f"https://huggingface.co/{self.repository}/resolve/{self.revision}/{path}"

    @property
    def metadata_url(self):
        return f"https://huggingface.co/api/models/{self.repository}/revision/{self.revision}?blobs=true"

    def asset(self):
        return ModelAsset(self.id, PurePosixPath(self.filename).name, self.url, self.size, self.sha256)

    def public(self):
        return {"id": self.id, "name": self.name, "provider": "huggingface", "repository": self.repository,
            "revision": self.revision, "filename": self.filename, "size": self.size,
            "sha256": self.sha256, "license": self.license,
            "model_url": f"https://huggingface.co/{self.repository}/tree/{self.revision}"}


PLANT_HUB = HubModel(PLANT_WEIGHTS.id, "PlanT 2.0 CPU", MODEL_REPOSITORY, MODEL_REVISION,
    PLANT_WEIGHTS.filename, PLANT_WEIGHTS.size, PLANT_WEIGHTS.sha256, "apache-2.0")
CATALOGUE = (PLANT_HUB,)


class HuggingFaceAccount:
    """A separate current-user DPAPI vault, populated only by an explicit Save."""
    def __init__(self, profile, *, _store=None):
        self.profile = Path(profile) / "providers" / "huggingface"
        if not self.profile.is_absolute():
            raise ValueError("Provider configuration requires an absolute profile")
        self._store = _store

    def _storage(self):
        if self._store is None:
            self._store = WindowsCredentialStore(self.profile)
        return self._store

    def _token(self):
        if self._store is None and not (self.profile / "remote-enrollment.bin").exists():
            return None
        record = self._storage().load()
        if record is None:
            return None
        if (not isinstance(record, dict) or set(record) != {"provider", "version", "token"}
                or record["provider"] != "huggingface" or type(record["version"]) is not int or record["version"] != 1):
            raise CredentialStoreError("Saved Hugging Face account configuration is invalid")
        return self._validate(record["token"])

    @staticmethod
    def _validate(token):
        if not isinstance(token, str) or not re.fullmatch(r"hf_[A-Za-z0-9_]{8,512}", token):
            raise ValueError("Enter a Hugging Face read token in the account dialog")
        return token

    def save(self, token):
        token = self._validate(token.strip() if isinstance(token, str) else token)
        self._storage().save({"provider": "huggingface", "version": 1, "token": token})

    def clear(self):
        if self._store is not None or (self.profile / "remote-enrollment.bin").exists():
            self._storage().clear()

    def status(self):
        return {"configured": self._token() is not None, "credential_storage_supported": os.name == "nt"}


class _HubRedirects(_HTTPSRedirects):
    def redirect_request(self, request, response, code, message, headers, newurl):
        redirected = super().redirect_request(request, response, code, message, headers, newurl)
        if redirected is not None:
            # Signed CDN URLs supply their own authorization. Never forward a
            # user's Hub bearer token, even through an intermediate redirect.
            redirected.remove_header("Authorization")
            redirected.remove_header("Proxy-authorization")
        return redirected


class HuggingFaceSource:
    def __init__(self, model=PLANT_HUB, account=None, *, _open=None):
        self.model, self.account = model, account
        self._open = _open or urllib.request.build_opener(urllib.request.ProxyHandler({}), _HubRedirects()).open

    def _request(self, url, headers=None):
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname != "huggingface.co" or parsed.port not in {None, 443}
                or parsed.username or parsed.password or parsed.fragment):
            raise AssetError("Hugging Face credentials require the official HTTPS Hub origin")
        request = urllib.request.Request(url, headers=headers or {})
        token = None if self.account is None else self.account._token()
        if token is not None:
            # urllib deliberately excludes these headers when constructing a
            # redirected request. The redirect handler strips them as well.
            request.add_unredirected_header("Authorization", "Bearer " + token)
        return request

    def _response(self, request, timeout):
        try:
            return self._open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            if status in {401, 403}:
                raise AssetError("Hugging Face access was refused. Check the configured read token and model access on Hugging Face.") from None
            if status == 429:
                raise AssetError("Hugging Face rate limit reached. Wait and resume the model download.") from None
            raise AssetError("The pinned Hugging Face model source is unavailable. Retry later.") from None
        except (OSError, urllib.error.URLError):
            raise AssetError("Hugging Face could not be reached. Check the connection and retry.") from None

    def open_asset(self, request, *, timeout=NETWORK_TIMEOUT):
        if request.full_url != self.model.url or request.get_method() != "GET":
            raise AssetError("Only this curated model's pinned download is allowed")
        allowed = {"user-agent", "accept-encoding", "range"}
        headers = {key: value for key, value in request.header_items() if key.lower() in allowed}
        return self._response(self._request(self.model.url, headers), timeout)

    def verify_source(self, *, cancelled=lambda: False):
        """Confirm the pinned metadata; never resolve main or trust a new hash."""
        _check_pause(cancelled)
        try:
            with self._response(self._request(self.model.metadata_url,
                    {"Accept": "application/json", "Accept-Encoding": "identity"}), NETWORK_TIMEOUT) as response:
                parsed = urllib.parse.urlsplit(response.geturl())
                if (response.status != 200 or parsed.scheme != "https" or parsed.hostname != "huggingface.co" or parsed.port not in {None, 443}
                        or response.headers.get("Content-Encoding", "identity").lower() != "identity"):
                    raise ValueError("Unexpected model metadata source")
                data, deadline = bytearray(), monotonic() + 20
                while True:
                    _check_pause(cancelled)
                    if monotonic() >= deadline:
                        raise ValueError("Model metadata deadline exceeded")
                    chunk = response.read1(min(65536, MAX_METADATA_BYTES + 1 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
                    if len(data) > MAX_METADATA_BYTES:
                        raise ValueError("Model metadata is too large")
                value = json.loads(data)
                if value["sha"] != self.model.revision or value["cardData"]["license"] != self.model.license:
                    raise ValueError("Pinned source identity or license differs")
                files = [item for item in value["siblings"] if item["rfilename"] == self.model.filename]
                if len(files) != 1:
                    raise ValueError("Pinned model file is missing or ambiguous")
                file = files[0]
                if (type(file["size"]) is not int or file["size"] != self.model.size
                        or file["lfs"]["sha256"] != self.model.sha256 or type(file["lfs"]["size"]) is not int
                        or file["lfs"]["size"] != self.model.size):
                    raise ValueError("Pinned model bytes differ")
                gated = value.get("gated", False)
                if gated not in (False, True, "auto", "manual"):
                    raise ValueError("Unknown model access policy")
                return {**self.model.public(), "verified": True, "gated": gated}
        except AssetError:
            raise
        except (ValueError, KeyError, TypeError, OSError, http.client.HTTPException, RecursionError):
            raise AssetError("Hugging Face metadata did not confirm the curated revision, file, hash and license. No model pin was changed.") from None
