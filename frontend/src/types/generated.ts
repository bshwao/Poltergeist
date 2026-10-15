/* This file is generated. Do not edit it directly.
 * Source: contracts/models.schema.json (exported from shared.models).
 * Regenerate with: npm run contracts:generate
 */

export type CreatedAt = string;
export type Id = string;
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "FailureKind".
 */
export type FailureKind = "collision" | "near_miss" | "hard_braking" | "traffic_violation";
export type Reproduced = boolean | null;
/**
 * @minItems 1
 */
export type Actors = [ActorSpec, ...ActorSpec[]];
export type Blueprint = string | null;
export type Id1 = string | null;
export type AnchorActorId = string;
export type X = number;
export type Y = number;
export type Z = number;
export type YawOffsetDeg = number;
export type PitchDeg = number;
export type RollDeg = number;
export type YawDeg = number;
export type SpawnPointIndex = number | null;
export type SpeedMps = number;
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ActorType".
 */
export type ActorType = "ego_vehicle" | "vehicle" | "lead_vehicle" | "crossing_vehicle" | "pedestrian";
export type Description = string;
export type DurationS = number;
export type Event = string | null;
export type Action = "set_speed";
export type ActorId = string;
export type SpeedMps1 = number;
export type TriggerTimeS = number;
export type Action1 = "brake";
export type ActorId1 = string;
export type DecelerationMps2 = number;
export type TriggerTimeS1 = number;
export type Events = (SpeedChangeEvent | BrakingEvent)[];
export type FixedDeltaSeconds = number;
export type Location = string | null;
export type Map = string;
export type Name = string;
export type Occlusion = boolean;
export type SchemaVersion = "1.0";
export type Seed = number;
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ScenarioTemplate".
 */
export type ScenarioTemplate = "intersection" | "pedestrian_crossing" | "occluded_pedestrian" | "sudden_braking";
export type Cloudiness = number;
export type FogDensity = number;
export type Precipitation = number;
export type SunAltitudeDeg = number;
export type Wetness = number;
export type Severity = number;
export type SimulationId = string;
export type Summary = string;
export type CollisionCount = number;
export type MaxDecelerationMps2 = number;
export type MinClearanceM = number | null;
export type MinTtcS = number | null;
export type Severity1 = number;
export type TrafficViolationCount = number;
export type Kind = "telemetry" | "recording" | "report";
export type MediaType = string;
export type Path = string;
export type Artifacts = ArtifactReference[];
export type MeasuredDurationS = number;
export type Agent = "baseline" | "custom";
export type CreatedAt1 = string;
export type Error = string | null;
export type FinishedAt = string | null;
export type Id2 = string;
export type Mode = "carla" | "test";
export type Progress = number;
export type StartedAt = string | null;
export type SimulationStatus = "queued" | "running" | "completed" | "failed" | "cancelled";
export type TelemetryFrames = number;
export type ActorId2 = string;
export type X1 = number;
export type Y1 = number;
export type Z1 = number;
export type Actors1 = ActorState[];
export type ImpulseNS = number;
export type OtherActorId = string;
export type Collisions = CollisionEvent[];
export type Brake = number;
export type HandBrake = boolean;
export type Reverse = boolean;
export type Steer = number;
export type Throttle = number;
export type Frame = number;
export type TimeS = number;
export type ActorId3 = string;
export type Details = string;
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "TrafficRule".
 */
export type TrafficRule = "red_light" | "stop_sign" | "speeding" | "wrong_way" | "sidewalk";
export type TrafficViolations = TrafficViolation[];
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "SimulationStatus".
 */
export type SimulationStatus1 = "queued" | "running" | "completed" | "failed" | "cancelled";

export interface PoltergeistContracts {
  failure: FailureReport;
  metrics: SafetyMetrics;
  result: SimulationResult;
  scenario: Scenario;
  simulation: Simulation;
  telemetry: TelemetryFrame;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "FailureReport".
 */
export interface FailureReport {
  created_at: CreatedAt;
  id: Id;
  kind: FailureKind;
  reproduced: Reproduced;
  scenario: Scenario;
  severity: Severity;
  simulation_id: SimulationId;
  summary: Summary;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "Scenario".
 */
export interface Scenario {
  actors: Actors;
  description: Description;
  duration_s: DurationS;
  event: Event;
  events: Events;
  fixed_delta_seconds: FixedDeltaSeconds;
  location: Location;
  map: Map;
  name: Name;
  occlusion: Occlusion;
  parameters: Parameters;
  schema_version: SchemaVersion;
  seed: Seed;
  template: ScenarioTemplate | null;
  weather: Weather;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ActorSpec".
 */
export interface ActorSpec {
  blueprint: Blueprint;
  id: Id1;
  relative_spawn: RelativeSpawn | null;
  spawn: Transform | null;
  spawn_point_index: SpawnPointIndex;
  speed_mps: SpeedMps;
  type: ActorType;
}
/**
 * Spawn in an anchor actor's local frame: x forward, y right, z up.
 *
 * The simulation adapter resolves this placement after spawning the anchor.
 * These coordinates do not imply the position lies on a drivable road.
 *
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "RelativeSpawn".
 */
export interface RelativeSpawn {
  anchor_actor_id: AnchorActorId;
  offset_m: Vector3;
  yaw_offset_deg: YawOffsetDeg;
}
/**
 * CARLA coordinates: x forward, y right, z up; units depend on the field.
 *
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "Vector3".
 */
export interface Vector3 {
  x: X;
  y: Y;
  z: Z;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "Transform".
 */
export interface Transform {
  position: Vector3;
  rotation: Rotation;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "Rotation".
 */
export interface Rotation {
  pitch_deg: PitchDeg;
  roll_deg: RollDeg;
  yaw_deg: YawDeg;
}
/**
 * Set an actor's target speed at simulation time trigger_time_s.
 *
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "SpeedChangeEvent".
 */
export interface SpeedChangeEvent {
  action: Action;
  actor_id: ActorId;
  speed_mps: SpeedMps1;
  trigger_time_s: TriggerTimeS;
}
/**
 * Begin braking at a positive deceleration magnitude until stopped.
 *
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "BrakingEvent".
 */
export interface BrakingEvent {
  action: Action1;
  actor_id: ActorId1;
  deceleration_mps2: DecelerationMps2;
  trigger_time_s: TriggerTimeS1;
}
export interface Parameters {
  [k: string]: boolean | number | string;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "Weather".
 */
export interface Weather {
  cloudiness: Cloudiness;
  fog_density: FogDensity;
  precipitation: Precipitation;
  sun_altitude_deg: SunAltitudeDeg;
  wetness: Wetness;
}
/**
 * Null minima mean unavailable/no relevant interaction; never encode infinity.
 *
 * Severity ranges from 0 (least severe) to 100 (most severe). The scoring
 * algorithm and detection thresholds will be implemented in section 3.
 *
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "SafetyMetrics".
 */
export interface SafetyMetrics {
  collision_count: CollisionCount;
  max_deceleration_mps2: MaxDecelerationMps2;
  min_clearance_m: MinClearanceM;
  min_ttc_s: MinTtcS;
  severity: Severity1;
  traffic_violation_count: TrafficViolationCount;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "SimulationResult".
 */
export interface SimulationResult {
  artifacts: Artifacts;
  measured_duration_s: MeasuredDurationS;
  metrics: SafetyMetrics;
  simulation: Simulation;
  telemetry_frames: TelemetryFrames;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ArtifactReference".
 */
export interface ArtifactReference {
  kind: Kind;
  media_type: MediaType;
  path: Path;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "Simulation".
 */
export interface Simulation {
  agent: Agent;
  created_at: CreatedAt1;
  error: Error;
  finished_at: FinishedAt;
  id: Id2;
  mode: Mode;
  progress: Progress;
  scenario: Scenario;
  started_at: StartedAt;
  status: SimulationStatus;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "TelemetryFrame".
 */
export interface TelemetryFrame {
  actors: Actors1;
  collisions: Collisions;
  control: ControlInput;
  ego: ActorState;
  frame: Frame;
  time_s: TimeS;
  traffic_violations: TrafficViolations;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ActorState".
 */
export interface ActorState {
  acceleration_mps2: Vector3;
  actor_id: ActorId2;
  bounding_box_extent: BoundingBoxExtent | null;
  transform: Transform;
  velocity_mps: Vector3;
}
/**
 * Positive half-lengths of an actor's bounding box, in metres.
 *
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "BoundingBoxExtent".
 */
export interface BoundingBoxExtent {
  x: X1;
  y: Y1;
  z: Z1;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "CollisionEvent".
 */
export interface CollisionEvent {
  impulse_n_s: ImpulseNS;
  other_actor_id: OtherActorId;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ControlInput".
 */
export interface ControlInput {
  brake: Brake;
  hand_brake: HandBrake;
  reverse: Reverse;
  steer: Steer;
  throttle: Throttle;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "TrafficViolation".
 */
export interface TrafficViolation {
  actor_id: ActorId3;
  details: Details;
  rule: TrafficRule;
}
