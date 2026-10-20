"""Transactional immutable registry versions, independent of the worker queue."""

from datetime import UTC, datetime

from sqlalchemy import select, update

from shared.models import AgentSnapshot, RegisteredAgent

from .models import AgentRegistrationRecord
from .persistence_errors import NotFoundError
from .repositories import _flush, _id, _page


class AgentRepository:
    def __init__(self, session):
        self.session = session

    @staticmethod
    def _stored(row):
        return RegisteredAgent(
            id=row.id, snapshot=AgentSnapshot.model_validate_json(row.payload),
            active=row.active, created_at=datetime.fromisoformat(row.created_at),
        )

    def create(self, snapshot: AgentSnapshot) -> RegisteredAgent:
        snapshot = AgentSnapshot.model_validate(snapshot.model_dump(mode="python"))
        row = AgentRegistrationRecord(
            id=str(snapshot.registration_id), payload=snapshot.model_dump_json(), active=True,
            created_at=datetime.now(UTC).isoformat(timespec="microseconds"),
        )
        self.session.add(row)
        _flush(self.session)
        return self._stored(row)

    def get(self, agent_id) -> RegisteredAgent:
        row = self.session.scalar(select(AgentRegistrationRecord).where(
            AgentRegistrationRecord.id == _id(agent_id)
        ).execution_options(populate_existing=True))
        if row is None:
            raise NotFoundError(f"Agent registration {agent_id} does not exist")
        return self._stored(row)

    def list(self, *, active=None, limit=50, offset=0) -> list[RegisteredAgent]:
        _page(limit, offset)
        statement = select(AgentRegistrationRecord)
        if active is not None:
            if type(active) is not bool:
                raise ValueError("active must be a boolean or None")
            statement = statement.where(AgentRegistrationRecord.active == active)
        statement = statement.order_by(AgentRegistrationRecord.created_at.desc(), AgentRegistrationRecord.id.desc())
        return [self._stored(row) for row in self.session.scalars(statement.limit(limit).offset(offset))]

    def deactivate(self, agent_id) -> RegisteredAgent:
        stored = self.get(agent_id)
        if stored.active:
            self.session.execute(update(AgentRegistrationRecord).where(
                AgentRegistrationRecord.id == _id(agent_id)
            ).values(active=False))
            _flush(self.session)
        return self.get(agent_id)
