"""Versioned agent messages must preserve evidence and reject invalid inputs."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from shared.models import (
    AGENT_PROTOCOL_VERSION,
    AgentContracts,
    AgentDiagnostics,
    AgentManifest,
    AgentObservation,
    AgentReset,
    AgentSensorSpec,
    AgentStepResult,
    ControlInput,
    Transform,
)


def manifest(**overrides):
    return AgentManifest.model_validate({
        "agent_id": "example-agent", "name": "Example Agent", "version": "0.1.0",
        "entry_point": "example.agent:ExampleAgent",
    } | overrides)


def reset(**overrides):
    return AgentReset.model_validate({
        "seed": 42, "map_name": "Town03", "ego_actor_id": "ego", "fixed_delta_seconds": 0.05,
    } | overrides)


def observation(**overrides):
    return AgentObservation.model_validate({
        "frame": 10, "time_s": 0.5, "dt_s": 0.05,
        "ego": {"actor_id": "ego"}, "target_speed_mps": 12.0,
    } | overrides)


def test_agent_messages_round_trip_without_simulator_dependencies():
    messages = AgentContracts(
        manifest=manifest(
            assets=["weights/model.onnx"], requirements_file="requirements.txt",
            configuration={"enabled": True, "threshold": 0.7, "classes": [1, "car", None]},
        ),
        reset=reset(destination=Transform(), route=[Transform()]),
        observation=observation(
            ego={"actor_id": "ego", "transform": {"position": {"x": 5}}},
            actors=[{"actor_id": "pedestrian"}], route_heading_deg=-90, traffic_light_red=False,
        ),
        diagnostics=AgentDiagnostics(
            maneuver="yield", reason_codes=["pedestrian_ahead"], hazard_actor_ids=["pedestrian"],
            target_speed_mps=0, planned_trajectory=[Transform()], metadata={"risk": {"ttc": 1.25}},
        ),
        step_result=AgentStepResult(frame=10, control=ControlInput(brake=0.8)),
    )

    payload = messages.model_dump_json()
    assert AgentContracts.model_validate_json(payload) == messages
    assert json.loads(payload)["manifest"]["protocol_version"] == AGENT_PROTOCOL_VERSION == "1.0"
    assert "NaN" not in payload and "Infinity" not in payload


@pytest.mark.parametrize("factory", [manifest, reset, observation, lambda **values: AgentStepResult(frame=0, control={}, **values)])
@pytest.mark.parametrize("version", ["0.9", "2.0", 1, None])
def test_unsupported_protocol_versions_are_rejected(factory, version):
    with pytest.raises(ValidationError):
        factory(protocol_version=version)


@pytest.mark.parametrize("factory", [
    manifest, reset, observation, lambda **values: AgentStepResult(frame=0, control={}, **values),
    AgentDiagnostics, lambda **values: AgentSensorSpec(id="front", type="sensor.camera.rgb", **values),
])
def test_unknown_fields_are_rejected_in_each_agent_contract(factory):
    with pytest.raises(ValidationError):
        factory(unknown=1)


@pytest.mark.parametrize("values", [
    {"agent_id": ""}, {"agent_id": "Uppercase"}, {"agent_id": "under_score"},
    {"agent_id": "-leading"}, {"agent_id": "trailing-"}, {"agent_id": "double--dash"},
    {"name": " "}, {"version": " "}, {"name": True}, {"version": 1},
    {"entry_point": "agent..py:Agent"}, {"entry_point": "agent"},
    {"entry_point": "../agent:Agent"}, {"entry_point": "agent:Agent.run"},
    {"entry_point": "agent:123Agent"}, {"entry_point": "agent:lambda"},
    {"entry_point": "class.agent:Agent"}, {"entry_point": "agent:Agent()"},
    {"observation_mode": "oracle"}, {"control_mode": "trajectory"},
])
def test_manifest_rejects_invalid_identity_and_capability_fields(values):
    with pytest.raises(ValidationError):
        manifest(**values)


@pytest.mark.parametrize("path", [
    "", " ", "/model.pt", "../model.pt", "models/../model.pt", "./model.pt", "models//model.pt",
    "models/", "C:/model.pt", "C:model.pt", "models\\model.pt", "\\\\server\\model.pt",
    "https://example.com/model.pt", "models/file:stream", "models/CON.pt", "models/NUL",
    "models/model.pt.", "models/invalid?.pt", "models/\x00model.pt",
])
@pytest.mark.parametrize("field", ["assets", "requirements_file"])
def test_package_references_must_be_portable_relative_paths(field, path):
    value = [path] if field == "assets" else path
    with pytest.raises(ValidationError):
        manifest(**{field: value})


def test_package_references_are_declarations_not_filesystem_checks():
    result = manifest(assets=["weights/future-model.pt"], requirements_file="environment/requirements.txt")
    assert result.assets == ["weights/future-model.pt"]
    assert result.requirements_file == "environment/requirements.txt"


def test_duplicate_asset_declarations_are_rejected():
    with pytest.raises(ValidationError, match="Asset paths must be unique"):
        manifest(assets=["weights/model.pt", "weights/model.pt"])


def test_sensor_declarations_round_trip_without_claiming_runtime_support():
    result = manifest(observation_mode="sensors", sensors=[
        {"id": "front", "type": "sensor.camera.rgb", "attributes": {"image_size_x": 640, "fov": 90.0}},
        {"id": "roof", "type": "sensor.lidar.ray_cast"},
        {"id": "imu", "type": "sensor.other.imu"},
        {"id": "gps", "type": "sensor.other.gnss"},
    ])
    assert AgentManifest.model_validate_json(result.model_dump_json()) == result
    assert len(result.sensors) == 4


@pytest.mark.parametrize("values", [
    {"observation_mode": "sensors"},
    {"sensors": [{"id": "front", "type": "sensor.camera.rgb"}]},
    {"observation_mode": "sensors", "sensors": [
        {"id": "front", "type": "sensor.camera.rgb"}, {"id": "front", "type": "sensor.other.imu"},
    ]},
    {"observation_mode": "sensors", "sensors": [{"id": " ", "type": "sensor.camera.rgb"}]},
    {"observation_mode": "sensors", "sensors": [{"id": "front", "type": "sensor.camera.depth"}]},
])
def test_inconsistent_or_invalid_sensor_declarations_are_rejected(values):
    with pytest.raises(ValidationError):
        manifest(**values)


@pytest.mark.parametrize("json_value", [
    {"nested": [float("nan")]}, {"nested": {"value": float("inf")}}, {"negative_inf": float("-inf")},
    {"tuple": (1, 2)}, {"set": {1}}, {"bytes": b"model"}, {"path": Path("weights/model.pt")},
    {"keys": {1: "value"}}, None, [], "string",
])
@pytest.mark.parametrize("factory", [
    lambda value: manifest(configuration=value),
    lambda value: AgentDiagnostics(metadata=value),
    lambda value: AgentSensorSpec(id="front", type="sensor.camera.rgb", attributes=value),
])
def test_json_payloads_reject_nonfinite_or_non_json_values(factory, json_value):
    with pytest.raises(ValidationError):
        factory(json_value)


def test_json_payloads_reject_cycles_but_allow_shared_acyclic_values():
    cyclic = {}
    cyclic["self"] = cyclic
    with pytest.raises(ValidationError, match="circular"):
        manifest(configuration=cyclic)

    shared = [True, None, 1, 0.5, "001"]
    result = manifest(configuration={"first": shared, "second": shared})
    assert result.configuration["first"] == shared
    assert result.configuration["first"][-1] == "001"


@pytest.mark.parametrize("values", [
    {"seed": -1}, {"seed": 2**31}, {"seed": True}, {"seed": "42"},
    {"map_name": " "}, {"ego_actor_id": " "},
    {"fixed_delta_seconds": 0}, {"fixed_delta_seconds": 0.2}, {"fixed_delta_seconds": True},
    {"fixed_delta_seconds": "0.05"}, {"fixed_delta_seconds": float("nan")},
    {"destination": {"position": {"x": float("inf")}}},
    {"route": [{"rotation": {"yaw_deg": float("nan")}}]},
])
def test_reset_requires_valid_scenario_identity_timing_and_route(values):
    with pytest.raises(ValidationError):
        reset(**values)


def test_unknown_observation_fields_remain_distinct_from_known_empty_values():
    unknown = observation()
    known = observation(actors=[], traffic_light_red=False)

    assert unknown.actors is None and unknown.traffic_light_red is None
    assert known.actors == [] and known.traffic_light_red is False
    assert AgentObservation.model_validate_json(unknown.model_dump_json()) == unknown
    assert AgentObservation.model_validate_json(known.model_dump_json()) == known


@pytest.mark.parametrize("values", [
    {"frame": -1}, {"frame": True}, {"frame": "10"}, {"frame": 1.5},
    {"time_s": -1}, {"time_s": True}, {"time_s": "0.5"}, {"time_s": float("inf")},
    {"dt_s": 0}, {"dt_s": -0.05}, {"dt_s": True}, {"dt_s": "0.05"}, {"dt_s": float("nan")},
    {"target_speed_mps": -1}, {"target_speed_mps": True}, {"target_speed_mps": float("inf")},
    {"route_heading_deg": True}, {"route_heading_deg": float("nan")}, {"traffic_light_red": "false"},
    {"actors": [{"actor_id": "ego"}]},
    {"actors": [{"actor_id": "other"}, {"actor_id": "other"}]},
    {"ego": {"actor_id": "ego", "velocity_mps": {"x": float("inf")}}},
    {"observation_mode": "sensors"},
])
def test_observation_rejects_invalid_timing_controls_or_actor_evidence(values):
    with pytest.raises(ValidationError):
        observation(**values)


@pytest.mark.parametrize("values", [
    {"maneuver": " "}, {"reason_codes": [""]}, {"hazard_actor_ids": [" "]},
    {"target_speed_mps": -1}, {"target_speed_mps": float("inf")}, {"target_speed_mps": True},
    {"planned_trajectory": [{"position": {"x": "3"}}]},
])
def test_diagnostics_reject_invalid_explanation_evidence(values):
    with pytest.raises(ValidationError):
        AgentDiagnostics.model_validate(values)


@pytest.mark.parametrize("values", [
    {"frame": -1}, {"frame": True}, {"control": None},
    {"control": {"throttle": 1.1}}, {"control": {"brake": -0.1}},
    {"control": {"steer": 1.1}}, {"control": {"throttle": True}},
    {"control": {"steer": float("nan")}}, {"control": {"reverse": "false"}},
    {"diagnostics": {"target_speed_mps": -1}},
])
def test_step_result_requires_valid_frame_and_vehicle_control(values):
    with pytest.raises(ValidationError):
        AgentStepResult.model_validate({"frame": 0, "control": {}} | values)


def test_step_result_cannot_omit_its_control():
    with pytest.raises(ValidationError):
        AgentStepResult(frame=0)


def test_mutable_defaults_are_not_shared_between_agent_instances():
    first_manifest, second_manifest = manifest(), manifest()
    first_reset, second_reset = reset(), reset()
    first_diagnostics, second_diagnostics = AgentDiagnostics(), AgentDiagnostics()
    first_manifest.assets.append("weights/model.pt")
    first_manifest.configuration["threshold"] = 0.5
    first_reset.route.append(Transform())
    first_diagnostics.reason_codes.append("pedestrian_ahead")
    first_diagnostics.metadata["risk"] = 0.5

    assert second_manifest.assets == [] and second_manifest.configuration == {}
    assert second_reset.route == []
    assert second_diagnostics.reason_codes == [] and second_diagnostics.metadata == {}


def test_agent_models_are_reachable_from_exported_contract_schema():
    from shared.contracts import PoltergeistContracts

    definitions = PoltergeistContracts.model_json_schema(mode="serialization")["$defs"]
    for model in (AgentManifest, AgentSensorSpec, AgentReset, AgentObservation, AgentDiagnostics, AgentStepResult):
        assert model.__name__ in definitions
    assert definitions["AgentStepResult"]["additionalProperties"] is False
