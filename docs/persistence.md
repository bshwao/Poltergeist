# Local persistence

Section 5 stores saved scenarios, simulation lifecycle/results, failure reports,
finalized telemetry, and optional raw sensor captures. It uses a local SQLite
database and the configured artifact directory. The runner executes synchronously;
section 6 wraps it in a durable single-worker queue and exposes saved data through
feature APIs. Section 7 persists search/reproduction/minimization operations and
their child-run journals. Dashboard controls remain later work. See the
[API walkthrough](api.md#generate-save-launch-and-read-a-test-run).

## Initialize explicitly

From the repository root, with the Python environment activated:

```text
python -m backend.persistence init
```

This creates the configured database parent, applies packaged migrations, and
creates the artifact root. It is safe to repeat against a compatible database.
`DATABASE_URL` defaults to `sqlite:///./storage/database/poltergeist.db` and
`STORAGE_DIR` defaults to `storage`; settings resolve relative paths against the
repository. Changing one setting does not rewrite the other.

Imports, settings loading, `Database(...)`, `ArtifactStore(...)`, and
`PersistenceService.from_settings()` do not connect or create directories.
API startup, `/health`, and pure scenario template/validation/generation routes
also leave persistence untouched. Other feature requests initialize it lazily;
direct Python callers invoke `initialize()` before repository or artifact
operations. Only plain local `sqlite` or
`sqlite+pysqlite` file URLs and `sqlite:///:memory:` are supported. Other engines,
host/credential components, and SQLite URL query options are rejected.

## Save, run, and reload

```python
from backend.services.persistence_service import PersistenceService
from scenarios import load_template
from simulator import SimulationRunner

service = PersistenceService.from_settings()
service.initialize()
try:
    saved = service.save_scenario(load_template("sudden_braking"))
    queued = service.create_simulation(saved.id, mode="test")
    output = SimulationRunner().run(queued.simulation)
    persisted = service.persist_output(output, expected_revision=queued.revision)

    loaded = service.read_output(queued.simulation.id)
    assert loaded.result == persisted.result
    assert loaded.telemetry == persisted.telemetry
    print(loaded.result.artifacts)
finally:
    service.database.dispose()
```

`create_simulation()` also accepts a `Scenario` or scenario mapping directly.
A saved-scenario UUID links the run to that record and copies its recipe at
creation. Later scenario edits cannot change the run's snapshot. Mode defaults
to `carla`; use `test` explicitly for a synthetic run. A queued record preserves
the UUID/mode/agent that the runner must return.

`persist_output()` accepts finalized `RunOutput`, validates its identity, recipe,
frame count, duration, ego identity, consecutive fixed-step timeline, and the full
execution window for completed runs, then returns a new output with storage-owned
artifact references. Pass its returned
value onward; the original result is not changed. Preexisting artifact references
are rejected so this operation cannot attach arbitrary files. Empty traces from
setup failures can also be saved. Partial finalized telemetry is retained for
failed/cancelled runs, with unknown measurements and unscored severity left null.

`read_output()` reloads the terminal result and validates its telemetry and every
referenced artifact, including raw sample hashes when a recording is present.
Results retain full shared-contract JSON: normalized recipe and seed, explicit
test/CARLA mode, evaluation settings and coverage, null observations, lifecycle
timestamps, and available runtime versions/resolved spawns.

## Transactions and revisions

`backend.repositories` exposes `ScenarioRepository`, `SimulationRepository`, and
`FailureRepository`. Each accepts a SQLAlchemy session; it validates and flushes
changes but does not commit. `Database.session()` owns the short transaction and
rolls it back if an operation fails:

```python
from backend.repositories import ScenarioRepository

with service.database.session() as session:
    scenarios = ScenarioRepository(session)
    current = scenarios.get(saved.id)
    edited = current.scenario.model_copy(deep=True)
    edited.name = "Braking variation"
    updated = scenarios.update(
        current.id, edited, expected_revision=current.revision,
    )
```

`Database.session(write=True)` is the default and begins a SQLite `BEGIN
IMMEDIATE` transaction, reserving writer intent before repository reads that
will precede an update. This lets the busy timeout serialize concurrent short
writers without a deferred read-to-write upgrade deadlock. Read-only units use
`session(write=False)` for an ordinary read transaction. Do not write inside a
read-only unit; expected revisions still reject stale edits independently of
locking. In-memory databases serialize transactions on their single connection.

Scenario updates/deletes and run updates/completion require `expected_revision`.
A successful change increments the revision; a stale write raises
`ConflictError`. Reload before deciding whether to retry. If progress was saved
while the runner executed, pass the latest revision to `persist_output()`.
Do not keep a database transaction open during a simulation or sensor callback.
`SimulationRepository.complete()` handles database invariants only; it has no
filesystem context. Use `PersistenceService.persist_output()` to commit runner
output with validated, published artifacts and a complete telemetry window.

Runs can move from queued to running and then to one terminal state. Progress
cannot decrease, the start time cannot change after execution starts, and
identity/recipe/mode/agent remain immutable. Terminal results cannot be replaced.
Referenced scenarios cannot be deleted. Lists have bounded pagination and stable
creation-time/UUID ordering; run lists can filter status/mode, and failure lists
can filter run and reproduction outcome.

Public database failures use `PersistenceError`, `NotFoundError`, `ConflictError`,
or `DatabaseInitializationError` from `backend.persistence_errors`. Invalid
artifacts raise `ArtifactError` from `backend.storage`.

## Capture raw sensor samples

For a map-valid CARLA scenario, subscribe a capture writer to the runner:

```python
from simulator import SimulationOptions, SimulationRunner

queued = service.create_simulation(scenario, mode="carla")
capture = service.artifacts.capture(queued.simulation.id)
try:
    output = SimulationRunner(options=SimulationOptions(camera=True, lidar=True)).run(
        queued.simulation, on_sensor=capture.write,
    )
    persisted = service.persist_output(
        output, expected_revision=queued.revision, capture=capture,
    )
except BaseException:
    capture.abort()
    raise
```

Creating a capture writer does not create files; its first sample starts private
staging. Samples and metadata are streamed to disk rather than retained for the
whole run. The writer is one-shot, belongs to one store/run, and requires strictly
increasing frames/timestamps within each sensor stream. A write error aborts it.
Keep it uncommitted until `persist_output()` handles the terminal output.

Healthy completed or cancelled runs publish a nonempty capture. Failed runs
discard it and still save finalized partial telemetry. Recording frame IDs must
exist in that telemetry, and sample count must match runtime metadata when
available. No samples produce no recording reference. Omitting a capture writer
is allowed; runtime sample count can therefore describe drained but unsaved data.

Recording manifests use `raw-sensor-v1`, include run/artifact UUIDs, sample count,
total bytes, and per-sample kind, frame, native timestamp, metadata, byte length,
SHA-256, and generated `.bin` filename. Camera data is raw `bgra8` with width and
height; LiDAR is raw `float32_xyzi` with channels. LiDAR byte order is not supplied
by the sensor source. These bytes are not an encoded video or a CARLA replay.
Test mode has no renderer and rejects optional sensor capture requests.

## Artifact layout and failure handling

All references are relative to `STORAGE_DIR` and use generated UUID components:

| Kind | Path | Media type |
| --- | --- | --- |
| Telemetry | `telemetry/<run UUID>/<artifact UUID>/frames.ndjson` | `application/x-ndjson` |
| Raw recording | `recordings/<run UUID>/<artifact UUID>/manifest.json` | `application/json` |
| JSON report | `reports/<run UUID>/<artifact UUID>/report.json` | `application/json` |

Telemetry contains one full `TelemetryFrame` JSON object per line. Raw sample
files sit beside their manifest. `ArtifactStore.write_report(run_id, payload)`
supports a JSON-object export separately from database failure reports. The API
serves indexed result-owned artifacts and indexed recording samples with
containment checks. Encoded playback, replay generation, and report-export
workflows remain later work.

The store validates kind/layout/media type, canonical IDs, run ownership, and
filesystem containment on access. Absolute paths, traversal, drive/URL prefixes,
unsafe path components, and symlink/junction traversal are rejected. Callers
receive references generated by the store rather than selecting destination
paths. Artifacts are written into private staging, files are flushed/fsynced,
and complete directories are published by a same-parent rename. Transient Windows
rename denials (native errors 5, 32, or 33) receive at most five attempts with
185 ms total backoff, rechecking containment and destination absence each time.
Persistent denials and unrelated errors still fail and trigger owned cleanup.

SQLite and artifact files do not share a transaction. The service publishes
files first, then saves references with a revision-checked database completion.
On exceptions, it removes only files created by that attempt and confirmed
unreferenced. If the database commit outcome cannot be determined, files are
retained for reconciliation. Abrupt process termination or power loss can leave
private staging or orphaned published files. There is no durable reconciliation
journal or automatic garbage collection, and this is not cross-filesystem atomic
commit. Back up the database and its referenced artifacts together.

## Failure records and migrations

`service.save_failure(FailureReport(...))` requires a persisted terminal parent
result. The scenario must match the run snapshot, severity must match its scored
metrics, and the selected kind needs positive collision, near-miss, hard-braking,
or traffic-violation evidence. Unscored or unsupported failure claims are
rejected. `FailureRepository.set_reproduced()` records true, false, or unknown;
completed automated reproduction updates it in the same transaction as the
operation's final outcome. See [reproduction policy](optimization.md#reproduce-a-saved-failure).

The packaged migration history contains revision 1 (`initial_persistence`),
revision 2 (`simulation_jobs`), and revision 3 (`optimization_journal`).
Initialization validates existing revision 1 or 2 databases before upgrading
them transactionally; existing scenarios, runs, and reports remain unchanged.
New databases receive all three revisions. The jobs
table stores FIFO order, options, evaluation settings, cancellation requests,
and durable execution claims alongside the parent run. Initialization enables
SQLite foreign keys and verifies
history/checksum, tables, columns, constraints, and indexes. Unknown/future or
changed migration history and unversioned populated databases are rejected.
Published migrations are immutable; future schema changes need a new migration.
This uses explicit SQL migrations rather than Alembic or automatic
`metadata.create_all()` repair. It does not import arbitrary legacy databases.

`OptimizationRepository` stores full operation requests, frozen execution/source
snapshots, lifecycle/revisions, engine versions, and ordered child trials. A
worker submission callback creates the child simulation, its job settings, and
the journal link in one transaction. Every linked child consumes budget,
including failed/cancelled runs. Trial assessment and any positive failure report
also commit together, preventing duplicate reports when assessment is replayed.
The controller uses short repository transactions and never holds one during a
simulation run.

## Worker ownership and restart

`SimulationService` creates a queued snapshot and job settings in one transaction,
then executes outside database transactions. It throttles running progress writes
and saves finalized output against the latest expected revision. Enabled raw
captures are connected to `CaptureWriter` automatically. Pending capacity counts
waiting jobs, excluding the active run; an OS lease prevents competing workers
against the same SQLite file.

Worker shutdown requests active cancellation and leaves waiting records for the
next startup. Restart only recovers jobs created through this worker service:
unclaimed queued jobs resume with their stored options/settings; cancelled waiting
jobs become terminal. A durable claim is written before runner construction or
native setup. Claimed/running nonterminal jobs become failed with interruption
diagnostics, even when setup had not yet emitted a running lifecycle update.
Directly persisted runs are not silently adopted.

Ordinary simulation failures persist a failed result and allow the queue to
continue. Storage or revision conflicts stop job acceptance and expose a queue
error until restart after the underlying problem is fixed. Abrupt termination
does not preserve a partial in-memory trace or reclaim orphaned files/native
resources. A blocked native call can outlast bounded shutdown and retains its
worker lease until exit. See [queue lifecycle](api.md#simulation-jobs).

The optimization controller independently recovers unfinished operations as
`interrupted`; explicit revision-checked resume replays and validates their
journals without resetting budgets or creating replacement children. The native
worker may already execute an existing linked unclaimed child before its parent
is resumed, while interrupted claimed children become failed. New optimization
trials require explicit parent resume. User cancellation is durable and
propagates to unfinished linked jobs. Controller shutdown interrupts the active
operation before native worker shutdown. Source reproduction flags change only
on a completed reproduction operation. See [optimization restart semantics](optimization.md#api-cancellation-and-restart).

Tests cover local SQLite and synthetic/fake-sensor integration. Real CARLA
execution, map anchors, sensor timing, and GPU behavior remain unverified.
