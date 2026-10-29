"""Typed account and local-approval pairing messages; no machine paths."""

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field, SecretStr, StrictBool, field_validator

from .base import ContractModel


class PermissionScope(StrEnum):
    STATUS = "status:read"
    EXECUTE_TEST = "run:execute:test"
    EXECUTE_CARLA = "run:execute:carla"
    CANCEL = "run:cancel"
    RESULT = "result:read"


class AccountInput(ContractModel):
    model_config = ConfigDict(str_strip_whitespace=False, hide_input_in_errors=True)
    username: Annotated[str, Field(min_length=3, max_length=32)]
    password: Annotated[SecretStr, Field(min_length=1, max_length=128)]


class EmptyInput(ContractModel):
    pass


class PasswordChangeInput(ContractModel):
    model_config = ConfigDict(str_strip_whitespace=False, hide_input_in_errors=True)
    old_password: Annotated[SecretStr, Field(min_length=1, max_length=128)]
    new_password: Annotated[SecretStr, Field(min_length=15, max_length=128)]


class AccountIdentity(ContractModel):
    account_id: UUID
    username: Annotated[str, Field(min_length=3, max_length=32)]


class BrowserSession(ContractModel):
    account: AccountIdentity
    csrf_token: Annotated[str, Field(min_length=43, max_length=43)] = Field(repr=False)
    expires_at: AwareDatetime


class ApprovedAgent(ContractModel):
    agent_id: UUID
    package_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class DevicePolicy(ContractModel):
    scopes: list[PermissionScope] = Field(min_length=1, max_length=5)
    max_duration_s: Annotated[float, Field(gt=0, le=120, strict=True)] = 120.
    max_ticks: Annotated[int, Field(ge=1, le=10000, strict=True)] = 10000
    max_waiting_runs: Annotated[int, Field(ge=0, le=10, strict=True)] = 10
    allow_map_reload: Literal[False] = False
    allow_raw_capture: StrictBool = False
    allow_evidence_read: bool = Field(default=False, exclude_if=lambda value: not value)
    allow_graphics_change: Literal[False] = False
    approved_agents: list[ApprovedAgent] = Field(default_factory=list, max_length=100)

    @field_validator("scopes")
    @classmethod
    def unique_scopes(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Scopes must be unique")
        return value

    @field_validator("approved_agents")
    @classmethod
    def unique_agents(cls, value):
        if len(value) != len({agent.agent_id for agent in value}):
            raise ValueError("Agent approvals must be unique")
        return value


class PairingCreateInput(ContractModel):
    model_config = ConfigDict(str_strip_whitespace=False, hide_input_in_errors=True)
    private_secret: Annotated[str, Field(pattern=r"^pa_[A-Za-z0-9_-]{43}$")] = Field(repr=False, json_schema_extra={"writeOnly": True})
    display_name: Annotated[str, Field(min_length=1, max_length=64)]
    requested_scopes: list[PermissionScope] = Field(min_length=1, max_length=5)

    @field_validator("requested_scopes")
    @classmethod
    def unique_scopes(cls, value):
        return DevicePolicy.unique_scopes(value)


PairingState = Literal["pending", "claimed", "approved", "denied", "expired", "cancelled"]


class PairingCreated(ContractModel):
    attempt_id: UUID
    device_id: UUID
    code: Annotated[str, Field(pattern=r"^[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{10}$")]
    expires_at: AwareDatetime
    poll_interval_s: Literal[3] = 3


class PairingStatus(ContractModel):
    attempt_id: UUID
    device_id: UUID
    state: PairingState
    revision: Annotated[int, Field(ge=1, strict=True)]
    expires_at: AwareDatetime
    claimed_account: AccountIdentity | None = None
    requested_scopes: list[PermissionScope]


class PairingClaimInput(ContractModel):
    code: Annotated[str, Field(pattern=r"^[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{10}$")]


class PairingApproveInput(ContractModel):
    model_config = ConfigDict(str_strip_whitespace=False, hide_input_in_errors=True)
    request_id: UUID
    expected_account_id: UUID
    expected_revision: Annotated[int, Field(ge=1, strict=True)]
    credential: Annotated[str, Field(pattern=r"^pd_[A-Za-z0-9_-]{43}$")] = Field(repr=False, json_schema_extra={"writeOnly": True})
    policy: DevicePolicy


class PairingDenyInput(ContractModel):
    expected_revision: Annotated[int, Field(ge=1, strict=True)]


class PendingApprovalCancelInput(ContractModel):
    model_config = ConfigDict(str_strip_whitespace=False, hide_input_in_errors=True)
    device_id: UUID
    owner_id: UUID
    credential: Annotated[str, Field(pattern=r"^pd_[A-Za-z0-9_-]{43}$")] = Field(repr=False, json_schema_extra={"writeOnly": True})


class PendingApprovalCancelled(ContractModel):
    attempt_id: UUID
    device_id: UUID
    owner_id: UUID
    outcome: Literal["cancelled", "expired", "denied", "revoked", "absent"]
    completed: Literal[True] = True


class DeviceView(ContractModel):
    device_id: UUID
    display_name: Annotated[str, Field(min_length=1, max_length=64)]
    owner: AccountIdentity
    ownership_generation: Annotated[int, Field(ge=1, strict=True)]
    policy_revision: Annotated[int, Field(ge=1, strict=True)]
    policy: DevicePolicy
    created_at: AwareDatetime
    revoked_at: AwareDatetime | None = None


class DeviceCredentialStatus(ContractModel):
    protocol_version: Literal["poltergeist-coordinator-v1"] = "poltergeist-coordinator-v1"
    device_id: UUID
    owner_id: UUID
    ownership_generation: Annotated[int, Field(ge=1, strict=True)]
    remote_execution_enabled: bool = False


class CoordinationContracts(ContractModel):
    empty: EmptyInput
    account_input: AccountInput
    password_change: PasswordChangeInput
    session: BrowserSession
    create: PairingCreateInput
    created: PairingCreated
    claim: PairingClaimInput
    status: PairingStatus
    approve: PairingApproveInput
    deny: PairingDenyInput
    cancel_pending_approval: PendingApprovalCancelInput
    pending_approval_cancelled: PendingApprovalCancelled
    device: DeviceView
    credential_status: DeviceCredentialStatus
