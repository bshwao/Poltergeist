"""Immutable local agent registrations and execution provenance.

Readiness means static file and declaration inspection, never a claim that
dependencies import, inference works, or a model drives safely.
"""

import json
from pathlib import Path
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue, StrictBool, field_validator, model_validator

from .agent import AgentManifest, AgentObservationMode, _require_json_object, _validate_package_path
from .base import ContractModel, NonEmptyString, NonNegativeInt, PositiveFloat

SHA256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", strict=True)]


def _absolute_local(value: str) -> str:
    path = Path(value)
    if not path.is_absolute() or value.startswith(("\\\\", "//")):
        raise ValueError("Registered file paths must be absolute local filesystem paths")
    return value


class AgentExecutionOptions(ContractModel):
    load_timeout_s: PositiveFloat = 30.0
    reset_timeout_s: PositiveFloat = 10.0
    step_timeout_s: PositiveFloat = 2.0
    close_timeout_s: PositiveFloat = 2.0
    terminate_timeout_s: PositiveFloat = 1.0
    max_message_bytes: Annotated[int, Field(gt=0, le=64 * 1024 * 1024, strict=True)] = 4 * 1024 * 1024


class AgentRegistration(ContractModel):
    package_path: NonEmptyString
    python_executable: NonEmptyString | None = None
    configuration: dict[str, JsonValue] = Field(default_factory=dict)
    model_files: list[NonEmptyString] = Field(default_factory=list, max_length=256)
    process_options: AgentExecutionOptions = Field(default_factory=AgentExecutionOptions)
    agent_control_mode: Literal["authoritative", "scripted"] = "authoritative"
    trusted_local_code: Literal[True]

    _json_configuration = field_validator("configuration", mode="before")(_require_json_object)

    @field_validator("trusted_local_code", mode="before")
    @classmethod
    def explicit_trust(cls, value):
        if value is not True:
            raise ValueError("trusted_local_code must explicitly be true for executable local Python packages")
        return value


class AgentFileFingerprint(ContractModel):
    path: NonEmptyString
    sha256: SHA256
    size_bytes: NonNegativeInt

    _relative_path = field_validator("path")(_validate_package_path)


class AgentExternalFileFingerprint(ContractModel):
    path: NonEmptyString
    sha256: SHA256
    size_bytes: NonNegativeInt

    _absolute_path = field_validator("path")(_absolute_local)


class AgentSnapshot(ContractModel):
    registration_id: UUID
    package_path: NonEmptyString
    python_executable: NonEmptyString
    python_sha256: SHA256
    python_size_bytes: NonNegativeInt
    manifest: AgentManifest
    configuration: dict[str, JsonValue]
    observation_mode: AgentObservationMode
    files: list[AgentFileFingerprint] = Field(min_length=1, max_length=20000)
    model_files: list[AgentExternalFileFingerprint] = Field(default_factory=list, max_length=256)
    runtime_files: list[AgentExternalFileFingerprint] = Field(default_factory=list, max_length=20000)
    package_sha256: SHA256
    process_options: AgentExecutionOptions = Field(default_factory=AgentExecutionOptions)
    agent_control_mode: Literal["authoritative", "scripted"] = "authoritative"

    _json_configuration = field_validator("configuration", mode="before")(_require_json_object)
    _absolute_paths = field_validator("package_path", "python_executable")(_absolute_local)

    @model_validator(mode="after")
    def consistent_snapshot(self) -> Self:
        canonical = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if canonical(self.configuration) != canonical(self.manifest.configuration):
            raise ValueError("Snapshot configuration must equal its effective manifest configuration")
        if self.observation_mode != self.manifest.observation_mode:
            raise ValueError("Snapshot observation mode must match its manifest")
        for collection in (self.files, self.model_files, self.runtime_files):
            paths = [item.path for item in collection]
            if paths != sorted(set(paths)):
                raise ValueError("Fingerprint paths must be sorted and unique")
        return self


class RegisteredAgent(ContractModel):
    id: UUID
    snapshot: AgentSnapshot
    active: StrictBool
    created_at: AwareDatetime

    @model_validator(mode="after")
    def consistent_identity(self) -> Self:
        if self.id != self.snapshot.registration_id:
            raise ValueError("Registration ID must match its immutable snapshot")
        return self


class AgentReadiness(ContractModel):
    agent_id: UUID
    ready: StrictBool
    inspection: Literal["static_only"] = "static_only"
    inference_checked: Literal[False] = False
    issues: list[NonEmptyString] = Field(default_factory=list)
    limitations: list[NonEmptyString] = Field(default_factory=lambda: [
        "Agent code and the selected interpreter were not executed",
        "Dependency availability and third-party environment contents were not verified",
        "Only package files, declared external models, first-party runtime code, and interpreter bytes are fingerprinted",
    ])
    checked_at: AwareDatetime
