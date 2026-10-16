"""Persist evidence-backed failure reports and explicit reproduction outcomes."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from backend.dependencies import get_persistence
from backend.repositories import FailureRepository
from backend.services.persistence_service import PersistenceService
from shared.models import FailureReport
from shared.models.api import ReproductionUpdate


router = APIRouter(prefix="/failures", tags=["failures"])
Persistence = Annotated[PersistenceService, Depends(get_persistence)]


@router.get("", response_model=list[FailureReport])
def list_failures(
    persistence: Persistence,
    simulation_id: UUID | None = None,
    reproduced: Literal["all", "true", "false", "unknown"] = "all",
    limit: Annotated[int, Query(ge=1, le=1000)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[FailureReport]:
    """Newest first. Unknown reproduction is distinct from a failed reproduction."""
    filters = {"simulation_id": simulation_id, "limit": limit, "offset": offset}
    if reproduced != "all":
        filters["reproduced"] = {"true": True, "false": False, "unknown": None}[reproduced]
    with persistence.database.session(write=False) as session:
        return FailureRepository(session).list(**filters)


@router.post("", response_model=FailureReport, status_code=status.HTTP_201_CREATED)
def create_failure(report: FailureReport, persistence: Persistence) -> FailureReport:
    """Require a scored terminal result with positive evidence for this kind."""
    return persistence.save_failure(report)


@router.get("/{failure_id}", response_model=FailureReport)
def get_failure(failure_id: UUID, persistence: Persistence) -> FailureReport:
    with persistence.database.session(write=False) as session:
        return FailureRepository(session).get(failure_id)


@router.patch("/{failure_id}/reproduction", response_model=FailureReport)
def update_reproduction(
    failure_id: UUID, update: ReproductionUpdate, persistence: Persistence,
) -> FailureReport:
    """Record an explicitly supplied outcome; this does not rerun a simulation."""
    with persistence.database.session() as session:
        return FailureRepository(session).set_reproduced(failure_id, update.reproduced)
