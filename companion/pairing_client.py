"""Bounded outbound enrollment; save protected credentials before approval."""

import json
import math
from pathlib import Path
import secrets
import ssl
from threading import Lock
from time import monotonic
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.models.coordination import (DeviceCredentialStatus, DevicePolicy, DeviceView,
    PairingApproveInput, PairingCreateInput, PairingCreated, PairingStatus, PermissionScope)
from shared.origins import service_origin
from shared.models.coordination import PendingApprovalCancelInput, PendingApprovalCancelled


class PairingClientError(RuntimeError):
    pass


def _parse(model, value):
    try:
        return model.model_validate(value)
    except Exception:
        raise PairingClientError("The coordinator returned an invalid enrollment message") from None


class JsonTransport:
    def __init__(self, origin, *, _ssl_context=None, _request_timeout_s=10):
        self.origin = service_origin(origin)
        if type(_request_timeout_s) not in (int, float) or not math.isfinite(_request_timeout_s) or not 0 < _request_timeout_s <= 10:
            raise ValueError("Enrollment request timeout must be positive and at most ten seconds")
        self._timeout = float(_request_timeout_s)
        self._lock, self._owner = Lock(), None
        self._trust_pem = None
        if _ssl_context is not None:
            if not _ssl_context.check_hostname or _ssl_context.verify_mode != ssl.CERT_REQUIRED:
                raise ValueError("Enrollment requires certificate and hostname verification")
            self._trust_pem = "".join(ssl.DER_cert_to_PEM_cert(value) for value in _ssl_context.get_ca_certs(binary_form=True))
            if not self._trust_pem or len(self._trust_pem) > 262144:
                raise ValueError("Enrollment trust certificates exceed their limit")

    def close(self):
        # The controller retains its profile lease while an OS cleanup is pending.
        if not self._lock.acquire(blocking=False):
            return False
        try:
            if self._owner is not None:
                if not self._owner.close():
                    return False
                self._owner = None
            return True
        finally:
            self._lock.release()

    def request(self, method, path, *, body=None, authorization=None):
        deadline = monotonic() + self._timeout
        if (method not in ("GET", "POST") or not isinstance(path, str) or len(path) > 4096
                or not path.startswith("/v1/") or any(value in path for value in ("?", "#", "\\", ".."))):
            raise PairingClientError("Unsupported coordinator operation")
        data = None
        if body is not None:
            data = json.dumps(body, allow_nan=False, separators=(",", ":")).encode("utf-8")
            if len(data) > 32768:
                raise PairingClientError("Enrollment request exceeds its limit")
        if authorization is not None and (not isinstance(authorization, str) or len(authorization) > 512
                or any(ord(char) < 32 or ord(char) == 127 for char in authorization)):
            raise PairingClientError("Unsupported coordinator authorization")
        if not self._lock.acquire(timeout=max(0., deadline - monotonic())):
            raise PairingClientError("The enrollment request exceeded its time limit")
        try:
            from .enrollment_process import EnrollmentProcess, EnrollmentTimeoutError
            if self._owner is not None:
                if not self._owner.close():
                    raise PairingClientError("The previous account request is still stopping")
                self._owner = None
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise PairingClientError("The enrollment request exceeded its time limit")
            message = {"origin": self.origin, "method": method, "path": path,
                "body_json": data.decode("utf-8") if data is not None else None,
                "authorization": authorization, "trust_pem": self._trust_pem, "socket_timeout_s": remaining}
            encoded = json.dumps(message, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) > 524288:
                raise PairingClientError("Enrollment request exceeds its limit")
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise PairingClientError("The enrollment request exceeded its time limit")
            self._owner = EnrollmentProcess(Path(__file__).with_name("enrollment_worker.py"),
                timeout_s=remaining, deadline=deadline, max_response_bytes=131072)
            try:
                raw = self._owner.exchange(encoded)
                answer = json.loads(raw)
                if type(answer) is not dict or type(answer.get("ok")) is not bool:
                    raise ValueError("Invalid worker answer")
                if monotonic() >= deadline:
                    raise PairingClientError("The enrollment request exceeded its time limit")
                if answer["ok"] and set(answer) == {"ok", "value"}:
                    return answer["value"]
                code = answer.get("code")
                if code == "http" and set(answer) == {"ok", "code", "status"} and type(answer["status"]) is int and 400 <= answer["status"] <= 599:
                    raise PairingClientError(f"The coordinator refused enrollment (HTTP {answer['status']})")
                if code == "redirect" and set(answer) == {"ok", "code"}:
                    raise PairingClientError("The coordinator redirected enrollment; no credential was forwarded")
                if code == "response" and set(answer) == {"ok", "code"}:
                    raise PairingClientError("Unexpected coordinator response")
                raise PairingClientError("The coordinator could not be reached securely")
            except EnrollmentTimeoutError:
                raise PairingClientError("The enrollment request exceeded its time limit") from None
            except PairingClientError:
                raise
            except Exception:
                raise PairingClientError("The coordinator could not be reached securely") from None
            finally:
                # exchange has already attempted its bounded cleanup. Retain an
                # unconfirmed owner instead of silently adding another wait.
                if self._owner.owned:
                    raise PairingClientError("The previous account request is still stopping") from None
                self._owner = None
        finally:
            self._lock.release()


class PairingClient:
    def __init__(self, origin, credential_store, *, _transport=None):
        self.origin = service_origin(origin)
        self.store = credential_store
        self.transport = _transport or JsonTransport(self.origin)

    def close(self):
        close = getattr(self.transport, "close", None)
        return close() is True if callable(close) else True

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

    def cancel_pending_approval(self):
        record = self._load()
        if record is None or record.state != "pending_approval" or record.approval is None:
            raise PairingClientError("There is no saved pending approval to cancel")
        body = PendingApprovalCancelInput(device_id=record.created.device_id,
            owner_id=record.approval.expected_account_id, credential=record.approval.credential)
        result = _parse(PendingApprovalCancelled, self.transport.request("POST",
            f"/v1/pairing/{record.created.attempt_id}/cancel-pending-approval",
            authorization="Pairing " + record.private_secret, body=body.model_dump(mode="json")))
        if (result.attempt_id != record.created.attempt_id or result.device_id != record.created.device_id
                or result.owner_id != record.approval.expected_account_id):
            raise PairingClientError("The coordinator returned a different cancellation binding")
        # A timeout, refusal or mismatched reply leaves the protected record
        # intact. Only the server's terminal result permits removing custody.
        self.store.clear()
        return result

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
