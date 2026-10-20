"""Bounded search, recorded reproduction, and local failure reduction."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from backend.dependencies import get_optimizer, get_persistence
from backend.services.persistence_service import PersistenceService
from backend.services.comparison_service import build_comparison
from shared.models.comparison import ComparisonReport
from backend.services.optimization_service import OptimizationService
from shared.models.api import QueueStatus
from shared.models.optimization import OptimizationRecord, OptimizationRequest, OptimizationResume, OptimizationView

router = APIRouter(prefix="/optimizations", tags=["optimizations"])
Optimizer = Annotated[OptimizationService, Depends(get_optimizer)]
Persistence = Annotated[PersistenceService, Depends(get_persistence)]


@router.get("/queue", response_model=QueueStatus)
def queue(optimizer: Optimizer):
    return optimizer.status()


@router.get("", response_model=list[OptimizationRecord])
def list_operations(optimizer: Optimizer, limit: Annotated[int, Query(ge=1, le=1000)] = 50,
                    offset: Annotated[int, Query(ge=0)] = 0):
    return optimizer.list(limit=limit, offset=offset)


@router.post("", response_model=OptimizationRecord, status_code=status.HTTP_202_ACCEPTED)
def create(request: OptimizationRequest, optimizer: Optimizer):
    return optimizer.submit(request)


@router.get("/{operation_id}", response_model=OptimizationView)
def get(operation_id: UUID, optimizer: Optimizer):
    return optimizer.get(operation_id)


@router.get("/{operation_id}/comparison", response_model=ComparisonReport)
def comparison(operation_id: UUID, persistence: Persistence, response: Response, download: bool = False):
    response.headers["Cache-Control"] = "no-store"
    if download:
        response.headers["Content-Disposition"] = f'attachment; filename="poltergeist-comparison-{operation_id}.json"'
    return build_comparison(persistence, operation_id)


@router.post("/{operation_id}/cancel", response_model=OptimizationRecord, status_code=status.HTTP_202_ACCEPTED)
def cancel(operation_id: UUID, optimizer: Optimizer):
    return optimizer.cancel(operation_id)


@router.post("/{operation_id}/resume", response_model=OptimizationRecord, status_code=status.HTTP_202_ACCEPTED)
def resume(operation_id: UUID, request: OptimizationResume, optimizer: Optimizer):
    return optimizer.resume(operation_id, expected_revision=request.expected_revision)
