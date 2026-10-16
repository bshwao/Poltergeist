"""Scenario recipes, pure template generation, and revision-checked storage."""

from dataclasses import asdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from backend.dependencies import get_persistence
from backend.repositories import ScenarioRepository
from backend.services.persistence_service import PersistenceService
from scenarios import (
    build_scenario,
    generate_scenario,
    get_parameter_specs,
    list_templates,
    load_template,
    validate_scenario,
)
from shared.models import Scenario, ScenarioTemplate
from shared.models.api import (
    ParameterInfo, ScenarioGenerate, ScenarioInput, ScenarioRecord, ScenarioUpdate, TemplateInfo,
)


router = APIRouter(prefix="/scenarios", tags=["scenarios"])
Persistence = Annotated[PersistenceService, Depends(get_persistence)]
# Normalize missing actor IDs before validating references to those defaults.
ScenarioBody = ScenarioInput


def _record(stored) -> ScenarioRecord:
    return ScenarioRecord(**{name: getattr(stored, name) for name in (
        "id", "scenario", "revision", "created_at", "updated_at",
    )})


def _template(template: ScenarioTemplate) -> TemplateInfo:
    return TemplateInfo(
        template=template,
        scenario=load_template(template),
        parameters=[ParameterInfo(**asdict(spec)) for spec in get_parameter_specs(template).values()],
    )


@router.get("/templates", response_model=list[TemplateInfo])
def templates() -> list[TemplateInfo]:
    """Inspect bundled recipes and bounds without initializing persistence."""
    return [_template(template) for template in list_templates()]


@router.get("/templates/{template}", response_model=TemplateInfo)
def template_details(template: ScenarioTemplate) -> TemplateInfo:
    return _template(template)


@router.post("/validate", response_model=Scenario)
def validate(recipe: ScenarioBody) -> Scenario:
    """Return a normalized recipe; map geometry remains adapter validation."""
    return validate_scenario(recipe)


@router.post("/generate", response_model=Scenario)
def generate(request: ScenarioGenerate) -> Scenario:
    """Build or sample a reproducible recipe without saving or executing it."""
    if request.sample:
        return generate_scenario(
            request.template, seed=request.seed, parameters=request.parameters, ranges=request.ranges,
        )
    if request.ranges:
        raise ValueError("ranges require sample=true")
    return build_scenario(request.template, seed=request.seed, parameters=request.parameters)


@router.get("", response_model=list[ScenarioRecord])
def list_scenarios(
    persistence: Persistence,
    limit: Annotated[int, Query(ge=1, le=1000)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ScenarioRecord]:
    with persistence.database.session(write=False) as session:
        return [_record(item) for item in ScenarioRepository(session).list(limit=limit, offset=offset)]


@router.post("", response_model=ScenarioRecord, status_code=status.HTTP_201_CREATED)
def create_scenario(recipe: ScenarioBody, persistence: Persistence) -> ScenarioRecord:
    # Engine validation is deliberately performed before repository writes too.
    return _record(persistence.save_scenario(validate_scenario(recipe)))


@router.get("/{scenario_id}", response_model=ScenarioRecord)
def get_scenario(scenario_id: UUID, persistence: Persistence) -> ScenarioRecord:
    with persistence.database.session(write=False) as session:
        return _record(ScenarioRepository(session).get(scenario_id))


@router.put("/{scenario_id}", response_model=ScenarioRecord)
def update_scenario(scenario_id: UUID, request: ScenarioUpdate, persistence: Persistence) -> ScenarioRecord:
    recipe = validate_scenario(request.scenario)
    with persistence.database.session() as session:
        return _record(ScenarioRepository(session).update(
            scenario_id, recipe, expected_revision=request.expected_revision,
        ))


@router.delete("/{scenario_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_scenario(
    scenario_id: UUID,
    persistence: Persistence,
    expected_revision: Annotated[int, Query(ge=1)],
) -> Response:
    with persistence.database.session() as session:
        ScenarioRepository(session).delete(scenario_id, expected_revision=expected_revision)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
