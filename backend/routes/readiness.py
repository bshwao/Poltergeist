"""Local setup snapshots, independent of storage and queue initialization."""

from fastapi import APIRouter, Request, Response

from backend.services.readiness_service import collect_readiness
from shared.models.readiness import ReadinessReport

router = APIRouter(prefix="/readiness", tags=["readiness"])


@router.get("", response_model=ReadinessReport)
def get_readiness(request: Request, response: Response) -> ReadinessReport:
    """Static inspection only: no CARLA imports, network probes or writes."""
    response.headers["Cache-Control"] = "no-store"
    return collect_readiness(request.app.state.settings)


@router.post("/carla-check", response_model=ReadinessReport)
def check_carla_connection(request: Request, response: Response) -> ReadinessReport:
    """Explicit read-only RPC probe, without starting CARLA or changing a world."""
    response.headers["Cache-Control"] = "no-store"
    return collect_readiness(request.app.state.settings, connection_requested=True)
