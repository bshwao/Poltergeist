# Reference AI pilot

Phase 2.5 integrates the **actual pretrained PlanT 2.0 model** with Poltergeist's
external agent worker. It predicts a local path and timed waypoints on each
observation. The model authors' tracking controllers turn those predictions into
steering, throttle, and brake. It has been exercised against live CARLA 0.9.16.
The original rule-based baseline remains available for comparison.

This is a maintained integration of a published model, not a model trained by
Poltergeist. Source revision, checkpoint hash, dependencies, preprocessing,
runtime device, and model outputs are retained for review. See the
[model package and installation instructions](../examples/agents/reference_ai/README.md).

## Run the installed pilot on this machine

Run these commands from the repository root in PowerShell. The reference worker
has already been prepared in `.agent-venv`; its verified checkpoint is cached in
`storage/models/plant2/`. Start one CARLA instance with Town03 loaded first.

```powershell
$carlaPy = 'C:\Users\erich\.venvs\poltergeist-carla\Scripts\python.exe'
& $carlaPy -m scripts.run_reference_ai --duration 8 --agent-python .agent-venv/Scripts/python.exe --output storage/reports/my-ai-pilot
```

The default trial is road following. The spectator is positioned by the existing
CARLA adapter. To inspect hazards and compare against the old controller:

```powershell
& $carlaPy -m scripts.run_reference_ai --suite --duration 8 --seed 0 --seed 43 --compare-baseline --ablate --agent-python .agent-venv/Scripts/python.exe --output storage/reports/my-ai-comparison
```

Use a new output directory for each launch; existing reports are never
overwritten. `--template` also accepts `sudden_braking`, `pedestrian_crossing`,
`intersection`, and `occluded_pedestrian`. `--scenario path.json` accepts a saved
recipe. Duration must retain every scheduled event. `--seed` can be repeated.
The suite runs serially so only one runner advances CARLA at a time.
Saved recipes keep their seed unless `--seed` explicitly overrides it. Cancelling
a trial stops the remaining suite jobs and retains the partial report.

For the optimized state-based benchmark, use the performance profile and two
seeds. `--require-real-time` returns a nonzero exit status if the measured
throughput gate fails, while retaining the complete report:

```powershell
& $carlaPy -m scripts.run_reference_ai --suite --duration 8 --seed 0 --seed 43 --performance --require-real-time --agent-python .agent-venv/Scripts/python.exe --output storage/reports/my-ai-performance
```

`--performance` selects no rendering and a 0.001 s sensor-settle window. An
explicit `--sensor-settle` overrides that value. For independently controlled
measurements without a rendered view, use `--no-rendering`.
The performance profile also raises the parent Python generation-zero GC
threshold to at least 50,000 during a dedicated trial. Automatic collection
remains enabled and the exact previous thresholds are restored afterward.
This reduces repeated scans over cached map geometry and retained evidence;
it does not impose a fixed memory limit. Active thresholds and OS memory
high-water marks are recorded.
The pilot restores the previous rendering/world settings afterward. Its
privileged map input does not require a camera renderer. A rendered spectator
view is unavailable while rendering is disabled. `--sensor-settle` defaults to
the existing 0.02 s collision-callback settling window; measured low-overhead
trials used the explicit positive value `0.001`. That is a different observation
timing configuration, recorded with the result, not evidence of identical
collision coverage. The general simulator CLI also accepts `--no-rendering`.

To prepare another machine, follow the package README: Python 3.12, an isolated
worker environment, matching PyTorch/torchvision CPU or CUDA wheels, pinned
inference dependencies, and `python -m scripts.setup_reference_ai --download`.
The CARLA-compatible parent environment and the model worker are separate.
Neither ordinary agent loading nor a dashboard launch downloads dependencies.

## What controls the car

The reference harness requests **authoritative** ego control. Scenario scripts
continue to control the lead vehicle, crossing traffic, pedestrians, and weather.
Conflicting scripted ego events are rejected before setup. Every successful
neural result is associated with its observation frame; there is no fallback
controller when inference fails.

The model receives privileged object geometry/motion, speed limits, relevant
traffic controls, a supplied local navigation route, and an ego-aligned semantic
road raster derived from the CARLA lane graph. This exposes actors hidden by
obstacles. It tests learned planning and behavior rather than camera perception
or independently choosing a destination/global route.

The map raster is a Poltergeist adaptation, not the authors' precomputed map
files. Lane widths and marking positions come from CARLA. Compound markings and
dash representation are simplified; these differences can affect model behavior.
The input axes, origin, scale, and source are explicit in structured scene messages.
Raster bytes use validated lossless base64 transport to avoid repeatedly
copying/validating thousands of JSON integers. Existing SDK 1.0 agents retain
their original interface.

The dedicated `--ablate` experiment still executes the neural model, then
deliberately replaces its controls with full braking. Both the original neural
control and the labelled intervention are retained. Changed vehicle motion
establishes control-path influence; this is not another trained model or a
comparison of model intelligence.

## Read the evidence

Each report directory contains:

These are local CLI artifacts; they are not inserted into the dashboard's
persisted run history in this phase.

- `manifest.json`: selected package/model, checkpoint and integration-source
  fingerprints, hardware, options, timeouts, and launch arguments.
- `report.json` / `report.md`: trial outcomes, timing, memory, comparisons, and
  separate live-execution/checkpoint/performance checks.
- A directory per trial containing its recipe, result, complete telemetry,
  summary, and frame-associated `decisions.jsonl`.

Each decision includes its observation, learned path/desired speed, proposed and
applied controls, interventions, resulting ego motion, network inference time,
agent-step time, process round trip, and complete-step time. Diagnostics do not
invent explanations for the model's decisions.

Setup/loading and neural warmup are measured separately. The original
`full_step` measurement excludes the evidence callback itself. The `cycle`
measurement includes observation preparation, inference, CARLA advancement,
telemetry validation, progress callbacks, and writing/flushing decision evidence.
The performance gate uses complete cycles; their raw times are retained in each
summary in decision order. Execution wall time and real-time factor also include
evidence I/O. All executed decisions, including the first, remain in the report.
OS process-memory high-water marks and device-wide GPU snapshots are recorded;
GPU peak allocation and a lane-invasion count are explicitly unavailable.
Mapped traffic-rule measurements remain available through the normal evaluator.

## Validation and performance

See [the dated validation record](reference-ai-validation.md) for actual local
trials and limitations, and [performance acceptance](reference-ai-performance.md)
for the optimized 20 Hz results. A completed experiment means that execution finished; it
can still contain collisions, traffic violations, or poor decisions.

The nominal 20 Hz step budget is 50 ms. The report's throughput gate requires
completed repeated live trials, real-time factor at least 1, and at least 95% of
complete cycles including evidence I/O within that budget. Real-time factor is simulated execution
seconds divided by execution wall seconds. Increasing a worker timeout does not
improve this factor.

The performance profile combines lossless scene caching, bounded CUDA graph
replay, float32 SDPA attention, reduced message copying, cached fixed signal
geometry, and scoped Windows timer precision. Windows timer requests are balanced
in both parent and worker and released on exit; they do not change physics time.
Signal states and all model decisions remain live observations on every tick.
CUDA captures cover 0–8 scene objects; larger scenes use the same network through
ordinary SDPA. `cuda_graphs: false` in the reference package configuration disables
capture. CPU execution remains supported without a 20 Hz promise.

The model controls CARLA through a synchronous observation/action loop, and CARLA
waits for inference. Review the report's measured gate on the actual hardware;
the performance profile cannot guarantee 20 Hz under every workload.

API/dashboard agent selection remains Phases 5 and 6. Current dashboard launches
continue to use the existing baseline; use the command above for the learned
reference pilot.

Phase 3 adds SDK 1.2 explicit framework driving tasks, finite routes and measured
goal completion. Use `--driving-task path.json` or embed the task in a recipe.
Initial speed is distinct from the advisory desired speed. The reference stack
declares endpoint padding, task speed caps and goal approach tracking assistance;
raw learned speed and the capped tracking target remain in diagnostics. See
[driving tasks](driving-tasks.md) for commands and outcome interpretation.
