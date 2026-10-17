"""Downloadable finalized evidence; no simulation worker dependency."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response

from backend.dependencies import get_persistence
from backend.services.persistence_service import PersistenceService
from backend.services.report_service import ReportFormat, export_report


router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/simulations/{simulation_id}", response_class=Response)
def simulation_report(
    simulation_id: UUID,
    persistence: Annotated[PersistenceService, Depends(get_persistence)],
    format: ReportFormat = "json",
    include_telemetry: bool = False,
) -> Response:
    """Export a terminal run. Finalized telemetry is opt-in and JSON-only."""
    exported = export_report(persistence, simulation_id, format=format, include_telemetry=include_telemetry)
    return Response(exported.content, media_type=exported.media_type, headers={
        "Content-Disposition": f'attachment; filename="{exported.filename}"',
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'",
    })
