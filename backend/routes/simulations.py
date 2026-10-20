"""Queued execution, persisted progress/results, and owned artifact access."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import FileResponse

from backend.dependencies import get_persistence, get_worker
from backend.persistence_errors import ConflictError
from backend.repositories import SimulationRepository
from backend.services.persistence_service import PersistenceService
from backend.services.simulation_service import SimulationService
from shared.models import SimulationResult, SimulationStatus
from shared.models.api import QueueStatus, SimulationLaunch, SimulationProgress, SimulationRecord, TelemetryPage


router = APIRouter(prefix="/simulations", tags=["simulations"])
Persistence = Annotated[PersistenceService, Depends(get_persistence)]
Worker = Annotated[SimulationService, Depends(get_worker)]


def _record(stored, worker) -> SimulationRecord:
    return SimulationRecord(**{name: getattr(stored, name) for name in (
        "simulation", "result", "scenario_id", "revision", "created_at", "updated_at",
    )}, job=worker.job_info(stored.simulation.id))


@router.get("/queue", response_model=QueueStatus)
def queue_status(worker: Worker) -> QueueStatus:
    return QueueStatus(**worker.status())


@router.post("", response_model=SimulationRecord, status_code=status.HTTP_202_ACCEPTED)
def launch(request: SimulationLaunch, worker: Worker) -> SimulationRecord:
    stored = worker.submit(
        request.scenario_id if request.scenario_id is not None else request.scenario,
        mode=request.mode, options=request.options, evaluation_settings=request.evaluation_settings,
        agent_id=request.agent_id,
    )
    return _record(stored, worker)


@router.get("", response_model=list[SimulationRecord])
def list_simulations(
    worker: Worker,
    status: SimulationStatus | None = None,
    mode: Literal["carla", "test"] | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[SimulationRecord]:
    with worker.persistence.database.session(write=False) as session:
        stored = SimulationRepository(session).list(status=status, mode=mode, limit=limit, offset=offset)
    return [_record(record, worker) for record in stored]


@router.get("/{simulation_id}", response_model=SimulationRecord)
def get_simulation(simulation_id: UUID, worker: Worker) -> SimulationRecord:
    return _record(worker.get(simulation_id), worker)


@router.get("/{simulation_id}/progress", response_model=SimulationProgress)
def progress(simulation_id: UUID, worker: Worker) -> SimulationProgress:
    stored = worker.get(simulation_id)
    simulation = stored.simulation
    job = worker.job_info(simulation_id)
    return SimulationProgress(
        simulation_id=simulation_id, status=simulation.status, progress=simulation.progress,
        revision=stored.revision, started_at=simulation.started_at, finished_at=simulation.finished_at,
        cancel_requested=False if job is None else job["cancel_requested"],
        queue_position=None if job is None else job["queue_position"], error=simulation.error,
    )


@router.post("/{simulation_id}/cancel", response_model=SimulationRecord, status_code=status.HTTP_202_ACCEPTED)
def cancel(simulation_id: UUID, worker: Worker) -> SimulationRecord:
    return _record(worker.cancel(simulation_id), worker)


def _result(simulation_id: UUID, persistence: PersistenceService) -> SimulationResult:
    result = persistence.get_simulation(simulation_id).result
    if result is None:
        raise ConflictError("Simulation output is not finalized yet")
    return result


@router.get("/{simulation_id}/result", response_model=SimulationResult)
def result(simulation_id: UUID, persistence: Persistence) -> SimulationResult:
    return _result(simulation_id, persistence)


@router.get("/{simulation_id}/telemetry", response_model=TelemetryPage)
def telemetry(
    simulation_id: UUID, persistence: Persistence,
    limit: Annotated[int, Query(ge=1, le=1000)] = 1000,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TelemetryPage:
    _result(simulation_id, persistence)
    output = persistence.read_output(simulation_id)
    return TelemetryPage(frames=list(output.telemetry[offset:offset + limit]), total=len(output.telemetry), offset=offset, limit=limit)


def _artifact(simulation_id, artifact_index, persistence):
    artifacts = _result(simulation_id, persistence).artifacts
    if artifact_index >= len(artifacts):
        raise HTTPException(404, "Artifact does not exist for this simulation")
    reference = artifacts[artifact_index]
    path = persistence.artifacts.validate_reference(reference, simulation_id=simulation_id)
    return reference, path


@router.get("/{simulation_id}/artifacts/{artifact_index}", response_class=FileResponse)
def download_artifact(simulation_id: UUID, artifact_index: Annotated[int, Path(ge=0)], persistence: Persistence):
    reference, path = _artifact(simulation_id, artifact_index, persistence)
    return FileResponse(path, media_type=reference.media_type, filename=path.name)


@router.get("/{simulation_id}/artifacts/{artifact_index}/samples/{sample_index}", response_class=FileResponse)
def download_sample(
    simulation_id: UUID, artifact_index: Annotated[int, Path(ge=0)],
    sample_index: Annotated[int, Path(ge=0)], persistence: Persistence,
):
    reference, _ = _artifact(simulation_id, artifact_index, persistence)
    if reference.kind != "recording":
        raise HTTPException(404, "Artifact is not a raw sensor recording")
    manifest = persistence.artifacts.read_capture_manifest(reference)
    if sample_index >= len(manifest["samples"]):
        raise HTTPException(404, "Raw sample does not exist")
    path = persistence.artifacts.capture_sample_path(reference, sample_index)
    return FileResponse(path, media_type="application/octet-stream", filename=path.name)
