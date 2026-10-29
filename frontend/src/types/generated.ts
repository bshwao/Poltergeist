/* This file is generated. Do not edit it directly.
 * Source: contracts/models.schema.json (exported from shared.models).
 * Regenerate with: npm run contracts:generate
 */

export type Assistance = string[];
export type HazardActorIds = string[];
export type Maneuver = string | null;
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "JsonValue".
 */
export type JsonValue = unknown;
export type X = number;
export type Y = number;
export type Z = number;
export type PitchDeg = number;
export type RollDeg = number;
export type YawDeg = number;
export type PlannedTrajectory = Transform[];
export type ReasonCodes = string[];
export type TargetSpeedMps = number | null;
export type AgentId = string;
export type Assets = string[];
export type ControlMode = "vehicle_control";
export type EntryPoint = string;
export type Name = string;
export type NavigationMode = "provided_route" | "agent_planner";
export type ObservationMode = "simulator_state" | "sensors";
export type ProtocolVersion = "1.0" | "1.1" | "1.2" | "1.3";
export type RequiredFeatures = "structured_scene"[];
export type RequirementsFile = string | null;
export type Id = string;
export type Type = "sensor.camera.rgb" | "sensor.lidar.ray_cast" | "sensor.other.imu" | "sensor.other.gnss";
export type Sensors = AgentSensorSpec[];
export type Version = string;
export type Actors = ActorState[] | null;
export type ActorId = string;
export type X1 = number;
export type Y1 = number;
export type Z1 = number;
export type SpeedLimitMps = number | null;
export type CompletionHeightToleranceM = number;
export type CompletionHoldS = number;
export type CompletionRadiusM = number;
export type CompletionSpeedMps = number | null;
/**
 * @maxItems 4096
 */
export type Route = Transform[];
export type RouteOwner = "framework" | "agent";
export type StopOnCompletion = boolean;
export type TargetSpeedMps1 = number | null;
export type DtS = number;
export type EgoStateSource = "simulator_state" | "simulated_ego_kinematics";
export type Frame = number;
export type ObservationMode1 = "simulator_state" | "sensors";
export type ProtocolVersion1 = "1.0" | "1.1" | "1.2" | "1.3";
export type RouteHeadingDeg = number | null;
export type Objects = AgentSceneObject[] | null;
export type ActorId1 = string;
export type IsStatic = boolean | null;
export type Kind = "vehicle" | "pedestrian" | "static" | "emergency" | "traffic_light" | "stop_sign";
export type TrafficLightState = ("red" | "yellow" | "green" | "off" | "unknown") | null;
export type Axes = "rows_right_columns_forward";
export type Classes = number[] | null;
export type DataBase64 = string | null;
export type EgoColumn = number;
export type EgoRow = number;
export type Height = number;
export type PixelsPerMeter = number;
export type Width = number;
export type Route1 = Transform[] | null;
export type SchemaVersion = "1.0";
export type Source = "carla_ground_truth" | "synthetic";
export type SpeedLimitMps1 = number | null;
export type SensorSource = ("carla_sensors" | "deterministic_fixture") | null;
/**
 * @maxItems 4
 */
export type Sensors1 =
  | []
  | [AgentSensorFrame]
  | [AgentSensorFrame, AgentSensorFrame]
  | [AgentSensorFrame, AgentSensorFrame, AgentSensorFrame]
  | [AgentSensorFrame, AgentSensorFrame, AgentSensorFrame, AgentSensorFrame];
export type Channels = number | null;
export type DataBase641 = string;
export type Encoding = "bgra8" | "float32_xyzi";
export type FovDeg = number | null;
export type Frame1 = number;
export type Height1 = number | null;
export type SensorId = string;
export type Sha256 = string;
export type TimestampS = number;
export type Type1 = "sensor.camera.rgb" | "sensor.lidar.ray_cast";
export type Width1 = number | null;
export type TargetSpeedMps2 = number | null;
export type TimeS = number;
export type TrafficLightRed = boolean | null;
export type EgoActorId = string;
export type FixedDeltaSeconds = number;
export type MapName = string;
export type ProtocolVersion2 = "1.0" | "1.1" | "1.2" | "1.3";
/**
 * @maxItems 4096
 */
export type Route2 = Transform[];
export type Seed = number;
/**
 * @maxItems 4
 */
export type Sensors2 =
  | []
  | [AgentSensorSpec]
  | [AgentSensorSpec, AgentSensorSpec]
  | [AgentSensorSpec, AgentSensorSpec, AgentSensorSpec]
  | [AgentSensorSpec, AgentSensorSpec, AgentSensorSpec, AgentSensorSpec];
export type Brake = number;
export type HandBrake = boolean;
export type Reverse = boolean;
export type Steer = number;
export type Throttle = number;
export type Frame2 = number;
export type ProtocolVersion3 = "1.0" | "1.1" | "1.2" | "1.3";
export type AgentId1 = string;
export type CheckedAt = string;
export type InferenceChecked = false;
export type Inspection = "static_only";
export type Issues = string[];
export type Limitations = string[];
export type Ready = boolean;
export type AgentControlMode = "authoritative" | "scripted";
/**
 * @maxItems 256
 */
export type ModelFiles = string[];
export type PackagePath = string;
export type CloseTimeoutS = number;
export type LoadTimeoutS = number;
export type MaxMessageBytes = number;
export type ResetTimeoutS = number;
export type StepTimeoutS = number;
export type TerminateTimeoutS = number;
export type PythonExecutable = string | null;
export type TrustedLocalCode = true;
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
export type Agent = "baseline" | "custom";
/**
 * @maxItems 64
 */
export type Assistance1 = string[];
export type EgoStateSource1 = "simulator_state" | "simulated_ego_kinematics";
export type GroundTruthActorInputs = boolean;
export type ObservationMode2 = "simulator_state" | "sensors";
/**
 * @maxItems 4
 */
export type Sensors3 =
  | []
  | [AgentSensorSpec]
  | [AgentSensorSpec, AgentSensorSpec]
  | [AgentSensorSpec, AgentSensorSpec, AgentSensorSpec]
  | [AgentSensorSpec, AgentSensorSpec, AgentSensorSpec, AgentSensorSpec];
export type Source1 = "carla_ground_truth" | "synthetic_state" | "carla_sensors" | "deterministic_fixture";
export type AgentControlMode1 = "authoritative" | "scripted";
/**
 * @minItems 1
 * @maxItems 20000
 */
export type Files = [AgentFileFingerprint, ...AgentFileFingerprint[]];
export type Path = string;
export type Sha2561 = string;
export type SizeBytes = number;
export type Path1 = string;
export type Sha2562 = string;
export type SizeBytes1 = number;
/**
 * @maxItems 256
 */
export type ModelFiles1 = AgentExternalFileFingerprint[];
export type ObservationMode3 = "simulator_state" | "sensors";
export type PackagePath1 = string;
export type PackageSha256 = string;
export type PythonExecutable1 = string;
export type PythonSha256 = string;
export type PythonSizeBytes = number;
export type RegistrationId = string;
/**
 * @maxItems 20000
 */
export type RuntimeFiles = AgentExternalFileFingerprint[];
export type EpisodesTruncated = boolean;
export type Coverage = "complete" | "partial" | "unavailable";
export type EligibleSamples = number;
export type FirstFrame = number;
export type FirstTimeS = number;
export type LastFrame = number;
export type LastTimeS = number;
export type Source2 = "telemetry" | "decisions" | "driving_outcome" | "stored_safety_metrics";
export type Evidence = EvidenceSpan[];
export type ObservedSamples = number;
export type Reason = string | null;
export type Unit = "m" | "m/s²" | "episodes" | "fraction";
export type Value = number | null;
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
export type SettingsSource = "recorded_evaluation" | "legacy_defaults";
export type DistanceM = number;
export type DurationS = number;
export type InterventionFrames = number;
export type ScriptInfluenced = boolean | null;
export type StoppingEpisodes = StoppingEpisode[];
export type TotalStoppingEpisodes = number;
export type ControlMode1 = ("scripted" | "authoritative") | null;
export type AppliedDecisions = number;
export type ChangedControlFrames = number;
export type DecisionCoverage = "unavailable" | "empty" | "partial" | "complete";
export type HazardClearancePairs = number;
export type HazardTtcPairs = number;
export type MeasuredDurationS = number;
export type PoseFrames = number;
export type ResponseEventsTruncated = boolean;
export type ScriptInterventionFrames = number;
export type TelemetryFrames = number;
export type TelemetryGaps = number;
export type TotalResponseEvents = number;
export type UnresolvedInputs = number;
export type VelocityFrames = number;
export type ControlMode2 = "scripted" | "authoritative";
export type FinalDistanceM = number | null;
export type FinalHeightErrorM = number | null;
export type InitialSpeedMps = number | null;
export type Frame3 = number;
export type Kind1 = "scripted_ego_speed_target" | "scripted_ego_braking";
export type TargetSpeedMps3 = number | null;
export type TimeS1 = number;
/**
 * @maxItems 200000
 */
export type Interventions = ControlIntervention[];
export type ReachedAtS = number | null;
/**
 * @maxItems 4096
 */
export type ResolvedRoute = Transform[];
export type RouteLengthM = number | null;
export type RouteOwner1 = "framework" | "agent";
export type RouteProgressM = number | null;
export type Status = "reached" | "timed_out" | "cancelled" | "failed" | "unavailable";
export type TargetSpeedMps4 = number | null;
export type TerminationReason = "goal_reached" | "duration_elapsed" | "cancelled" | "execution_failed";
export type ActorId2 = string;
export type ClearanceM = number | null;
export type EgoEvent = boolean;
export type EventAction = ("set_speed" | "brake") | null;
export type EventIndex = number | null;
export type Frame4 = number | null;
export type Id1 = string;
export type Kind2 = "scenario_event" | "measured_hazard";
export type Onset = "scheduled_command" | "threshold_crossing" | "first_observed";
export type Reason1 = string | null;
export type ScheduledTimeS = number | null;
export type Source3 = "scenario_script" | "evaluator_ground_truth";
export type TimeS2 = number;
export type TtcS = number | null;
export type Attribution =
  "sdk_agent" | "baseline_controller" | "legacy_custom" | "script_influenced" | "unknown" | "measured_motion";
export type Channel =
  | "proposed_braking"
  | "applied_braking"
  | "proposed_steering_change"
  | "applied_steering_change"
  | "measured_deceleration"
  | "measured_heading_change";
export type Evidence1 = EvidenceSpan[];
export type LatencyS = number | null;
export type ObservedUntilS = number | null;
export type Reason2 = string | null;
export type ReferenceFrame = number | null;
export type ReferenceValue = number | null;
export type ResponseFrame = number | null;
export type ResponseTimeS = number | null;
export type Status1 = "observed" | "already_active" | "not_observed" | "unavailable";
export type Threshold = number;
export type Unit1 = "normalized_control" | "m/s²" | "degrees";
export type Value1 = number | null;
export type WindowComplete = boolean;
export type WindowEndS = number;
export type Responses = BehaviorResponse[];
export type Events = EventBehavior[];
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "SimulationStatus".
 */
export type SimulationStatus = "queued" | "running" | "completed" | "failed" | "cancelled";
export type InterpretationNotes = string[];
export type Mode1 = "carla" | "test";
export type ReferenceSource = "recorded_resolved_route" | null;
export type RouteOwner2 = ("framework" | "agent") | null;
export type SafetyEvidence = EvidenceSpan[];
export type SchemaVersion1 = "1.0";
export type BrakeThreshold = number;
export type DecelerationThresholdMps2 = number;
export type HazardClearanceM = number;
export type HazardTtcS = number;
export type HeadingChangeDeg = number;
export type MaxResponseEvents = number;
export type MaxSampleGapS1 = number;
export type MaxStoppingEpisodes = number;
export type MethodVersion1 = "1.0";
export type ResponseWindowS = number;
export type RouteHeightToleranceM = number;
export type SteeringChangeThreshold = number;
export type SimulationId = string;
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
export type TaskEvidence = EvidenceSpan[];
export type CancelRequested = boolean;
/**
 * @maxItems 8
 */
export type ComparisonAgents =
  | []
  | [ComparisonAgent]
  | [ComparisonAgent, ComparisonAgent]
  | [ComparisonAgent, ComparisonAgent, ComparisonAgent]
  | [ComparisonAgent, ComparisonAgent, ComparisonAgent, ComparisonAgent]
  | [ComparisonAgent, ComparisonAgent, ComparisonAgent, ComparisonAgent, ComparisonAgent]
  | [ComparisonAgent, ComparisonAgent, ComparisonAgent, ComparisonAgent, ComparisonAgent, ComparisonAgent]
  | [
      ComparisonAgent,
      ComparisonAgent,
      ComparisonAgent,
      ComparisonAgent,
      ComparisonAgent,
      ComparisonAgent,
      ComparisonAgent
    ]
  | [
      ComparisonAgent,
      ComparisonAgent,
      ComparisonAgent,
      ComparisonAgent,
      ComparisonAgent,
      ComparisonAgent,
      ComparisonAgent,
      ComparisonAgent
    ];
export type AgentId2 = string | null;
export type CreatedAt = string;
export type EngineVersion = string | null;
export type Error = string | null;
export type Mode2 = "carla" | "test";
export type AllowMapReload = boolean;
export type Camera = boolean;
export type CameraHeight = number;
export type CameraWidth = number;
export type CarlaQuality = ("Low" | "Epic") | null;
export type Lidar = boolean;
export type MaxTicks = number;
export type NoRenderingMode = boolean | null;
export type SensorSettleS = number;
export type SensorTimeoutS = number;
export type FinishedAt = string | null;
export type Id2 = string;
export type BestFailureId = string | null;
/**
 * @minItems 1
 */
export type Actors1 = [ActorSpec, ...ActorSpec[]];
export type Blueprint = string | null;
export type Id3 = string | null;
export type AnchorActorId = string;
export type YawOffsetDeg = number;
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
export type ActorId3 = string;
export type SpeedMps1 = number;
export type TriggerTimeS = number;
export type Action1 = "brake";
export type ActorId4 = string;
export type DecelerationMps2 = number;
export type TriggerTimeS1 = number;
export type Events1 = (SpeedChangeEvent | BrakingEvent)[];
export type FixedDeltaSeconds1 = number;
export type Location = string | null;
export type Map = string;
export type Name1 = string;
export type Occlusion = boolean;
export type SchemaVersion2 = "1.0";
export type Seed1 = number;
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
export type BestSimulationId = string | null;
export type Reproduced = boolean | null;
export type Successes = number;
export type Summary = string;
export type TrialsUsed = number;
export type Unknowns = number;
export type Request = SearchRequest | ComparisonRequest | ReproductionRequest | MinimizeRequest;
export type AgentId3 = string | null;
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "FailureKind".
 */
export type FailureKind = "collision" | "near_miss" | "hard_braking" | "traffic_violation";
export type Kind3 = "search";
export type MinimumSeverity = number;
export type Mode3 = "carla" | "test";
export type Seed2 = number;
export type Strategy = "random" | "optuna";
export type Trials = number;
/**
 * @minItems 1
 * @maxItems 8
 */
export type Agents =
  | [string | null]
  | [string | null, string | null]
  | [string | null, string | null, string | null]
  | [string | null, string | null, string | null, string | null]
  | [string | null, string | null, string | null, string | null, string | null]
  | [string | null, string | null, string | null, string | null, string | null, string | null]
  | [string | null, string | null, string | null, string | null, string | null, string | null, string | null]
  | [
      string | null,
      string | null,
      string | null,
      string | null,
      string | null,
      string | null,
      string | null,
      string | null
    ];
export type Kind4 = "compare";
export type MinimumSeverity1 = number;
export type Mode4 = "carla" | "test";
export type Repeats = number;
/**
 * @minItems 1
 * @maxItems 20
 */
export type Seeds =
  | [number]
  | [number, number]
  | [number, number, number]
  | [number, number, number, number]
  | [number, number, number, number, number]
  | [number, number, number, number, number, number]
  | [number, number, number, number, number, number, number]
  | [number, number, number, number, number, number, number, number]
  | [number, number, number, number, number, number, number, number, number]
  | [number, number, number, number, number, number, number, number, number, number]
  | [number, number, number, number, number, number, number, number, number, number, number]
  | [number, number, number, number, number, number, number, number, number, number, number, number]
  | [number, number, number, number, number, number, number, number, number, number, number, number, number]
  | [number, number, number, number, number, number, number, number, number, number, number, number, number, number]
  | [
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number
    ]
  | [
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number
    ]
  | [
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number
    ]
  | [
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number
    ]
  | [
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number
    ]
  | [
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number,
      number
    ];
export type FailureId = string;
export type Kind5 = "reproduce";
export type MinimumSeverity2 = number;
export type Repeats1 = number;
export type RequiredSuccesses = number | null;
export type FailureId1 = string;
export type Kind6 = "minimize";
export type MaxTrials = number;
export type MinimumSeverity3 = number;
export type Repeats2 = number;
export type Revision = number;
export type CreatedAt1 = string;
export type Id4 = string;
export type Reproduced1 = boolean | null;
export type Severity1 = number;
export type SimulationId1 = string;
export type Summary1 = string;
export type StartedAt = string | null;
export type OptimizationStatus = "queued" | "running" | "completed" | "failed" | "cancelled" | "interrupted";
export type AgentIndex = number;
export type Assessed = boolean;
export type CandidateIndex = number;
export type FailureId2 = string | null;
export type Matches = boolean | null;
export type Number = number;
export type OperationId = string;
export type Reason3 = string | null;
export type RepeatIndex = number;
export type Role = "search" | "compare" | "reproduce" | "minimize_baseline" | "minimize_candidate";
export type Score = number | null;
export type SimulationId2 = string;
export type Trials1 = OptimizationTrial[];
export type AgentIndex1 = number;
export type AssessedRuns = number;
export type CompletedRuns = number;
export type MatchingRuns = number;
export type EligibleResults = number;
export type Maximum = number | null;
export type Mean = number | null;
export type Minimum = number | null;
export type ObservedResults = number;
export type SampleStddev = number | null;
export type UnavailableResults = number;
export type Unit2 = string;
export type Name2 = string;
export type NegativeRuns = number;
export type PartialCoverageResults = number;
export type PlannedRuns = number;
export type RegistrationId1 = string | null;
export type SimulationIds = string[];
export type UnassessedRuns = number;
export type UnknownRuns = number;
export type Version1 = string | null;
export type Groups1 = ComparisonGroup[];
export type Notes = string[];
export type ComparedAgentIndex = number;
export type ComparedSimulationId = string | null;
export type Reason4 = string;
export type ReferenceSimulationId = string | null;
export type RepeatIndex1 = number;
export type Seed3 = number;
export type SeverityDifference = number | null;
/**
 * @maxItems 200
 */
export type Pairs = ComparisonPair[];
export type ReferenceAgentIndex = 0;
export type PairedComparisons = PairedComparison[];
export type SchemaVersion3 = "poltergeist-comparison-v1";
export type Applied = number;
export type Available = boolean;
export type Coverage1 = "unavailable" | "empty" | "partial" | "complete";
export type Limit = number;
export type Offset = number;
export type RecordingArtifactIndex = number | null;
export type ControlMode3 = "scripted" | "authoritative";
export type Frame5 = number;
export type FullStepWallTimeS = number | null;
export type Interventions1 = ("scripted_ego_speed_target" | "scripted_ego_braking")[];
export type ResultingFrame = number | null;
export type RoundTripWallTimeS = number | null;
/**
 * @maxItems 4
 */
export type Sensors4 =
  | []
  | [DecisionSensor]
  | [DecisionSensor, DecisionSensor]
  | [DecisionSensor, DecisionSensor, DecisionSensor]
  | [DecisionSensor, DecisionSensor, DecisionSensor, DecisionSensor];
export type ByteLength = number;
export type SampleIndex = number;
export type Status2 = "applied" | "unresolved";
export type TimeS3 = number;
export type WorkerStepWallTimeS = number | null;
export type Records = DecisionRecord[];
export type SimulationId3 = string;
export type Actors2 = ActorState[] | null;
export type Collisions = CollisionEvent[] | null;
export type EventId = string | null;
export type ImpulseNS = number;
export type OtherActorId = string;
export type Frame6 = number;
export type RuleChecks = TrafficRule[] | null;
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "TrafficRule".
 */
export type TrafficRule = "red_light" | "stop_sign" | "speeding" | "wrong_way" | "sidewalk";
export type TimeS4 = number;
export type TrafficViolations = TrafficViolation[] | null;
export type ActorId5 = string;
export type Details = string;
export type EventId1 = string | null;
export type SourceId = string | null;
export type Telemetry = TelemetryFrame[];
export type Total = number;
export type Transitions = number;
export type Accepting = boolean;
export type ActiveId = string | null;
export type Error1 = string | null;
export type Pending = number;
export type Started = boolean;
export type WorkerAlive = boolean;
export type Active = boolean;
export type CreatedAt2 = string;
export type Id5 = string;
export type Reproduced2 = boolean | null;
export type DurationS2 = number | null;
export type Map1 = string | null;
export type Sample = boolean;
export type Seed4 = number;
export type CreatedAt3 = string;
export type Id6 = string;
export type Revision1 = number;
export type UpdatedAt = string;
export type ExpectedRevision = number;
export type AgentId4 = string | null;
export type Mode5 = "carla" | "test";
export type ScenarioId = string | null;
export type CancelRequested1 = boolean;
export type Error2 = string | null;
export type FinishedAt1 = string | null;
export type Progress = number;
export type QueuePosition = number | null;
export type Revision2 = number;
export type SimulationId4 = string;
export type StartedAt1 = string | null;
export type CreatedAt4 = string;
export type CancelRequested2 = boolean;
export type QueuePosition1 = number | null;
export type Kind7 = "telemetry" | "recording" | "report" | "decisions";
export type MediaType = string;
export type Path2 = string;
export type Artifacts = ArtifactReference[];
export type Controller = "baseline" | "legacy_custom" | "sdk_agent";
/**
 * @maxItems 200000
 */
export type Interventions2 = ControlIntervention[];
export type Mode6 = "scripted" | "authoritative";
export type Navigation = "legacy_assisted" | "framework_route" | "agent_planner";
export type MeasuredDurationS1 = number;
export type Adapter = "carla" | "test";
export type ClientVersion = string | null;
export type FixedDeltaSeconds2 = number;
export type MapName1 = string;
export type Seed5 = number;
export type SensorSamplesCount = number;
export type ServerVersion = string | null;
export type Agent1 = "baseline" | "custom";
export type CreatedAt5 = string;
export type Error3 = string | null;
export type FinishedAt2 = string | null;
export type Id7 = string;
export type Mode7 = "carla" | "test";
export type Progress1 = number;
export type StartedAt2 = string | null;
export type SimulationStatus1 = "queued" | "running" | "completed" | "failed" | "cancelled";
export type TelemetryFrames1 = number;
export type Revision3 = number;
export type ScenarioId1 = string | null;
export type UpdatedAt1 = string;
export type Frames = TelemetryFrame[];
export type Limit1 = number;
export type Offset1 = number;
export type Total1 = number;
export type Default = number;
export type Description1 = string;
export type Integer = boolean;
export type Maximum1 = number;
export type Minimum1 = number;
export type Name3 = string;
export type Unit3 = string;
export type Parameters3 = ParameterInfo[];
export type CreatedAt6 = string;
export type Operation = "execute" | "cancel" | "status" | "result";
export type Reply =
  (ExecuteAccepted | CancelAccepted | DeviceStatusReply | RunResultReply | CommandRejected | EvidenceReply) | null;
export type AcceptedAt = string;
export type Kind8 = "accepted";
export type RequestId = string;
export type SimulationId5 = string;
export type AcceptedAt1 = string;
export type Kind9 = "cancel_requested";
export type RequestId1 = string;
export type SimulationId6 = string;
export type Terminal = boolean;
export type Accepting1 = boolean;
export type Active1 = boolean;
export type Kind10 = "status";
export type RequestId2 = string;
export type WaitingRemoteRuns = number;
export type Kind11 = "result";
export type RequestId3 = string;
export type Agent2 = "baseline";
export type CancelRequested3 = boolean | null;
export type CreatedAt7 = string;
export type DeviceId = string;
export type Code = "execution_failed" | "result_unavailable";
export type CorrelationId = string;
export type Message =
  | "The experiment failed on this computer. Review its local diagnostics."
  | "The finalized result is unavailable on this computer.";
export type ArtifactReferenceCount = number | null;
export type State = "local_only" | "missing";
export type TelemetryFrames2 = number | null;
export type ExecuteRequestId = string;
export type FinishedAt3 = string | null;
export type MeasuredDurationS2 = number | null;
export type CollisionCount1 = number | null;
export type ActorPairs1 = number;
export type BrakingFrames1 = number;
export type ClearanceFrames1 = number;
export type ClearancePairs1 = number;
export type CollisionFrames1 = number;
export type Complete = boolean;
export type HasGaps1 = boolean;
export type TotalFrames1 = number;
export type TrafficRuleFrames1 = number;
export type TtcFrames1 = number;
export type TtcPairs1 = number;
export type BrakingFullSeverityMps21 = number;
export type BrakingWeight1 = number;
export type ClearanceWarningM1 = number;
export type ClearanceWeight1 = number;
export type CollisionEpisodeGapS1 = number;
export type HardBrakingThresholdMps21 = number;
export type MaxSampleGapS2 = number;
export type MethodVersion2 = "1.0";
export type SpeedingToleranceMps1 = number;
export type StoppedSpeedMps1 = number;
export type TrafficRuleWeight1 = number;
export type TtcHorizonS1 = number;
export type TtcWarningS1 = number;
export type TtcWeight1 = number;
export type ViolationEpisodeGapS1 = number;
export type ViolationsForFullSeverity1 = number;
export type HardBrakingCount1 = number | null;
export type MaxDecelerationMps21 = number | null;
export type MaxStoppingDistanceM1 = number | null;
export type MaxStoppingTimeS1 = number | null;
export type MinClearanceM1 = number | null;
export type MinTtcS1 = number | null;
export type Severity2 = number | null;
export type TrafficViolationCount1 = number | null;
export type Mode8 = "test" | "carla";
export type OwnershipGeneration = number;
export type Progress2 = number;
export type ProtocolVersion4 = "poltergeist-hosted-run-v1";
export type ControlMode4 = "scripted" | null;
export type Controller1 = "baseline" | null;
export type EgoStateSource2 = "simulator_state" | null;
export type GroundTruthActorInputs1 = boolean | null;
export type Navigation1 = ("legacy_assisted" | "framework_route") | null;
export type ObservationMode4 = "simulator_state" | null;
export type Source4 = ("synthetic_state" | "carla_ground_truth") | null;
export type QueuePosition2 = number | null;
export type ReceiptRevision = number;
export type ResultAvailable = boolean;
export type Revision4 = number;
export type Adapter1 = "test" | "carla";
export type ClientVersion1 = string | null;
export type FixedDeltaSeconds3 = number;
export type MapName2 = string | null;
export type Seed6 = number;
export type ServerVersion1 = string | null;
export type Name4 = string;
export type Sha2563 = string;
export type SimulationId7 = string;
export type StartedAt3 = string | null;
export type Status3 = "queued" | "running" | "completed" | "failed" | "cancelled";
export type UpdatedAt2 = string;
export type Code1 =
  | "permission_denied"
  | "invalid_request"
  | "request_conflict"
  | "queue_full"
  | "device_unavailable"
  | "run_not_found"
  | "policy_denied"
  | "result_unavailable";
export type Kind12 = "rejected";
export type RequestId4 = string;
export type ArtifactIndex = number;
export type DataBase642 = string | null;
export type Frame7 = number | null;
export type Collisions1 = number | null;
export type Frame8 = number;
export type SpeedMps2 = number | null;
export type TimeS5 = number;
export type TrafficViolations1 = number | null;
/**
 * @maxItems 100
 */
export type Frames1 = EvidenceFrame[];
export type Kind13 = "evidence";
export type NextOffset = number | null;
export type Offset2 = number;
export type ArtifactIndex1 = number;
export type SampleCount = number | null;
export type State1 = "available" | "missing" | "deleted" | "limit_exceeded";
export type TotalBytes = number | null;
/**
 * @maxItems 64
 */
export type Recordings = EvidenceRecording[];
export type RequestId5 = string;
export type Resource = "telemetry" | "recordings" | "samples" | "sample";
export type ByteLength1 = number;
export type Frame9 = number;
export type IndexOffset = number | null;
export type Kind14 = "camera" | "lidar";
export type Metadata1 = CameraFormat | LidarFormat;
export type Encoding1 = "bgra8";
export type Height2 = number;
export type Width2 = number;
export type Channels1 = number;
export type Encoding2 = "float32_xyzi";
export type SampleIndex1 = number;
export type Sha2564 = string;
export type Timestamp = number;
export type SampleSha256 = string | null;
/**
 * @maxItems 100
 */
export type Samples = EvidenceSample[];
export type Sha2565 = string | null;
export type SimulationId8 = string;
export type State2 = "available" | "missing" | "deleted" | "changed" | "limit_exceeded";
export type Timestamp1 = number | null;
export type TotalBytes1 = number | null;
export type Version2 = string | null;
export type RequestId6 = string;
export type SimulationId9 = string | null;
export type Status4 = "queued" | "accepted" | "completed" | "rejected";
export type UpdatedAt3 = string;
/**
 * @maxItems 50
 */
export type Items = RemoteCommandView[];
export type NextBefore = string | null;
export type BaselineOnly = true;
export type CarlaReadiness = "unknown";
export type EvidenceSupported = boolean;
export type LastSeenAt = string | null;
export type ProtocolCompatible = boolean | null;
export type ProtocolVersion5 = "poltergeist-relay-v1" | null;
export type RecordingsSupported = boolean;
export type RemoteRequestsEnabled = boolean;
export type State3 = "offline" | "connecting" | "connected" | "paused" | "revoked";
export type CreatedAt8 = string;
export type DeviceId1 = string;
export type DisplayName = string;
export type AccountId = string;
export type Username = string;
export type OwnershipGeneration1 = number;
export type AllowEvidenceRead = boolean;
export type AllowGraphicsChange = false;
export type AllowMapReload1 = false;
export type AllowRawCapture = boolean;
export type AgentId5 = string;
export type PackageDigest = string;
/**
 * @maxItems 100
 */
export type ApprovedAgents = ApprovedAgent[];
export type MaxDurationS = number;
export type MaxTicks1 = number;
export type MaxWaitingRuns = number;
/**
 * @minItems 1
 * @maxItems 5
 */
export type Scopes =
  | [PermissionScope]
  | [PermissionScope, PermissionScope]
  | [PermissionScope, PermissionScope, PermissionScope]
  | [PermissionScope, PermissionScope, PermissionScope, PermissionScope]
  | [PermissionScope, PermissionScope, PermissionScope, PermissionScope, PermissionScope];
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "PermissionScope".
 */
export type PermissionScope = "status:read" | "run:execute:test" | "run:execute:carla" | "run:cancel" | "result:read";
export type PolicyRevision = number;
export type RevokedAt = string | null;
/**
 * @maxItems 13
 */
export type Presets =
  | []
  | [BrowserScenarioPreset]
  | [BrowserScenarioPreset, BrowserScenarioPreset]
  | [BrowserScenarioPreset, BrowserScenarioPreset, BrowserScenarioPreset]
  | [BrowserScenarioPreset, BrowserScenarioPreset, BrowserScenarioPreset, BrowserScenarioPreset]
  | [BrowserScenarioPreset, BrowserScenarioPreset, BrowserScenarioPreset, BrowserScenarioPreset, BrowserScenarioPreset]
  | [
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset
    ]
  | [
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset
    ]
  | [
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset
    ]
  | [
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset
    ]
  | [
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset
    ]
  | [
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset
    ]
  | [
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset
    ]
  | [
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset,
      BrowserScenarioPreset
    ];
export type Description2 = string;
export type Family = "installation" | "intersection" | "pedestrian_crossing" | "sudden_braking" | "occluded_pedestrian";
export type Id8 = string;
export type IntendedStress = ("low" | "medium" | "high") | null;
export type Label = string;
export type Password = string;
export type Username1 = string;
export type Credential = string;
export type ExpectedAccountId = string;
export type ExpectedRevision1 = number;
export type RequestId7 = string;
export type Credential1 = string;
export type DeviceId2 = string;
export type OwnerId = string;
export type Code2 = string;
export type DisplayName1 = string;
export type PrivateSecret = string;
/**
 * @minItems 1
 * @maxItems 5
 */
export type RequestedScopes =
  | [PermissionScope]
  | [PermissionScope, PermissionScope]
  | [PermissionScope, PermissionScope, PermissionScope]
  | [PermissionScope, PermissionScope, PermissionScope, PermissionScope]
  | [PermissionScope, PermissionScope, PermissionScope, PermissionScope, PermissionScope];
export type AttemptId = string;
export type Code3 = string;
export type DeviceId3 = string;
export type ExpiresAt = string;
export type PollIntervalS = 3;
export type DeviceId4 = string;
export type OwnerId1 = string;
export type OwnershipGeneration2 = number;
export type ProtocolVersion6 = "poltergeist-coordinator-v1";
export type RemoteExecutionEnabled = boolean;
export type ExpectedRevision2 = number;
export type NewPassword = string;
export type OldPassword = string;
export type AttemptId1 = string;
export type Completed = true;
export type DeviceId5 = string;
export type Outcome = "cancelled" | "expired" | "denied" | "revoked" | "absent";
export type OwnerId2 = string;
export type CsrfToken = string;
export type ExpiresAt1 = string;
export type AttemptId2 = string;
export type DeviceId6 = string;
export type ExpiresAt2 = string;
export type RequestedScopes1 = PermissionScope[];
export type Revision5 = number;
export type State4 = "pending" | "claimed" | "approved" | "denied" | "expired" | "cancelled";
export type Request1 = SearchRequest | ComparisonRequest | ReproductionRequest | MinimizeRequest;
export type ExpectedRevision3 = number;
export type Detail = string;
export type Id9 = string;
export type Label1 = string;
export type NextStep = string | null;
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ReadinessStatus".
 */
export type ReadinessStatus = "ready" | "missing" | "unsupported" | "not_running" | "not_checked" | "unknown" | "error";
export type Checks = ReadinessCheck[];
export type Summary2 = string;
export type CarlaConnectionRequested = boolean;
export type CheckedAt1 = string;
export type SchemaVersion4 = "poltergeist-readiness-v1";
export type Scope = "local_core";
export type Command = ExecuteCommand | CancelCommand | StatusCommand | ResultCommand | EvidenceRequest;
export type AgentId6 = null;
export type Kind15 = "execute";
export type Mode9 = "test" | "carla";
export type Camera1 = boolean;
export type Lidar1 = boolean;
export type MaxTicks2 = number;
export type RequestId8 = string;
export type Kind16 = "cancel";
export type RequestId9 = string;
export type SimulationId10 = string;
export type Kind17 = "status";
export type RequestId10 = string;
export type Kind18 = "result";
export type RequestId11 = string;
export type SimulationId11 = string;
export type ArtifactIndex2 = number;
export type Kind19 = "evidence";
export type Limit2 = number;
export type Offset3 = number;
export type RequestId12 = string;
export type Resource1 = "telemetry" | "recordings" | "samples" | "sample";
export type SampleCursor = number | null;
export type SampleIndex2 = number;
export type SimulationId12 = string;
export type Version3 = string | null;
export type Envelope = ServerHello | ClientReady | RelayCommand | RelayReply | RelayPing | RelayPong;
export type ConnectionGeneration = number;
export type DeadTimeoutS = 90;
export type DeviceId7 = string;
export type EvidenceSupported1 = boolean;
export type HeartbeatS = 30;
export type Kind20 = "hello";
export type OwnerId3 = string;
export type OwnershipGeneration3 = number;
export type PolicyRevision1 = number;
export type ProtocolVersion7 = "poltergeist-relay-v1";
export type RecordingsSupported1 = boolean;
export type ConnectionGeneration1 = number;
export type DeviceId8 = string;
export type EvidenceSupported2 = boolean;
export type Kind21 = "ready";
export type OwnershipGeneration4 = number;
export type ProtocolVersion8 = "poltergeist-relay-v1";
export type RecordingsSupported2 = boolean;
export type RemoteExecutionEnabled1 = boolean;
export type Command1 = ExecuteCommand | CancelCommand | StatusCommand | ResultCommand | EvidenceRequest;
export type ConnectionGeneration2 = number;
export type DeviceId9 = string;
export type Kind22 = "command";
export type OwnershipGeneration5 = number;
export type ProtocolVersion9 = "poltergeist-relay-v1";
export type ConnectionGeneration3 = number;
export type DeviceId10 = string;
export type Kind23 = "reply";
export type OwnershipGeneration6 = number;
export type ProtocolVersion10 = "poltergeist-relay-v1";
export type Reply1 =
  ExecuteAccepted | CancelAccepted | DeviceStatusReply | RunResultReply | CommandRejected | EvidenceReply;
export type ConnectionGeneration4 = number;
export type DeviceId11 = string;
export type Kind24 = "ping";
export type Nonce = string;
export type OwnershipGeneration7 = number;
export type ProtocolVersion11 = "poltergeist-relay-v1";
export type ConnectionGeneration5 = number;
export type DeviceId12 = string;
export type Kind25 = "pong";
export type Nonce1 = string;
export type OwnershipGeneration8 = number;
export type ProtocolVersion12 = "poltergeist-relay-v1";
export type Reply2 =
  ExecuteAccepted | CancelAccepted | DeviceStatusReply | RunResultReply | CommandRejected | EvidenceReply;
export type ProtocolVersion13 = "poltergeist-runner-v1";
export type RequestId13 = string;
export type SimulationId13 = string;
export type CheckedAt2 = string;
export type ExecutionModes = ("test" | "carla")[];
export type ImplementationVersion = string;
export type LocalFeatures = ("baseline" | "approved_sdk_agents" | "synthetic_sensor_fixtures" | "behavior_reports")[];
export type MaxConcurrentRuns = 1;
export type Platform = "windows-x86_64";
export type ProtocolVersion14 = "poltergeist-runner-v1";
export type RemoteExecutionEnabled2 = false;
export type SdkProtocols = ("1.0" | "1.1" | "1.2" | "1.3")[];
export type AgentId7 = string | null;
export type Mode10 = "test" | "carla";
export type ProtocolVersion15 = "poltergeist-runner-v1";
export type RequestId14 = string;
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "OptimizationStatus".
 */
export type OptimizationStatus1 = "queued" | "running" | "completed" | "failed" | "cancelled" | "interrupted";

export interface PoltergeistContracts {
  agent: AgentContracts;
  api: ApiContracts;
  browser: BrowserContracts;
  coordination: CoordinationContracts;
  failure: FailureReport;
  metrics: SafetyMetrics;
  optimization: OptimizationContracts;
  readiness: ReadinessReport;
  remote: RemoteContracts;
  result: SimulationResult;
  runner: RunnerContracts;
  scenario: Scenario;
  simulation: Simulation;
  telemetry: TelemetryFrame;
}
/**
 * Schema entry point; this grouping is not an agent message.
 *
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "AgentContracts".
 */
export interface AgentContracts {
  diagnostics: AgentDiagnostics;
  manifest: AgentManifest;
  observation: AgentObservation;
  reset: AgentReset;
  step_result: AgentStepResult;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "AgentDiagnostics".
 */
export interface AgentDiagnostics {
  assistance: Assistance;
  hazard_actor_ids: HazardActorIds;
  maneuver: Maneuver;
  metadata: Metadata;
  planned_trajectory: PlannedTrajectory;
  reason_codes: ReasonCodes;
  target_speed_mps: TargetSpeedMps;
}
export interface Metadata {
  [k: string]: JsonValue;
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
 * via the `definition` "Rotation".
 */
export interface Rotation {
  pitch_deg: PitchDeg;
  roll_deg: RollDeg;
  yaw_deg: YawDeg;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "AgentManifest".
 */
export interface AgentManifest {
  agent_id: AgentId;
  assets: Assets;
  configuration: Configuration;
  control_mode: ControlMode;
  entry_point: EntryPoint;
  name: Name;
  navigation_mode: NavigationMode;
  observation_mode: ObservationMode;
  protocol_version: ProtocolVersion;
  required_features: RequiredFeatures;
  requirements_file: RequirementsFile;
  sensors: Sensors;
  version: Version;
}
export interface Configuration {
  [k: string]: JsonValue;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "AgentSensorSpec".
 */
export interface AgentSensorSpec {
  attributes: Attributes;
  id: Id;
  mount: Transform;
  type: Type;
}
export interface Attributes {
  [k: string]: JsonValue;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "AgentObservation".
 */
export interface AgentObservation {
  actors: Actors;
  driving_task: DrivingTask | null;
  dt_s: DtS;
  ego: ActorState;
  ego_state_source: EgoStateSource;
  frame: Frame;
  observation_mode: ObservationMode1;
  protocol_version: ProtocolVersion1;
  route_heading_deg: RouteHeadingDeg;
  scene: AgentSceneContext | null;
  sensor_source: SensorSource;
  sensors: Sensors1;
  target_speed_mps: TargetSpeedMps2;
  time_s: TimeS;
  traffic_light_red: TrafficLightRed;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ActorState".
 */
export interface ActorState {
  acceleration_mps2: Vector3 | null;
  actor_id: ActorId;
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
 * Explicit navigation objective, independent of an actor's initial speed.
 *
 * Framework routes use world-coordinate waypoints. Agent-owned navigation
 * supplies only a destination so the agent must plan the route itself.
 *
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "DrivingTask".
 */
export interface DrivingTask {
  completion_height_tolerance_m: CompletionHeightToleranceM;
  completion_hold_s: CompletionHoldS;
  completion_radius_m: CompletionRadiusM;
  completion_speed_mps: CompletionSpeedMps;
  destination: Transform | null;
  route: Route;
  route_owner: RouteOwner;
  stop_on_completion: StopOnCompletion;
  target_speed_mps: TargetSpeedMps1;
}
/**
 * Versioned optional privileged scene context for SDK 1.1 agents.
 *
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "AgentSceneContext".
 */
export interface AgentSceneContext {
  objects: Objects;
  road_raster: AgentRoadRaster | null;
  route: Route1;
  schema_version: SchemaVersion;
  source: Source;
  speed_limit_mps: SpeedLimitMps1;
}
/**
 * Privileged object state; transform is the measured world box center.
 *
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "AgentSceneObject".
 */
export interface AgentSceneObject {
  actor_id: ActorId1;
  extent: BoundingBoxExtent;
  is_static: IsStatic;
  kind: Kind;
  traffic_light_state: TrafficLightState;
  transform: Transform;
  velocity_mps: Vector3 | null;
}
/**
 * Ego-aligned semantic map, not camera perception.
 *
 * Classes: 0 background, 1 driving road, 2 sidewalk, 3 solid lane line,
 * 4 broken lane line. Rows increase ego-right, columns increase ego-forward.
 * Payload is either flat row-major classes or lossless base64 uint8 bytes;
 * origin identifies the ego reference point.
 *
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "AgentRoadRaster".
 */
export interface AgentRoadRaster {
  axes: Axes;
  classes: Classes;
  data_base64: DataBase64;
  ego_column: EgoColumn;
  ego_row: EgoRow;
  height: Height;
  pixels_per_meter: PixelsPerMeter;
  width: Width;
}
/**
 * One lossless, native-frame-associated CARLA camera or LiDAR sample.
 *
 * Camera bytes are row-major BGRA uint8. LiDAR bytes are little-endian float32
 * quadruples (x forward, y right, z up, intensity) in the sensor's local frame.
 * Timestamp is relative to this episode's first observation.
 *
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "AgentSensorFrame".
 */
export interface AgentSensorFrame {
  channels: Channels;
  data_base64: DataBase641;
  encoding: Encoding;
  fov_deg: FovDeg;
  frame: Frame1;
  height: Height1;
  sensor_id: SensorId;
  sha256: Sha256;
  timestamp_s: TimestampS;
  type: Type1;
  width: Width1;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "AgentReset".
 */
export interface AgentReset {
  destination: Transform | null;
  driving_task: DrivingTask | null;
  ego_actor_id: EgoActorId;
  fixed_delta_seconds: FixedDeltaSeconds;
  map_name: MapName;
  protocol_version: ProtocolVersion2;
  route: Route2;
  seed: Seed;
  sensors: Sensors2;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "AgentStepResult".
 */
export interface AgentStepResult {
  control: ControlInput;
  diagnostics: AgentDiagnostics | null;
  frame: Frame2;
  protocol_version: ProtocolVersion3;
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
 * via the `definition` "ApiContracts".
 */
export interface ApiContracts {
  agent_readiness: AgentReadiness;
  agent_registration: AgentRegistration;
  analytics: AnalyticsSummary;
  behavior_analysis: BehaviorAnalysis;
  behavior_settings: BehaviorSettings;
  comparison_report: ComparisonReport;
  decision_page: DecisionPage;
  queue_status: QueueStatus;
  registered_agent: RegisteredAgent;
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
 * via the `definition` "AgentReadiness".
 */
export interface AgentReadiness {
  agent_id: AgentId1;
  checked_at: CheckedAt;
  inference_checked: InferenceChecked;
  inspection: Inspection;
  issues: Issues;
  limitations: Limitations;
  ready: Ready;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "AgentRegistration".
 */
export interface AgentRegistration {
  agent_control_mode: AgentControlMode;
  configuration: Configuration1;
  model_files: ModelFiles;
  package_path: PackagePath;
  process_options: AgentExecutionOptions;
  python_executable: PythonExecutable;
  trusted_local_code: TrustedLocalCode;
}
export interface Configuration1 {
  [k: string]: JsonValue;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "AgentExecutionOptions".
 */
export interface AgentExecutionOptions {
  close_timeout_s: CloseTimeoutS;
  load_timeout_s: LoadTimeoutS;
  max_message_bytes: MaxMessageBytes;
  reset_timeout_s: ResetTimeoutS;
  step_timeout_s: StepTimeoutS;
  terminate_timeout_s: TerminateTimeoutS;
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
 * via the `definition` "BehaviorAnalysis".
 */
export interface BehaviorAnalysis {
  agent: Agent;
  agent_inputs: AgentInputInfo | null;
  agent_snapshot: AgentSnapshot | null;
  braking: BrakingBehavior;
  control_mode: ControlMode1;
  coverage: BehaviorCoverage;
  driving_outcome: DrivingOutcome | null;
  events: Events;
  execution_status: SimulationStatus;
  interpretation_notes: InterpretationNotes;
  mode: Mode1;
  route: RouteBehavior;
  safety_evidence: SafetyEvidence;
  schema_version: SchemaVersion1;
  settings: BehaviorSettings;
  simulation_id: SimulationId;
  stored_safety_metrics: SafetyMetrics;
  task_evidence: TaskEvidence;
}
/**
 * Persisted provenance of agent inputs, separate from evaluator telemetry.
 *
 * Sensor driving still receives ideal simulated ego localization. Naming that
 * assistance prevents a camera/LiDAR run from implying a learned localization
 * stack, or a deterministic fixture from implying actual CARLA sensor data.
 *
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "AgentInputInfo".
 */
export interface AgentInputInfo {
  assistance: Assistance1;
  ego_state_source: EgoStateSource1;
  ground_truth_actor_inputs: GroundTruthActorInputs;
  observation_mode: ObservationMode2;
  sensors: Sensors3;
  source: Source1;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "AgentSnapshot".
 */
export interface AgentSnapshot {
  agent_control_mode: AgentControlMode1;
  configuration: Configuration2;
  files: Files;
  manifest: AgentManifest;
  model_files: ModelFiles1;
  observation_mode: ObservationMode3;
  package_path: PackagePath1;
  package_sha256: PackageSha256;
  process_options: AgentExecutionOptions;
  python_executable: PythonExecutable1;
  python_sha256: PythonSha256;
  python_size_bytes: PythonSizeBytes;
  registration_id: RegistrationId;
  runtime_files: RuntimeFiles;
}
export interface Configuration2 {
  [k: string]: JsonValue;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "AgentFileFingerprint".
 */
export interface AgentFileFingerprint {
  path: Path;
  sha256: Sha2561;
  size_bytes: SizeBytes;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "AgentExternalFileFingerprint".
 */
export interface AgentExternalFileFingerprint {
  path: Path1;
  sha256: Sha2562;
  size_bytes: SizeBytes1;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "BrakingBehavior".
 */
export interface BrakingBehavior {
  episodes_truncated: EpisodesTruncated;
  hard_braking_count: BehaviorMeasure;
  max_deceleration: BehaviorMeasure;
  settings: EvaluationSettings;
  settings_source: SettingsSource;
  stopping_episodes: StoppingEpisodes;
  total_stopping_episodes: TotalStoppingEpisodes;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "BehaviorMeasure".
 */
export interface BehaviorMeasure {
  coverage: Coverage;
  eligible_samples: EligibleSamples;
  evidence: Evidence;
  observed_samples: ObservedSamples;
  reason: Reason;
  unit: Unit;
  value: Value;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "EvidenceSpan".
 */
export interface EvidenceSpan {
  first_frame: FirstFrame;
  first_time_s: FirstTimeS;
  last_frame: LastFrame;
  last_time_s: LastTimeS;
  source: Source2;
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
 * via the `definition` "StoppingEpisode".
 */
export interface StoppingEpisode {
  distance_m: DistanceM;
  duration_s: DurationS;
  evidence: EvidenceSpan;
  intervention_frames: InterventionFrames;
  script_influenced: ScriptInfluenced;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "BehaviorCoverage".
 */
export interface BehaviorCoverage {
  applied_decisions: AppliedDecisions;
  changed_control_frames: ChangedControlFrames;
  decision_coverage: DecisionCoverage;
  hazard_clearance_pairs: HazardClearancePairs;
  hazard_ttc_pairs: HazardTtcPairs;
  measured_duration_s: MeasuredDurationS;
  pose_frames: PoseFrames;
  response_events_truncated: ResponseEventsTruncated;
  script_intervention_frames: ScriptInterventionFrames;
  telemetry_frames: TelemetryFrames;
  telemetry_gaps: TelemetryGaps;
  total_response_events: TotalResponseEvents;
  unresolved_inputs: UnresolvedInputs;
  velocity_frames: VelocityFrames;
}
/**
 * Navigation measurements separate from execution and safety outcomes.
 *
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "DrivingOutcome".
 */
export interface DrivingOutcome {
  control_mode: ControlMode2;
  destination: Transform;
  final_distance_m: FinalDistanceM;
  final_height_error_m: FinalHeightErrorM;
  initial_speed_mps: InitialSpeedMps;
  interventions: Interventions;
  reached_at_s: ReachedAtS;
  resolved_route: ResolvedRoute;
  route_length_m: RouteLengthM;
  route_owner: RouteOwner1;
  route_progress_m: RouteProgressM;
  status: Status;
  target_speed_mps: TargetSpeedMps4;
  termination_reason: TerminationReason;
}
/**
 * A recorded scenario-script influence on the ego's requested control.
 *
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ControlIntervention".
 */
export interface ControlIntervention {
  applied_control: ControlInput;
  frame: Frame3;
  kind: Kind1;
  proposed_control: ControlInput;
  target_speed_mps: TargetSpeedMps3;
  time_s: TimeS1;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "EventBehavior".
 */
export interface EventBehavior {
  anchor: BehaviorAnchor;
  responses: Responses;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "BehaviorAnchor".
 */
export interface BehaviorAnchor {
  actor_id: ActorId2;
  clearance_m: ClearanceM;
  ego_event: EgoEvent;
  event_action: EventAction;
  event_index: EventIndex;
  frame: Frame4;
  id: Id1;
  kind: Kind2;
  onset: Onset;
  reason: Reason1;
  scheduled_time_s: ScheduledTimeS;
  source: Source3;
  time_s: TimeS2;
  ttc_s: TtcS;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "BehaviorResponse".
 */
export interface BehaviorResponse {
  attribution: Attribution;
  channel: Channel;
  evidence: Evidence1;
  latency_s: LatencyS;
  observed_until_s: ObservedUntilS;
  reason: Reason2;
  reference_frame: ReferenceFrame;
  reference_value: ReferenceValue;
  response_frame: ResponseFrame;
  response_time_s: ResponseTimeS;
  status: Status1;
  threshold: Threshold;
  unit: Unit1;
  value: Value1;
  window_complete: WindowComplete;
  window_end_s: WindowEndS;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "RouteBehavior".
 */
export interface RouteBehavior {
  max_deviation: BehaviorMeasure;
  mean_deviation: BehaviorMeasure;
  recorded_completion_fraction: BehaviorMeasure;
  recorded_length: BehaviorMeasure;
  recorded_progress: BehaviorMeasure;
  reference_source: ReferenceSource;
  route_owner: RouteOwner2;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "BehaviorSettings".
 */
export interface BehaviorSettings {
  brake_threshold: BrakeThreshold;
  deceleration_threshold_mps2: DecelerationThresholdMps2;
  hazard_clearance_m: HazardClearanceM;
  hazard_ttc_s: HazardTtcS;
  heading_change_deg: HeadingChangeDeg;
  max_response_events: MaxResponseEvents;
  max_sample_gap_s: MaxSampleGapS1;
  max_stopping_episodes: MaxStoppingEpisodes;
  method_version: MethodVersion1;
  response_window_s: ResponseWindowS;
  route_height_tolerance_m: RouteHeightToleranceM;
  steering_change_threshold: SteeringChangeThreshold;
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
 * via the `definition` "ComparisonReport".
 */
export interface ComparisonReport {
  evidence: OptimizationView;
  groups: Groups1;
  notes: Notes;
  paired_comparisons: PairedComparisons;
  schema_version: SchemaVersion3;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "OptimizationView".
 */
export interface OptimizationView {
  operation: OptimizationRecord;
  trials: Trials1;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "OptimizationRecord".
 */
export interface OptimizationRecord {
  agent_snapshot: AgentSnapshot | null;
  cancel_requested: CancelRequested;
  comparison_agents: ComparisonAgents;
  created_at: CreatedAt;
  engine_version: EngineVersion;
  error: Error;
  execution: ExecutionSpec;
  finished_at: FinishedAt;
  id: Id2;
  outcome: OptimizationOutcome | null;
  request: Request;
  revision: Revision;
  source_failure: FailureReport | null;
  started_at: StartedAt;
  status: OptimizationStatus;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ComparisonAgent".
 */
export interface ComparisonAgent {
  agent_id: AgentId2;
  snapshot: AgentSnapshot | null;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ExecutionSpec".
 */
export interface ExecutionSpec {
  evaluation_settings: EvaluationSettings;
  mode: Mode2;
  options: SimulationOptions;
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
  carla_quality: CarlaQuality;
  lidar: Lidar;
  max_ticks: MaxTicks;
  no_rendering_mode: NoRenderingMode;
  sensor_settle_s: SensorSettleS;
  sensor_timeout_s: SensorTimeoutS;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "OptimizationOutcome".
 */
export interface OptimizationOutcome {
  best_failure_id: BestFailureId;
  best_scenario: Scenario | null;
  best_simulation_id: BestSimulationId;
  reproduced: Reproduced;
  successes: Successes;
  summary: Summary;
  trials_used: TrialsUsed;
  unknowns: Unknowns;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "Scenario".
 */
export interface Scenario {
  actors: Actors1;
  description: Description;
  driving_task: DrivingTask | null;
  duration_s: DurationS1;
  event: Event;
  events: Events1;
  fixed_delta_seconds: FixedDeltaSeconds1;
  location: Location;
  map: Map;
  name: Name1;
  occlusion: Occlusion;
  parameters: Parameters;
  schema_version: SchemaVersion2;
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
  id: Id3;
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
 * Set an actor's target speed at simulation time trigger_time_s.
 *
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "SpeedChangeEvent".
 */
export interface SpeedChangeEvent {
  action: Action;
  actor_id: ActorId3;
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
  actor_id: ActorId4;
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
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "SearchRequest".
 */
export interface SearchRequest {
  agent_id: AgentId3;
  driving_task: DrivingTask | null;
  evaluation_settings: EvaluationSettings;
  failure_kind: FailureKind | null;
  kind: Kind3;
  minimum_severity: MinimumSeverity;
  mode: Mode3;
  options: SimulationOptions;
  parameters: Parameters1;
  ranges: Ranges;
  seed: Seed2;
  strategy: Strategy;
  template: ScenarioTemplate;
  trials: Trials;
}
export interface Parameters1 {
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
 * via the `definition` "ComparisonRequest".
 */
export interface ComparisonRequest {
  agents: Agents;
  evaluation_settings: EvaluationSettings;
  failure_kind: FailureKind | null;
  kind: Kind4;
  minimum_severity: MinimumSeverity1;
  mode: Mode4;
  options: SimulationOptions;
  repeats: Repeats;
  scenario: Scenario;
  seeds: Seeds;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ReproductionRequest".
 */
export interface ReproductionRequest {
  failure_id: FailureId;
  kind: Kind5;
  minimum_severity: MinimumSeverity2;
  repeats: Repeats1;
  required_successes: RequiredSuccesses;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "MinimizeRequest".
 */
export interface MinimizeRequest {
  failure_id: FailureId1;
  kind: Kind6;
  max_trials: MaxTrials;
  minimum_severity: MinimumSeverity3;
  repeats: Repeats2;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "FailureReport".
 */
export interface FailureReport {
  created_at: CreatedAt1;
  id: Id4;
  kind: FailureKind;
  reproduced: Reproduced1;
  scenario: Scenario;
  severity: Severity1;
  simulation_id: SimulationId1;
  summary: Summary1;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "OptimizationTrial".
 */
export interface OptimizationTrial {
  agent_index: AgentIndex;
  assessed: Assessed;
  candidate_index: CandidateIndex;
  failure_id: FailureId2;
  matches: Matches;
  number: Number;
  operation_id: OperationId;
  reason: Reason3;
  repeat_index: RepeatIndex;
  role: Role;
  scenario: Scenario;
  score: Score;
  simulation_id: SimulationId2;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ComparisonGroup".
 */
export interface ComparisonGroup {
  agent_index: AgentIndex1;
  assessed_runs: AssessedRuns;
  completed_runs: CompletedRuns;
  input_sources: InputSources;
  matching_runs: MatchingRuns;
  metrics: Metrics;
  name: Name2;
  negative_runs: NegativeRuns;
  partial_coverage_results: PartialCoverageResults;
  planned_runs: PlannedRuns;
  registration_id: RegistrationId1;
  simulation_ids: SimulationIds;
  status_counts: StatusCounts1;
  task_outcomes: TaskOutcomes;
  unassessed_runs: UnassessedRuns;
  unknown_runs: UnknownRuns;
  version: Version1;
}
export interface InputSources {
  [k: string]: number;
}
export interface Metrics {
  [k: string]: ComparisonDistribution;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ComparisonDistribution".
 */
export interface ComparisonDistribution {
  eligible_results: EligibleResults;
  maximum: Maximum;
  mean: Mean;
  minimum: Minimum;
  observed_results: ObservedResults;
  sample_stddev: SampleStddev;
  unavailable_results: UnavailableResults;
  unit: Unit2;
}
export interface StatusCounts1 {
  [k: string]: number;
}
export interface TaskOutcomes {
  [k: string]: number;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "PairedComparison".
 */
export interface PairedComparison {
  compared_agent_index: ComparedAgentIndex;
  difference: ComparisonDistribution;
  pairs: Pairs;
  reference_agent_index: ReferenceAgentIndex;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ComparisonPair".
 */
export interface ComparisonPair {
  compared_simulation_id: ComparedSimulationId;
  reason: Reason4;
  reference_simulation_id: ReferenceSimulationId;
  repeat_index: RepeatIndex1;
  seed: Seed3;
  severity_difference: SeverityDifference;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "DecisionPage".
 */
export interface DecisionPage {
  applied: Applied;
  available: Available;
  coverage: Coverage1;
  limit: Limit;
  offset: Offset;
  recording_artifact_index: RecordingArtifactIndex;
  records: Records;
  reset: AgentReset | null;
  simulation_id: SimulationId3;
  telemetry: Telemetry;
  total: Total;
  transitions: Transitions;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "DecisionRecord".
 */
export interface DecisionRecord {
  agent_result: AgentStepResult | null;
  applied_control: ControlInput | null;
  control_mode: ControlMode3;
  frame: Frame5;
  full_step_wall_time_s: FullStepWallTimeS;
  interventions: Interventions1;
  observation: Observation;
  proposed_control: ControlInput | null;
  resulting_ego: ActorState | null;
  resulting_frame: ResultingFrame;
  round_trip_wall_time_s: RoundTripWallTimeS;
  sensors: Sensors4;
  status: Status2;
  time_s: TimeS3;
  worker_step_wall_time_s: WorkerStepWallTimeS;
}
export interface Observation {
  [k: string]: JsonValue;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "DecisionSensor".
 */
export interface DecisionSensor {
  byte_length: ByteLength;
  header: Header;
  sample_index: SampleIndex;
}
export interface Header {
  [k: string]: JsonValue;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "TelemetryFrame".
 */
export interface TelemetryFrame {
  actors: Actors2;
  collisions: Collisions;
  control: ControlInput;
  ego: ActorState;
  frame: Frame6;
  rule_checks: RuleChecks;
  time_s: TimeS4;
  traffic_violations: TrafficViolations;
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
 * via the `definition` "TrafficViolation".
 */
export interface TrafficViolation {
  actor_id: ActorId5;
  details: Details;
  event_id: EventId1;
  rule: TrafficRule;
  source_id: SourceId;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "QueueStatus".
 */
export interface QueueStatus {
  accepting: Accepting;
  active_id: ActiveId;
  error: Error1;
  pending: Pending;
  started: Started;
  worker_alive: WorkerAlive;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "RegisteredAgent".
 */
export interface RegisteredAgent {
  active: Active;
  created_at: CreatedAt2;
  id: Id5;
  snapshot: AgentSnapshot;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ReproductionUpdate".
 */
export interface ReproductionUpdate {
  reproduced: Reproduced2;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ScenarioGenerate".
 */
export interface ScenarioGenerate {
  duration_s: DurationS2;
  map: Map1;
  parameters: Parameters2;
  ranges: Ranges1;
  sample: Sample;
  seed: Seed4;
  template: ScenarioTemplate;
}
export interface Parameters2 {
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
 * via the `definition` "ScenarioRecord".
 */
export interface ScenarioRecord {
  created_at: CreatedAt3;
  id: Id6;
  revision: Revision1;
  scenario: Scenario;
  updated_at: UpdatedAt;
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
  agent_id: AgentId4;
  evaluation_settings: EvaluationSettings;
  mode: Mode5;
  options: SimulationOptions;
  scenario: Scenario | null;
  scenario_id: ScenarioId;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "SimulationProgress".
 */
export interface SimulationProgress {
  cancel_requested: CancelRequested1;
  error: Error2;
  finished_at: FinishedAt1;
  progress: Progress;
  queue_position: QueuePosition;
  revision: Revision2;
  simulation_id: SimulationId4;
  started_at: StartedAt1;
  status: SimulationStatus;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "SimulationRecord".
 */
export interface SimulationRecord {
  created_at: CreatedAt4;
  job: JobInfo | null;
  result: SimulationResult | null;
  revision: Revision3;
  scenario_id: ScenarioId1;
  simulation: Simulation;
  updated_at: UpdatedAt1;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "JobInfo".
 */
export interface JobInfo {
  agent_snapshot: AgentSnapshot | null;
  cancel_requested: CancelRequested2;
  evaluation_settings: EvaluationSettings;
  options: SimulationOptions;
  queue_position: QueuePosition1;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "SimulationResult".
 */
export interface SimulationResult {
  agent_inputs: AgentInputInfo | null;
  artifacts: Artifacts;
  control_audit: ControlAudit | null;
  driving_outcome: DrivingOutcome | null;
  measured_duration_s: MeasuredDurationS1;
  metrics: SafetyMetrics;
  runtime: SimulationRuntime | null;
  simulation: Simulation;
  telemetry_frames: TelemetryFrames1;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ArtifactReference".
 */
export interface ArtifactReference {
  kind: Kind7;
  media_type: MediaType;
  path: Path2;
}
/**
 * Control ownership and scenario assistance for both new and legacy runs.
 *
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ControlAudit".
 */
export interface ControlAudit {
  controller: Controller;
  interventions: Interventions2;
  mode: Mode6;
  navigation: Navigation;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "SimulationRuntime".
 */
export interface SimulationRuntime {
  adapter: Adapter;
  client_version: ClientVersion;
  fixed_delta_seconds: FixedDeltaSeconds2;
  map_name: MapName1;
  resolved_spawns: ResolvedSpawns;
  seed: Seed5;
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
  agent: Agent1;
  agent_snapshot: AgentSnapshot | null;
  created_at: CreatedAt5;
  error: Error3;
  finished_at: FinishedAt2;
  id: Id7;
  mode: Mode7;
  progress: Progress1;
  scenario: Scenario;
  started_at: StartedAt2;
  status: SimulationStatus1;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "TelemetryPage".
 */
export interface TelemetryPage {
  frames: Frames;
  limit: Limit1;
  offset: Offset1;
  total: Total1;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "TemplateInfo".
 */
export interface TemplateInfo {
  parameters: Parameters3;
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
  maximum: Maximum1;
  minimum: Minimum1;
  name: Name3;
  unit: Unit3;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "BrowserContracts".
 */
export interface BrowserContracts {
  commands: BrowserCommandPage;
  device: BrowserDeviceView;
  presets: Presets;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "BrowserCommandPage".
 */
export interface BrowserCommandPage {
  items: Items;
  next_before: NextBefore;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "RemoteCommandView".
 */
export interface RemoteCommandView {
  created_at: CreatedAt6;
  operation: Operation;
  reply: Reply;
  request_id: RequestId6;
  simulation_id: SimulationId9;
  status: Status4;
  updated_at: UpdatedAt3;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ExecuteAccepted".
 */
export interface ExecuteAccepted {
  accepted_at: AcceptedAt;
  kind: Kind8;
  request_id: RequestId;
  simulation_id: SimulationId5;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "CancelAccepted".
 */
export interface CancelAccepted {
  accepted_at: AcceptedAt1;
  kind: Kind9;
  request_id: RequestId1;
  simulation_id: SimulationId6;
  terminal: Terminal;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "DeviceStatusReply".
 */
export interface DeviceStatusReply {
  accepting: Accepting1;
  active: Active1;
  kind: Kind10;
  request_id: RequestId2;
  waiting_remote_runs: WaitingRemoteRuns;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "RunResultReply".
 */
export interface RunResultReply {
  kind: Kind11;
  request_id: RequestId3;
  summary: HostedRunSummaryV1;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "HostedRunSummaryV1".
 */
export interface HostedRunSummaryV1 {
  agent: Agent2;
  cancel_requested: CancelRequested3;
  created_at: CreatedAt7;
  device_id: DeviceId;
  error: HostedPublicErrorV1 | null;
  evidence: HostedEvidenceV1;
  execute_request_id: ExecuteRequestId;
  finished_at: FinishedAt3;
  measured_duration_s: MeasuredDurationS2;
  metrics: HostedSafetyMetricsV1 | null;
  mode: Mode8;
  ownership_generation: OwnershipGeneration;
  progress: Progress2;
  protocol_version: ProtocolVersion4;
  provenance: HostedProvenanceV1 | null;
  queue_position: QueuePosition2;
  receipt_revision: ReceiptRevision;
  result_available: ResultAvailable;
  revision: Revision4;
  runtime: HostedRuntimeV1 | null;
  scenario: HostedScenarioV1;
  simulation_id: SimulationId7;
  started_at: StartedAt3;
  status: Status3;
  updated_at: UpdatedAt2;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "HostedPublicErrorV1".
 */
export interface HostedPublicErrorV1 {
  code: Code;
  correlation_id: CorrelationId;
  message: Message;
}
/**
 * The caller checks local availability; counts describe persisted metadata.
 *
 * No path, download URL or transfer capability is published. A known recorded
 * frame count can survive a missing artifact without implying that it is zero.
 *
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "HostedEvidenceV1".
 */
export interface HostedEvidenceV1 {
  artifact_reference_count: ArtifactReferenceCount;
  state: State;
  telemetry_frames: TelemetryFrames2;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "HostedSafetyMetricsV1".
 */
export interface HostedSafetyMetricsV1 {
  collision_count: CollisionCount1;
  coverage: HostedCoverageV1;
  evaluation_settings: HostedEvaluationSettingsV1 | null;
  hard_braking_count: HardBrakingCount1;
  max_deceleration_mps2: MaxDecelerationMps21;
  max_stopping_distance_m: MaxStoppingDistanceM1;
  max_stopping_time_s: MaxStoppingTimeS1;
  min_clearance_m: MinClearanceM1;
  min_ttc_s: MinTtcS1;
  severity: Severity2;
  traffic_violation_count: TrafficViolationCount1;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "HostedCoverageV1".
 */
export interface HostedCoverageV1 {
  actor_pairs: ActorPairs1;
  braking_frames: BrakingFrames1;
  clearance_frames: ClearanceFrames1;
  clearance_pairs: ClearancePairs1;
  collision_frames: CollisionFrames1;
  complete: Complete;
  has_gaps: HasGaps1;
  total_frames: TotalFrames1;
  traffic_rule_frames: TrafficRuleFrames1;
  ttc_frames: TtcFrames1;
  ttc_pairs: TtcPairs1;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "HostedEvaluationSettingsV1".
 */
export interface HostedEvaluationSettingsV1 {
  braking_full_severity_mps2: BrakingFullSeverityMps21;
  braking_weight: BrakingWeight1;
  clearance_warning_m: ClearanceWarningM1;
  clearance_weight: ClearanceWeight1;
  collision_episode_gap_s: CollisionEpisodeGapS1;
  hard_braking_threshold_mps2: HardBrakingThresholdMps21;
  max_sample_gap_s: MaxSampleGapS2;
  method_version: MethodVersion2;
  speeding_tolerance_mps: SpeedingToleranceMps1;
  stopped_speed_mps: StoppedSpeedMps1;
  traffic_rule_weight: TrafficRuleWeight1;
  ttc_horizon_s: TtcHorizonS1;
  ttc_warning_s: TtcWarningS1;
  ttc_weight: TtcWeight1;
  violation_episode_gap_s: ViolationEpisodeGapS1;
  violations_for_full_severity: ViolationsForFullSeverity1;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "HostedProvenanceV1".
 */
export interface HostedProvenanceV1 {
  control_mode: ControlMode4;
  controller: Controller1;
  ego_state_source: EgoStateSource2;
  ground_truth_actor_inputs: GroundTruthActorInputs1;
  navigation: Navigation1;
  observation_mode: ObservationMode4;
  source: Source4;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "HostedRuntimeV1".
 */
export interface HostedRuntimeV1 {
  adapter: Adapter1;
  client_version: ClientVersion1;
  fixed_delta_seconds: FixedDeltaSeconds3;
  map_name: MapName2;
  seed: Seed6;
  server_version: ServerVersion1;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "HostedScenarioV1".
 */
export interface HostedScenarioV1 {
  name: Name4;
  sha256: Sha2563;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "CommandRejected".
 */
export interface CommandRejected {
  code: Code1;
  kind: Kind12;
  request_id: RequestId4;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "EvidenceReply".
 */
export interface EvidenceReply {
  artifact_index?: ArtifactIndex;
  data_base64: DataBase642;
  frame: Frame7;
  frames: Frames1;
  kind: Kind13;
  next_offset: NextOffset;
  offset: Offset2;
  recordings?: Recordings;
  request_id: RequestId5;
  resource: Resource;
  sample?: EvidenceSample | null;
  sample_sha256: SampleSha256;
  samples?: Samples;
  sha256: Sha2565;
  simulation_id: SimulationId8;
  state: State2;
  timestamp: Timestamp1;
  total_bytes: TotalBytes1;
  version: Version2;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "EvidenceFrame".
 */
export interface EvidenceFrame {
  collisions: Collisions1;
  control: ControlInput;
  frame: Frame8;
  speed_mps: SpeedMps2;
  time_s: TimeS5;
  traffic_violations: TrafficViolations1;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "EvidenceRecording".
 */
export interface EvidenceRecording {
  artifact_index: ArtifactIndex1;
  sample_count: SampleCount;
  state: State1;
  total_bytes: TotalBytes;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "EvidenceSample".
 */
export interface EvidenceSample {
  byte_length: ByteLength1;
  frame: Frame9;
  index_offset: IndexOffset;
  kind: Kind14;
  metadata: Metadata1;
  sample_index: SampleIndex1;
  sha256: Sha2564;
  timestamp: Timestamp;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "CameraFormat".
 */
export interface CameraFormat {
  encoding: Encoding1;
  height: Height2;
  width: Width2;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "LidarFormat".
 */
export interface LidarFormat {
  channels: Channels1;
  encoding: Encoding2;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "BrowserDeviceView".
 */
export interface BrowserDeviceView {
  connection: BrowserConnectionView;
  device: DeviceView;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "BrowserConnectionView".
 */
export interface BrowserConnectionView {
  baseline_only: BaselineOnly;
  carla_readiness: CarlaReadiness;
  evidence_supported: EvidenceSupported;
  last_seen_at: LastSeenAt;
  protocol_compatible: ProtocolCompatible;
  protocol_version: ProtocolVersion5;
  recordings_supported: RecordingsSupported;
  remote_requests_enabled: RemoteRequestsEnabled;
  state: State3;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "DeviceView".
 */
export interface DeviceView {
  created_at: CreatedAt8;
  device_id: DeviceId1;
  display_name: DisplayName;
  owner: AccountIdentity;
  ownership_generation: OwnershipGeneration1;
  policy: DevicePolicy;
  policy_revision: PolicyRevision;
  revoked_at: RevokedAt;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "AccountIdentity".
 */
export interface AccountIdentity {
  account_id: AccountId;
  username: Username;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "DevicePolicy".
 */
export interface DevicePolicy {
  allow_evidence_read?: AllowEvidenceRead;
  allow_graphics_change: AllowGraphicsChange;
  allow_map_reload: AllowMapReload1;
  allow_raw_capture: AllowRawCapture;
  approved_agents: ApprovedAgents;
  max_duration_s: MaxDurationS;
  max_ticks: MaxTicks1;
  max_waiting_runs: MaxWaitingRuns;
  scopes: Scopes;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ApprovedAgent".
 */
export interface ApprovedAgent {
  agent_id: AgentId5;
  package_digest: PackageDigest;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "BrowserScenarioPreset".
 */
export interface BrowserScenarioPreset {
  description: Description2;
  family: Family;
  id: Id8;
  intended_stress: IntendedStress;
  label: Label;
  recipe: Scenario;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "CoordinationContracts".
 */
export interface CoordinationContracts {
  account_input: AccountInput;
  approve: PairingApproveInput;
  cancel_pending_approval: PendingApprovalCancelInput;
  claim: PairingClaimInput;
  create: PairingCreateInput;
  created: PairingCreated;
  credential_status: DeviceCredentialStatus;
  deny: PairingDenyInput;
  device: DeviceView;
  empty: EmptyInput;
  password_change: PasswordChangeInput;
  pending_approval_cancelled: PendingApprovalCancelled;
  session: BrowserSession;
  status: PairingStatus;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "AccountInput".
 */
export interface AccountInput {
  password: Password;
  username: Username1;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "PairingApproveInput".
 */
export interface PairingApproveInput {
  credential: Credential;
  expected_account_id: ExpectedAccountId;
  expected_revision: ExpectedRevision1;
  policy: DevicePolicy;
  request_id: RequestId7;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "PendingApprovalCancelInput".
 */
export interface PendingApprovalCancelInput {
  credential: Credential1;
  device_id: DeviceId2;
  owner_id: OwnerId;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "PairingClaimInput".
 */
export interface PairingClaimInput {
  code: Code2;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "PairingCreateInput".
 */
export interface PairingCreateInput {
  display_name: DisplayName1;
  private_secret: PrivateSecret;
  requested_scopes: RequestedScopes;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "PairingCreated".
 */
export interface PairingCreated {
  attempt_id: AttemptId;
  code: Code3;
  device_id: DeviceId3;
  expires_at: ExpiresAt;
  poll_interval_s: PollIntervalS;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "DeviceCredentialStatus".
 */
export interface DeviceCredentialStatus {
  device_id: DeviceId4;
  owner_id: OwnerId1;
  ownership_generation: OwnershipGeneration2;
  protocol_version: ProtocolVersion6;
  remote_execution_enabled: RemoteExecutionEnabled;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "PairingDenyInput".
 */
export interface PairingDenyInput {
  expected_revision: ExpectedRevision2;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "EmptyInput".
 */
export interface EmptyInput {}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "PasswordChangeInput".
 */
export interface PasswordChangeInput {
  new_password: NewPassword;
  old_password: OldPassword;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "PendingApprovalCancelled".
 */
export interface PendingApprovalCancelled {
  attempt_id: AttemptId1;
  completed: Completed;
  device_id: DeviceId5;
  outcome: Outcome;
  owner_id: OwnerId2;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "BrowserSession".
 */
export interface BrowserSession {
  account: AccountIdentity;
  csrf_token: CsrfToken;
  expires_at: ExpiresAt1;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "PairingStatus".
 */
export interface PairingStatus {
  attempt_id: AttemptId2;
  claimed_account: AccountIdentity | null;
  device_id: DeviceId6;
  expires_at: ExpiresAt2;
  requested_scopes: RequestedScopes1;
  revision: Revision5;
  state: State4;
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
 * via the `definition` "OptimizationResume".
 */
export interface OptimizationResume {
  expected_revision: ExpectedRevision3;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ReadinessReport".
 */
export interface ReadinessReport {
  carla: WorkflowReadiness;
  carla_connection_requested: CarlaConnectionRequested;
  checked_at: CheckedAt1;
  schema_version: SchemaVersion4;
  scope: Scope;
  synthetic: WorkflowReadiness;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "WorkflowReadiness".
 */
export interface WorkflowReadiness {
  checks: Checks;
  status: ReadinessStatus;
  summary: Summary2;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ReadinessCheck".
 */
export interface ReadinessCheck {
  detail: Detail;
  id: Id9;
  label: Label1;
  next_step: NextStep;
  status: ReadinessStatus;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "RemoteContracts".
 */
export interface RemoteContracts {
  command: Command;
  envelope: Envelope;
  reply: Reply2;
  view: RemoteCommandView;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ExecuteCommand".
 */
export interface ExecuteCommand {
  agent_id: AgentId6;
  evaluation_settings: EvaluationSettings;
  kind: Kind15;
  mode: Mode9;
  options: RemoteRunOptions;
  request_id: RequestId8;
  scenario: Scenario;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "RemoteRunOptions".
 */
export interface RemoteRunOptions {
  camera?: Camera1;
  lidar?: Lidar1;
  max_ticks: MaxTicks2;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "CancelCommand".
 */
export interface CancelCommand {
  kind: Kind16;
  request_id: RequestId9;
  simulation_id: SimulationId10;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "StatusCommand".
 */
export interface StatusCommand {
  kind: Kind17;
  request_id: RequestId10;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ResultCommand".
 */
export interface ResultCommand {
  kind: Kind18;
  request_id: RequestId11;
  simulation_id: SimulationId11;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "EvidenceRequest".
 */
export interface EvidenceRequest {
  artifact_index: ArtifactIndex2;
  kind: Kind19;
  limit: Limit2;
  offset: Offset3;
  request_id: RequestId12;
  resource: Resource1;
  sample_cursor?: SampleCursor;
  sample_index: SampleIndex2;
  simulation_id: SimulationId12;
  version: Version3;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ServerHello".
 */
export interface ServerHello {
  connection_generation: ConnectionGeneration;
  dead_timeout_s: DeadTimeoutS;
  device_id: DeviceId7;
  evidence_supported?: EvidenceSupported1;
  heartbeat_s: HeartbeatS;
  kind: Kind20;
  owner_id: OwnerId3;
  ownership_generation: OwnershipGeneration3;
  policy: DevicePolicy;
  policy_revision: PolicyRevision1;
  protocol_version: ProtocolVersion7;
  recordings_supported?: RecordingsSupported1;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "ClientReady".
 */
export interface ClientReady {
  connection_generation: ConnectionGeneration1;
  device_id: DeviceId8;
  evidence_supported?: EvidenceSupported2;
  kind: Kind21;
  ownership_generation: OwnershipGeneration4;
  protocol_version: ProtocolVersion8;
  recordings_supported?: RecordingsSupported2;
  remote_execution_enabled: RemoteExecutionEnabled1;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "RelayCommand".
 */
export interface RelayCommand {
  command: Command1;
  connection_generation: ConnectionGeneration2;
  device_id: DeviceId9;
  kind: Kind22;
  ownership_generation: OwnershipGeneration5;
  protocol_version: ProtocolVersion9;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "RelayReply".
 */
export interface RelayReply {
  connection_generation: ConnectionGeneration3;
  device_id: DeviceId10;
  kind: Kind23;
  ownership_generation: OwnershipGeneration6;
  protocol_version: ProtocolVersion10;
  reply: Reply1;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "RelayPing".
 */
export interface RelayPing {
  connection_generation: ConnectionGeneration4;
  device_id: DeviceId11;
  kind: Kind24;
  nonce: Nonce;
  ownership_generation: OwnershipGeneration7;
  protocol_version: ProtocolVersion11;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "RelayPong".
 */
export interface RelayPong {
  connection_generation: ConnectionGeneration5;
  device_id: DeviceId12;
  kind: Kind25;
  nonce: Nonce1;
  ownership_generation: OwnershipGeneration8;
  protocol_version: ProtocolVersion12;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "RunnerContracts".
 */
export interface RunnerContracts {
  cancel: RunnerCancelRequest;
  capabilities: RunnerCapabilities;
  execute: RunnerExecuteRequest;
  progress: SimulationProgress;
  result: SimulationResult;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "RunnerCancelRequest".
 */
export interface RunnerCancelRequest {
  protocol_version: ProtocolVersion13;
  request_id: RequestId13;
  simulation_id: SimulationId13;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "RunnerCapabilities".
 */
export interface RunnerCapabilities {
  carla_status: ReadinessStatus;
  checked_at: CheckedAt2;
  execution_modes: ExecutionModes;
  implementation_version: ImplementationVersion;
  local_features: LocalFeatures;
  max_concurrent_runs: MaxConcurrentRuns;
  platform: Platform;
  protocol_version: ProtocolVersion14;
  remote_execution_enabled: RemoteExecutionEnabled2;
  sdk_protocols: SdkProtocols;
  synthetic_status: ReadinessStatus;
}
/**
 * This interface was referenced by `PoltergeistContracts`'s JSON-Schema
 * via the `definition` "RunnerExecuteRequest".
 */
export interface RunnerExecuteRequest {
  agent_id: AgentId7;
  evaluation_settings: EvaluationSettings;
  mode: Mode10;
  options: SimulationOptions;
  protocol_version: ProtocolVersion15;
  request_id: RequestId14;
  scenario: Scenario;
}
