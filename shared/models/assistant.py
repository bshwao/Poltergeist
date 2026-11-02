"""Opt-in, single-call language assistance; no execution or provider secrets."""

from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, field_validator, model_validator

from .base import ContractModel
from .analysis import AnalysisCoverage, AnalysisObservation
from .experimentation import DiscoveryPlan, DiscoveryProposal
from .scenario import ScenarioTemplate
from .simulation import FailureKind


Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class AssistantBudget(ContractModel):
    max_provider_calls: Literal[1] = 1
    max_input_bytes: Annotated[int, Field(ge=1024, le=32768, strict=True)] = 16384
    max_output_tokens: Annotated[int, Field(ge=128, le=2048, strict=True)] = 1024


class AssistantRequest(ContractModel):
    request_id: UUID = Field(default_factory=uuid4)
    kind: Literal["plan", "analysis"]
    prompt: Annotated[str, Field(min_length=1, max_length=1000)]
    budget: AssistantBudget = Field(default_factory=AssistantBudget)
    proposal: DiscoveryProposal | None = None
    simulation_id: UUID | None = None
    observation_ids: list[Annotated[int, Field(ge=0, le=31, strict=True)]] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def scope(self) -> Self:
        if self.kind == "plan":
            if self.proposal is None or self.simulation_id is not None or self.observation_ids:
                raise ValueError("Planning requires one fixed proposal and no recorded evidence")
        elif self.proposal is not None or self.simulation_id is None or not self.observation_ids:
            raise ValueError("Analysis requires one selected run and 1–16 selected observations")
        if len(set(self.observation_ids)) != len(self.observation_ids):
            raise ValueError("Select each observation once")
        return self


class AssistantApproval(ContractModel):
    digest: Digest
    approve: Literal[True]

    @field_validator("approve", mode="before")
    @classmethod
    def explicit_approval(cls, value):
        if value is not True:
            raise ValueError("Sending selected data to OpenAI requires explicit boolean approval")
        return value


class PlanTool(ContractModel):
    template: ScenarioTemplate
    failure_kind: FailureKind


class AnalysisTool(ContractModel):
    observation_ids: list[Annotated[int, Field(ge=0, le=31, strict=True)]] = Field(min_length=1, max_length=16)


class DeclineTool(ContractModel):
    reason: Literal["unsupported_action", "insufficient_evidence", "outside_budget"]


class AssistantEvidence(ContractModel):
    simulation_id: UUID
    source_digest: Digest
    telemetry_digest: Digest
    state: Literal["complete", "partial"]
    mode: Literal["carla", "test"]
    coverage: AnalysisCoverage
    observations: list[AnalysisObservation] = Field(min_length=1, max_length=16)
    observation_ids: list[int]
    unresolved_questions: list[str]
    limitations: list[str]


class AssistantPreview(ContractModel):
    id: UUID
    kind: Literal["plan", "analysis"]
    model: str
    digest: Digest
    created_at: AwareDatetime
    expires_at: AwareDatetime
    budget: AssistantBudget
    input_bytes: int
    # Exact provider JSON, excluding only its Authorization header.
    payload: str
    redactions_applied: bool


class AssistantRecord(ContractModel):
    schema_version: Literal["poltergeist-openai-assistance-v1"] = "poltergeist-openai-assistance-v1"
    id: UUID
    preview: AssistantPreview
    state: Literal["preview", "running", "completed", "declined", "failed"]
    message: str
    plan: DiscoveryPlan | None = None
    analysis: AssistantEvidence | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    reserved_output_tokens: int = 0


class AssistantStatus(ContractModel):
    enabled: bool
    configured: bool
    model: str
    daily_request_limit: int
    daily_output_token_limit: int
    scope: Literal["local_api_only"] = "local_api_only"


class AssistantContracts(ContractModel):
    request: AssistantRequest
    approval: AssistantApproval
    record: AssistantRecord
    status: AssistantStatus
