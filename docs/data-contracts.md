# Shared data contracts

Import Python models from `shared.models`. The simulator, evaluation engine, and
API use these models without importing CARLA. Their generated serialization schema
is `contracts/models.schema.json`; the matching TypeScript interfaces live in
`frontend/src/types/generated.ts`. Re-export modules retain the original frontend
import paths. Do not hand-edit the generated files.

## Validation and wire format

The local capability snapshot is `ReadinessReport`, version
`poltergeist-readiness-v1`, with fixed scope `local_core`. Its synthetic and CARLA
workflows preserve `ready`, `missing`, `unsupported`, `not_running`,
`not_checked`, `unknown` and `error` as distinct statuses. Each identified check
includes a readable detail and optional next step. `ready` is scoped to checked
prerequisites; experiment execution remains explicitly `not_checked`.
See [API readiness semantics](api.md#local-setup-readiness). This is a current
local-core report, not a hosted-device protocol or packaged-companion guarantee.

`RunnerContracts` defines the proposed `poltergeist-runner-v1` capabilities,
explicit-mode execute request and cancel request alongside existing progress and
result models. It accepts local registration IDs rather than executable/package
paths. No dispatcher, transport, idempotency or remote authorization is implemented;
see [the runner boundary](runner-boundary.md).

- Scenario `schema_version` is `"1.0"`. Unknown fields are rejected, including in
  nested objects. All physical numbers must be finite; booleans and numeric
  strings are rejected for physical values, counts, scores, and seeds.
- Python models accept omitted fields with documented defaults. Serialized
  payloads include those defaults and explicit nulls; generated TypeScript types
  describe that complete wire format. The API's create/update request models
  accept narrower input shapes with defaults; consult `/openapi.json` for request
  requirements.
- UUIDs and timezone-aware ISO 8601 timestamps serialize as strings. New timestamps
  default to UTC. Invalid lifecycle timestamp ordering is rejected.
- Validation is performed at construction/deserialization. When changing an
  interdependent record, construct and validate the entire updated payload;
  Pydantic `model_copy(update=...)` does not validate updates. Model instances and
  their collections are mutable, so do not share a mutable scenario between runs.
- NaN and infinity are never valid. Missing or inapplicable TTC/clearance is
  `null`, not infinity or zero. Zero represents actual zero clearance/TTC.

## Autonomous agent messages

The versioned user-agent messages are documented in [the agent SDK guide](agent-sdk.md).
They include manifests, sensor declarations, episode resets, simulator-state
observations, optional diagnostics, and frame-associated vehicle controls.
Agent protocols `"1.0"` and `"1.1"` are independent of the scenario schema version. These types
are exported alongside the existing contracts; dashboard agent launch support
remains a later integration phase.

SDK 1.1 agents can declare `required_features: ["structured_scene"]`. Their reset,
observation, and response versions must match that manifest. `AgentSceneContext`
has its own version, explicit ground-truth/synthetic source, nullable world route,
classified objects with measured world box pose/extents/motion, map speed limit,
and optional semantic `AgentRoadRaster`. The raster declares ego-relative axes,
pixel origin, scale, and dimensions; exactly one validated flat class list or
lossless base64 uint8 payload is accepted. Unknown data remains null. SDK 1.0
observations retain their original behavior and do not carry scene context.
See [the reference AI pilot](reference-ai-pilot.md) for the first consumer.

## Scenario and actors

`Scenario` contains a name, optional template kind, map, seed, duration, fixed time
step, weather, actors, and scalar parameters. Seeds are integers from 0 through
2,147,483,647. Defaults are `Town03`, seed 0, duration 60 seconds, and a 0.05 second
time step. The step must be positive and at most 0.1 seconds; duration must permit
at least one tick. A seed enables controlled variation but does not by itself
guarantee determinism in CARLA.

Exactly one actor must have type `ego_vehicle`. Other types are `vehicle`,
`lead_vehicle`, `crossing_vehicle`, and `pedestrian`. Explicit actor IDs must be
unique within a scenario. The scenario loader now fills omitted IDs/blueprints
deterministically. Placements can use one of an absolute transform, a map spawn
point index, or a relative actor-local offset/yaw. Relative references must exist
and be acyclic. Unspecified placements and map-specific validity are resolved by
the simulator adapter. Existing `location`, `event`, and `occlusion`
fields remain descriptive metadata alongside typed events.

Distances/positions are metres, speeds are metres per second, time is seconds,
and rotation is degrees. CARLA uses a left-handed coordinate system (x forward,
y right, z up). Weather percentages range from 0 to 100 and sun altitude from
-90 to 90 degrees. Scalar parameters may be strings, booleans, integers, or finite
floats. The [scenario engine](scenarios.md) now defines template ranges and binds
their values to concrete weather, actor, and event fields. Generic custom
scenarios may retain application-specific scalar parameters.

`Scenario.events` contains discriminated `set_speed` and `brake` actions, actor
IDs, and elapsed `trigger_time_s` values before the end of the scenario. Speed
changes specify `speed_mps`; braking specifies positive `deceleration_mps2`.
These describe requested behavior; the runner dispatches them on fixed tick
boundaries, and physical controls approximate the requested motion. See
[simulation timing](simulation.md#timing-and-events).

## Telemetry and metrics

`TelemetryFrame` has a frame index, elapsed simulation time, ego state, other actor
states, ego controls, collisions, and traffic violations. Actor IDs must be unique
across ego and other actors in each frame. Velocity is in m/s; acceleration is
signed world-space m/s². Bounding box extents are positive half-lengths in metres.
Pose, velocity, and acceleration default to null when unobserved. Optional
`bounding_box_transform` is the world pose of the actual bounding-box center;
otherwise the box is assumed centered on the actor pose. A positive
`speed_limit_mps` permits measured speeding detection.
Collision impulse is newton-seconds. Throttle/brake range from 0 to 1 and steer
from -1 to 1. Control flags are booleans.

`SafetyMetrics` describes collision/violation counts, minimum clearance, minimum
TTC, maximum deceleration, hard-braking episode count, maximum completed stopping
time/distance, severity, and observation coverage. Missing counts and braking
values default to null. Event/actor lists also default to null (unobserved), while
an explicit empty list means observed with no entries. Collision/rule event IDs
deduplicate observations; rule `source_id` distinguishes signals/signs/zones.

`TelemetryFrame.rule_checks` identifies the `TrafficRule` families checked for
that observation. An explicit empty list makes no compliance claim; a subset
permits partial observations and known positive events. A full family list with
an observed violation feed establishes full rule coverage for that frame. For
compatibility with older traces, `rule_checks: null` retains the legacy meaning
that a non-null `traffic_violations` feed covers every family. A null violation
feed remains unobserved regardless of `rule_checks`. Producers should explicitly
enumerate checked families. Duplicate family entries are rejected.

`EvaluationCoverage` records observed frame and pair counts and trace gaps. Its
Python `complete` property is derived from those counts and is not a serialized
field. `EvaluationSettings` preserves versioned thresholds and weights.
`evaluate_safety` scores sensor collisions at 100, complete noncollision windows
from 0 through 99, and incomplete noncollision windows as null. Bare model defaults
are not evaluated results. See [methodology](methodology.md) for algorithms,
thresholds, coverage requirements, and event grouping.

## Simulation, results, and failures

`Simulation` stores a UUID, full scenario snapshot, agent, execution mode, status,
progress, timestamps, and optional error. Modes are `carla` and `test`; the latter
identifies the synthetic adapter. Status invariants are:

| Status | Required state |
| --- | --- |
| `queued` | No execution timestamps; progress 0 |
| `running` | Start timestamp; no finish timestamp; progress below 1 |
| `completed` | Start and finish timestamps; progress 1 |
| `failed` | Finish timestamp and nonempty error; may fail before starting |
| `cancelled` | Finish timestamp; may cancel before starting |

Errors are only allowed on failed records. A `SimulationResult` references a
terminal run and includes metrics, frame count, measured duration, and artifact
references. Artifact paths are relative to configured storage, with absolute
paths, parent traversal, and drive/URL prefixes rejected. `ArtifactStore` further
requires generated kind/run/artifact UUID layouts, matching media types, existing
files, and containment without symlink/junction traversal. API downloads select
zero-based artifact indexes from finalized results and sample indexes from raw
recording manifests. They do not accept caller-supplied filesystem paths. See
[artifact storage](persistence.md#artifact-layout-and-failure-handling).

Optional `SimulationResult.runtime` contains a `SimulationRuntime`: adapter,
server/client versions when applicable, actual map name, fixed time step, seed,
resolved actor spawn transforms, and the number of raw camera/LiDAR samples drained
by the runner. The adapter must match the run's mode. This preserves the normalized
scenario as the requested recipe while recording actual resolved placements.
Runtime metadata may be absent when setup fails before it can be measured; it is
not a recording or replay artifact. `telemetry_frames` includes the initial sample
at elapsed time zero, and `measured_duration_s` describes the returned trace.
Failed or cancelled runs retain collected metrics, but their noncollision
severity is null even if the shorter window was fully observed. A known collision
still scores 100. The runner creates no artifact files itself; explicitly passing
finalized output to `PersistenceService.persist_output()` stores telemetry and
returns a new result with valid storage-owned references.

`FailureReport` contains the simulation UUID, a scenario snapshot with its seed,
failure kind, severity, summary, and reproduction outcome. `reproduced: null`
means no reproduction result is available. Automated repetition thresholds are
defined in [optimization policy](optimization.md#reproduce-a-saved-failure).
Persistence requires a terminal
parent result with matching scenario/severity and positive evidence for the
selected failure kind; it does not infer failure from an unknown metric.

Database records retain complete JSON contracts, including explicit nulls,
evaluation settings/coverage, test mode, runtime, and scenario snapshots.
Repository wrappers `StoredScenario` and `StoredSimulation` add database revision
and storage timestamps. HTTP responses expose generated `ScenarioRecord` and
`SimulationRecord` DTOs containing those fields. Editing a saved scenario does
not change existing run recipes. Run updates/completion use revision checks and terminal records are
immutable. Raw recording manifests are storage format `raw-sensor-v1`, separate
from the shared result schema; `recording` references denote raw samples, not
encoded video or CARLA replay files. See [local persistence](persistence.md).

## API contracts and job settings

`shared.models.api` defines generated request/response DTOs without changing the
domain models' serialized shape. `ScenarioUpdate` wraps a normalized recipe and
positive integer `expected_revision`. `ScenarioGenerate` provides template,
seed, fixed scalar parameters, optional numeric ranges, and a `sample` flag.
Ranges require sampling. The scenario loader normalizes missing actor IDs and
blueprints before validating references.

`SimulationLaunch` requires exactly one of `scenario_id` or inline `scenario`.
Mode defaults to `carla`; `test` must be requested explicitly. Its `options`
(`SimulationOptions` in `shared.models.runtime`) and `evaluation_settings`
(`EvaluationSettings`) are persisted with the job. Camera/LiDAR requests require
CARLA mode. API jobs always use `baseline`; no uploaded code or arbitrary agent
name is part of this request.

`SimulationRecord.job` contains stored options/settings, a cancellation-request
flag, and a one-based queue position or null. It is null for manually stored runs
outside worker ownership. `SimulationProgress` returns lifecycle, revision,
timestamps, error, cancellation state, and queue position. Progress is persisted
at a throttled wall-clock interval and can lag the current tick. Cancellation is
a request, so callers continue polling until a terminal status. `QueueStatus`
exposes started/accepting/worker-alive flags, pending count, active UUID, and an
optional fatal error. Durable execution claims are internal worker metadata.

`TelemetryPage` contains `frames`, `total`, `offset`, and `limit` (1–1000). Pages
refer to finalized stored traces. `ReproductionUpdate.reproduced` is explicitly
true, false, or null; patching it does not perform a rerun. `AnalyticsSummary`
contains separate `ModeAnalytics` groups with lifecycle/evidence counts and mean
severity across all scored terminal results. Missing scores stay null; observed
metric counts can include partial lower bounds. See [API usage](api.md).

## Optimization contracts

`shared.models.optimization` defines generated contracts for a discriminated
`OptimizationRequest`: `SearchRequest`, `ReproductionRequest`, or `MinimizeRequest`.
HTTP requests include `kind`. Search inherits `ExecutionSpec` (mode, simulation
options, and evaluation settings); reproduction/minimization inherit the source
run's persisted worker-job settings and do not accept replacements.

`SearchRequest` holds template parameters/ranges, strategy, seed, trial budget,
optional target failure kind, and minimum severity. `ReproductionRequest` holds
a failure UUID, repeat count, and optional required-success threshold.
`MinimizeRequest` holds a failure UUID, total execution budget, and repeats per
baseline/candidate. Both also allow a minimum severity. Counts are strict bounded
integers; unknown fields and nonfinite scores/ranges are rejected.

`OptimizationRecord` retains the request, frozen execution specification, optional
source failure snapshot, revision, timestamps, cancellation request, engine
version, error, and outcome. Its states are `queued`, `running`, `completed`,
`failed`, `cancelled`, and `interrupted`. Queued/running states have no finish
time; running requires a start time. Completed/failed/cancelled/interrupted
states require a finish time, and only failed records carry an error. Explicit
revision-checked resume changes an interrupted record back to queued, preserving
its journal and budget.

`OptimizationTrial` links a zero-based journal number to one immutable child
simulation, its full scenario, role, candidate/repetition indexes, and assessment.
`assessed` distinguishes a pending assessment from an assessed unknown result.
`score` is the completed run's severity or null; `matches` is true, false, or null
under the requested evidence policy. `reason` explains the assessment, and an
optional `failure_id` references its saved positive evidence report.

`OptimizationView` returns an operation and its ordered trials.
`OptimizationOutcome` records consumed child executions, positive and unknown
counts, optional best run/failure/recipe, a tri-state reproduction decision, and
a summary. An operation can complete with unknown evidence or no best failure;
completion describes its requested procedure. See [optimization usage](optimization.md)
for objective selection, budgets, minimization, and interruption rules.
