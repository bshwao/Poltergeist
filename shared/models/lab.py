"""Allowlisted companion experiment actions; no filesystem or shell operations."""

from datetime import UTC, datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .api import SimulationLaunch, SimulationRecord
from .base import ContractModel
from .experimentation import (DiscoveryApproval, DiscoveryPlan, DiscoveryProposal, DiscoveryReport,
    GalleryScenario, GuidanceFeedback, ReplayHighlights, SavedFeedback, SavedSuite, SuiteLaunch,
    SuiteReport, SuiteSave, SuiteUpdate)
from .optimization import OptimizationRecord
from .evidence import EvidenceFrame
from .analysis import EvidenceAnalysis, RegressionPolicy, RegressionReport


class LabGallery(ContractModel):
    kind: Literal["gallery"] = "gallery"


class LabPage(ContractModel):
    kind: Literal["suites", "plans", "feedbacks"]
    limit: Annotated[int, Field(ge=1, le=20, strict=True)] = 11
    offset: Annotated[int, Field(ge=0, strict=True)] = 0
    before: AwareDatetime | None = None


class LabIdentity(ContractModel):
    kind: Literal["suite_get", "suite_delete", "plan_get", "discovery_report", "suite_report",
        "operation_get", "operation_cancel", "operation_resume", "run_get", "run_cancel", "highlights", "analysis_get"]
    id: UUID
    expected_revision: Annotated[int, Field(ge=1, strict=True)] | None = None

    @model_validator(mode="after")
    def revision_required(self) -> Self:
        if (self.kind in {"suite_delete", "operation_resume"}) != (self.expected_revision is not None):
            raise ValueError("Only deletion and resumption actions require an expected revision")
        return self


class LabSuiteRuns(ContractModel):
    kind: Literal["suite_runs"] = "suite_runs"
    id: UUID
    limit: Annotated[int, Field(ge=1, le=20, strict=True)] = 11
    offset: Annotated[int, Field(ge=0, strict=True)] = 0
    before: AwareDatetime | None = None


class LabSuiteSave(ContractModel):
    kind: Literal["suite_save"] = "suite_save"
    save: SuiteSave


class LabSuiteUpdate(ContractModel):
    kind: Literal["suite_update"] = "suite_update"
    id: UUID
    save: SuiteUpdate


class LabSuiteRun(ContractModel):
    kind: Literal["suite_run"] = "suite_run"
    id: UUID
    launch: SuiteLaunch


class LabPropose(ContractModel):
    kind: Literal["plan_propose"] = "plan_propose"
    proposal: DiscoveryProposal


class LabApprove(ContractModel):
    kind: Literal["plan_approve"] = "plan_approve"
    id: UUID
    approval: DiscoveryApproval


class LabFeedback(ContractModel):
    kind: Literal["feedback"] = "feedback"
    feedback: GuidanceFeedback


class LabLaunch(ContractModel):
    kind: Literal["run_launch"] = "run_launch"
    launch: SimulationLaunch

    @model_validator(mode="after")
    def remote_baseline_only(self) -> Self:
        if self.launch.agent_id is not None or self.launch.scenario_id is not None:
            raise ValueError("Hosted guided runs accept a complete baseline recipe")
        return self


class LabFrame(ContractModel):
    kind: Literal["frame_get"] = "frame_get"
    id: UUID
    frame: Annotated[int, Field(ge=0, strict=True)]


class LabRegression(ContractModel):
    kind: Literal["regression_report"] = "regression_report"
    id: UUID
    policy: RegressionPolicy = Field(default_factory=RegressionPolicy)


LabAction = Annotated[LabGallery | LabPage | LabIdentity | LabSuiteRuns | LabSuiteSave | LabSuiteUpdate | LabSuiteRun
    | LabPropose | LabApprove | LabFeedback | LabLaunch | LabFrame | LabRegression, Field(discriminator="kind")]


class LabCommand(ContractModel):
    kind: Literal["lab"] = "lab"
    request_id: UUID
    action: LabAction
    mode: Literal["test", "carla"] | None = None

    @model_validator(mode="after")
    def execution_mode(self) -> Self:
        if self.action.kind in {"suite_run", "plan_approve", "run_launch", "operation_resume"} and self.mode is None:
            raise ValueError("An execution action requires its explicit mode")
        if self.action.kind == "run_launch" and self.action.launch.mode != self.mode:
            raise ValueError("Guided run mode differs from its command")
        if self.action.kind == "suite_run" and self.action.launch.request_id != self.request_id:
            raise ValueError("A suite launch preserves one outer and inner request identity")
        return self


class LabGalleryResult(ContractModel):
    kind: Literal["gallery"] = "gallery"
    items: list[GalleryScenario] = Field(max_length=32)


class LabSuitesResult(ContractModel):
    kind: Literal["suites"] = "suites"
    items: list[SavedSuite] = Field(max_length=20)
    next_offset: Annotated[int, Field(ge=0, strict=True)] | None = None
    snapshot_at: AwareDatetime


class LabPlansResult(ContractModel):
    kind: Literal["plans"] = "plans"
    items: list[DiscoveryPlan] = Field(max_length=20)
    next_offset: Annotated[int, Field(ge=0, strict=True)] | None = None
    snapshot_at: AwareDatetime


class LabFeedbacksResult(ContractModel):
    kind: Literal["feedbacks"] = "feedbacks"
    items: list[SavedFeedback] = Field(max_length=20)
    next_offset: Annotated[int, Field(ge=0, strict=True)] | None = None
    snapshot_at: AwareDatetime


class LabSuiteResult(ContractModel):
    kind: Literal["suite"] = "suite"
    suite: SavedSuite


class LabPlanResult(ContractModel):
    kind: Literal["plan"] = "plan"
    plan: DiscoveryPlan


class LabOperationResult(ContractModel):
    kind: Literal["operation"] = "operation"
    operation: OptimizationRecord


class LabSuiteRunsResult(ContractModel):
    kind: Literal["suite_runs"] = "suite_runs"
    items: list[OptimizationRecord] = Field(max_length=20)
    next_offset: Annotated[int, Field(ge=0, strict=True)] | None = None
    snapshot_at: AwareDatetime


class LabDiscoveryResult(ContractModel):
    kind: Literal["discovery"] = "discovery"
    report: DiscoveryReport


class LabSuiteReportResult(ContractModel):
    kind: Literal["suite_report"] = "suite_report"
    report: SuiteReport


class LabRunResult(ContractModel):
    kind: Literal["run"] = "run"
    record: SimulationRecord


class LabHighlightsResult(ContractModel):
    kind: Literal["highlights"] = "highlights"
    highlights: ReplayHighlights


class LabAnalysisResult(ContractModel):
    kind: Literal["analysis"] = "analysis"
    analysis: EvidenceAnalysis


class LabRegressionResult(ContractModel):
    kind: Literal["regression"] = "regression"
    report: RegressionReport


class LabFrameResult(ContractModel):
    kind: Literal["frame"] = "frame"
    simulation_id: UUID
    frame: EvidenceFrame


class LabFeedbackResult(ContractModel):
    kind: Literal["feedback"] = "feedback"
    feedback: SavedFeedback


class LabDeletedResult(ContractModel):
    kind: Literal["deleted"] = "deleted"
    id: UUID


LabResult = Annotated[LabGalleryResult | LabSuitesResult | LabPlansResult | LabFeedbacksResult | LabSuiteResult
    | LabPlanResult | LabOperationResult | LabSuiteRunsResult | LabDiscoveryResult | LabSuiteReportResult | LabRunResult
    | LabHighlightsResult | LabFrameResult | LabFeedbackResult | LabDeletedResult | LabAnalysisResult | LabRegressionResult, Field(discriminator="kind")]


class LabReply(ContractModel):
    kind: Literal["lab"] = "lab"
    request_id: UUID
    result: LabResult


class LabReceipt(ContractModel):
    id: UUID
    digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    reply: LabReply
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
