# Run experiment batches

This baseline-only CLI retains its existing sequential batch format. For
registered agents, matched comparisons, repeated trials, agent-aware search,
reproduction and minimization, use the dashboard's Experiments tab or the
[`/optimizations` API](agent-comparisons.md).

The experiment CLI submits inline scenario snapshots to the running Poltergeist
API, then polls each run to its finalized result before submitting the next.
It uses the same persisted queue and worker as the dashboard. It does not open
the database, instantiate another simulator, or start a server.

Start the API from the repository root in one terminal:

```powershell
.\.venv\Scripts\python.exe -m backend
```

Keep one API process/worker for the configured database and CARLA server. Start
the CLI in another terminal. Its default API URL is `http://127.0.0.1:8000`;
`--api-url` can point at another HTTP(S) API, including a deployment path prefix.
Credentials, query strings, fragments, and redirects are rejected. `/health`
alone reports liveness; a launch also needs working persistence and a worker.

## Generate a template batch

Every invocation requires an explicit execution mode:

```powershell
.\.venv\Scripts\python.exe -m scripts.run_experiment --template pedestrian_crossing --mode test --count 2 --seed 1 --output experiment.json
```

`test` runs the deterministic synthetic adapter. Its output is useful for testing
the workflow and is not CARLA driving evidence. `carla` requires the matching
CARLA installation and a running server described in [setup](setup.md). A CARLA
failure never falls back to synthetic mode. Camera/LiDAR options require `carla`.

Available templates are `intersection`, `pedestrian_crossing`,
`occluded_pedestrian`, and `sudden_braking`. The CLI asks the API to generate each
template with its default parameters and the requested seed. It does not sample
new parameter values. To change parameters, generate/edit a recipe in the
dashboard or scenario API and pass that recipe as a file.

`--count` accepts 1–100 runs and defaults to 1. Seeds increase by one per run.
The default first seed is 0 for a template, or the file's existing seed. An
explicit `--seed` overrides that first seed. The entire batch must remain in the
range 0–2147483647; overflow is rejected before any simulations are submitted.

The module command works from a source checkout with dependencies installed.
After installing/reinstalling the editable package, the equivalent console
command is also available in the virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\poltergeist-experiment.exe --template sudden_braking --mode test --seed 5 --output braking.json
```

On macOS/Linux use `.venv/bin/python` and `.venv/bin/poltergeist-experiment`.
Reinstalling registers new console entry points; an existing environment created
before section 10 can still use the module command immediately.

## Use a scenario file and recorded settings

Supply exactly one of `--scenario` or `--template`. A scenario file can be YAML,
YML, or JSON and must contain a complete recipe. The same normalized recipe is
used for every batch item, with only its seed changed. Actor defaults are
materialized and all recipes/settings are validated before the first launch.
All planned recipes are also checked against the API before executing the batch.

```powershell
.\.venv\Scripts\python.exe -m scripts.run_experiment --scenario my-scenario.yaml --mode test --count 3 --seed 20 --options options.json --evaluation-settings evaluation.json --output custom-experiment.json
```

The optional settings files are JSON objects. Omitted fields receive the shared
contract defaults, which are included in each submitted snapshot. For example,
`options.json` can contain:

```json
{
  "max_ticks": 5000,
  "allow_map_reload": false
}
```

`evaluation.json` can contain:

```json
{
  "clearance_warning_m": 4.0,
  "speeding_tolerance_mps": 0.7
}
```

These are scoring settings, not a promise that a scene will produce a failure.
Unknown fields, invalid ranges, incompatible camera/LiDAR test settings,
duplicate JSON keys, and non-finite JSON numbers are rejected. Changing a
threshold changes the evaluation method's inputs; compare runs using their
recorded settings and coverage. The CLI verifies that accepted jobs retain the
requested recipe, mode, execution options, and evaluation settings.

## Receipts and results

Without `--output`, stdout contains one JSON object. Progress messages and run
IDs go to stderr, so stdout can be redirected or parsed independently:

```powershell
.\.venv\Scripts\python.exe -m scripts.run_experiment --template intersection --mode test > experiment.json
```

Use `--output` for guarded publication. The CLI writes a temporary file in the
destination directory and publishes the finished JSON atomically. It refuses an
existing destination by default, including a file created by another writer
while the batch runs. `--force` explicitly permits replacing an existing output.
An output may never replace its scenario/settings input, even with `--force`.
The destination directory must already exist.

The `poltergeist-experiment-v1` object contains the API URL, creation/finish
times, execution mode, requested count, batch status/error, and a `runs` array.
Each submitted entry records:

- `launch`: the exact inline recipe and complete execution/evaluation settings.
- `submission`: `accepted`, `rejected`, or `uncertain` after an attempted launch.
- `simulation_id` and `accepted_record`, when the receipt is valid.
- `progress`: the last confirmed progress contract.
- `result`: the complete finalized simulation result, or `null` if unavailable.
- `error`, plus a cancellation receipt/error if interruption required one.

Results preserve the simulation status/mode, runtime provenance, aggregate
metrics, observation coverage, evaluation settings, and artifact references.
Unavailable metrics remain JSON `null`; they are not converted to zero or a
successful safety classification. A completed run can still have a positive
failure score. A failed/cancelled run can retain partial evidence. Inspect the
recorded status, metrics, and coverage together. The CLI does not create failure
reports automatically; the dashboard can save supported evidence from a result.

Accepted runs remain available in dashboard **Simulations** and the API's
`/simulations/{id}` and `/simulations/{id}/result` routes, independent of the
local output file. File output is published when the command exits normally or
handles an execution error/interruption; it is not a checkpoint during a running
batch. A forced process kill or machine failure may prevent local publication.
Use the accepted IDs printed to stderr and persisted API history for recovery.

## Errors, deadlines, and interruption

`--request-timeout` defaults to 10 seconds per HTTP request.
`--wait-timeout` defaults to a 300 second execution budget across the entire
batch after prevalidation. Polling and execution requests use the remaining
budget; `--poll-interval` defaults to 0.5 seconds. Increase the wait budget for
large batches or CARLA runs:

```powershell
.\.venv\Scripts\python.exe -m scripts.run_experiment --scenario my-scenario.yaml --mode carla --count 5 --wait-timeout 1800 --request-timeout 20 --output carla-batch.json
```

If a run ends `failed` or `cancelled`, its finalized result is retained and the
CLI continues with the next planned item. The batch exits nonzero when any run
has either status. A transport error, invalid API response, or wait deadline
stops further submissions and retains the known receipts in the output. A wait
deadline does not cancel an accepted run; it can continue in the API queue.

The CLI never automatically retries execution mutations. If submission loses
its response, times out, or returns an invalid receipt, acceptance may be
uncertain. Check dashboard/API history before retrying to avoid duplicates.
The command cannot safely identify an unconfirmed run solely from a recipe.

Press Ctrl+C to stop further submissions. If this invocation has a confirmed
active job, it sends one cooperative cancellation request for that ID only.
Cancellation is asynchronous; inspect its persisted result in history after
the worker finalizes. A second interruption or API outage may prevent confirming
cancellation. If Ctrl+C occurs before a valid launch receipt arrives, the CLI
does not guess which job to cancel and marks the submission uncertain.

| Exit code | Meaning |
| --- | --- |
| `0` | Every requested run finalized with status `completed` |
| `1` | API/execution/output error, deadline, or a failed/cancelled result |
| `2` | Invalid arguments/configuration or incompatible prevalidated recipe |
| `130` | Interrupted by Ctrl+C |

Configuration errors occur before simulation submission. When stdout/output
publication fails, known run IDs are still printed to stderr. If output
publication also fails after another error, the original nonzero exit code is
retained. No command success implies live CARLA validation; synthetic and CARLA
results always retain their distinct modes.
