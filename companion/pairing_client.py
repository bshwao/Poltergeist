"""Bounded outbound enrollment; save protected credentials before approval."""

import json
import secrets
import ssl
from typing import Literal
from urllib import error, request
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.models.coordination import (DeviceCredentialStatus, DevicePolicy, DeviceView,
    PairingApproveInput, PairingCreateInput, PairingCreated, PairingStatus, PermissionScope)
from shared.origins import service_origin


class PairingClientError(RuntimeError):
    pass


def _parse(model, value):
    try:
        return model.model_validate(value)
    except Exception:
        raise PairingClientError("The coordinator returned an invalid enrollment message") from None


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise PairingClientError("The coordinator redirected enrollment; no credential was forwarded")


class JsonTransport:
    def __init__(self, origin, *, _ssl_context=None):
        self.origin = service_origin(origin)
        self._opener = request.build_opener(request.ProxyHandler({}), _NoRedirect(),
            request.HTTPSHandler(context=_ssl_context or ssl.create_default_context()))

    def request(self, method, path, *, body=None, authorization=None):
        if not path.startswith("/v1/") or "?" in path or "#" in path or "\\" in path or ".." in path:
            raise PairingClientError("Unsupported coordinator operation")
        headers = {"Accept": "application/json", "User-Agent": "Poltergeist-Companion/0.1"}
        data = None
        if body is not None:
            data = json.dumps(body, allow_nan=False, separators=(",", ":")).encode("utf-8")
            if len(data) > 32768:
                raise PairingClientError("Enrollment request exceeds its limit")
            headers["Content-Type"] = "application/json"
        if authorization is not None:
            headers["Authorization"] = authorization
        operation = request.Request(self.origin + path, data=data, headers=headers, method=method)
        try:
            with self._opener.open(operation, timeout=10) as response:
                content = response.read(65537)
                if len(content) > 65536 or response.headers.get_content_type() != "application/json":
                    raise PairingClientError("Unexpected coordinator response")
                return json.loads(content, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
        except PairingClientError:
            raise
        except error.HTTPError as failure:
            raise PairingClientError(f"The coordinator refused enrollment (HTTP {failure.code})") from None
        except Exception:
            raise PairingClientError("The coordinator could not be reached securely") from None


class PairingClient:
    def __init__(self, origin, credential_store, *, _transport=None):
        self.origin = service_origin(origin)
        self.store = credential_store
        self.transport = _transport or JsonTransport(self.origin)

    def _load(self):
        record = self.store.load()
        if record is None:
            return None
        try:
            record = EnrollmentRecord.model_validate(record)
            if record.origin != self.origin:
                raise ValueError("Origin mismatch")
            return record
        except Exception:
            raise PairingClientError("Saved enrollment does not match this coordinator") from None

    def begin(self, display_name, requested_scopes):
        if self._load() is not None:
            raise PairingClientError("Review or resume the existing enrollment before starting another")
        secret = "pa_" + secrets.token_urlsafe(32)
        body = PairingCreateInput(private_secret=secret, display_name=display_name, requested_scopes=requested_scopes)
        created = _parse(PairingCreated, self.transport.request("POST", "/v1/pairing", body=body.model_dump(mode="json")))
        record = EnrollmentRecord(origin=self.origin, state="attempt", created=created, private_secret=secret,
            requested_scopes=body.model_dump(mode="json")["requested_scopes"])
        self.store.save(record.model_dump(mode="json"))
        return created

    def saved_status(self):
        """Local display projection. Never expose a bearer or approval payload."""
        record = self._load()
        if record is None:
            return {"state": "unpaired", "origin": self.origin}
        result = {"state": record.state, "origin": self.origin,
            "device_id": str(record.created.device_id),
            "requested_scopes": [scope.value for scope in record.requested_scopes],
            "remote_execution_enabled": False}
        if record.state == "attempt":
            result.update(code=record.created.code, expires_at=record.created.expires_at.isoformat())
        if record.approval is not None:
            result.update(owner_id=str(record.approval.expected_account_id),
                policy=record.approval.policy.model_dump(mode="json"))
        return result

    def poll(self):
        record = self._load()
        if record is None:
            raise PairingClientError("Start pairing on this computer first")
        result = _parse(PairingStatus, self.transport.request("GET", f"/v1/pairing/{record.created.attempt_id}",
            authorization="Pairing " + record.private_secret))
        if (result.attempt_id != record.created.attempt_id or result.device_id != record.created.device_id
                or set(result.requested_scopes) != set(record.requested_scopes)):
            raise PairingClientError("The coordinator returned a different pairing binding")
        return result

    def approve(self, displayed_status, policy):
        record = self._load()
        if record is None or record.state != "attempt":
            raise PairingClientError("Resume pending approval instead of creating another credential")
        displayed = PairingStatus.model_validate(displayed_status)
        current = self.poll()
        if (current.state != "claimed" or current.claimed_account is None or current != displayed):
            raise PairingClientError("Pairing changed; review the account and permissions again")
        policy = DevicePolicy.model_validate(policy)
        if not set(policy.scopes).issubset(set(current.requested_scopes)):
            raise PairingClientError("Approve only permissions requested by this pairing")
        body = PairingApproveInput(request_id=uuid4(), expected_account_id=current.claimed_account.account_id,
            expected_revision=current.revision, credential="pd_" + secrets.token_urlsafe(32), policy=policy)
        pending = record.model_copy(update={"state": "pending_approval", "approval": body})
        # This must succeed before the first approval leaves the computer.
        self.store.save(pending.model_dump(mode="json"))
        return self._complete(pending)

    def resume(self):
        record = self._load()
        if record is None or record.state not in ("pending_approval", "paired") or record.approval is None:
            raise PairingClientError("There is no saved approval to reconcile")
        return self._complete(record, reconcile=True)

    def _complete(self, record, *, reconcile=False):
        body = record.approval
        if record.state == "pending_approval":
            try:
                response = self.transport.request("POST", f"/v1/pairing/{record.created.attempt_id}/approve",
                    authorization="Pairing " + record.private_secret, body=body.model_dump(mode="json"))
            except PairingClientError:
                if not reconcile:
                    raise
                # The approval may have committed before its reply was lost.
                # Its short-lived attempt can be retired while this credential
                # remains valid. Only an authenticated binding confirms success.
                status = self._confirm(record)
            else:
                device = _parse(DeviceView, response)
                if (device.device_id != record.created.device_id or device.owner.account_id != body.expected_account_id
                        or device.policy != body.policy or device.revoked_at is not None):
                    raise PairingClientError("The approved device binding changed")
                status = self._confirm(record)
        else:
            status = self._confirm(record)
        self.store.save(record.model_copy(update={"state": "paired"}).model_dump(mode="json"))
        return status

    def _confirm(self, record):
        body = record.approval
        status = _parse(DeviceCredentialStatus, self.transport.request("GET", "/v1/device/session",
            authorization="Bearer " + body.credential))
        if (status.device_id != record.created.device_id or status.owner_id != body.expected_account_id
                or status.ownership_generation != 1):
            raise PairingClientError("The saved credential belongs to a different device")
        return status

    def deny(self, displayed_status):
        record = self._load()
        if record is None or record.state != "attempt":
            raise PairingClientError("Reconcile a pending approval before denying pairing")
        displayed = PairingStatus.model_validate(displayed_status)
        current = self.poll()
        if current.state in ("denied", "expired", "cancelled"):
            self.store.clear()
            return
        if current != displayed or current.state not in ("pending", "claimed"):
            raise PairingClientError("Pairing changed; review it again")
        result = _parse(PairingStatus, self.transport.request("POST", f"/v1/pairing/{record.created.attempt_id}/deny",
            authorization="Pairing " + record.private_secret, body={"expected_revision": current.revision}))
        if result.state != "denied" or result.device_id != record.created.device_id:
            raise PairingClientError("The coordinator did not confirm pairing denial")
        self.store.clear()

    def forget_unapproved_attempt(self):
        record = self._load()
        if record is not None and record.state != "attempt":
            raise PairingClientError("Reconcile or revoke the saved credential before forgetting enrollment")
        self.store.clear()

    def revoke(self):
        record = self._load()
        if record is None or record.approval is None:
            raise PairingClientError("There is no paired credential to revoke")
        response = self.transport.request("POST", "/v1/device/revoke", body={},
            authorization="Bearer " + record.approval.credential)
        if response != {"device_id": str(record.created.device_id), "revoked": True}:
            raise PairingClientError("The coordinator did not confirm revocation")
        self.store.clear()


class EnrollmentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=False, hide_input_in_errors=True)
    version: Literal[1] = 1
    origin: str = Field(max_length=256)
    state: Literal["attempt", "pending_approval", "paired"]
    created: PairingCreated
    private_secret: str = Field(pattern=r"^pa_[A-Za-z0-9_-]{43}$", repr=False)
    requested_scopes: list[PermissionScope] = Field(min_length=1, max_length=5)
    approval: PairingApproveInput | None = None

    @model_validator(mode="after")
    def validate_state(self):
        if (self.state == "attempt") != (self.approval is None):
            raise ValueError("Invalid saved approval state")
        return self
