"""On-demand finalized behavior analysis; both endpoints are read-only."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response

from backend.dependencies import get_persistence
from backend.services.persistence_service import PersistenceService
from backend.services.behavior_service import build_behavior_analysis
from shared.models.behavior import BehaviorAnalysis, BehaviorSettings


router = APIRouter(prefix="/simulations", tags=["behavior analysis"])
Persistence = Annotated[PersistenceService, Depends(get_persistence)]


@router.get("/{simulation_id}/analysis", response_model=BehaviorAnalysis)
def behavior_analysis(simulation_id: UUID, persistence: Persistence, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return build_behavior_analysis(persistence, simulation_id)


@router.post("/{simulation_id}/analysis", response_model=BehaviorAnalysis)
def configured_behavior_analysis(simulation_id: UUID, settings: BehaviorSettings, persistence: Persistence, response: Response):
    """Apply explicit analysis thresholds without changing stored run settings."""
    response.headers["Cache-Control"] = "no-store"
    return build_behavior_analysis(persistence, simulation_id, settings=settings)
