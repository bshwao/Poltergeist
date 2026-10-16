"""Transactional repositories over validated shared contracts.

Repositories flush but never commit. The caller owns the session and therefore
the atomic boundary across scenarios, runs, and failure reports. Returned values
are detached snapshots, never mutable ORM rows.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from scenarios import validate_scenario
from shared.models import (
    EvaluationSettings, FailureKind, FailureReport, Scenario, Simulation,
    SimulationResult, SimulationStatus,
)

from .models import FailureRecord, ScenarioRecord, SimulationRecord
from .persistence_errors import ConflictError, NotFoundError, PersistenceError


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _canonical(value) -> str:
    # Python equality conflates True/1/1.0, which may be distinct recipe values.
    return json.dumps(value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False)


def _id(value: UUID | str) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as error:
        raise PersistenceError("Record ID must be a UUID") from error


def _revision(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PersistenceError("expected_revision must be a positive integer")


def _page(limit: int, offset: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise PersistenceError("limit must be an integer between 1 and 1000")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise PersistenceError("offset must be a nonnegative integer")


def _flush(session: Session) -> None:
    try:
        session.flush()
    except IntegrityError as error:
        # The transaction is intentionally not repaired/committed here. The
        # owning Database.session() rolls it back, including any related writes.
        raise ConflictError("Duplicate ID or database constraint conflicts with the write") from error


def _scenario(value: Scenario) -> Scenario:
    try:
        payload = value.model_dump(mode="python", warnings=False) if isinstance(value, Scenario) else value
        return validate_scenario(payload)
    except (ValueError, TypeError) as error:
        raise PersistenceError(f"Invalid scenario: {error}") from error


def _simulation(value: Simulation) -> Simulation:
    if not isinstance(value, Simulation):
        raise PersistenceError("A Simulation contract is required")
    try:
        payload = value.model_dump(mode="python", warnings=False)
        payload["scenario"] = _scenario(value.scenario).model_dump(mode="python")
        return Simulation.model_validate(payload)
    except (ValueError, TypeError) as error:
        raise PersistenceError(f"Invalid simulation: {error}") from error


def _result(value: SimulationResult) -> SimulationResult:
    if not isinstance(value, SimulationResult):
        raise PersistenceError("A SimulationResult contract is required")
    try:
        payload = value.model_dump(mode="python", warnings=False)
        payload["simulation"] = _simulation(value.simulation).model_dump(mode="python")
        return SimulationResult.model_validate(payload)
    except (ValueError, TypeError) as error:
        raise PersistenceError(f"Invalid simulation result: {error}") from error


@dataclass(frozen=True)
class StoredScenario:
    id: UUID
    scenario: Scenario
    revision: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class StoredSimulation:
    simulation: Simulation
    result: SimulationResult | None
    scenario_id: UUID | None
    revision: int
    created_at: datetime
    updated_at: datetime


class _Repository:
    def __init__(self, session: Session):
        self.session = session

    def _get(self, model, record_id):
        row = self.session.scalar(
            select(model).where(model.id == _id(record_id)).execution_options(populate_existing=True)
        )
        if row is None:
            raise NotFoundError(f"{model.__tablename__} record {record_id} does not exist")
        return row


class ScenarioRepository(_Repository):
    @staticmethod
    def _stored(row: ScenarioRecord) -> StoredScenario:
        return StoredScenario(
            UUID(row.id), Scenario.model_validate_json(row.payload), row.revision,
            datetime.fromisoformat(row.created_at), datetime.fromisoformat(row.updated_at),
        )

    def create(self, scenario: Scenario, *, id: UUID | None = None) -> StoredScenario:
        scenario = _scenario(scenario)
        timestamp = _now()
        row = ScenarioRecord(id=_id(id if id is not None else uuid4()), payload=scenario.model_dump_json(), revision=1,
                             created_at=timestamp, updated_at=timestamp)
        self.session.add(row)
        _flush(self.session)
        return self._stored(row)

    def get(self, id: UUID | str) -> StoredScenario:
        return self._stored(self._get(ScenarioRecord, id))

    def list(self, *, limit: int = 50, offset: int = 0) -> list[StoredScenario]:
        _page(limit, offset)
        rows = self.session.scalars(select(ScenarioRecord).order_by(
            ScenarioRecord.created_at.desc(), ScenarioRecord.id.desc()
        ).limit(limit).offset(offset))
        return [self._stored(row) for row in rows]

    def update(self, id: UUID | str, scenario: Scenario, *, expected_revision: int) -> StoredScenario:
        _revision(expected_revision)
        self._get(ScenarioRecord, id)
        scenario = _scenario(scenario)
        changed = self.session.execute(update(ScenarioRecord).where(
            ScenarioRecord.id == _id(id), ScenarioRecord.revision == expected_revision,
        ).values(payload=scenario.model_dump_json(), revision=expected_revision + 1, updated_at=_now()))
        if changed.rowcount != 1:
            raise ConflictError("Scenario revision changed; reload before updating")
        _flush(self.session)
        return self.get(id)

    def delete(self, id: UUID | str, *, expected_revision: int) -> None:
        _revision(expected_revision)
        self._get(ScenarioRecord, id)
        try:
            changed = self.session.execute(delete(ScenarioRecord).where(
                ScenarioRecord.id == _id(id), ScenarioRecord.revision == expected_revision,
            ))
        except IntegrityError as error:
            raise ConflictError("Scenario is referenced by a run and cannot be deleted") from error
        if changed.rowcount != 1:
            raise ConflictError("Scenario revision changed; reload before deleting")


class SimulationRepository(_Repository):
    @staticmethod
    def _stored(row: SimulationRecord) -> StoredSimulation:
        simulation = Simulation.model_validate_json(row.payload)
        result = SimulationResult.model_validate_json(row.result) if row.result is not None else None
        terminal = simulation.status not in (SimulationStatus.QUEUED, SimulationStatus.RUNNING)
        if (
            str(simulation.id) != row.id or simulation.status.value != row.status or simulation.mode != row.mode
            or _timestamp(simulation.created_at) != row.created_at
            or (result is None) == terminal
            or (result is not None and _canonical(result.simulation) != _canonical(simulation))
        ):
            raise PersistenceError("Stored simulation projections or terminal result are inconsistent")
        return StoredSimulation(
            simulation, result,
            UUID(row.scenario_id) if row.scenario_id else None, row.revision,
            datetime.fromisoformat(row.created_at), datetime.fromisoformat(row.updated_at),
        )

    def create(self, simulation: Simulation, *, scenario_id: UUID | str | None = None) -> StoredSimulation:
        simulation = _simulation(simulation)
        if simulation.status != SimulationStatus.QUEUED:
            raise PersistenceError("New simulations must be queued; use complete() to finalize a queued run")
        if scenario_id is not None:
            saved = ScenarioRepository(self.session).get(scenario_id)
            if _canonical(saved.scenario) != _canonical(simulation.scenario):
                raise PersistenceError("Run scenario must match its referenced saved scenario at creation")
        row = SimulationRecord(
            id=_id(simulation.id), scenario_id=_id(scenario_id) if scenario_id is not None else None,
            payload=simulation.model_dump_json(), result=None, status=simulation.status.value,
            mode=simulation.mode, revision=1, created_at=_timestamp(simulation.created_at), updated_at=_now(),
        )
        self.session.add(row)
        _flush(self.session)
        return self._stored(row)

    def get(self, id: UUID | str) -> StoredSimulation:
        return self._stored(self._get(SimulationRecord, id))

    def list(self, *, status: SimulationStatus | str | None = None,
             mode: Literal["carla", "test"] | None = None, limit: int = 50, offset: int = 0) -> list[StoredSimulation]:
        _page(limit, offset)
        query = select(SimulationRecord)
        if status is not None:
            try:
                status = SimulationStatus(status)
            except ValueError as error:
                raise PersistenceError("Unknown simulation status filter") from error
            query = query.where(SimulationRecord.status == status.value)
        if mode is not None:
            if mode not in ("carla", "test"):
                raise PersistenceError("Unknown simulation mode filter")
            query = query.where(SimulationRecord.mode == mode)
        rows = self.session.scalars(query.order_by(
            SimulationRecord.created_at.desc(), SimulationRecord.id.desc()
        ).limit(limit).offset(offset))
        return [self._stored(row) for row in rows]

    @staticmethod
    def _validate_change(previous: Simulation, current: Simulation) -> None:
        immutable = ("id", "agent", "mode", "created_at")
        if any(getattr(previous, field) != getattr(current, field) for field in immutable) or (
            _canonical(previous.scenario) != _canonical(current.scenario)
        ):
            raise PersistenceError("Run identity, scenario snapshot, agent, mode, and creation time are immutable")
        if previous.status not in (SimulationStatus.QUEUED, SimulationStatus.RUNNING):
            raise ConflictError("Terminal simulations are immutable")
        if previous.status == SimulationStatus.RUNNING and current.status == SimulationStatus.QUEUED:
            raise PersistenceError("Simulation lifecycle cannot return to queued")
        if current.progress < previous.progress:
            raise PersistenceError("Simulation progress cannot decrease")
        if previous.started_at is not None and previous.started_at != current.started_at:
            raise PersistenceError("Simulation start time is immutable once execution starts")

    def _write(self, simulation: Simulation, *, expected_revision: int,
               result: SimulationResult | None = None) -> StoredSimulation:
        _revision(expected_revision)
        previous = self.get(simulation.id)
        if previous.revision != expected_revision:
            raise ConflictError("Simulation revision changed; reload before updating")
        self._validate_change(previous.simulation, simulation)
        changed = self.session.execute(update(SimulationRecord).where(
            SimulationRecord.id == _id(simulation.id), SimulationRecord.revision == expected_revision,
        ).values(payload=simulation.model_dump_json(), status=simulation.status.value,
                 result=result.model_dump_json() if result is not None else None,
                 revision=expected_revision + 1, updated_at=_now()))
        if changed.rowcount != 1:
            raise ConflictError("Simulation revision changed; reload before updating")
        _flush(self.session)
        return self.get(simulation.id)

    def update(self, simulation: Simulation, *, expected_revision: int) -> StoredSimulation:
        simulation = _simulation(simulation)
        if simulation.status not in (SimulationStatus.QUEUED, SimulationStatus.RUNNING):
            raise PersistenceError("Terminal state requires complete() and its evaluated result")
        return self._write(simulation, expected_revision=expected_revision)

    def complete(self, result: SimulationResult, *, expected_revision: int) -> StoredSimulation:
        result = _result(result)
        return self._write(result.simulation, result=result, expected_revision=expected_revision)


_UNSET = object()


class FailureRepository(_Repository):
    @staticmethod
    def _stored(row: FailureRecord) -> FailureReport:
        return FailureReport.model_validate_json(row.payload)

    def create(self, report: FailureReport) -> FailureReport:
        if not isinstance(report, FailureReport):
            raise PersistenceError("A FailureReport contract is required")
        try:
            payload = report.model_dump(mode="python", warnings=False)
            payload["scenario"] = _scenario(report.scenario).model_dump(mode="python")
            report = FailureReport.model_validate(payload)
        except (ValueError, TypeError) as error:
            raise PersistenceError(f"Invalid failure report: {error}") from error
        run = SimulationRepository(self.session).get(report.simulation_id)
        if run.result is None:
            raise PersistenceError("Failure reports require a persisted terminal simulation result")
        if _canonical(report.scenario) != _canonical(run.simulation.scenario):
            raise PersistenceError("Failure scenario must match its run's immutable scenario snapshot")
        metrics = run.result.metrics
        if metrics.severity is None or report.severity != metrics.severity:
            raise PersistenceError("Failure severity must match the scored terminal result")
        settings = metrics.evaluation_settings or EvaluationSettings()
        evidence = {
            FailureKind.COLLISION: (metrics.collision_count or 0) > 0,
            FailureKind.HARD_BRAKING: (metrics.hard_braking_count or 0) > 0,
            FailureKind.TRAFFIC_VIOLATION: (metrics.traffic_violation_count or 0) > 0,
            FailureKind.NEAR_MISS: (
                metrics.min_ttc_s is not None and metrics.min_ttc_s < settings.ttc_warning_s
            ) or (
                metrics.min_clearance_m is not None and metrics.min_clearance_m < settings.clearance_warning_m
            ),
        }
        if not evidence[report.kind]:
            raise PersistenceError("Failure kind has no positive evidence in the stored evaluation")
        row = FailureRecord(id=_id(report.id), simulation_id=_id(report.simulation_id),
                            payload=report.model_dump_json(), reproduced=report.reproduced,
                            created_at=_timestamp(report.created_at))
        self.session.add(row)
        _flush(self.session)
        return self._stored(row)

    def get(self, id: UUID | str) -> FailureReport:
        return self._stored(self._get(FailureRecord, id))

    def list(self, *, simulation_id: UUID | str | None = None, reproduced=_UNSET,
             limit: int = 50, offset: int = 0) -> list[FailureReport]:
        _page(limit, offset)
        query = select(FailureRecord)
        if simulation_id is not None:
            query = query.where(FailureRecord.simulation_id == _id(simulation_id))
        if reproduced is not _UNSET:
            if reproduced is not None and type(reproduced) is not bool:
                raise PersistenceError("reproduced filter must be true, false, or null")
            query = query.where(FailureRecord.reproduced.is_(reproduced))
        rows = self.session.scalars(query.order_by(
            FailureRecord.created_at.desc(), FailureRecord.id.desc()
        ).limit(limit).offset(offset))
        return [self._stored(row) for row in rows]

    def set_reproduced(self, id: UUID | str, value: bool | None) -> FailureReport:
        if value is not None and type(value) is not bool:
            raise PersistenceError("reproduced must be true, false, or null")
        report = self.get(id)
        report.reproduced = value
        self.session.execute(update(FailureRecord).where(FailureRecord.id == _id(id)).values(
            payload=report.model_dump_json(), reproduced=value,
        ))
        _flush(self.session)
        return report


__all__ = ["ScenarioRepository", "SimulationRepository", "FailureRepository", "StoredScenario", "StoredSimulation"]
