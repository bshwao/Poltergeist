"""HTTP scenario recipes preserve engine normalization and storage revisions."""

from copy import deepcopy
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from backend.config import Settings
from backend.main import create_app
from backend.services.persistence_service import PersistenceService
from scenarios import build_scenario
from scenarios.parameters import get_parameter_specs
from scenarios.stress_presets import STRESS_PRESETS
from shared.models import ScenarioTemplate


def recipe(**changes):
    return {"name": "API recipe", "duration_s": 0.1, "actors": [{"type": "ego_vehicle"}]} | changes


@pytest.fixture
def api(tmp_path):
    storage = tmp_path / "api-storage"
    settings = Settings(_env_file=None, storage_dir=storage,
                        database_url=f"sqlite:///{(storage / 'state.db').as_posix()}")
    with TestClient(create_app(settings)) as client:
        yield client, settings


def test_pure_scenario_routes_do_not_initialize_database_or_storage(api):
    client, settings = api
    assert client.get("/health").status_code == 200
    assert client.get("/scenarios/templates").status_code == 200
    assert client.get("/scenarios/templates/sudden_braking").status_code == 200
    assert client.get("/scenarios/stress-presets").status_code == 200
    assert client.post("/scenarios/validate", json=recipe()).status_code == 200
    assert client.post("/scenarios/generate", json={"template": "intersection"}).status_code == 200
    assert not settings.storage_dir.exists()


def test_stress_presets_are_pure_canonical_recipes_with_a_validated_seed(api):
    client, settings = api
    response = client.get("/scenarios/stress-presets?seed=19")
    assert response.status_code == 200
    assert response.json() == [preset.scenario(seed=19).model_dump(mode="json") for preset in STRESS_PRESETS]
    assert len(response.json()) == 12
    for seed in (-1, 2**31, "invalid"):
        assert client.get(f"/scenarios/stress-presets?seed={seed}").status_code == 422
    assert not settings.storage_dir.exists()


@pytest.mark.parametrize("template", list(ScenarioTemplate))
@pytest.mark.parametrize("sample", [False, True])
def test_selected_map_is_generated_validated_and_saved_without_carla(api, template, sample):
    client, settings = api
    response = client.post("/scenarios/generate", json={"template": template.value, "map": "Town10HD_Opt", "sample": sample})
    assert response.status_code == 200, response.text
    recipe = response.json()
    assert recipe["map"] == "Town10HD_Opt"
    assert client.post("/scenarios/validate", json=recipe).json() == recipe
    assert not settings.storage_dir.exists()
    assert client.post("/scenarios", json=recipe).json()["scenario"]["map"] == "Town10HD_Opt"


def test_template_metadata_has_reproducible_scenarios_and_parameter_bounds(api):
    client, _ = api
    response = client.get("/scenarios/templates")
    assert response.status_code == 200
    templates = response.json()
    assert [item["template"] for item in templates] == [item.value for item in ScenarioTemplate]
    for template in templates:
        assert client.get(f"/scenarios/templates/{template['template']}").json() == template
        assert template["scenario"] == build_scenario(template["template"]).model_dump(mode="json")
        parameters = {item["name"]: item for item in template["parameters"]}
        assert set(parameters) == set(template["scenario"]["parameters"])
        for name, spec in parameters.items():
            assert spec["minimum"] <= spec["default"] <= spec["maximum"]
            assert spec["default"] == template["scenario"]["parameters"][name]
            assert spec["unit"] and spec["description"]
            assert isinstance(spec["integer"], bool)
            internal = get_parameter_specs(template["template"])[name]
            assert spec["minimum"] == internal.accepted_minimum
            assert spec["maximum"] == internal.accepted_maximum
            assert set(spec) == {"name", "minimum", "maximum", "default", "unit", "description", "integer"}


def test_validation_normalizes_actor_defaults_before_resolving_references(api):
    client, settings = api
    payload = recipe(
        duration_s=1.0,
        actors=[{"type": "ego_vehicle"}, {"type": "vehicle", "relative_spawn": {
            "anchor_actor_id": "ego", "offset_m": {"x": 20.0},
        }}],
        events=[{"action": "set_speed", "actor_id": "vehicle", "trigger_time_s": 0.5, "speed_mps": 3.0}],
    )
    original = deepcopy(payload)
    response = client.post("/scenarios/validate", json=payload)
    assert response.status_code == 200, response.text
    normalized = response.json()
    assert [actor["id"] for actor in normalized["actors"]] == ["ego", "vehicle"]
    assert normalized["actors"][0]["blueprint"] == "vehicle.tesla.model3"
    assert normalized["actors"][1]["relative_spawn"]["anchor_actor_id"] == "ego"
    assert payload == original
    assert not settings.storage_dir.exists()
    saved = client.post("/scenarios", json=payload)
    assert saved.status_code == 201, saved.text
    assert saved.json()["scenario"] == normalized


def test_scenario_crud_round_trip_revisions_and_pagination(api):
    client, _ = api
    assert client.get("/scenarios").json() == []
    first = client.post("/scenarios", json=recipe()).json()
    assert first["revision"] == 1
    assert first["created_at"] == first["updated_at"]
    assert first["created_at"].endswith("Z")
    assert first["scenario"]["actors"][0]["id"] == "ego"
    assert client.get(f"/scenarios/{first['id']}").json() == first

    second = client.post("/scenarios", json=recipe(name="second")).json()
    assert client.get("/scenarios?limit=1").json() == [second]
    assert client.get("/scenarios?limit=1&offset=1").json() == [first]
    changed = client.put(f"/scenarios/{first['id']}", json={
        "scenario": recipe(name="updated"), "expected_revision": 1,
    })
    assert changed.status_code == 200, changed.text
    changed = changed.json()
    assert changed["id"] == first["id"]
    assert changed["revision"] == 2
    assert changed["created_at"] == first["created_at"]
    assert changed["scenario"]["name"] == "updated"
    assert client.put(f"/scenarios/{first['id']}", json={
        "scenario": recipe(name="stale"), "expected_revision": 1,
    }).status_code == 409
    assert client.delete(f"/scenarios/{first['id']}?expected_revision=1").status_code == 409
    assert client.get(f"/scenarios/{first['id']}").json() == changed
    deleted = client.delete(f"/scenarios/{first['id']}?expected_revision=2")
    assert deleted.status_code == 204 and deleted.content == b""
    assert client.get(f"/scenarios/{first['id']}").status_code == 404
    assert client.get("/scenarios").json() == [second]


def test_referenced_scenario_cannot_be_deleted_or_change_queued_snapshot(api):
    client, settings = api
    first = client.post("/scenarios", json=recipe()).json()
    persistence = PersistenceService.from_settings(settings)
    persistence.initialize()
    try:
        from uuid import UUID

        queued = persistence.create_simulation(UUID(first["id"]), mode="test")
        changed = client.put(f"/scenarios/{first['id']}", json={
            "scenario": recipe(name="edited after queue"), "expected_revision": 1,
        })
        assert changed.status_code == 200
        assert persistence.get_simulation(queued.simulation.id).simulation.scenario.name == "API recipe"
        assert client.delete(f"/scenarios/{first['id']}?expected_revision=2").status_code == 409
        assert client.get(f"/scenarios/{first['id']}").status_code == 200
    finally:
        persistence.database.dispose()


@pytest.mark.parametrize("method,path,body,expected", [
    ("get", "/scenarios/not-a-uuid", None, 422),
    ("get", f"/scenarios/{uuid4()}", None, 404),
    ("put", f"/scenarios/{uuid4()}", {"scenario": recipe(), "expected_revision": 1}, 404),
    ("delete", f"/scenarios/{uuid4()}?expected_revision=1", None, 404),
    ("get", "/scenarios/templates/not-a-template", None, 422),
    ("get", "/scenarios?limit=0", None, 422),
    ("get", "/scenarios?limit=1001", None, 422),
    ("get", "/scenarios?offset=-1", None, 422),
    ("get", "/scenarios?limit=true", None, 422),
    ("delete", f"/scenarios/{uuid4()}", None, 422),
    ("delete", f"/scenarios/{uuid4()}?expected_revision=0", None, 422),
    ("post", "/scenarios", recipe(unknown=True), 422),
    ("post", "/scenarios", recipe(seed=True), 422),
    ("post", "/scenarios/validate", recipe(actors=[]), 422),
    ("put", f"/scenarios/{uuid4()}", {"scenario": recipe(), "expected_revision": True}, 422),
    ("put", f"/scenarios/{uuid4()}", {"scenario": recipe(), "expected_revision": 1, "extra": 1}, 422),
])
def test_scenario_routes_validate_http_boundaries(api, method, path, body, expected):
    client, _ = api
    response = client.request(method, path, **({"json": body} if body is not None else {}))
    assert response.status_code == expected, response.text
    assert "detail" in response.json()


def test_generation_is_deterministic_bounded_and_does_not_save(api):
    client, settings = api
    fixed = {"template": "sudden_braking", "seed": 23, "parameters": {"lead_distance_m": 8.0}}
    response = client.post("/scenarios/generate", json=fixed)
    assert response.status_code == 200
    assert response.json() == build_scenario("sudden_braking", seed=23, parameters={"lead_distance_m": 8.0}).model_dump(mode="json")
    request = fixed | {"sample": True, "ranges": {"ego_speed_mps": [4.0, 5.0]}}
    sampled = client.post("/scenarios/generate", json=request)
    assert sampled.status_code == 200
    assert sampled.json() == client.post("/scenarios/generate", json=request).json()
    assert 4.0 <= sampled.json()["parameters"]["ego_speed_mps"] <= 5.0
    assert sampled.json()["parameters"]["lead_distance_m"] == 8.0
    assert sampled.json()["seed"] == 23
    assert not settings.storage_dir.exists()
    assert client.get("/scenarios").json() == []


@pytest.mark.parametrize("template", list(ScenarioTemplate))
@pytest.mark.parametrize("sample", [False, True])
def test_generation_accepts_selected_duration_without_changing_template_defaults(api, template, sample):
    client, settings = api
    request = {"template": template.value, "seed": 23, "sample": sample}
    original = client.post("/scenarios/generate", json=request)
    selected = client.post("/scenarios/generate", json=request | {"duration_s": 90.0})
    default = client.post("/scenarios/generate", json=request | {"duration_s": None})

    assert selected.status_code == default.status_code == original.status_code == 200
    assert selected.json() == original.json() | {"duration_s": 90.0}
    assert default.json() == original.json()
    assert client.post("/scenarios/validate", json=selected.json()).json() == selected.json()
    assert client.get(f"/scenarios/templates/{template.value}").json()["scenario"]["duration_s"] == 30.0
    assert not settings.storage_dir.exists()


@pytest.mark.parametrize("template", list(ScenarioTemplate))
@pytest.mark.parametrize("sample", [False, True])
def test_extended_fixed_values_and_delay_survive_generate_validate_and_save(api, template, sample):
    client, _ = api
    delay = "brake_delay_s" if template == ScenarioTemplate.SUDDEN_BRAKING else "crossing_delay_s"
    response = client.post("/scenarios/generate", json={
        "template": template.value, "sample": sample, "duration_s": 60,
        "parameters": {"ego_speed_mps": 40, "fog_density_pct": 90, "sun_altitude_deg": -45, delay: 45},
    })
    assert response.status_code == 200, response.text
    generated = response.json()
    assert generated["parameters"]["ego_speed_mps"] == 40
    assert generated["actors"][0]["speed_mps"] == 40
    assert generated["events"][0]["trigger_time_s"] == 45
    assert generated["duration_s"] == 60
    validated = client.post("/scenarios/validate", json=generated)
    assert validated.status_code == 200, validated.text
    assert validated.json() == generated
    saved = client.post("/scenarios", json=generated)
    assert saved.status_code == 201, saved.text
    assert saved.json()["scenario"] == generated
    assert client.get(f"/scenarios/{saved.json()['id']}").json() == saved.json()


@pytest.mark.parametrize("changes", [
    {"template": "unknown"},
    {"seed": -1},
    {"seed": True},
    {"duration_s": 0},
    {"duration_s": -1},
    {"duration_s": 0.01},
    {"duration_s": True},
    {"duration_s": "30"},
    {"duration_s": 2.0},
    {"sample": True, "duration_s": 5.0, "parameters": {"brake_delay_s": 5.0}},
    {"sample": "true"},
    {"parameters": {"unknown": 2.0}},
    {"parameters": {"ego_speed_mps": 100.0}},
    {"parameters": {"ego_speed_mps": True}},
    {"parameters": {"ego_speed_mps": "12"}},
    {"ranges": {"ego_speed_mps": [4.0, 5.0]}},
    {"sample": True, "ranges": {"ego_speed_mps": [5.0, 4.0]}},
    {"sample": True, "ranges": {"ego_speed_mps": [0.0, 5.0]}},
    {"sample": True, "ranges": {"unknown": [4.0, 5.0]}},
    {"sample": True, "ranges": {"ego_speed_mps": [True, 5.0]}},
    {"sample": True, "ranges": {"ego_speed_mps": ["4", 5.0]}},
    {"extra": "unsupported"},
])
def test_generation_rejects_invalid_recipes_without_creating_storage(api, changes):
    client, settings = api
    response = client.post("/scenarios/generate", json={"template": "sudden_braking"} | changes)
    assert response.status_code == 422, response.text
    assert not settings.storage_dir.exists()


def test_template_parameters_must_agree_with_actor_recipe_on_every_write(api):
    client, _ = api
    payload = build_scenario("sudden_braking").model_dump(mode="json")
    saved = client.post("/scenarios", json=payload).json()
    payload["actors"][0]["speed_mps"] = 24.0
    assert client.post("/scenarios/validate", json=payload).status_code == 422
    assert client.post("/scenarios", json=payload).status_code == 422
    assert client.put(f"/scenarios/{saved['id']}", json={
        "scenario": payload, "expected_revision": 1,
    }).status_code == 422
    assert client.get(f"/scenarios/{saved['id']}").json() == saved
