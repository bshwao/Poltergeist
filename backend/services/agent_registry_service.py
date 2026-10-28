"""Local package registration and explicit static readiness checks."""

from datetime import UTC, datetime
from uuid import uuid4

from shared.models import AgentReadiness, AgentRegistration, AgentSnapshot
from simulator.agents.fingerprints import build_agent_snapshot, verify_agent_snapshot

from backend.agent_repository import AgentRepository
from backend.persistence_errors import ConflictError


class AgentRegistryService:
    def __init__(self, database):
        self.database = database

    def register(self, request: AgentRegistration):
        request = AgentRegistration.model_validate(request.model_dump(mode="python"))
        try:
            snapshot = build_agent_snapshot(request, uuid4())
        except (ValueError, OSError, RuntimeError) as error:
            raise ValueError(f"Cannot register local agent: {error}") from error
        with self.database.session() as session:
            return AgentRepository(session).create(snapshot)

    def get(self, agent_id):
        with self.database.session(write=False) as session:
            return AgentRepository(session).get(agent_id)

    def list(self, *, active=None, limit=50, offset=0):
        with self.database.session(write=False) as session:
            return AgentRepository(session).list(active=active, limit=limit, offset=offset)

    def deactivate(self, agent_id):
        with self.database.session() as session:
            return AgentRepository(session).deactivate(agent_id)

    @staticmethod
    def verify(snapshot: AgentSnapshot) -> None:
        try:
            verify_agent_snapshot(snapshot)
        except (ValueError, OSError, RuntimeError) as error:
            raise ConflictError(str(error)) from error

    def resolve(self, agent_id, *, session=None) -> AgentSnapshot:
        if session is None:
            with self.database.session(write=False) as owned:
                return self.resolve(agent_id, session=owned)
        registered = AgentRepository(session).get(agent_id)
        if not registered.active:
            raise ConflictError("Agent registration is inactive; register a new version for future launches")
        self.verify(registered.snapshot)
        return registered.snapshot

    def readiness(self, agent_id) -> AgentReadiness:
        registered = self.get(agent_id)
        issues = []
        if not registered.active:
            issues.append("Agent registration is inactive")
        try:
            self.verify(registered.snapshot)
        except ConflictError as error:
            issues.append(str(error))
        extra = {}
        isolated = registered.snapshot.isolated_runtime
        if isolated is not None and isolated.manifest_sha256 is not None:
            extra["limitations"] = ["Agent code and the selected interpreter were not executed",
                "The declared runtime inventory was checked against its pinned manifest",
                "Dependency imports, inference, drivers and hardware compatibility were not tested"]
        return AgentReadiness(agent_id=registered.id, ready=not issues, issues=issues,
                              checked_at=datetime.now(UTC), **extra)
