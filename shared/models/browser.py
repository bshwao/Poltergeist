"""Public, owner-scoped website projections; never device credentials or paths."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from .base import ContractModel
from .coordination import DeviceView
from .remote import RemoteCommandView
from .scenario import Scenario


class BrowserConnectionView(ContractModel):
    state: Literal["offline", "connecting", "connected", "paused", "revoked"]
    last_seen_at: AwareDatetime | None = None
    protocol_version: Literal["poltergeist-relay-v1"] | None = None
    protocol_compatible: bool | None = None
    remote_requests_enabled: bool = False
    baseline_only: Literal[True] = True
    carla_readiness: Literal["unknown"] = "unknown"
    evidence_supported: bool = False
    recordings_supported: bool = False
    lab_supported: bool = False


class BrowserDeviceView(ContractModel):
    device: DeviceView
    connection: BrowserConnectionView


class BrowserCommandPage(ContractModel):
    items: list[RemoteCommandView] = Field(max_length=50)
    next_before: UUID | None = None


class BrowserScenarioPreset(ContractModel):
    id: Annotated[str, Field(min_length=1, max_length=64)]
    label: Annotated[str, Field(min_length=1, max_length=80)]
    description: Annotated[str, Field(max_length=400)]
    family: Literal["installation", "intersection", "pedestrian_crossing", "sudden_braking", "occluded_pedestrian"]
    intended_stress: Literal["low", "medium", "high"] | None = None
    recipe: Scenario


class BrowserContracts(ContractModel):
    device: BrowserDeviceView
    commands: BrowserCommandPage
    presets: list[BrowserScenarioPreset] = Field(max_length=13)
