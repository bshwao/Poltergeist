# Implementation plan and handoff

Work is split into ten sections. Each section should deliver usable code,
appropriate tests, updated documentation, and a handoff here. A checked item means
that section's scope is complete, not that the whole application is functional.

| Section | Scope | Status |
| --- | --- | --- |
| 1 | Foundation, configuration, dependency setup, shared data models, frontend build | Complete |
| 2 | Scenario loading, validation, parameter constraints, seeded generation, full starter templates | Complete |
| 3 | Collision, clearance, TTC, braking, rule metrics, severity scoring | Complete |
| 4 | CARLA world/actors/sensors, baseline agent, telemetry, lifecycle, cancellation, cleanup, test adapter | Complete |
| 5 | Database models, migrations, scenario/run/failure persistence, artifact storage | Complete |
| 6 | Scenario APIs, single-worker simulation queue, progress/cancellation, results/failures/analytics | Complete |
| 7 | Random/Optuna search, severity objectives, reproduction, failure minimization | Next |
| 8 | Dashboard navigation, scenario forms, validation, launch controls | Planned |
| 9 | Live status, telemetry/metrics charts, failure browsing, comparisons, available recording playback | Planned |
| 10 | Experiment/report CLI, exports, Docker integration, end-to-end checks, setup completion | Planned |

Target: a local application with one simulation worker. Authentication, public
hosting, and training a custom ML agent are separate extensions.

## Section 1 handoff

- Python contracts live in `shared/models/`; public imports use `shared.models`.
  See [data contracts](data-contracts.md) for units, lifecycle rules, and defaults.
- Python schemas and TypeScript are generated and tracked. Run the export and
  frontend generation after model changes; both have nonmutating drift checks.
- Root `.env` config is validated in `backend.config.Settings`. Relative paths
  are anchored to the repository. The CARLA connection helper reads those
  settings lazily or accepts explicit connection settings.
- The API has `/health` and CORS, with no simulator/database side effects.
- Core dependencies are locked separately from optional CARLA/search extras.
  Existing Python 3.14 works for core; use Python 3.12 for CARLA 0.9.16.
- Frontend dependencies are installed/locked, React/Vite types are configured,
  and type checking plus production build work. The UI is still the starter.
- Docker dependency-copy ordering is fixed and the CARLA image version is pinned;
  container builds and GPU/runtime integration remain section 10 validation.

## Section 2 handoff

- Public scenario APIs are exported from `scenarios`: file/text/template loading,
  validation, saving, parameter metadata/validation/sampling, and default/seeded
  scenario construction. See [scenario engine usage](scenarios.md).
- YAML/JSON parsing rejects duplicate keys and unsafe/invalid document forms.
  Normalization fills deterministic IDs and blueprints without mutating inputs.
- Parameter specs define bounds, defaults, units, and descriptions. Built-in
  parameter values are bound to actual actor/weather/event fields; loaded records
  with contradictory values are rejected.
- All four templates now contain explicit actors, map-relative placement data,
  parameters, and typed timed events. Shared contracts and generated frontend
  types include map-index/relative spawns and speed/braking events.
- Seeded generation uses isolated RNG state, permits fixed values and narrowed
  ranges, and produces self-contained save/load records. Batch seeds are unique
  consecutive values with wraparound, and preserve prefix stability.
- Spawn-point 0 and relative paths are symbolic starter layouts. Actual map,
  lane/junction, ground/overlap, visibility, and physics validation remain for
  the section 4 adapter. No CARLA execution is claimed.

## Section 3 handoff

- Public `evaluation` APIs provide whole-window evaluation, pair clearance/TTC,
  event summaries, braking/stopping summaries, severity calculation, and input
  validation. See [methodology](methodology.md) for examples and assumptions.
- Geometry uses full 3D oriented boxes and a constant-linear-velocity/fixed-
  orientation TTC prediction. Missing boxes are not replaced with point actors.
- Sensor contacts and map-reported ego rule events are grouped into episodes;
  stable event IDs reject conflicting replays. Speeding can also be derived from
  velocity/limit measurements, with partial rule coverage retained.
- Measured or interval-derived braking supports reverse motion and separates
  cornering acceleration. Stopping metrics use completed continuous observations.
- Telemetry poses/velocities/accelerations and event/actor feeds now default to
  null. Empty feeds mean observed empty. Existing consumers must handle those
  nulls; generated JSON Schema/TypeScript are updated accordingly.
- `SafetyMetrics` includes observation coverage, completed-stop maxima,
  hard-braking count, and a snapshot of `EvaluationSettings`. Unknown observations
  stay null. Only complete noncollision windows receive a severity score; known
  sensor collisions score 100 even with partial coverage.

## Section 4 handoff

- `SimulationRunner` performs one run per instance using a copied scenario or
  queued simulation, fixed ticks, stable timed-event ordering, baseline/custom
  agents, cooperative cancellation, snapshot callbacks, finalization, cleanup,
  and evaluation. `RunOutput` returns the terminal result plus finalized telemetry
  in memory. See [simulation usage](simulation.md) for timing and callback details.
- CARLA imports lazily and requires matching 0.9.16 versions. The world manager
  configures synchronous steps/substeps/weather, guards map reload behind explicit
  opt-in and an empty dynamic world, and restores the original map/settings/weather.
  Owned resource cleanup continues through failures and retains failed handles
  for retry; foreign road users are observed but never destroyed.
- Actors use validated blueprints, dependency-ordered placements, map/ground/lane
  and built-in layout checks, native snapshots, and true composed world bounding
  boxes. Actual map/version/seed/step/resolved spawns are retained in optional
  `SimulationRuntime`. Built-in spawn index 0 is still a symbolic anchor and may
  be rejected; no real Town03 layout or physical behavior is certified.
- Collision collection uses a periodic IMU heartbeat, bounded frame queues and
  waits, a collision settling window, and final late-event merging. Optional
  RGB/LiDAR capture streams deliver bounded raw byte samples through callbacks.
  Camera capture requires rendering enabled. The independent-stream timing
  assumption needs live validation; returned telemetry supersedes provisional
  frame callbacks. Section 4 does not persist or encode captures.
- The rule monitor supplies map-aware speeding, red-light crossing, stop-trigger,
  wrong-way, and sidewalk observations with stable source/event IDs. Explicit
  `rule_checks` preserves partial coverage when APIs or state are unavailable;
  legacy null retains the previous full-feed convention. Failed/cancelled runs
  keep noncollision severity null; known collisions still score 100.
- The deterministic synthetic adapter exercises the same runner/evaluator with
  explicit `mode: "test"`, virtual spawn anchors, approximate controls/kinematics,
  and sampled box contacts. It has no real road network, meshes, continuous
  contact physics, signals, perception, or renderer. Optional capture requests
  require CARLA. It cannot certify a scenario's map validity or real driving safety.
- `python -m simulator` accepts a template or saved scenario and explicit test/
  CARLA mode. It emits result JSON or writes the requested result file. The API
  still exposes no simulation launch routes, and the dashboard is unchanged.

## Section 5 handoff

- `Database` explicitly initializes local SQLite with packaged transactional
  migrations, foreign keys, and history/checksum/schema verification. Unsupported
  engines, unversioned populated databases, and incompatible schemas are rejected.
  Imports, service construction, API startup, and `/health` create no database or
  artifact directories. Use `python -m backend.persistence init` to opt in.
- `ScenarioRepository`, `SimulationRepository`, and `FailureRepository` use
  caller-owned short transactions, validated full contract JSON, bounded stable
  listing, and indexed lifecycle fields. Scenario edits/deletes and run updates
  use expected revisions. Run snapshots/identity stay immutable; terminal results
  cannot be replaced, and referenced scenarios cannot be deleted. Null metrics,
  test mode, settings/coverage, and available runtime provenance round-trip.
- `PersistenceService` creates queued records from recipes or saved-scenario IDs,
  validates finalized runner timelines including completed execution windows,
  publishes artifacts, and saves terminal output with a revision check. It can
  reload the result and finalized telemetry with artifact integrity checks.
  Repository completion alone has no filesystem context; use the service for
  runner output. See [local persistence](persistence.md) for runnable examples.
- `ArtifactStore` writes NDJSON telemetry and JSON reports under generated
  kind/run/artifact UUID paths, with containment and symlink/junction checks.
  `CaptureWriter.write` subscribes to `on_sensor`, streams raw bytes/metadata,
  and publishes `raw-sensor-v1` manifests with hashes, native frame/time, dimensions
  or channels, and encoding metadata. It creates no encoded video or replay.
  Healthy completed/cancelled captures must match telemetry frame IDs and runtime
  sample counts; failed captures are discarded while partial telemetry is saved.
- Files publish before the revision-checked database completion. Exception cleanup
  removes only this attempt's confirmed unreferenced artifacts and retains files
  when commit state is uncertain. Process/power loss can leave staging or orphans;
  there is no durable reconciliation journal or automatic garbage collection.
  Filesystem and SQLite commits are not one atomic transaction.
- Failure reports require a scored terminal parent, matching scenario/severity,
  and positive evidence for their selected kind. Reproduction remains an explicit
  true/false/unknown observation, with search/reproduction policy in section 7.
  The synchronous runner remains independent of database ownership. No feature
  API worker queue or dashboard changes were added; live CARLA remains unverified.

## Section 6 handoff

- The FastAPI routes now provide pure template/validation/seeded generation,
  revision-checked saved scenario CRUD, queued baseline-agent launches,
  progress/cancellation, finalized results/telemetry/artifacts, evidence-backed
  failure records/reproduction flags, and analytics. Routes are rooted at
  `/scenarios`, `/simulations`, `/failures`, and `/analytics`, without `/api`.
  See the [API walkthrough](api.md#generate-save-launch-and-read-a-test-run).
- `ApiRuntime` lazily initializes persistence for saved-data features and starts
  the worker for queue/lifecycle requests. Imports, app startup, `/health`, and
  pure scenario endpoints remain free of database/storage/CARLA side effects.
  Launch requires one saved UUID or inline recipe; mode defaults to CARLA and
  never falls back to synthetic test execution. Options/settings use generated
  shared DTOs and are durable. API jobs use baseline agents only.
- `SimulationService` provides one FIFO worker, pending capacity excluding the
  active run, throttled revision-checked progress, cooperative cancellation,
  capture streaming, and finalized persistence. A nonblocking OS lease protects
  each SQLite file; in-memory databases use object ownership. Shutdown requests
  active cancellation and waits up to 15 seconds by default, preserving waiting
  jobs. A blocked native call retains its database/lease until it exits.
- Migration 2 adds durable job configuration, cancellation requests, FIFO order,
  and execution claims. A claim is written before runner construction/native
  setup. Restart resumes unclaimed waiting jobs, fails interrupted claimed/running
  jobs, and does not adopt manually stored runs. Ordinary runner failures let the
  queue continue; storage/revision failures expose a fatal queue error and stop
  acceptance until restart after the underlying issue is resolved. Abrupt-loss
  artifact/native cleanup limits remain as documented in section 5.
- SQLite write transactions reserve writer intent before repository reads, while
  explicit read-only scopes use read transactions. Simulation ticks and captures
  stay outside those transactions. This handles progress/cancellation concurrency
  without a deferred read-to-write lock upgrade deadlock; expected revisions
  remain authoritative for stale writes.
- Artifact access uses result-owned indexes and contained storage references;
  raw sample downloads validate manifest/sample integrity. Telemetry responses
  are paginated to 1000 frames but currently validate/read the full trace in
  memory. Raw captures remain bytes/manifests rather than encoded playback.
  Analytics scan all history, separate CARLA/test modes, include every scored
  terminal result in mean severity, and retain unknown counts/scores. Reproduction
  patches record explicit observations without executing a rerun.
- Integrated tests, contract checks, the frontend build, and a full synthetic API
  save/launch/read/download/reopen smoke check passed. The dashboard remains its
  starter UI, and live CARLA and container execution remain unverified.

## Resume with section 7

Implement bounded random/Optuna search over scenario parameters, severity
objectives that handle unscored runs, explicit repeated-run reproduction policy,
and failure minimization. Reuse normalized scenario generation and durable worker
launches; preserve recipe/seed/options/evaluation snapshots and mode distinctions.
Keep one CARLA tick owner and avoid database transactions during execution.

Search should explain its trial budget, cancellation, objective values, and
reproduction evidence without converting unknown observations into successful
safety outcomes. Store supported failure records through existing evidence
checks. Define how an interrupted search resumes and how it relates to the
worker's existing claimed-job interruption policy. UI remains sections 8–9.

## Verification

See [setup](setup.md) for exact commands. Run Python tests, schema drift check,
frontend contract drift check, and frontend production build before handing off.
Tests use deterministic synthetic runs and fake CARLA APIs; real CARLA simulation
behavior remains unverified.

Section 1 validation: 117 Python tests passed on Python 3.14. After updating the
HTTP test client to match Starlette, all 35 backend/configuration/CARLA-helper
tests also passed with warnings treated as errors. Schema drift check, generated
TypeScript drift check, TypeScript compilation, Vite production build, Python
dependency consistency, and Git whitespace checks passed. CARLA execution and
container builds were not run.

Section 2 validation: 284 Python tests passed with warnings treated as errors.
Schema and frontend contract drift checks, TypeScript compilation, Vite
production build, and Git whitespace checks passed. The new checks cover safe
parsing and actor normalization, placement/event references, parameter bounds,
seeded generation, binding consistency, and saved-scenario round trips. Real
CARLA map validity and event execution remain section 4 work.

Section 3 validation: 508 Python tests passed with warnings treated as errors.
The suite includes analytical 3D geometry/TTC cases, measured and inferred
braking/stops, collision/rule episode boundaries and replay/source transitions,
unknown-data coverage, scoring thresholds, and JSON round trips. The tracked
schema and TypeScript contracts were regenerated; frontend contract checks,
TypeScript compilation, Vite production build, and whitespace checks passed.
No real CARLA execution or map-aware sensor detection was performed.

Section 4 validation: 672 Python tests passed with warnings treated as errors.
Tests cover lifecycle/event scheduling, cancellation,
agent controls, mutation isolation, failure cleanup, synthetic scenarios, CARLA
adapter orchestration, world/actor restoration, callback timing/overflow, and
partial map-rule coverage. The schema/TypeScript contracts were regenerated,
frontend contract checks and the production build passed, and a full 30 second
synthetic sudden-braking CLI run completed with 601 telemetry frames. Schema and
frontend contract drift checks also passed. CARLA is not installed in the
current Python 3.14 environment; real client/server execution, GPU behavior, map
anchors, and sensor timing have not been verified.

Section 5 validation: 842 Python tests passed with warnings treated as errors.
Schema/frontend contract drift checks and the frontend production build passed.
The persistence tests exercise SQLite initialization/migration rejection,
transactions/revisions, immutable snapshots/results, failure evidence, contained
artifact paths, raw capture integrity, finalized trace validation, and cleanup
after interrupted writes. A Windows concurrent-directory resolution issue was
fixed with strict ancestor checks; repeated shared/independent-store concurrency
checks completed 480 artifact publications. The explicit initialization CLI
succeeded in isolated storage. A full 30 second synthetic sudden-braking run
persisted 601 telemetry frames and a failure report, then loaded both unchanged
after reopening SQLite. Live CARLA and container integration remain unverified.

Section 6 validation: 973 Python tests passed with warnings treated as errors.
Python schema and generated TypeScript drift checks and the frontend production
build passed. Tests cover scenario CRUD/generation, job ordering/capacity,
cancellation/restart and durable claims, migration 1-to-2 upgrades, exclusive
worker ownership, concurrent SQLite progress/cancellation writes, failure evidence
and reproduction flags, analytics/unknowns, stored settings, artifact/sample
access, and initialization boundaries. A full 30 second synthetic API smoke run
generated and saved a recipe, launched test mode, polled completion, loaded the
601-frame result, paged telemetry, downloaded all 601 NDJSON lines, saved a
near-miss failure, and queried test-mode analytics. Closing and recreating the API
against the same database reloaded the identical result and failure. Live CARLA
and container integration remain unverified.
