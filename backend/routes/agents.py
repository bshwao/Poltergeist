"""Trusted local registry operations; no uploads or dependency installation."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from backend.dependencies import get_registry
from backend.services.agent_registry_service import AgentRegistryService
from shared.models import AgentReadiness, AgentRegistration, RegisteredAgent

router = APIRouter(prefix="/agents", tags=["agents"])
Registry = Annotated[AgentRegistryService, Depends(get_registry)]


@router.post("", response_model=RegisteredAgent, status_code=status.HTTP_201_CREATED)
def register(request: AgentRegistration, registry: Registry) -> RegisteredAgent:
    return registry.register(request)


@router.get("", response_model=list[RegisteredAgent])
def list_agents(
    registry: Registry, active: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[RegisteredAgent]:
    return registry.list(active=active, limit=limit, offset=offset)


@router.get("/{agent_id}", response_model=RegisteredAgent)
def get_agent(agent_id: UUID, registry: Registry) -> RegisteredAgent:
    return registry.get(agent_id)


@router.get("/{agent_id}/readiness", response_model=AgentReadiness)
def readiness(agent_id: UUID, registry: Registry) -> AgentReadiness:
    return registry.readiness(agent_id)


@router.post("/{agent_id}/deactivate", response_model=RegisteredAgent)
def deactivate(agent_id: UUID, registry: Registry) -> RegisteredAgent:
    return registry.deactivate(agent_id)
