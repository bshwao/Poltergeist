"""Local, explicit GitHub publication; no relay or automatic external writes."""

from ipaddress import ip_address
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from backend.services.github_provider import GitHubError
from backend.services.github_service import GitHubService
from shared.models.github import GitHubApproval, GitHubInputs, GitHubPreviewRequest, GitHubPublication, GitHubStatus


def local_only(request: Request):
    try:
        local = request.client is not None and ip_address(request.client.host).is_loopback
    except ValueError:
        local = False
    if not local:
        raise HTTPException(403, "GitHub reporting is available only on this computer's local dashboard.")


router = APIRouter(prefix="/github", tags=["optional GitHub regression checks"], dependencies=[Depends(local_only)])


def service(request: Request):
    return GitHubService(request.app.state.settings, request.app.state.runtime.persistence)


@router.get("/status", response_model=GitHubStatus)
def status(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return GitHubService.status(request.app.state.settings)


@router.post("/previews", response_model=GitHubPublication, status_code=201)
def preview(value: GitHubPreviewRequest, response: Response, workspace=Depends(service)):
    response.headers["Cache-Control"] = "no-store"
    try:
        return workspace.preview(value)
    except GitHubError as error:
        raise HTTPException(409, str(error)) from None


@router.get("/inputs/{identity}", response_model=GitHubInputs)
def inputs(identity: UUID, response: Response, workspace=Depends(service)):
    response.headers["Cache-Control"] = "no-store"
    return workspace.inputs(identity)


@router.get("/publications", response_model=list[GitHubPublication])
def history(response: Response, workspace=Depends(service)):
    response.headers["Cache-Control"] = "no-store"
    return workspace.store.history()


@router.get("/publications/{identity}", response_model=GitHubPublication)
def get(identity: UUID, response: Response, workspace=Depends(service)):
    response.headers["Cache-Control"] = "no-store"
    return workspace.store.get(identity)


@router.post("/publications/{identity}/publish", response_model=GitHubPublication)
def publish(identity: UUID, value: GitHubApproval, response: Response, workspace=Depends(service)):
    response.headers["Cache-Control"] = "no-store"
    return workspace.publish(identity, value)


@router.post("/publications/{identity}/reconcile", response_model=GitHubPublication)
def reconcile(identity: UUID, response: Response, workspace=Depends(service)):
    response.headers["Cache-Control"] = "no-store"
    return workspace.reconcile(identity)
