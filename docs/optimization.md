# Search, reproduction, and minimization

Section 7 adds bounded experiments through `/optimizations`. One controller
submits child runs through the existing single simulation worker; it never
owns a second CARLA tick loop. Every child remains available through
`/simulations/{id}` with its actual result, metrics, telemetry, and mode. The
dashboard controls arrive in sections 8–9.

## Run a search

With the API running, submit a small synthetic search from PowerShell:

```powershell
$base = 'http://localhost:8000'
$request = @{
    kind = 'search'
    template = 'sudden_braking'
    strategy = 'random'
    trials = 4
    seed = 7
    mode = 'test'
    parameters = @{}
    ranges = @{}
} | ConvertTo-Json -Depth 30
$operation = Invoke-RestMethod "$base/optimizations" -Method Post -ContentType 'application/json' -Body $request
$view = Invoke-RestMethod "$base/optimizations/$($operation.id)"
$view.operation
$view.trials
```

Poll the detail route until the operation is `completed`, `failed`, `cancelled`,
or `interrupted`. Its response contains `operation` and the ordered `trials`.
Each trial records its zero-based number, role, candidate/repetition indexes,
full recipe, child simulation UUID, assessment, and optional saved failure UUID.
An operation's outcome summarizes used trials, positive and unknown assessments,
and the best available recipe/run/failure references. A best scored run can have
no failure reference when it supplies no matching failure evidence.

Search accepts a bundled `template`, fixed `parameters`, and narrowed numeric
`ranges` using the [scenario parameter metadata](scenarios.md). Omitted ranges
use the template bounds. `trials` defaults to 20 and is bounded to 1–1000.
The request seed controls sampling; child recipes use consecutive distinct seeds
with wraparound at the shared seed limit. Execution options and evaluation
settings are frozen when the operation is created. Mode defaults to `carla`;
select `test` explicitly for synthetic execution. Test mode rejects raw camera
or LiDAR capture options.

`random` uses an isolated seeded random generator. `optuna` uses a real Optuna
4.x TPE sampler with seeded ask/tell trials and a maximizing severity objective.
Install the optional extra before selecting it:

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[optimization]'
```

The optional extra is declared separately from the core dependency lock. Optuna
4.9.0 has been exercised locally; replay requires the recorded exact engine
version. A missing or unsupported Optuna installation returns 503 before an
operation is saved. Random search needs no Optuna installation, and neither
sampler imports CARLA.

## Scores and failure evidence

Only completed runs with a known severity provide an optimization score. An
observed score of zero stays zero. Failed, cancelled, or unscored runs provide
an unknown objective, even when their saved partial result contains a known
collision. Their actual results remain unchanged and their executions consume
budget. Optuna records an unavailable objective as a failed sampler trial.

`failure_kind` optionally targets `collision`, `near_miss`, `hard_braking`, or
`traffic_violation`. With a target kind, only positive matching trials supply
the sampler's objective. The journal still preserves each completed run's known
score when its target does not match. Without a target, search maximizes all
known completed scores. `minimum_severity` (0–100, default 0) gates positive
failure assessments and reports; it does not remove otherwise known untargeted
search scores.

Assessments use the result's recorded evaluation thresholds. A known positive
observation can establish a failure with partial channel coverage. A negative
requires complete, gap-free coverage of the relevant family; missing evidence
remains unknown. Without a target, positive evidence is selected in this order:
collision, near miss, hard braking, then traffic violation. A positive matching
assessment creates an evidence-checked `FailureReport` and records its UUID in
the same transaction as the trial assessment.

## Reproduce a saved failure

Use a saved failure UUID, such as `best_failure_id` from a search outcome:

```json
{
  "kind": "reproduce",
  "failure_id": "<saved failure UUID>",
  "repeats": 3,
  "required_successes": 3,
  "minimum_severity": 0
}
```

The source failure and execution specification are snapshotted. Every repetition
uses its exact recipe and seed, mode, simulation options, and evaluation settings.
The source run must have persisted worker-job settings; manually stored results
without that metadata cannot supply an automated reproduction or minimization.
These requests do not accept replacement execution settings.

`repeats` defaults to 3 and is bounded to 1–20. `required_successes` defaults to
all requested repetitions and must be between 1 and `repeats`. The controller
executes all requested repetitions and reports:

| Reproduction outcome | Evidence |
| --- | --- |
| `true` | Confirmed positives reach `required_successes` |
| `false` | Confirmed positives plus unknown repetitions cannot reach the threshold |
| `null` | Unknown repetitions could still change whether the threshold is reached |

Only a completed reproduction operation updates the source report's
`reproduced` field, atomically with operation completion. Cancellation or
interruption leaves that field unchanged. The manual
`PATCH /failures/{id}/reproduction` endpoint still records an explicit observation
without executing runs.

## Reduce a failing recipe

```json
{
  "kind": "minimize",
  "failure_id": "<saved failure UUID>",
  "max_trials": 20,
  "repeats": 2,
  "minimum_severity": 0
}
```

Minimization first confirms the original recipe in every baseline repetition.
An inconclusive or negative baseline ends the operation without accepting a
reduction. It then proposes deterministic, valid simplifications and accepts a
candidate only when all its repetitions positively establish the source failure
kind and severity threshold. Unknown or failed runs cannot accept a candidate.
Mode, seed, options, and evaluation settings remain those of the source run.

The greedy cost compares actor count, event count, template-parameter deviations,
weather, numeric controls, then duration. Reductions preserve built-in template
bindings and the ego actor, remove eligible dependency-leaf actors and their
events, move parameters toward defaults, and simplify valid controls/weather or
duration. Each acceptance has a strictly lower cost. The result is a tested local
reduction; it does not prove a globally minimal failure.

`max_trials` is 1–200, default 20. `repeats` is 1–10, default 2, and the budget
must allow the initial baseline group. Baseline and candidate executions all
consume the same budget, including failures and cancellations. The controller
starts a new candidate only when the remaining budget fits its whole repetition
group, so it may finish with fewer than `max_trials` used. An interrupted partial
group is reused and completed on resume.

## API, cancellation, and restart

| Method and route | Behavior |
| --- | --- |
| `POST /optimizations` | Submit a `search`, `reproduce`, or `minimize` request; HTTP 202 |
| `GET /optimizations?limit=50&offset=0` | List operation records |
| `GET /optimizations/queue` | Controller acceptance, pending count, active UUID, and fatal error |
| `GET /optimizations/{id}` | Operation and complete ordered trial journal |
| `POST /optimizations/{id}/cancel` | Persist cancellation and propagate it to linked unfinished runs; HTTP 202 |
| `POST /optimizations/{id}/resume` | Resume an interrupted operation using `{ "expected_revision": 4 }`; HTTP 202 |

The request body must include its `kind`. Listing is paginated to at most 1000
operations; each detail response includes all its trials, bounded by the request
budget. Optimization routes lazily start the controller and native worker. The
controller has a bounded waiting queue using `SIMULATION_QUEUE_SIZE`; it submits
one child at a time and waits if the shared simulation queue is full. Direct
simulation launches use that same worker. No database transaction spans a run.

Migration 3 adds durable operation and trial records. A child's queued run, job
settings, and journal link are created in one transaction. Linking a child
consumes budget immediately. An interrupted result assessment can be completed
from that same child without creating another execution or duplicate failure.

At controller startup, previously queued or running operations become
`interrupted` and require an explicit revision-checked resume. The simulation
worker independently recovers its jobs: an already-linked unclaimed child may
execute before its parent is resumed, while an interrupted claimed child becomes
failed. The controller creates no new child trials until the parent is resumed.
Resume replays recorded proposals and assessments, verifies recipes and engine
version, reuses linked children, and keeps the original budget. A changed engine
version or inconsistent history is rejected instead of silently restarting.

Application shutdown stops the controller before the native worker. An active
operation is interrupted; queued operations are recovered as interrupted at next
startup. User cancellation is durable for queued, running, and interrupted
operations and propagates to linked unfinished children. Native cancellation
remains cooperative, so a blocking native call can outlast the controller's
response. Poll operation and child status as needed. Completed, failed, and
cancelled operations cannot be resumed.

Synthetic runs and fake CARLA boundaries exercise this workflow. Live CARLA
physics, map anchors, sensor timing, GPU behavior, and container execution remain
unverified. See [worker recovery](api.md#simulation-jobs) and
[artifact recovery limits](persistence.md#artifact-layout-and-failure-handling).
