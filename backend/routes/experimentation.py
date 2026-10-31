"""Guided workflows and explicit, idempotent approval of bounded experiment plans."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from backend.dependencies import get_optimizer, get_persistence
from backend.services.experimentation_service import ExperimentationService, gallery, replay_highlights
from backend.services.comparison_service import build_suite_report
from shared.models.experimentation import (DiscoveryApproval, DiscoveryPlan, DiscoveryProposal, DiscoveryReport,
    GalleryScenario, GuidanceFeedback, ReplayHighlights, SavedFeedback, SavedSuite, SuiteLaunch, SuiteReport, SuiteSave, SuiteUpdate)
from shared.models.optimization import OptimizationRecord
from shared.models.analysis import EvidenceAnalysis, RegressionPolicy, RegressionReport
from backend.services.evidence_analysis_service import build_evidence_analysis
from backend.services.regression_service import build_regression_report

router = APIRouter(prefix="/experimentation", tags=["experimentation"])


def service(persistence=Depends(get_persistence)):
    return ExperimentationService(persistence)


Service = Annotated[ExperimentationService, Depends(service)]
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0)]


@router.get("/gallery", response_model=list[GalleryScenario])
def scenario_gallery():
    return gallery()


@router.get("/suites", response_model=list[SavedSuite])
def suites(workspace: Service, limit: PageLimit = 50, offset: PageOffset = 0):
    return workspace.list_documents("suite", limit=limit, offset=offset)


@router.post("/suites", response_model=SavedSuite, status_code=201)
def save_suite(request: SuiteSave, workspace: Service):
    return workspace.save_suite(request)


@router.get("/suites/{identity}", response_model=SavedSuite)
def get_suite(identity: UUID, workspace: Service, response: Response, download: bool = False):
    if download:
        response.headers["Content-Disposition"] = f'attachment; filename="poltergeist-suite-{identity}.json"'
    response.headers["Cache-Control"] = "no-store"
    return workspace.get_document("suite", identity)


@router.put("/suites/{identity}", response_model=SavedSuite)
def update_suite(identity: UUID, request: SuiteUpdate, workspace: Service):
    return workspace.save_suite(request, identity=identity)


@router.delete("/suites/{identity}", status_code=204)
def delete_suite(identity: UUID, workspace: Service, expected_revision: Annotated[int, Query(ge=1)]):
    workspace.delete_suite(identity, expected_revision)
    return Response(status_code=204)


@router.post("/suites/{identity}/runs", response_model=OptimizationRecord, status_code=202)
def run_suite(identity: UUID, request: SuiteLaunch, workspace: Service, optimizer=Depends(get_optimizer)):
    return workspace.launch_suite(identity, request, optimizer)


@router.get("/suites/{identity}/runs", response_model=list[OptimizationRecord])
def suite_runs(identity: UUID, workspace: Service, limit: PageLimit = 50, offset: PageOffset = 0):
    return workspace.suite_runs(identity, limit=limit, offset=offset)


@router.get("/suite-runs/{identity}", response_model=SuiteReport)
def suite_report(identity: UUID, workspace: Service, response: Response, download: bool = False):
    if download:
        response.headers["Content-Disposition"] = f'attachment; filename="poltergeist-suite-report-{identity}.json"'
    response.headers["Cache-Control"] = "no-store"
    return build_suite_report(workspace.persistence, identity)


@router.post("/discovery/plans", response_model=DiscoveryPlan, status_code=201)
def propose(request: DiscoveryProposal, workspace: Service):
    return workspace.propose(request)


@router.get("/discovery/plans", response_model=list[DiscoveryPlan])
def plans(workspace: Service, limit: PageLimit = 50, offset: PageOffset = 0):
    return workspace.list_documents("discovery", limit=limit, offset=offset)


@router.get("/discovery/plans/{identity}", response_model=DiscoveryPlan)
def get_plan(identity: UUID, workspace: Service):
    return workspace.get_document("discovery", identity)


@router.post("/discovery/plans/{identity}/approve", response_model=OptimizationRecord, status_code=202)
def approve(identity: UUID, request: DiscoveryApproval, workspace: Service, optimizer=Depends(get_optimizer)):
    return workspace.approve(identity, request, optimizer)


@router.get("/discovery/plans/{identity}/report", response_model=DiscoveryReport)
def discovery_report(identity: UUID, workspace: Service, response: Response, download: bool = False):
    if download:
        response.headers["Content-Disposition"] = f'attachment; filename="poltergeist-discovery-{identity}.json"'
    response.headers["Cache-Control"] = "no-store"
    return workspace.discovery_report(identity)


@router.get("/highlights/{identity}", response_model=ReplayHighlights)
def highlights(identity: UUID, workspace: Service):
    return replay_highlights(workspace.persistence, identity)


@router.get("/analysis/{identity}", response_model=EvidenceAnalysis)
def evidence_analysis(identity: UUID, workspace: Service, response: Response, download: bool = False):
    if download:
        response.headers["Content-Disposition"] = f'attachment; filename="poltergeist-analysis-{identity}.json"'
    response.headers["Cache-Control"] = "no-store"
    return build_evidence_analysis(workspace.persistence, identity)


@router.get("/regressions/{identity}", response_model=RegressionReport)
def regression_report(identity: UUID, workspace: Service, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return build_regression_report(workspace.persistence, identity)


@router.post("/regressions/{identity}", response_model=RegressionReport)
def review_regression(identity: UUID, policy: RegressionPolicy, workspace: Service, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return build_regression_report(workspace.persistence, identity, policy)


@router.post("/feedback", response_model=SavedFeedback, status_code=201)
def feedback(request: GuidanceFeedback, workspace: Service):
    return workspace.save_feedback(request)


@router.get("/feedback", response_model=list[SavedFeedback])
def feedback_history(workspace: Service, limit: PageLimit = 50, offset: PageOffset = 0):
    return workspace.list_documents("feedback", limit=limit, offset=offset)
