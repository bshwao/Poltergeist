"""Point-in-time local prerequisites, separate from execution evidence."""

from enum import Enum
from typing import Literal

from pydantic import AwareDatetime

from .base import ContractModel, NonEmptyString


class ReadinessStatus(str, Enum):
    ready = "ready"
    missing = "missing"
    unsupported = "unsupported"
    not_running = "not_running"
    not_checked = "not_checked"
    unknown = "unknown"
    error = "error"


class ReadinessCheck(ContractModel):
    id: NonEmptyString
    label: NonEmptyString
    status: ReadinessStatus
    detail: NonEmptyString
    next_step: NonEmptyString | None = None


class WorkflowReadiness(ContractModel):
    status: ReadinessStatus
    summary: NonEmptyString
    checks: list[ReadinessCheck]


class ReadinessReport(ContractModel):
    schema_version: Literal["poltergeist-readiness-v1"] = "poltergeist-readiness-v1"
    scope: Literal["local_core"] = "local_core"
    checked_at: AwareDatetime
    carla_connection_requested: bool
    synthetic: WorkflowReadiness
    carla: WorkflowReadiness
