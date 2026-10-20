"""Downloadable finalized evidence; no simulation worker dependency."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response

from backend.dependencies import get_persistence
from backend.services.persistence_service import PersistenceService
from backend.services.report_service import ReportFormat, export_report
from shared.models.behavior import BehaviorSettings


router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/simulations/{simulation_id}", response_class=Response)
def simulation_report(
    simulation_id: UUID,
    persistence: Annotated[PersistenceService, Depends(get_persistence)],
    format: ReportFormat = "json",
    include_telemetry: bool = False,
    include_behavior: bool = False,
) -> Response:
    """Export a terminal run. Finalized telemetry is opt-in and JSON-only."""
    exported = export_report(persistence, simulation_id, format=format, include_telemetry=include_telemetry, include_behavior=include_behavior)
    return _download(exported)


@router.post("/simulations/{simulation_id}", response_class=Response)
def configured_simulation_report(
    simulation_id: UUID,
    settings: BehaviorSettings,
    persistence: Annotated[PersistenceService, Depends(get_persistence)],
    format: ReportFormat = "json",
    include_telemetry: bool = False,
) -> Response:
    """Read-only behavior export with the same settings body as POST analysis."""
    exported = export_report(persistence, simulation_id, format=format, include_telemetry=include_telemetry,
                             include_behavior=True, behavior_settings=settings)
    return _download(exported)


def _download(exported):
    return Response(exported.content, media_type=exported.media_type, headers={
        "Content-Disposition": f'attachment; filename="{exported.filename}"',
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
        "X-Poltergeist-Report-Schema": exported.schema,
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'",
    })
