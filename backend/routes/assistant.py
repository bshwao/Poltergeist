"""Local trusted-API assistance. Never forwarded by the account/device relay."""

from uuid import UUID
from ipaddress import ip_address

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from backend.services.assistant_service import AssistantService
from shared.models.assistant import AssistantApproval, AssistantRecord, AssistantRequest, AssistantStatus


def local_only(request: Request):
    try:
        local = request.client is not None and ip_address(request.client.host).is_loopback
    except ValueError:
        local = False
    if not local:
        raise HTTPException(403, "OpenAI assistance is available only on this computer's local dashboard.")


router = APIRouter(prefix="/assistant", tags=["optional OpenAI assistance"], dependencies=[Depends(local_only)])


def service(request: Request):
    return AssistantService(request.app.state.settings, request.app.state.runtime.persistence)


@router.get("/status", response_model=AssistantStatus)
def status(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return AssistantService.status(request.app.state.settings)


@router.post("/previews", response_model=AssistantRecord, status_code=201)
def preview(value: AssistantRequest, response: Response, workspace=Depends(service)):
    response.headers["Cache-Control"] = "no-store"
    return workspace.preview(value)


@router.get("/requests", response_model=list[AssistantRecord])
def history(response: Response, workspace=Depends(service)):
    response.headers["Cache-Control"] = "no-store"
    return workspace.store.history()


@router.get("/requests/{identity}", response_model=AssistantRecord)
def get(identity: UUID, response: Response, workspace=Depends(service)):
    response.headers["Cache-Control"] = "no-store"
    return workspace.store.get(identity)


@router.post("/requests/{identity}/send", response_model=AssistantRecord)
def send(identity: UUID, value: AssistantApproval, response: Response, workspace=Depends(service)):
    response.headers["Cache-Control"] = "no-store"
    return workspace.run(identity, value)
