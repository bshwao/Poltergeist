"""Worker-owned launch metadata, in the same transaction as a queued run."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update

from shared.models import EvaluationSettings
from shared.models.registry import AgentSnapshot
from simulator.protocols import SimulationOptions

from .models import SimulationJobRecord, SimulationRecord
from .persistence_errors import ConflictError, NotFoundError
from .repositories import SimulationRepository, _flush, _id


@dataclass(frozen=True)
class StoredJob:
    simulation_id: UUID
    options: SimulationOptions
    evaluation_settings: EvaluationSettings
    cancel_requested: bool
    claimed: bool
    agent_snapshot: AgentSnapshot | None = None


class JobRepository:
    def __init__(self, session):
        self.session = session

    @staticmethod
    def _stored(row):
        return StoredJob(UUID(row.simulation_id), SimulationOptions.model_validate_json(row.options),
                         EvaluationSettings.model_validate_json(row.evaluation_settings), row.cancel_requested, row.claimed,
                         None if row.agent_snapshot is None else AgentSnapshot.model_validate_json(row.agent_snapshot))

    def create(self, simulation_id, options, evaluation_settings, *, agent_snapshot=None):
        row = SimulationJobRecord(
            simulation_id=_id(simulation_id), options=options.model_dump_json(),
            evaluation_settings=evaluation_settings.model_dump_json(), cancel_requested=False, claimed=False,
            agent_snapshot=None if agent_snapshot is None else AgentSnapshot.model_validate(
                agent_snapshot.model_dump(mode="json")).model_dump_json(),
            created_at=datetime.now(UTC).isoformat(timespec="microseconds"),
        )
        self.session.add(row)
        _flush(self.session)
        return self._stored(row)

    def get(self, simulation_id, *, required=True):
        row = self.session.scalar(select(SimulationJobRecord).where(SimulationJobRecord.simulation_id == _id(simulation_id)))
        if row is None:
            if required:
                raise NotFoundError(f"Simulation {simulation_id} is not managed by the worker")
            return None
        return self._stored(row)

    def pending(self):
        rows = self.session.scalars(select(SimulationJobRecord).join(
            SimulationRecord, SimulationRecord.id == SimulationJobRecord.simulation_id,
        ).where(SimulationRecord.status.in_(("queued", "running"))).order_by(SimulationJobRecord.id))
        return [(SimulationRepository(self.session).get(row.simulation_id), self._stored(row)) for row in rows]

    def request_cancel(self, simulation_id):
        self.get(simulation_id)
        self.session.execute(update(SimulationJobRecord).where(
            SimulationJobRecord.simulation_id == _id(simulation_id),
        ).values(cancel_requested=True))
        _flush(self.session)

    def claim(self, simulation_id):
        """Mark execution ownership durably before factory, native setup, or captures."""
        self.get(simulation_id)
        changed = self.session.execute(update(SimulationJobRecord).where(
            SimulationJobRecord.simulation_id == _id(simulation_id), SimulationJobRecord.claimed.is_(False),
        ).values(claimed=True))
        if changed.rowcount != 1:
            raise ConflictError("Simulation job was already claimed for execution")
        _flush(self.session)


__all__ = ["JobRepository", "StoredJob"]
