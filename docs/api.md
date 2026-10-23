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

## Local setup readiness

`GET /readiness` returns a typed `poltergeist-readiness-v1` snapshot with scope
`local_core`, an aware `checked_at` timestamp, and separate `synthetic` and
`carla` capabilities. Each includes a status, summary and identified checks with
details and nullable next steps. Responses use `Cache-Control: no-store`.

The automatic snapshot shares installation diagnostics with `scripts.doctor`
and executable discovery with the CARLA launcher. It reads runtime/package
metadata, validates configuration without opening SQLite, estimates storage
permissions without writing a probe file, and inspects bounded known simulator
locations. It does not inspect Node/Git or gate runtime capabilities on developer
tools. A packaged runtime and its supported distribution remain future work.

`POST /readiness/carla-check` explicitly requests a fresh snapshot plus a
read-only connection check of the configured endpoint. It never starts CARLA.
Network resolution, TCP connection, optional native import and RPC share a
subprocess deadline of 12 seconds (2-second socket/RPC timeouts plus bounded
startup allowance). Without supported client metadata, only listener presence is
checked. Map downloads and Python bytecode writes are disabled for this probe;
it reads world settings without ticks, actors, map loads or setting changes.
The endpoint uses configured settings only and accepts no host/executable overrides.

Status semantics:

| Status | Meaning |
| --- | --- |
| `ready` | That check's prerequisites passed; a workflow is ready to **attempt**, not proven to execute |
| `missing` | Required package metadata or local executable was not found |
| `unsupported` | Runtime/client version or the responding client/server pair is incompatible |
| `not_running` | An explicit connection was refused at the configured endpoint; no process-existence claim |
| `not_checked` | No observation was requested/performed, including experiment execution |
| `unknown` | Permission estimate, listener identity, native import or network/RPC result is inconclusive |
| `error` | Static configuration is invalid or the executable has an unsupported launcher extension |

Executable presence never verifies a CARLA version. Client metadata does not
prove native import works. Only a successful explicit RPC check establishes
reported client/server compatibility. A verified separately started server does
not require discoverable local launcher files; remote hosts are not searched
locally. All experiment execution remains `not_checked`, even after RPC succeeds.
No custom worker/model, sensor pipeline, database migration, driving safety or
throughput is certified. Failure to fetch the report leaves readiness unknown;
HTTP 200 represents a valid report, even when prerequisites are missing.

These endpoints do not initialize persistence, queues, agents or models, and
do not install dependencies or write application data. `/health` remains process
liveness only. Concurrent explicit probes are independent bounded reads, not a
lease on the simulator or permission to share a synchronous tick owner.

## Collision-stress catalogue

`GET /scenarios/stress-presets` returns twelve ordinary `Scenario` recipes at
seed 7. Optional `?seed=19` selects another integer seed in `[0, 2**31-1]` without
sampling different parameters. There are three fixed levels per existing family,
using Town10HD_Opt and eight-second windows. The endpoint performs no storage,
queue, agent or simulator initialization. Read the [calibration and baseline
benchmark](collision-stress.md) before interpreting the levels; regular dashboard
launches retain the server's current signals, while calibration used fixed green.

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
(default false), `ranges`, and optional `duration_s`. Omitted/null duration keeps
the template default; a supplied duration must be finite, positive, at least one
fixed step, and longer than every event trigger. Without sampling, omitted parameters use template
defaults. Set `sample: true` to draw deterministic values from template bounds,
optionally narrowed by ranges:

```json
{
  "template": "sudden_braking",
  "seed": 7,
  "sample": true,
  "parameters": {},
  "ranges": {},
  "duration_s": 120
}
```

Template metadata's `minimum` and `maximum` describe accepted manual input values,
which can be wider than the normal sampling ranges. For example, a fixed
`ego_speed_mps` of 40 is accepted and stored unchanged. Each sampling range is a
two-number `[minimum, maximum]` array. Ranges require `sample: true` and remain
within the normal sampling ranges documented in the scenario engine. Fixed values
can lie outside those sampling ranges, within the accepted input constraints. See
[scenario generation](scenarios.md).

## Local agent registry

| Method and route | Behavior |
| --- | --- |
| `POST /agents` | Persist an immutable trusted local registration; HTTP 201 |
| `GET /agents` | List registrations |
| `GET /agents/{id}` | Read the registration's snapshot and active state |
| `GET /agents/{id}/readiness` | Static declaration/fingerprint checks; never executes agent code |
| `POST /agents/{id}/deactivate` | Disable future launches, preserving queued jobs and history |

Registration requires `package_path` and `trusted_local_code: true`. Optional
fields are `python_executable`, configuration overrides in `configuration`,
absolute external weight paths in `model_files`, `process_options` and
`agent_control_mode` (`authoritative` by default). Omitted Python selects the
backend interpreter. Paths belong to the backend machine. Every registration
receives a new UUID; changing code or configuration requires registering again.
Inspection does not import code, install dependencies or start CARLA. The
readiness response says `inspection: "static_only"` and
`inference_checked: false`; it does not establish model or simulator readiness.

Use only trusted local packages/environments: execution has the backend user's
permissions and the process boundary is not a security sandbox. See the
[registry guide](agent-registry.md) for PowerShell examples, fingerprint coverage
and drift behavior. The [dashboard guide](dashboard.md) covers the Agents page
and launch selector backed by these routes.

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
| `GET /simulations/{id}/decisions?offset=0&limit=25` | Recorded inputs/actions/diagnostics with synchronized telemetry; limit 1–100, 409 while unfinished |
| `GET /simulations/{id}/analysis` | Deterministic behavior analysis of finalized evidence with default settings; 409 while unfinished |
| `POST /simulations/{id}/analysis` | Same read-only analysis with a `BehaviorSettings` JSON body; does not launch or alter a run |
| `GET /simulations/{id}/artifacts/{artifact_index}` | Download a result-owned artifact |
| `GET /simulations/{id}/artifacts/{artifact_index}/samples/{sample_index}` | Download one raw recording sample |

Launch accepts exactly one of `scenario_id` or `scenario`, plus `mode`, `options`,
`evaluation_settings` and optional `agent_id`. Options and evaluation settings
use shared contracts and are stored with the job, including for restart. Omit
`agent_id` or pass null for the baseline; otherwise pass an active registration's
UUID from `/agents`. The resolved agent snapshot is persisted with the job and
simulation, including effective configuration, observation mode, worker settings
and file fingerprints. Launch does not accept agent-package paths, uploads or
per-run agent overrides. See [registered agents](agent-registry.md),
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

New queued runs include a `decisions` NDJSON artifact and a paged review endpoint.
Sensor inputs reference the existing raw recording instead of duplicating bytes.
See [decision evidence](decision-timeline.md) for the format, reconstruction,
coverage and compatibility rules. Agent diagnostics are reports from the agent,
not inferred reasoning. Phase 8.1 provides [behavior analysis](behavior-analysis.md)
through the read-only analysis endpoints. Phase 8.2 adds dashboard review and
[behavior report exports](reports.md) using those same measurements and settings.

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

`POST /optimizations` accepts a discriminated `kind` of `search`, `compare`,
`reproduce`, or `minimize` and returns an operation record with HTTP 202. Search uses random or
optional Optuna sampling over bundled template parameters. Reproduction and
minimization reference a saved failure and inherit its recorded recipe and
execution settings and registered agent snapshot. Search supports `agent_id`
and `driving_task`. Comparisons freeze a recipe, ordered registered-agent
selections (`null` for baseline), distinct seeds and repetitions. All child runs
use the existing single simulation worker.

`GET /optimizations` lists records, `GET /optimizations/queue` returns controller
status, and `GET /optimizations/{id}` returns the operation and complete trial
journal. `POST /optimizations/{id}/cancel` persists cancellation.
`POST /optimizations/{id}/resume` accepts a positive `expected_revision` and
resumes an interrupted operation by replaying its journal without resetting its
execution budget. These routes start the controller lazily. The read-only
`GET /optimizations/{id}/comparison` route uses persistence directly and returns
the `poltergeist-comparison-v1` summary; `?download=true` exports JSON. See
[agent comparisons](agent-comparisons.md) and
[search, reproduction, and minimization](optimization.md) for request examples,
unknown-score policy, repetition thresholds, budgets, and restart semantics.

## Export finalized evidence

`GET /reports/simulations/{id}?format=json|csv|html` downloads an owned finalized
run report. JSON can opt into the finalized trace with `include_telemetry=true`;
CSV and HTML reject that option. The immutable record, failure observations,
mode, nullable metrics, coverage, settings, and runtime provenance are retained.
Add `include_behavior=true` for version 2 reports containing behavior analysis,
bounded recorded diagnostics, qualified descriptions and frame evidence. The
dashboard enables this option by default. `POST /reports/simulations/{id}` accepts
a `BehaviorSettings` JSON body (the same as POST analysis) and always produces a
version 2 report; format and optional JSON telemetry remain query parameters.
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
