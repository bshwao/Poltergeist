/* This file is generated. Do not edit it directly.
 * Source: contracts/models.schema.json (exported from shared.models).
 * Regenerate with: npm run contracts:generate
 */

export type CollisionObservedRuns = number;
export type CollisionRuns = number;
export type FailureReports = number;
export type MeanSeverity = number | null;
export type Mode = "carla" | "test";
export type ResultCount = number;
export type ScoredRuns = number;
export type TotalRuns = number;
export type TrafficObservedRuns = number;
export type TrafficViolationRuns = number;
export type UnscoredResults = number;
export type Groups = ModeAnalytics[];
export type Accepting = boolean;
export type ActiveId = string | null;
export type Error = string | null;
export type Pending = number;
export type Started = boolean;
export type WorkerAlive = boolean;
export type Reproduced = boolean | null;
export type DurationS = number | null;
export type Sample = boolean;
export type Seed = number;
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ScenarioTemplate".
 */
export type ScenarioTemplate = "intersection" | "pedestrian_crossing" | "occluded_pedestrian" | "sudden_braking";
export type CreatedAt = string;
export type Id = string;
export type Revision = number;
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
export type DurationS1 = number;
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
export type Seed1 = number;
export type Cloudiness = number;
export type FogDensity = number;
export type Precipitation = number;
export type SunAltitudeDeg = number;
export type Wetness = number;
export type UpdatedAt = string;
export type ExpectedRevision = number;
export type BrakingFullSeverityMps2 = number;
export type BrakingWeight = number;
export type ClearanceWarningM = number;
export type ClearanceWeight = number;
export type CollisionEpisodeGapS = number;
export type HardBrakingThresholdMps2 = number;
export type MaxSampleGapS = number;
export type MethodVersion = "1.0";
export type SpeedingToleranceMps = number;
export type StoppedSpeedMps = number;
export type TrafficRuleWeight = number;
export type TtcHorizonS = number;
export type TtcWarningS = number;
export type TtcWeight = number;
export type ViolationEpisodeGapS = number;
export type ViolationsForFullSeverity = number;
export type Mode1 = "carla" | "test";
export type AllowMapReload = boolean;
export type Camera = boolean;
export type CameraHeight = number;
export type CameraWidth = number;
export type Lidar = boolean;
export type MaxTicks = number;
export type SensorSettleS = number;
export type SensorTimeoutS = number;
export type ScenarioId = string | null;
export type CancelRequested = boolean;
export type Error1 = string | null;
export type FinishedAt = string | null;
export type Progress = number;
export type QueuePosition = number | null;
export type Revision1 = number;
export type SimulationId = string;
export type StartedAt = string | null;
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "SimulationStatus".
 */
export type SimulationStatus = "queued" | "running" | "completed" | "failed" | "cancelled";
export type CreatedAt1 = string;
export type CancelRequested1 = boolean;
export type QueuePosition1 = number | null;
export type Kind = "telemetry" | "recording" | "report";
export type MediaType = string;
export type Path = string;
export type Artifacts = ArtifactReference[];
export type MeasuredDurationS = number;
export type CollisionCount = number | null;
export type ActorPairs = number;
export type BrakingFrames = number;
export type ClearanceFrames = number;
export type ClearancePairs = number;
export type CollisionFrames = number;
export type HasGaps = boolean;
export type TotalFrames = number;
export type TrafficRuleFrames = number;
export type TtcFrames = number;
export type TtcPairs = number;
export type HardBrakingCount = number | null;
export type MaxDecelerationMps2 = number | null;
export type MaxStoppingDistanceM = number | null;
export type MaxStoppingTimeS = number | null;
export type MinClearanceM = number | null;
export type MinTtcS = number | null;
export type Severity = number | null;
export type TrafficViolationCount = number | null;
export type Adapter = "carla" | "test";
export type ClientVersion = string | null;
export type FixedDeltaSeconds1 = number;
export type MapName = string;
export type Seed2 = number;
export type SensorSamplesCount = number;
export type ServerVersion = string | null;
export type Agent = "baseline" | "custom";
export type CreatedAt2 = string;
export type Error2 = string | null;
export type FinishedAt1 = string | null;
export type Id2 = string;
export type Mode2 = "carla" | "test";
export type Progress1 = number;
export type StartedAt1 = string | null;
export type SimulationStatus1 = "queued" | "running" | "completed" | "failed" | "cancelled";
export type TelemetryFrames = number;
export type Revision2 = number;
export type ScenarioId1 = string | null;
export type UpdatedAt1 = string;
export type Actors1 = ActorState[] | null;
export type ActorId2 = string;
export type X1 = number;
export type Y1 = number;
export type Z1 = number;
export type SpeedLimitMps = number | null;
export type Collisions = CollisionEvent[] | null;
export type EventId = string | null;
export type ImpulseNS = number;
export type OtherActorId = string;
export type Brake = number;
export type HandBrake = boolean;
export type Reverse = boolean;
export type Steer = number;
export type Throttle = number;
export type Frame = number;
export type RuleChecks = TrafficRule[] | null;
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "TrafficRule".
 */
export type TrafficRule = "red_light" | "stop_sign" | "speeding" | "wrong_way" | "sidewalk";
export type TimeS = number;
export type TrafficViolations = TrafficViolation[] | null;
export type ActorId3 = string;
export type Details = string;
export type EventId1 = string | null;
export type SourceId = string | null;
export type Frames = TelemetryFrame[];
export type Limit = number;
export type Offset = number;
export type Total = number;
export type Default = number;
export type Description1 = string;
export type Integer = boolean;
export type Maximum = number;
export type Minimum = number;
export type Name1 = string;
export type Unit = string;
export type Parameters2 = ParameterInfo[];
export type CreatedAt3 = string;
export type Id3 = string;
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "FailureKind".
 */
export type FailureKind = "collision" | "near_miss" | "hard_braking" | "traffic_violation";
export type Reproduced1 = boolean | null;
export type Severity1 = number;
export type SimulationId1 = string;
export type Summary = string;
export type CancelRequested2 = boolean;
export type CreatedAt4 = string;
export type EngineVersion = string | null;
export type Error3 = string | null;
export type Mode3 = "carla" | "test";
export type FinishedAt2 = string | null;
export type Id4 = string;
export type BestFailureId = string | null;
export type BestSimulationId = string | null;
export type Reproduced2 = boolean | null;
export type Successes = number;
export type Summary1 = string;
export type TrialsUsed = number;
export type Unknowns = number;
export type Request = SearchRequest | ReproductionRequest | MinimizeRequest;
export type Kind1 = "search";
export type MinimumSeverity = number;
export type Mode4 = "carla" | "test";
export type Seed3 = number;
export type Strategy = "random" | "optuna";
export type Trials = number;
export type FailureId = string;
export type Kind2 = "reproduce";
export type MinimumSeverity1 = number;
export type Repeats = number;
export type RequiredSuccesses = number | null;
export type FailureId1 = string;
export type Kind3 = "minimize";
export type MaxTrials = number;
export type MinimumSeverity2 = number;
export type Repeats1 = number;
export type Revision3 = number;
export type StartedAt2 = string | null;
export type OptimizationStatus = "queued" | "running" | "completed" | "failed" | "cancelled" | "interrupted";
export type Request1 = SearchRequest | ReproductionRequest | MinimizeRequest;
export type ExpectedRevision1 = number;
export type Assessed = boolean;
export type CandidateIndex = number;
export type FailureId2 = string | null;
export type Matches = boolean | null;
export type Number = number;
export type OperationId = string;
export type Reason = string | null;
export type RepeatIndex = number;
export type Role = "search" | "reproduce" | "minimize_baseline" | "minimize_candidate";
export type Score = number | null;
export type SimulationId2 = string;
export type Trials1 = OptimizationTrial[];
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "OptimizationStatus".
 */
export type OptimizationStatus1 = "queued" | "running" | "completed" | "failed" | "cancelled" | "interrupted";

export interface PoltergeistContracts {
  api: ApiContracts;
  failure: FailureReport;
  metrics: SafetyMetrics;
  optimization: OptimizationContracts;
  result: SimulationResult;
  scenario: Scenario;
  simulation: Simulation;
  telemetry: TelemetryFrame;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ApiContracts".
 */
export interface ApiContracts {
  analytics: AnalyticsSummary;
  queue_status: QueueStatus;
  reproduction_update: ReproductionUpdate;
  scenario_generate: ScenarioGenerate;
  scenario_record: ScenarioRecord;
  scenario_update: ScenarioUpdate;
  simulation_launch: SimulationLaunch;
  simulation_progress: SimulationProgress;
  simulation_record: SimulationRecord;
  telemetry_page: TelemetryPage;
  template: TemplateInfo;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "AnalyticsSummary".
 */
export interface AnalyticsSummary {
  groups: Groups;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ModeAnalytics".
 */
export interface ModeAnalytics {
  collision_observed_runs: CollisionObservedRuns;
  collision_runs: CollisionRuns;
  failure_kinds: FailureKinds;
  failure_reports: FailureReports;
  mean_severity: MeanSeverity;
  mode: Mode;
  result_count: ResultCount;
  scored_runs: ScoredRuns;
  status_counts: StatusCounts;
  total_runs: TotalRuns;
  traffic_observed_runs: TrafficObservedRuns;
  traffic_violation_runs: TrafficViolationRuns;
  unscored_results: UnscoredResults;
}
export interface FailureKinds {
  [k: string]: number;
}
export interface StatusCounts {
  [k: string]: number;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "QueueStatus".
 */
export interface QueueStatus {
  accepting: Accepting;
  active_id: ActiveId;
  error: Error;
  pending: Pending;
  started: Started;
  worker_alive: WorkerAlive;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ReproductionUpdate".
 */
export interface ReproductionUpdate {
  reproduced: Reproduced;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ScenarioGenerate".
 */
export interface ScenarioGenerate {
  duration_s: DurationS;
  parameters: Parameters;
  ranges: Ranges;
  sample: Sample;
  seed: Seed;
  template: ScenarioTemplate;
}
export interface Parameters {
  [k: string]: boolean | number | string;
}
export interface Ranges {
  /**
   * @minItems 2
   * @maxItems 2
   */
  [k: string]: [unknown, unknown];
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ScenarioRecord".
 */
export interface ScenarioRecord {
  created_at: CreatedAt;
  id: Id;
  revision: Revision;
  scenario: Scenario;
  updated_at: UpdatedAt;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "Scenario".
 */
export interface Scenario {
  actors: Actors;
  description: Description;
  duration_s: DurationS1;
  event: Event;
  events: Events;
  fixed_delta_seconds: FixedDeltaSeconds;
  location: Location;
  map: Map;
  name: Name;
  occlusion: Occlusion;
  parameters: Parameters1;
  schema_version: SchemaVersion;
  seed: Seed1;
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
export interface Parameters1 {
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
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ScenarioUpdate".
 */
export interface ScenarioUpdate {
  expected_revision: ExpectedRevision;
  scenario: Scenario;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "SimulationLaunch".
 */
export interface SimulationLaunch {
  evaluation_settings: EvaluationSettings;
  mode: Mode1;
  options: SimulationOptions;
  scenario: Scenario | null;
  scenario_id: ScenarioId;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "EvaluationSettings".
 */
export interface EvaluationSettings {
  braking_full_severity_mps2: BrakingFullSeverityMps2;
  braking_weight: BrakingWeight;
  clearance_warning_m: ClearanceWarningM;
  clearance_weight: ClearanceWeight;
  collision_episode_gap_s: CollisionEpisodeGapS;
  hard_braking_threshold_mps2: HardBrakingThresholdMps2;
  max_sample_gap_s: MaxSampleGapS;
  method_version: MethodVersion;
  speeding_tolerance_mps: SpeedingToleranceMps;
  stopped_speed_mps: StoppedSpeedMps;
  traffic_rule_weight: TrafficRuleWeight;
  ttc_horizon_s: TtcHorizonS;
  ttc_warning_s: TtcWarningS;
  ttc_weight: TtcWeight;
  violation_episode_gap_s: ViolationEpisodeGapS;
  violations_for_full_severity: ViolationsForFullSeverity;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "SimulationOptions".
 */
export interface SimulationOptions {
  allow_map_reload: AllowMapReload;
  camera: Camera;
  camera_height: CameraHeight;
  camera_width: CameraWidth;
  lidar: Lidar;
  max_ticks: MaxTicks;
  sensor_settle_s: SensorSettleS;
  sensor_timeout_s: SensorTimeoutS;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "SimulationProgress".
 */
export interface SimulationProgress {
  cancel_requested: CancelRequested;
  error: Error1;
  finished_at: FinishedAt;
  progress: Progress;
  queue_position: QueuePosition;
  revision: Revision1;
  simulation_id: SimulationId;
  started_at: StartedAt;
  status: SimulationStatus;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "SimulationRecord".
 */
export interface SimulationRecord {
  created_at: CreatedAt1;
  job: JobInfo | null;
  result: SimulationResult | null;
  revision: Revision2;
  scenario_id: ScenarioId1;
  simulation: Simulation;
  updated_at: UpdatedAt1;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "JobInfo".
 */
export interface JobInfo {
  cancel_requested: CancelRequested1;
  evaluation_settings: EvaluationSettings;
  options: SimulationOptions;
  queue_position: QueuePosition1;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "SimulationResult".
 */
export interface SimulationResult {
  artifacts: Artifacts;
  measured_duration_s: MeasuredDurationS;
  metrics: SafetyMetrics;
  runtime: SimulationRuntime | null;
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
 * Null minima mean unavailable/no relevant interaction; never encode infinity.
 *
 * Severity is an observed collision score (100), or a score from a completely
 * observed trace. Otherwise it is null. Settings preserve scoring provenance.
 *
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "SafetyMetrics".
 */
export interface SafetyMetrics {
  collision_count: CollisionCount;
  coverage: EvaluationCoverage;
  evaluation_settings: EvaluationSettings | null;
  hard_braking_count: HardBrakingCount;
  max_deceleration_mps2: MaxDecelerationMps2;
  max_stopping_distance_m: MaxStoppingDistanceM;
  max_stopping_time_s: MaxStoppingTimeS;
  min_clearance_m: MinClearanceM;
  min_ttc_s: MinTtcS;
  severity: Severity;
  traffic_violation_count: TrafficViolationCount;
}
/**
 * Counts of actual observations; empty actor lists are observed empty scenes.
 *
 * A pair's TTC is evaluated even when its known trajectory has no predicted
 * contact. Missing geometry/velocity is not an evaluated pair.
 *
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "EvaluationCoverage".
 */
export interface EvaluationCoverage {
  actor_pairs: ActorPairs;
  braking_frames: BrakingFrames;
  clearance_frames: ClearanceFrames;
  clearance_pairs: ClearancePairs;
  collision_frames: CollisionFrames;
  has_gaps: HasGaps;
  total_frames: TotalFrames;
  traffic_rule_frames: TrafficRuleFrames;
  ttc_frames: TtcFrames;
  ttc_pairs: TtcPairs;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "SimulationRuntime".
 */
export interface SimulationRuntime {
  adapter: Adapter;
  client_version: ClientVersion;
  fixed_delta_seconds: FixedDeltaSeconds1;
  map_name: MapName;
  resolved_spawns: ResolvedSpawns;
  seed: Seed2;
  sensor_samples_count: SensorSamplesCount;
  server_version: ServerVersion;
}
export interface ResolvedSpawns {
  [k: string]: Transform;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "Simulation".
 */
export interface Simulation {
  agent: Agent;
  created_at: CreatedAt2;
  error: Error2;
  finished_at: FinishedAt1;
  id: Id2;
  mode: Mode2;
  progress: Progress1;
  scenario: Scenario;
  started_at: StartedAt1;
  status: SimulationStatus1;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "TelemetryPage".
 */
export interface TelemetryPage {
  frames: Frames;
  limit: Limit;
  offset: Offset;
  total: Total;
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
  rule_checks: RuleChecks;
  time_s: TimeS;
  traffic_violations: TrafficViolations;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ActorState".
 */
export interface ActorState {
  acceleration_mps2: Vector3 | null;
  actor_id: ActorId2;
  bounding_box_extent: BoundingBoxExtent | null;
  bounding_box_transform: Transform | null;
  speed_limit_mps: SpeedLimitMps;
  transform: Transform | null;
  velocity_mps: Vector3 | null;
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
  event_id: EventId;
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
  event_id: EventId1;
  rule: TrafficRule;
  source_id: SourceId;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "TemplateInfo".
 */
export interface TemplateInfo {
  parameters: Parameters2;
  scenario: Scenario;
  template: ScenarioTemplate;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ParameterInfo".
 */
export interface ParameterInfo {
  default: Default;
  description: Description1;
  integer: Integer;
  maximum: Maximum;
  minimum: Minimum;
  name: Name1;
  unit: Unit;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "FailureReport".
 */
export interface FailureReport {
  created_at: CreatedAt3;
  id: Id3;
  kind: FailureKind;
  reproduced: Reproduced1;
  scenario: Scenario;
  severity: Severity1;
  simulation_id: SimulationId1;
  summary: Summary;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "OptimizationContracts".
 */
export interface OptimizationContracts {
  record: OptimizationRecord;
  request: Request1;
  resume: OptimizationResume;
  trial: OptimizationTrial;
  view: OptimizationView;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "OptimizationRecord".
 */
export interface OptimizationRecord {
  cancel_requested: CancelRequested2;
  created_at: CreatedAt4;
  engine_version: EngineVersion;
  error: Error3;
  execution: ExecutionSpec;
  finished_at: FinishedAt2;
  id: Id4;
  outcome: OptimizationOutcome | null;
  request: Request;
  revision: Revision3;
  source_failure: FailureReport | null;
  started_at: StartedAt2;
  status: OptimizationStatus;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ExecutionSpec".
 */
export interface ExecutionSpec {
  evaluation_settings: EvaluationSettings;
  mode: Mode3;
  options: SimulationOptions;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "OptimizationOutcome".
 */
export interface OptimizationOutcome {
  best_failure_id: BestFailureId;
  best_scenario: Scenario | null;
  best_simulation_id: BestSimulationId;
  reproduced: Reproduced2;
  successes: Successes;
  summary: Summary1;
  trials_used: TrialsUsed;
  unknowns: Unknowns;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "SearchRequest".
 */
export interface SearchRequest {
  evaluation_settings: EvaluationSettings;
  failure_kind: FailureKind | null;
  kind: Kind1;
  minimum_severity: MinimumSeverity;
  mode: Mode4;
  options: SimulationOptions;
  parameters: Parameters3;
  ranges: Ranges1;
  seed: Seed3;
  strategy: Strategy;
  template: ScenarioTemplate;
  trials: Trials;
}
export interface Parameters3 {
  [k: string]: boolean | number | string;
}
export interface Ranges1 {
  /**
   * @minItems 2
   * @maxItems 2
   */
  [k: string]: [unknown, unknown];
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ReproductionRequest".
 */
export interface ReproductionRequest {
  failure_id: FailureId;
  kind: Kind2;
  minimum_severity: MinimumSeverity1;
  repeats: Repeats;
  required_successes: RequiredSuccesses;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "MinimizeRequest".
 */
export interface MinimizeRequest {
  failure_id: FailureId1;
  kind: Kind3;
  max_trials: MaxTrials;
  minimum_severity: MinimumSeverity2;
  repeats: Repeats1;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "OptimizationResume".
 */
export interface OptimizationResume {
  expected_revision: ExpectedRevision1;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "OptimizationTrial".
 */
export interface OptimizationTrial {
  assessed: Assessed;
  candidate_index: CandidateIndex;
  failure_id: FailureId2;
  matches: Matches;
  number: Number;
  operation_id: OperationId;
  reason: Reason;
  repeat_index: RepeatIndex;
  role: Role;
  scenario: Scenario;
  score: Score;
  simulation_id: SimulationId2;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "OptimizationView".
 */
export interface OptimizationView {
  operation: OptimizationRecord;
  trials: Trials1;
}
