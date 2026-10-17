# Architecture

Poltergeist generates CARLA scenarios, runs agents through them, evaluates telemetry,
and exposes results through the backend API and dashboard.

The shared boundary is `shared.models`: Pydantic models validate scenarios,
telemetry, run lifecycle records, results, and failures. A deterministic schema
export generates the frontend types. See [data contracts](data-contracts.md).

The API factory `backend.main.create_app()` builds the application with validated
settings and CORS. `python -m backend` uses configured host/port. Bootstrapping
does not open a CARLA connection, initialize a database, or create storage.

The section 2 scenario engine safely loads and normalizes YAML/JSON, validates
template parameter bindings, and produces seeded configurations with typed
events and symbolic actor-relative geometry. It is independent of CARLA. See
[scenario engine usage](scenarios.md).

The section 3 evaluator consumes validated telemetry, groups ego events, computes
3D clearance/TTC and braking/stopping metrics, and returns coverage-aware
`SafetyMetrics` with versioned evaluation settings. It has no CARLA dependency.
Explicit per-frame rule coverage keeps unavailable map checks from appearing as
compliance. See [methodology](methodology.md).

The section 4 `SimulationRunner` owns one synchronous run: scenario snapshot,
fixed-step event dispatch, driving controls, cooperative cancellation, sensor
finalization, resource cleanup, and evaluation. It returns a terminal result plus
the finalized telemetry in memory. Frame/progress/raw sensor callbacks let
orchestration and storage subscribe without putting database work in adapters.
Callback failures fail the run and still execute cleanup.

The adapter boundary in `simulator.protocols` separates the runner and agents from
CARLA. `CarlaSimulationAdapter` imports its optional client lazily, checks 0.9.16
versions, manages a synchronous world, validates/spawns actors, and collects native
snapshot and sensor observations. It restores prior world settings/weather and
destroys only resources it owns. The `TestSimulationAdapter` supplies deterministic
synthetic kinematics without a server. Test results retain `mode: "test"`; its
virtual geometry does not certify CARLA map validity or physics behavior.

`BaselineAgent` regulates speed/heading and brakes for observed hazards or a red
light. `BaseAgent` and `CustomAgent` support injected controllers. This development
baseline has no perception pipeline or complete route/intersection planner.
Runtime metadata records versions, actual map, seed, step, and resolved spawns.
See [simulation usage and limitations](simulation.md).

Section 5 adds SQLAlchemy models, packaged versioned SQLite migrations, and
transactional repositories. Saved scenarios are editable with revision checks;
runs retain independent immutable recipes and terminal results. Full contract
JSON preserves unknown observations, execution mode, settings, and runtime
provenance alongside indexed lifecycle fields. Failure reports reference scored
terminal runs and require matching recipes/severity and positive failure evidence.

`PersistenceService` coordinates queued records, finalized `RunOutput`, and the
`ArtifactStore`. It publishes finalized telemetry and optional streamed raw
capture files before completing the database record with a revision check.
Exceptions remove only this attempt's confirmed unreferenced files; uncertain
commit outcomes retain them. Database/filesystem commit is not atomic, and
abrupt termination may leave staging or orphaned files for manual reconciliation.
References use generated kind/run/artifact paths with containment/link checks.
See [persistence usage and limits](persistence.md).

Initialization is explicit through `PersistenceService.initialize()` or
`python -m backend.persistence init`, and lazy on the first persistence-backed
API request. Constructing services and bootstrapping the API do not connect or
create directories. `/health` and pure scenario template/validation/generation
routes leave persistence untouched.

Section 6 introduces `ApiRuntime` as the lazy owner of persistence and the
`SimulationService` worker. Scenario CRUD, failure records/reproduction flags,
results/artifacts, and analytics use short persistence operations. Queue,
launch/list/detail/progress/cancellation routes also start the worker and recover
its jobs. API launch DTOs validate exactly one saved scenario UUID or inline
recipe, explicit CARLA/test mode, and serializable execution/evaluation options.
The HTTP interface launches baseline agents only; Python still permits injected
controllers. New API DTOs are part of the generated schema and TypeScript types.

`SimulationService` owns one FIFO thread and a bounded waiting queue. A file-based
OS lease prevents simultaneous workers against the same SQLite file; in-memory
databases use object ownership. Job configuration, cancellation requests, and an
execution claim are durable. Claiming precedes runner construction/native setup.
Unclaimed waiting jobs resume after restart, whereas interrupted claimed/running
jobs become failed. Recovery only adopts worker-owned jobs. Ordinary run failures
are saved and the queue continues; storage/revision failures stop new acceptance
and expose a fatal queue error until restart.

Running progress uses throttled short updates; simulation ticks and raw sensor
writes do not hold database transactions open. SQLite write units reserve writer
intent before reading data they will update; read-only scopes use read
transactions. This avoids a deferred read-to-write lock upgrade racing with
progress/cancellation. Finalization publishes artifacts and saves the terminal
result before that result is exposed as finished. Artifact download routes accept
indexes into owned results/manifests and validate their storage references.

Application lifespan stops the optimization controller, then requests cooperative
active cancellation and bounded worker shutdown (15 seconds each by default),
leaving waiting jobs for restart. A native call
cannot be forcibly interrupted, and a blocked worker retains its database/lease
until exit. These lifecycle controls do not recover partial in-memory telemetry
or repair CARLA resources after abrupt process loss. See [API behavior](api.md).

Analytics read the complete persisted history in one read transaction, separate
test/CARLA modes, and preserve unknown metrics. Mean severity includes all scored
terminal results, including known collisions from incomplete runs.

Section 7 adds the lazy `OptimizationService` controller and generated contracts
for search, reproduction, minimization, and their durable journals. Random and
optional Optuna 4.x sampling, objective assessment, reproduction policy, and
valid greedy recipe reduction live in `optimization/` without CARLA or database
ownership. The controller submits every child through `SimulationService`, so
direct launches and experiments share one native worker. No transaction spans a
simulation execution.

Migration 3 records full requests, source failures, execution settings, engine
versions, revisioned operation states, and ordered child trials. Child creation
and its journal link share a transaction; positive failure creation and trial
assessment also share one transaction. Search retains actual child metrics but
excludes failed/unscored objectives rather than inventing zero scores. Repeated
reproduction preserves the exact source execution; minimization first confirms
the baseline and accepts only lower-cost candidates confirmed in every repeat.

Controller startup changes unfinished operations to interrupted. Explicit resume
replays the saved proposals and assessments, verifies engine version and recipe,
reuses existing children, and preserves the budget. The native worker may recover
an already-linked unclaimed child before its parent is resumed; the controller
creates no new trials until resume. Shutdown interrupts operations before native
worker shutdown; durable user cancellation propagates to linked unfinished runs.
See [optimization policies and API](optimization.md).

The frontend remains its starter screen; sections 8–9 add dashboard controls and
result views. Live CARLA and container execution remain unverified.
