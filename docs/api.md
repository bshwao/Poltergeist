# API

The FastAPI application is exposed from `backend.main:app`. Run
`python -m backend` from the repository root, or use `create_app(settings)` for
tests and alternate configuration. Interactive docs are at `/docs`; the OpenAPI
document is at `/openapi.json`. Routes below are rooted directly at the server
URL, with no `/api` prefix. The Docker dashboard proxy exposes those same routes
under `/api` on its own origin.

`GET /health` returns HTTP 200 with
`{"status":"ok","service":"poltergeist"}`. It checks process liveness only.
Imports, application startup, health, OpenAPI, and the pure scenario endpoints
do not contact CARLA, create a database, or create artifact directories. Saved
scenario, result, failure, and analytics operations initialize local persistence
lazily. Queue, launch, simulation list/detail/progress, and cancellation operations
also start the single worker and perform restart recovery. Optimization routes
start its experiment controller and recover interrupted operations. CORS uses the configured
`CORS_ORIGINS`.

## Generate, save, launch, and read a test run

Start the API in one terminal. In a second PowerShell terminal:

```powershell
$base = 'http://localhost:8000'
$recipe = Invoke-RestMethod "$base/scenarios/generate" -Method Post -ContentType 'application/json' -Body '{"template":"sudden_braking","seed":7}'
$saved = Invoke-RestMethod "$base/scenarios" -Method Post -ContentType 'application/json' -Body ($recipe | ConvertTo-Json -Depth 100)
$launch = @{ scenario_id = $saved.id; mode = 'test' } | ConvertTo-Json
$run = Invoke-RestMethod "$base/simulations" -Method Post -ContentType 'application/json' -Body $launch
$runId = $run.simulation.id
do {
    $progress = Invoke-RestMethod "$base/simulations/$runId/progress"
    $progress | Select-Object status, progress, queue_position, cancel_requested, error
    if ($progress.status -in @('queued', 'running')) { Start-Sleep -Milliseconds 250 }
} while ($progress.status -in @('queued', 'running'))
$result = Invoke-RestMethod "$base/simulations/$runId/result"
$result.metrics
$page = Invoke-RestMethod "$base/simulations/$runId/telemetry?offset=0&limit=100"
$page | Select-Object total, offset, limit
# Artifact indexes are positions in the result's artifacts array.
for ($index = 0; $index -lt $result.artifacts.Count; $index++) {
    if ($result.artifacts[$index].kind -eq 'telemetry') {
        Invoke-WebRequest "$base/simulations/$runId/artifacts/$index" -OutFile 'telemetry.ndjson'
        break
    }
}
Invoke-RestMethod "$base/analytics?mode=test"
```

To launch without saving a scenario first, send the normalized recipe inline:

```powershell
$launch = @{ scenario = $recipe; mode = 'test'; options = @{ max_ticks = 1000 } } | ConvertTo-Json -Depth 100
$run = Invoke-RestMethod "$base/simulations" -Method Post -ContentType 'application/json' -Body $launch
```

Both examples explicitly choose synthetic `test` mode. Omitted mode means
`carla`; an unavailable client/server produces a failed run, with no synthetic
fallback. Test mode rejects camera/LiDAR options. Synthetic results exercise the
software path and do not establish real driving safety.

## Scenarios

| Method and route | Behavior |
| --- | --- |
| `GET /scenarios/templates` | All bundled recipes and parameter metadata |
| `GET /scenarios/templates/{template}` | One recipe and its parameter bounds/defaults/units |
| `POST /scenarios/validate` | Normalize and validate a scenario body; no save or execution |
| `POST /scenarios/generate` | Build a seeded recipe or sample its parameters; no save or execution |
| `GET /scenarios?limit=50&offset=0` | Saved scenario records |
| `POST /scenarios` | Save a scenario body; HTTP 201 |
| `GET /scenarios/{id}` | One saved record |
| `PUT /scenarios/{id}` | Replace its recipe using `{ "scenario": {...}, "expected_revision": 1 }` |
| `DELETE /scenarios/{id}?expected_revision=1` | Revision-checked delete; HTTP 204 |

The pure endpoints validate configuration and parameter bindings; actual map,
lane, ground, overlap, and sensor validity are checked by the adapter at execution.
Saved records contain `id`, `scenario`, `revision`, `created_at`, and `updated_at`.
Edits increment revisions; stale writes and deletion of a referenced recipe
return 409. Existing runs retain their original recipe snapshots.

Generation accepts `template`, `seed` (default 0), fixed `parameters`, `sample`
(default false), and `ranges`. Without sampling, omitted parameters use template
defaults. Set `sample: true` to draw deterministic values from template bounds,
optionally narrowed by ranges:

```json
{
  "template": "sudden_braking",
  "seed": 7,
  "sample": true,
  "parameters": {},
  "ranges": {}
}
```

Use the template metadata for valid parameter names and bounds. Each range is a
two-number `[minimum, maximum]` array. Ranges require `sample: true`; fixed values
and narrowed ranges must satisfy the template constraints. See
[scenario generation](scenarios.md).

## Simulation jobs

| Method and route | Behavior |
| --- | --- |
| `POST /simulations` | Persist and enqueue a launch; HTTP 202 |
| `GET /simulations/queue` | Worker state, pending count, active UUID, and fatal error |
| `GET /simulations?status=completed&mode=test&limit=50&offset=0` | List persisted runs; filters optional |
| `GET /simulations/{id}` | Lifecycle, optional terminal result, revision, and job settings |
| `GET /simulations/{id}/progress` | Pollable lifecycle/progress and queue/cancellation state |
| `POST /simulations/{id}/cancel` | Request cancellation; HTTP 202 |
| `GET /simulations/{id}/result` | Finalized `SimulationResult`; 409 while unfinished |
| `GET /simulations/{id}/telemetry?offset=0&limit=1000` | Finalized telemetry page; 409 while unfinished |
| `GET /simulations/{id}/artifacts/{artifact_index}` | Download a result-owned artifact |
| `GET /simulations/{id}/artifacts/{artifact_index}/samples/{sample_index}` | Download one raw recording sample |

Launch accepts exactly one of `scenario_id` or `scenario`, plus `mode`, `options`,
and `evaluation_settings`. Options and evaluation settings use shared contracts
and are stored with the job, including for restart. The API launches the baseline
agent; it does not accept uploaded agent code or a custom agent selector. See
[execution options](simulation.md) and [contracts](data-contracts.md).

One FIFO worker executes one run at a time. A nonblocking OS lease prevents two
workers from sharing a SQLite file; in-memory databases use an object ownership
guard. Use one backend process per database. The configured queue capacity
(default 100) counts waiting jobs, excluding the active job. Full queues return
429. Queue positions are one-based for waiting jobs and null otherwise. Progress
is persisted at a throttled interval (default 0.25 seconds), so polling does not
promise a newly stored value on every tick. Pending jobs have no start timestamp;
native setup may run before the first running progress update is available.

Cancellation persists the request. A waiting job becomes cancelled promptly with
an empty terminal result. Active cancellation is cooperative: a blocking native
call or callback must return before the runner observes it. A late request may
race with completion. The response records the current state; continue polling
until terminal. Repeating cancellation of a terminal worker-owned run returns
that run unchanged.

On shutdown, the application stops accepting jobs, requests active cancellation,
and waits up to `SIMULATION_SHUTDOWN_TIMEOUT_S` (default 15 seconds). Pending jobs
remain persisted for the next worker. A blocked native call cannot be forcibly
killed; the running worker retains its database and lease until it exits.

Restart recovers only jobs created by the worker service. Truly waiting,
unclaimed jobs resume in FIFO order with their stored options/settings; pending
cancel requests become cancelled. A durable execution claim is written before
runner construction or native setup. Claimed or running nonterminal jobs become
failed with interruption diagnostics rather than being silently rerun, even if
their last lifecycle record was still queued. Manually persisted runs are not
adopted. Abrupt termination cannot recover an in-memory partial trace or clean
CARLA resources; the [artifact recovery limits](persistence.md) still apply.

Normal simulation/setup failures save a failed result and let the next job run.
A storage or revision failure stops acceptance, appears in `queue.error`, and
requires an application restart after the underlying problem is resolved. It
does not replace an immutable result or report an unsaved run as completed.

## Results and artifacts

Terminal results preserve metrics, unknown values, execution mode, scenario
snapshot, runtime provenance when available, and storage-owned references.
Telemetry pages have `frames`, `total`, `offset`, and `limit`; limit is at most
1000. The current implementation validates and reads the complete trace into
memory before slicing the response. It is response pagination, not a streaming
large-trace reader.

Artifact and sample indexes are zero-based positions in the result and recording
manifest arrays. Requests do not accept arbitrary filesystem paths. Access checks
validate ownership, storage containment, the generated layout/media type, and
regular-file existence before serving. The telemetry endpoint validates the
trace; raw-sample access validates the manifest and sample hashes. A recording
artifact download is a `raw-sensor-v1` JSON manifest; individual sample downloads
are raw bytes. These are not encoded video or CARLA replay files.

## Failures and analytics

| Method and route | Behavior |
| --- | --- |
| `GET /failures?simulation_id={id}&reproduced=unknown&limit=50&offset=0` | List reports, newest first; filters optional |
| `POST /failures` | Save a full `FailureReport`; HTTP 201 |
| `GET /failures/{id}` | One report |
| `PATCH /failures/{id}/reproduction` | Record `{ "reproduced": true }`, false, or null |
| `GET /analytics?mode=test` | Aggregate all persisted history; mode filter optional |

A report requires a scored terminal parent, a matching recipe/severity, and
positive evidence for its selected failure kind. Launching a run does not
automatically create a failure report. `reproduced` filters are `all` (default),
`true`, `false`, or `unknown`. The reproduction patch records an explicit
observation. Automated reproduction runs use `/optimizations` and retain their
child-run evidence.

Analytics always separate CARLA and test groups. They include lifecycle counts,
scored/unscored result counts, mean severity, observed/positive collision and
traffic counts, and failure kinds over all history. Mean severity includes every
scored terminal result, including known collisions in failed/cancelled runs;
unknown scores never become zero. Observed run counts mean non-null counts and
can include partial positive lower bounds; they are not complete coverage rates.

## Optimization operations

`POST /optimizations` accepts a discriminated `kind` of `search`, `reproduce`, or
`minimize` and returns an operation record with HTTP 202. Search uses random or
optional Optuna sampling over bundled template parameters. Reproduction and
minimization reference a saved failure and inherit its recorded recipe and
execution settings. All child runs use the existing single simulation worker.

`GET /optimizations` lists records, `GET /optimizations/queue` returns controller
status, and `GET /optimizations/{id}` returns the operation and complete trial
journal. `POST /optimizations/{id}/cancel` persists cancellation.
`POST /optimizations/{id}/resume` accepts a positive `expected_revision` and
resumes an interrupted operation by replaying its journal without resetting its
execution budget. Each route starts the controller lazily. See
[search, reproduction, and minimization](optimization.md) for request examples,
unknown-score policy, repetition thresholds, budgets, and restart semantics.

## Export finalized evidence

`GET /reports/simulations/{id}?format=json|csv|html` downloads an owned finalized
run report. JSON can opt into the finalized trace with `include_telemetry=true`;
CSV and HTML reject that option. The immutable record, failure observations,
mode, nullable metrics, coverage, settings, and runtime provenance are retained.
The route uses persistence only and does not start a simulation worker. Unknown
IDs return 404; runs without a terminal persisted result return 409. See
[report schemas, formats, and CLI](reports.md). [Experiment batches](experiments.md)
submit through the existing simulation routes rather than creating a second worker.

## Errors and limits

Errors use a JSON `detail` field. Validation errors from request parsing also
identify invalid fields. Common statuses are:

| HTTP status | Meaning |
| --- | --- |
| 404 | Missing record, artifact index, or sample |
| 409 | Stale revision, referenced scenario, unfinished result, conflicting worker ownership, or unsafe optimization resume |
| 422 | Invalid request, parameter binding, launch, or failure evidence |
| 429 | Waiting queue full |
| 503 | Worker/controller unavailable, missing/unsupported optional search dependency, persistence initialization/access failure, or invalid/unavailable stored artifact |

List limits are 1–1000, with a nonnegative offset. This local API has no
authentication or public-hosting configuration. The dashboard provides recipe
forms, launches, history, results, failure evidence, comparisons, and exports.
Search/reproduction/minimization remain available through their API. Live CARLA
execution and container runtime integration remain unverified.
