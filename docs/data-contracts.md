# Shared data contracts

Import Python models from `shared.models`. The simulator, evaluation engine, and
API use these models without importing CARLA. Their generated serialization schema
is `contracts/models.schema.json`; the matching TypeScript interfaces live in
`frontend/src/types/generated.ts`. Re-export modules retain the original frontend
import paths. Do not hand-edit the generated files.

## Validation and wire format

- Scenario `schema_version` is `"1.0"`. Unknown fields are rejected, including in
  nested objects. All physical numbers must be finite; booleans and numeric
  strings are rejected for physical values, counts, scores, and seeds.
- Python models accept omitted fields with documented defaults. Serialized
  payloads include those defaults and explicit nulls; generated TypeScript types
  describe that complete wire format. Future create/update request models can
  define narrower input shapes.
- UUIDs and timezone-aware ISO 8601 timestamps serialize as strings. New timestamps
  default to UTC. Invalid lifecycle timestamp ordering is rejected.
- Validation is performed at construction/deserialization. When changing an
  interdependent record, construct and validate the entire updated payload;
  Pydantic `model_copy(update=...)` does not validate updates. Model instances and
  their collections are mutable, so do not share a mutable scenario between runs.
- NaN and infinity are never valid. Missing or inapplicable TTC/clearance is
  `null`, not infinity or zero. Zero represents actual zero clearance/TTC.

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
the simulator adapter in section 4. Existing `location`, `event`, and `occlusion`
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
These describe requested behavior for the section 4 adapter to execute.

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
is reserved for the forthcoming lightweight adapter. Status invariants are:

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
paths, parent traversal, and drive/URL prefixes rejected. These are references;
file serving and storage containment checks belong to the storage/API sections.

`FailureReport` contains the simulation UUID, a scenario snapshot with its seed,
failure kind, severity, summary, and reproduction outcome. `reproduced: null`
means no reproduction result is available. The optimization section will define
how repeated runs establish reproducibility.
