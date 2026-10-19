"""Versioned, CARLA-free boundary between driving agents and Poltergeist.

Simulator-state observations intentionally contain ground-truth information.
Sensor declarations describe future input capabilities; they do not imply that
the current SDK can deliver those inputs.
"""

import keyword
import math
import base64
import binascii
from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, StrictBool, StrictStr, field_validator, model_validator

from .base import ContractModel, NonEmptyString, NonNegativeFloat, NonNegativeInt, PositiveFloat, Seed
from .scenario import DrivingTask, Transform, Vector3
from .telemetry import ActorState, BoundingBoxExtent, ControlInput

AGENT_PROTOCOL_VERSION = "1.0"
AgentProtocolVersion = Literal["1.0", "1.1", "1.2"]
AgentObservationMode = Literal["simulator_state", "sensors"]
AgentSensorType = Literal[
    "sensor.camera.rgb", "sensor.lidar.ray_cast", "sensor.other.imu", "sensor.other.gnss",
]
AgentIdentifier = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
AgentEntryPoint = Annotated[
    str,
    Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*$"),
]


def _require_json_object(value: object) -> object:
    """Reject coercions and non-finite numbers, including nested JSON values."""
    if not isinstance(value, dict):
        raise ValueError("Must be a JSON object")
    pending: list[tuple[object, frozenset[int]]] = [(value, frozenset())]
    while pending:
        item, ancestors = pending.pop()
        if item is None or type(item) in (str, bool, int):
            continue
        if type(item) is float:
            if not math.isfinite(item):
                raise ValueError("JSON values must contain only finite numbers")
            continue
        if type(item) not in (list, dict):
            raise ValueError("Values must use only JSON objects, arrays, strings, booleans, numbers, or null")
        identity = id(item)
        if identity in ancestors:
            raise ValueError("JSON values must not contain circular references")
        nested_ancestors = ancestors | {identity}
        if isinstance(item, dict):
            if any(type(key) is not str for key in item):
                raise ValueError("JSON object keys must be strings")
            pending.extend((nested, nested_ancestors) for nested in item.values())
        else:
            pending.extend((nested, nested_ancestors) for nested in item)
    return value


def _validate_package_path(value: str) -> str:
    """Require an unambiguous package-relative path on Windows and POSIX."""
    if "\\" in value or ":" in value or any(ord(character) < 32 for character in value):
        raise ValueError("Package paths must be relative and use forward slashes")
    components = value.split("/")
    if any(component in ("", ".", "..") for component in components):
        raise ValueError("Package paths must not contain absolute, empty, dot, or traversal components")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if any(
        component.endswith((".", " "))
        or any(character in '<>"|?*' for character in component)
        or component.split(".", 1)[0].upper() in reserved
        for component in components
    ):
        raise ValueError("Package paths must use portable file names")
    return value


class AgentSensorSpec(ContractModel):
    id: NonEmptyString
    type: AgentSensorType
    attributes: dict[str, JsonValue] = Field(default_factory=dict)

    _json_attributes = field_validator("attributes", mode="before")(_require_json_object)


class AgentManifest(ContractModel):
    protocol_version: AgentProtocolVersion = AGENT_PROTOCOL_VERSION
    agent_id: AgentIdentifier
    name: NonEmptyString
    version: NonEmptyString
    entry_point: AgentEntryPoint
    observation_mode: AgentObservationMode = "simulator_state"
    control_mode: Literal["vehicle_control"] = "vehicle_control"
    navigation_mode: Literal["provided_route", "agent_planner"] = "provided_route"
    sensors: list[AgentSensorSpec] = Field(default_factory=list)
    assets: list[NonEmptyString] = Field(default_factory=list)
    requirements_file: NonEmptyString | None = None
    configuration: dict[str, JsonValue] = Field(default_factory=dict)
    required_features: list[Literal["structured_scene"]] = Field(default_factory=list)

    _json_configuration = field_validator("configuration", mode="before")(_require_json_object)

    @field_validator("entry_point")
    @classmethod
    def validate_entry_point(cls, value: str) -> str:
        module_name, class_name = value.split(":")
        if any(keyword.iskeyword(part) for part in [*module_name.split("."), class_name]):
            raise ValueError("Entry point must use valid Python module and class identifiers")
        return value

    @field_validator("assets")
    @classmethod
    def validate_assets(cls, value: list[str]) -> list[str]:
        for path in value:
            _validate_package_path(path)
        if len(value) != len(set(value)):
            raise ValueError("Asset paths must be unique")
        return value

    @field_validator("requirements_file")
    @classmethod
    def validate_requirements_path(cls, value: str | None) -> str | None:
        return _validate_package_path(value) if value is not None else None

    @model_validator(mode="after")
    def validate_sensor_declarations(self) -> Self:
        sensor_ids = [sensor.id for sensor in self.sensors]
        if len(sensor_ids) != len(set(sensor_ids)):
            raise ValueError("Sensor IDs must be unique")
        if self.observation_mode == "sensors" and not self.sensors:
            raise ValueError("Sensor observation mode requires at least one declared sensor")
        if self.observation_mode == "simulator_state" and self.sensors:
            raise ValueError("Simulator-state observation mode must not declare sensors")
        if len(set(self.required_features)) != len(self.required_features):
            raise ValueError("Required agent features must be unique")
        if self.required_features and (self.protocol_version not in {"1.1", "1.2"} or self.observation_mode != "simulator_state"):
            raise ValueError("Structured scene features require SDK 1.1 or 1.2 simulator_state observations")
        if self.navigation_mode == "agent_planner" and self.protocol_version != "1.2":
            raise ValueError("Agent-owned navigation requires SDK protocol 1.2")
        return self


class AgentReset(ContractModel):
    protocol_version: AgentProtocolVersion = AGENT_PROTOCOL_VERSION
    seed: Seed
    map_name: NonEmptyString
    ego_actor_id: NonEmptyString
    fixed_delta_seconds: Annotated[float, Field(gt=0, le=0.1, strict=True)]
    destination: Transform | None = None
    route: list[Transform] = Field(default_factory=list, max_length=4096)
    driving_task: DrivingTask | None = None

    @model_validator(mode="after")
    def validate_driving_task(self) -> Self:
        if self.driving_task is not None:
            if self.protocol_version != "1.2":
                raise ValueError("Driving tasks require SDK protocol 1.2")
            if self.driving_task.route:
                raise ValueError("Reset driving task descriptor must omit its route; use reset.route")
            if self.driving_task.route_owner == "agent" and self.route:
                raise ValueError("Agent-owned navigation must not receive a framework route")
            if self.driving_task.route_owner == "framework" and len(self.route) < 2:
                at_destination = len(self.route) == 1 and math.dist(
                    (self.route[0].position.x, self.route[0].position.y, self.route[0].position.z),
                    (self.driving_task.destination.position.x, self.driving_task.destination.position.y,
                     self.driving_task.destination.position.z),
                ) <= 1e-6
                if not at_destination:
                    raise ValueError("Framework-owned navigation requires a resolved route or its single destination point")
            if self.driving_task.route_owner == "framework":
                for first, second in zip(self.route, self.route[1:]):
                    if first.position == second.position:
                        raise ValueError("Resolved route must not contain adjacent duplicate positions")
                end = self.route[-1].position
                destination = self.driving_task.destination.position
                if math.dist((end.x, end.y, end.z), (destination.x, destination.y, destination.z)) > 1e-6:
                    raise ValueError("Resolved route must end at the driving task destination")
            if self.destination is None or math.dist(
                (self.destination.position.x, self.destination.position.y, self.destination.position.z),
                (self.driving_task.destination.position.x, self.driving_task.destination.position.y,
                 self.driving_task.destination.position.z),
            ) > 1e-6:
                raise ValueError("Reset destination must match the driving task destination")
        return self


class AgentSceneObject(ContractModel):
    """Privileged object state; transform is the measured world box center."""

    actor_id: NonEmptyString
    kind: Literal["vehicle", "pedestrian", "static", "emergency", "traffic_light", "stop_sign"]
    transform: Transform
    extent: BoundingBoxExtent
    velocity_mps: Vector3 | None = None
    traffic_light_state: Literal["red", "yellow", "green", "off", "unknown"] | None = None
    is_static: StrictBool | None = None


class AgentRoadRaster(ContractModel):
    """Ego-aligned semantic map, not camera perception.

    Classes: 0 background, 1 driving road, 2 sidewalk, 3 solid lane line,
    4 broken lane line. Rows increase ego-right, columns increase ego-forward.
    Payload is either flat row-major classes or lossless base64 uint8 bytes;
    origin identifies the ego reference point.
    """

    width: Annotated[int, Field(gt=0, le=512, strict=True)]
    height: Annotated[int, Field(gt=0, le=512, strict=True)]
    pixels_per_meter: PositiveFloat
    classes: list[Annotated[int, Field(ge=0, le=4, strict=True)]] | None = Field(default=None, max_length=262144)
    data_base64: StrictStr | None = Field(default=None, max_length=349528)
    axes: Literal["rows_right_columns_forward"] = "rows_right_columns_forward"
    ego_row: Annotated[float, Field(ge=0, strict=True)]
    ego_column: Annotated[float, Field(ge=0, strict=True)]

    @model_validator(mode="after")
    def validate_raster(self) -> Self:
        if (self.classes is None) == (self.data_base64 is None):
            raise ValueError("Road raster requires exactly one classes or data_base64 payload")
        if self.data_base64 is not None:
            try:
                values = base64.b64decode(self.data_base64, validate=True)
            except (ValueError, binascii.Error) as error:
                raise ValueError("Road raster data must be valid base64") from error
            if values and max(values) > 4:
                raise ValueError("Road raster classes must lie between 0 and 4")
        else:
            values = self.classes
        if len(values) != self.width * self.height:
            raise ValueError("Road raster class count must match width times height")
        if self.ego_row >= self.height or self.ego_column >= self.width:
            raise ValueError("Road raster ego origin must lie within the image")
        return self


class AgentSceneContext(ContractModel):
    """Versioned optional privileged scene context for SDK 1.1 agents."""

    schema_version: Literal["1.0"] = "1.0"
    source: Literal["carla_ground_truth", "synthetic"]
    route: list[Transform] | None = Field(default=None, max_length=256)
    objects: list[AgentSceneObject] | None = Field(default=None, max_length=4096)
    speed_limit_mps: PositiveFloat | None = None
    road_raster: AgentRoadRaster | None = None

    @model_validator(mode="after")
    def validate_objects(self) -> Self:
        ids = [item.actor_id for item in self.objects or []]
        if len(ids) != len(set(ids)):
            raise ValueError("Scene object identities must be unique")
        return self


class AgentObservation(ContractModel):
    protocol_version: AgentProtocolVersion = AGENT_PROTOCOL_VERSION
    observation_mode: Literal["simulator_state"] = "simulator_state"
    frame: NonNegativeInt
    time_s: NonNegativeFloat
    dt_s: PositiveFloat
    ego: ActorState
    # None means unavailable; [] means observed with no other actors.
    actors: list[ActorState] | None = None
    target_speed_mps: NonNegativeFloat | None = None
    route_heading_deg: Annotated[float, Field(strict=True)] | None = None
    traffic_light_red: StrictBool | None = None
    scene: AgentSceneContext | None = None
    driving_task: DrivingTask | None = None

    @model_validator(mode="after")
    def validate_actor_ids(self) -> Self:
        ids = [self.ego.actor_id, *(actor.actor_id for actor in self.actors or [])]
        if len(ids) != len(set(ids)):
            raise ValueError("Observation actor IDs must be unique, including the ego")
        if self.scene is not None and self.protocol_version not in {"1.1", "1.2"}:
            raise ValueError("Structured scene context requires SDK protocol 1.1 or 1.2")
        if self.protocol_version in {"1.0", "1.1"} and self.target_speed_mps is None:
            raise ValueError("SDK 1.0 and 1.1 observations require a numeric target_speed_mps")
        if self.driving_task is not None:
            if self.protocol_version != "1.2":
                raise ValueError("Driving tasks require SDK protocol 1.2")
            if self.driving_task.route:
                raise ValueError("Observation driving task descriptor must omit its route")
            if self.driving_task.route_owner == "agent":
                if self.route_heading_deg is not None or (self.scene is not None and self.scene.route is not None):
                    raise ValueError("Agent-owned navigation must not receive a framework route or heading")
        return self


class AgentDiagnostics(ContractModel):
    assistance: list[NonEmptyString] = Field(default_factory=list)
    maneuver: NonEmptyString | None = None
    reason_codes: list[NonEmptyString] = Field(default_factory=list)
    hazard_actor_ids: list[NonEmptyString] = Field(default_factory=list)
    target_speed_mps: NonNegativeFloat | None = None
    planned_trajectory: list[Transform] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    _json_metadata = field_validator("metadata", mode="before")(_require_json_object)


class AgentStepResult(ContractModel):
    protocol_version: AgentProtocolVersion = AGENT_PROTOCOL_VERSION
    frame: NonNegativeInt
    control: ControlInput
    diagnostics: AgentDiagnostics | None = None


class AgentContracts(ContractModel):
    """Schema entry point; this grouping is not an agent message."""

    manifest: AgentManifest
    reset: AgentReset
    observation: AgentObservation
    diagnostics: AgentDiagnostics
    step_result: AgentStepResult
