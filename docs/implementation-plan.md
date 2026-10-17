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
| 7 | Random/Optuna search, severity objectives, reproduction, failure minimization | Complete |
| 8 | Dashboard navigation, scenario forms, validation, launch controls | Complete |
| 9 | Live status, telemetry/metrics charts, failure browsing, comparisons, available raw capture inspection | Complete |
| 10 | Experiment/report CLI, exports, Docker integration, end-to-end checks, setup completion | Complete |

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

## Section 7 handoff

- `/optimizations` provides random/optional Optuna search, repeated reproduction,
  local minimization, operation/trial inspection, cancellation, and revision-checked
  resume. `OptimizationService` submits child runs through the existing native
  worker, preserving one tick owner. Requests and responses use generated shared
  contracts. See [optimization usage and policy](optimization.md).
- Sampling is seeded and bounded over validated template parameter spaces.
  Optuna uses its real 4.x TPE ask/tell interface; 4.9.0 is exercised locally and
  remains an optional extra outside the core lock. Only completed, scored runs
  provide objectives; unknown/failed/cancelled objectives never become zero.
  Target kinds use coverage-aware positive evidence, and eligible positive
  trials create checked failure records atomically with their assessments.
- Reproduction snapshots the source failure and its exact recipe/seed, mode,
  simulation options, and evaluation settings. Persisted worker-job metadata is
  required. The default policy requires all three repetitions; configurable
  thresholds preserve true/false/unknown evidence. Only a completed reproduction
  updates the source report's reproduction flag, in the completion transaction.
- Minimization first confirms the baseline, then tests deterministic valid
  lower-complexity recipes. Every candidate repeat must confirm the failure.
  Template bindings and actor dependencies remain valid; the result is a tested
  greedy local reduction, not a global minimum. Baseline/candidate executions
  share one budget, including failed/cancelled children; a new incomplete repeat
  group is never launched.
- Migration 3 adds revisioned operation snapshots and ordered child journals.
  Child run/job/link creation is atomic. Startup marks unfinished parents
  interrupted; explicit resume checks engine version and deterministic replay,
  reuses linked children, and retains the original budget. The simulation worker
  may already recover an existing unclaimed linked child before parent resume;
  no new child trials are created until resume. Interrupted claimed children
  follow the worker's existing failed-result recovery rule.
- Controller shutdown interrupts active work before native worker shutdown.
  User cancellation is durable and propagates to linked unfinished runs. No
  database transaction spans execution, and existing artifact/native recovery
  limits still apply. Dashboard controls, live CARLA, and container integration
  remain unverified or later work as described below.

## Section 8A handoff

- The responsive dashboard shell provides Overview and Scenarios hash navigation,
  keyboard focus/skip navigation, and a live API connection indicator. Health is
  process liveness only; the overview presents loaded template counts rather
  than invented simulation or failure statistics. See [dashboard usage](dashboard.md).
- The scenario workspace fetches all four template recipes and parameter
  metadata through the typed API client. Inputs use API-defined bounds, units,
  defaults, and integer rules, with a strict 31-bit nonnegative seed. Generation
  uses fixed parameter values and returns a full normalized recipe without
  launching a simulation or saving a scenario.
- The full recipe JSON editor supports custom edits and server normalization.
  Local field/JSON errors and structured server validation paths are displayed;
  unexpected service diagnostics are replaced with useful request errors.
  Form changes and JSON edits invalidate the displayed validation state.
- Superseded requests are aborted and old responses are ignored. Navigating
  between Overview and Scenarios retains the draft, as do failed or empty
  catalog refreshes after a successful load. Reloading the browser discards the
  unsaved draft; there is no browser storage or saved-scenario integration yet.
- Dependency-free frontend tests use Node's built-in runner and the existing
  TypeScript package. Generated shared types remain the API contract source.
  Live CARLA geometry, driving behavior, and sensor timing remain unverified.

## Section 8B handoff

- The opt-in saved-scenario library lists eight records per page with next/previous
  controls and refresh. Loading fetches the current record rather than using the
  list snapshot. Replacing unsaved work requires an in-page confirmation. Custom
  recipes use the JSON editor without an unrelated template parameter form.
- Validated recipes can be saved as new records, updated with their expected
  revision, or saved as separate copies. HTTP 409 keeps the workspace draft and
  offers the latest revision or a copy. Delayed save responses preserve newer
  JSON and only advance the matching record's association. Confirmed deletion
  uses the listed revision, retains the workspace draft, and detaches its saved
  association. The API can reject deletion of records referenced by runs.
- A recipe name input edits the JSON and requires validation again. Validation
  synchronizes template parameters and seed from normalized JSON; regeneration
  preserves the current name. Loading a saved record supplies validated JSON.
  Overview/Scenarios navigation retains drafts, while browser reload discards
  unsaved edits. Saved records persist in SQLite; there is no autosave or browser
  storage.
- Execution mode starts unselected and requires an explicit CARLA/test choice.
  Execution and evaluation foldouts expose all current shared settings with
  field bounds and cross-field rules, including severity weights totaling 100.
  Synthetic mode disables camera/LiDAR and clears captures selected in CARLA
  mode. Changing execution settings does not invalidate the recipe validation.
- Launch submits a copied inline recipe and settings through the existing API,
  preserving the exact validated workspace even if its saved record changes.
  Consequently, the run stores its recipe snapshot without a saved-scenario
  foreign-key association. Later workspace edits cannot change an in-flight
  submission. HTTP 202 shows the run ID, mode, status, and available queue position
  at acceptance, without polling or implying successful execution.
- **Check queue** explicitly starts/recovers the lazy worker and returns an
  advisory snapshot; opening the saved library initializes persistence. Health,
  template generation, and validation remain pure. HTTP 409/429/503 and other
  request failures are surfaced without automatic mutation retries. An uncertain
  network response directs users to inspect API run history before retrying.
  Existing backend contracts are unchanged, and live CARLA remains unverified.

## Section 9 handoff

- Simulations and Failures are mounted dashboard pages. Launch receipts and
  failure parent-run links use bookmarkable `#simulations/<UUID>` deep links.
  Saved run history pages ten records, filters by mode/status, and refreshes
  explicitly so uncertain launch acceptance can be inspected before retrying.
- The selected run polls progress serially until a terminal status, then loads
  finalized output. Request timeouts and capped retry delays retain the last
  confirmed observation; cancellation updates cannot be overwritten by older
  revisions. Navigation aborts polling and outstanding requests. Cooperative
  cancellation distinguishes an accepted request from terminal cancellation.
  Run detail retains the original recipe, execution settings, and provenance.
- Final safety cards preserve unknown values, real zeroes, measurement units,
  frame/pair coverage, partial traces, and saved scoring settings. Motion charts
  request at most 200 finalized frames per page and identify the displayed
  window. Direct measured velocity/deceleration series break at missing/skipped
  observations and gaps above the recorded timing policy; the frame inspector
  exposes exact values and controls. They do
  not replace backend TTC/clearance or aggregate braking evaluation.
- Indexed artifact downloads avoid accepting storage paths from page inputs.
  Raw capture inspection loads manifests and individual sensor samples only on
  explicit actions, verifies SHA-256/byte length, and aborts superseded loads.
  Camera frames decode BGRA8 to actual canvas pixels. LiDAR reports finite point
  counts and sensor-frame bounds under an explicit little-endian assumption.
  Empty captures and synthetic-mode limitations remain visible. Individual raw
  samples are not encoded video or reconstructed simulation replay.
- Failure history pages eight records, filters by parent run/reproduction state,
  and reads each report with its actual parent result, recipe, coverage, mode,
  settings, and runtime. Manual reproduction flags are explicit evidence updates
  and do not rerun a scenario. Two reports can be selected across pages for
  comparisons, with unknowns retained and compatibility notes for modes, agents,
  options, scoring differences, partial observations, or missing results.
  A finalized run can save a report from scored positive evidence using its
  exact recipe/severity and a user-written summary. Reproduction begins unknown;
  duplicate submissions and stale responses are guarded without automatic
  mutation retries.
- Overview loads timestamped analytics on demand from all persisted history.
  CARLA/test groups, scored/unscored results, status counts, reports, and observed
  event denominators remain separate. Mean severity under varied recorded
  settings is not presented as a controlled safety comparison.
- The existing single backend worker remains the simulation owner. Optimization
  search/reproduction/minimization APIs remain available without new dashboard
  controls. Live CARLA and browser visual checks remain unverified. See
  [dashboard usage](dashboard.md) for the full flow and limitations.

## Section 10 handoff

- `scripts.run_experiment` submits explicitly selected CARLA/test batches through
  the existing API worker. All recipe/seed/settings inputs are validated before
  execution, runs submit sequentially, and receipts retain accepted IDs,
  terminal results, partial/unknown metrics, coverage, and provenance. Request
  and batch deadlines, uncertain acceptance, Ctrl+C cancellation, nonzero exit
  codes, and atomic non-clobber output are explicit. It creates no second worker.
- `/reports/simulations/{id}` reads finalized persistence without starting the
  worker and exports JSON, CSV, or printable HTML. The full immutable record and
  failure observations retain nulls, settings, coverage, and runtime. Finalized
  telemetry is JSON-only and opt-in. CSV uses lossless JSON Pointer/value cells;
  user text is protected from spreadsheet formula execution. HTML escapes user
  fields and has restrictive content headers. `scripts.export_report` streams
  complete downloads to atomic output files without replacing existing files
  unless `--force` is selected. Both CLIs close HTTP error responses explicitly.
- Result pages expose all three report downloads. The API client also accepts
  safe root-relative proxy prefixes such as `/api`. Page titles follow navigation.
  A real mobile check found and fixed overflow from absolute hidden table labels;
  scroll containers now own those positions and comparison IDs wrap.
- The default Docker stack contains a runtime-only locked API image, built
  dashboard/nginx same-origin proxy, one backend process, health checks, graceful
  shutdown, and a durable database/artifact volume. CARLA/GPU is an explicit
  optional profile and client-image build option. Runtime dependency pins are in
  `requirements-core.lock`. Configuration does not require a local `.env` file.
- `poltergeist-experiment` and `poltergeist-report` console entry points are
  packaged, and `make check` includes frontend tests. README/setup/API/architecture
  and [experiment](experiments.md), [report](reports.md), [Docker](docker.md), and
  [dashboard](dashboard.md) guides describe the completed local workflow.

All ten code sections are implemented. The local synthetic workflow is verified;
live CARLA map placement, sensor delivery, GPU execution, and actual container
build/runtime still require a suitable external environment. Docker Desktop's
engine pipe was missing and its official start command failed because the launcher
registry key was absent; no system installation or WSL changes were attempted.
Dedicated search UI controls and encoded/reconstructed replay remain optional
extensions, with search/reproduction/minimization already available through APIs.

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

Section 7 validation: 1,159 Python tests passed with warnings treated as errors.
Python schema and generated TypeScript drift checks, frontend production build,
dependency consistency, and Git whitespace checks passed. Coverage includes real
Optuna 4.9.0 suggestions/replay, unknown objectives, repeated reproduction,
baseline/candidate minimization budgets, migration 1/2-to-3 preservation,
transactional trial/failure writes, cancellation races, policy-version checks,
shutdown and partial-group resume without duplicate children. A synthetic API
search measured a near miss across 601 frames and reproduced the identical score
twice without injected events. API reproduction/minimization records and source
flags reloaded unchanged after reopening storage. A transient Windows directory
rename denial found during integration now has bounded publication retries,
with cleanup and competing-owner regressions. Live CARLA and container execution
remain unverified.

Section 8A validation: 33 frontend tests and 116 focused Python scenario/API and
contract tests passed; Python warnings were treated as errors. JSON Schema and
generated TypeScript drift checks, TypeScript compilation, the production build,
and Git whitespace checks passed. Browser checks against the running API covered
template selection, edited parameters and seed, generation, local numeric/JSON
errors, server validation errors, normalized actor IDs, and validation state.
Overview/Scenarios navigation, API outage/recovery, and an empty-catalog fixture
all preserved the existing draft. Desktop (1280 × 900) and mobile (390 × 844)
layouts were checked, with no horizontal overflow in the mobile view. Saved
scenario and launch controls remain section 8B; no live CARLA run was attempted.

Section 8B validation: 90 frontend tests and all 1,159 Python tests passed;
Python warnings were treated as errors. JSON Schema and generated TypeScript
drift checks, TypeScript compilation, the production build, and Git whitespace
checks passed. Matching React test-renderer interaction tests exercise real
components, including dirty-load confirmation, custom/bound forms, stale save
responses, conflict recovery, deletion/load interlocks, original delete revision
checks after automatic refresh, pagination, explicit modes, settings validation,
queue errors, immutable launches, duplicate-submit protection, and late receipts.
The typed client also exercised an isolated running API: create/read/update/copy/
delete, stale revision rejection, queue inspection, and a synthetic run accepted
as queued and completed with five telemetry frames and the requested settings.
Browser automation could not initialize because its kernel assets path was
unavailable, including after reset; no new browser or visual verification is
claimed for 8B. Recheck the new library/launch layout when that tool is available.
The earlier 8A browser checks remain recorded above. Live CARLA remains unverified.

Section 9 validation: 194 frontend tests and all 1,159 Python tests passed;
Python warnings were treated as errors. JSON Schema and TypeScript contract
drift checks, TypeScript compilation, production build, dependency consistency,
and Git whitespace checks passed. React interaction tests cover draft retention
across navigation, lazy data pages, serial polling, cancellation/revision races,
history filters and pagination, nullable metrics/coverage, timed chart gaps,
bounded telemetry pages, evidence-backed report creation, manual reproduction,
and comparisons. Raw capture tests verify SHA-256/length, BGRA8 canvas pixels
through a canvas test double, little-endian LiDAR bounds, and superseded reads.
An isolated real HTTP API smoke test used the typed client to launch/read/filter
synthetic runs, page telemetry, cancel waiting/active runs, retain unknown partial
severity, create a naturally observed near-miss report, patch false/unknown
reproduction observations, query analytics, and download indexed telemetry.
Explicitly labelled synthetic camera/LiDAR byte fixtures passed manifest,
sample download, and SHA-256 checks; these were not live CARLA captures.
Browser automation failed to initialize because its kernel assets path was
unavailable; no section 9 browser or visual verification is claimed. Live CARLA
and container integration remain unverified.

Section 10 validation: all 1,287 Python tests passed with warnings treated as
errors, and all 197 frontend tests passed. Contract drift checks, TypeScript
compilation/production build, dependency consistency, wheel packaging, installed
console entry-point help, Compose default/CARLA profile parsing, and Git whitespace
checks passed. Full-suite checking caught unclosed HTTPError wrappers in both
new CLIs; production cleanup and garbage-collection regressions now pass.

A real synthetic worker integration test verifies generate/save/launch/poll,
positive failure evidence, all three reproduction states, exports/telemetry,
immutable recipes after library edits, and identical evidence after reopening
SQLite/artifacts without starting a worker to read reports. A real HTTP CLI batch
completed two 601-frame runs with seeds 1/2 and recorded settings. JSON, CSV, and
HTML files exported through the running API and retained null TTC, synthetic
mode, settings, coverage, failures, and exact optional telemetry.

Browser checks at 1280 × 900 and 390 × 844 covered generation, edited-name
validation, saving, explicit synthetic launches, live-to-final result tracking,
draft retention, failure creation/detail/manual unknown flags, two-report
comparisons, mode-separated historical analytics, and telemetry pagination through
frames 201–400. Mobile document
width matched its viewport after the hidden-label fix. Browser console checks
showed no errors. The browser download-event check timed out and reset its tool;
no completed browser file transfer is claimed. Real API/CLI report transfers and
their files were verified separately. Live CARLA and container builds/runtime
remain unverified for the environment reasons above.
