"""Dashboard analytics API routes."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends

from backend.dependencies import get_persistence
from backend.services.analytics_service import AnalyticsService
from backend.services.persistence_service import PersistenceService
from shared.models.api import AnalyticsSummary


router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("", response_model=AnalyticsSummary)
def get_analytics(
    persistence: Annotated[PersistenceService, Depends(get_persistence)],
    mode: Literal["carla", "test"] | None = None,
) -> AnalyticsSummary:
    """Separate modes, include all persisted runs, and preserve unknown metrics.

    Mean severity includes every scored terminal result. Observed run counts
    indicate non-null metric counts, including known partial lower bounds.
    """
    return AnalyticsService(persistence).summarize(mode)
