# Registered autonomous agents (Phase 5)

The local agent registry connects the external Python worker to persistent API
jobs. Register a compatible package and its worker environment, select that
registration when launching a simulation, and retain the selected agent's
manifest, observation mode and code/model fingerprints with the queued run.
The dashboard Agents page and launch selector remain Phase 6.
See [Phase 5 validation](phase5-validation.md) for the tests and their limits.

## Prepare a trusted local package

Run the backend from an environment containing Poltergeist. For CARLA jobs, use
the CARLA-compatible parent Python environment from [setup](setup.md). The
separately selected worker Python needs Poltergeist and the agent's dependencies
installed; the worker does not need the CARLA Python API. Nothing in registration
automatically installs dependencies, downloads weights or starts CARLA.

Begin with `examples/agents/starter` to exercise the API without an ML framework.
It is rule based. The [PlanT reference](reference-ai-pilot.md) uses actual trained
weights and requires its documented model/environment setup. The
[camera/LiDAR pilot](sensor-agents.md) is a separate rule-based example with SDK
1.3 sensor inputs.

Paths refer to files on the **backend's machine**, not a visitor's browser or
another computer. Agent code runs with that backend user's filesystem, network
and hardware access. Registration is for trusted local code; the worker process
is not a security sandbox and does not make public uploads safe.

## Register and run the starter through the API

Start the backend with `python -m backend`. From another PowerShell terminal in
the repository root, register the starter and the worker Python you prepared:

```powershell
$base = 'http://localhost:8000'
$registration = @{
    package_path = (Resolve-Path 'examples/agents/starter').Path
    python_executable = (Resolve-Path '.venv/Scripts/python.exe').Path
    trusted_local_code = $true
    configuration = @{ emergency_ttc_s = 2.0 }
    process_options = @{ load_timeout_s = 30.0; step_timeout_s = 2.0 }
    agent_control_mode = 'authoritative'
} | ConvertTo-Json -Depth 100
$agent = Invoke-RestMethod "$base/agents" -Method Post -ContentType 'application/json' -Body $registration
$agent.id
Invoke-RestMethod "$base/agents/$($agent.id)/readiness"
```

`trusted_local_code: true` is required: submitting a registration declares that
you trust this package for later execution. The optional `configuration` object
overrides manifest configuration values; the effective configuration is saved
with the registration. Omit `python_executable` to use the backend interpreter.
Explicitly selecting an absolute path is useful when the backend and model use
different environments. Only use environments you trust.

Create a short synthetic scenario and launch with the registration's UUID:

```powershell
$recipe = Invoke-RestMethod "$base/scenarios/generate" -Method Post -ContentType 'application/json' -Body '{"template":"sudden_braking","seed":7,"duration_s":5}'
$launch = @{
    scenario = $recipe
    mode = 'test'
    agent_id = $agent.id
} | ConvertTo-Json -Depth 100
$run = Invoke-RestMethod "$base/simulations" -Method Post -ContentType 'application/json' -Body $launch
$runId = $run.simulation.id
do {
    $progress = Invoke-RestMethod "$base/simulations/$runId/progress"
    if ($progress.status -in @('queued', 'running')) { Start-Sleep -Milliseconds 250 }
} while ($progress.status -in @('queued', 'running'))
$result = Invoke-RestMethod "$base/simulations/$runId/result"
$result.simulation | Select-Object status, error, agent, agent_snapshot
$result.agent_inputs
```

`agent_id` is the returned **registration UUID**, not the manifest's human-readable
`agent_id` such as `starter-agent`. Omit it or pass null to launch the baseline.
The existing `scenario_id` alternative, execution `options`, and
`evaluation_settings` remain available. The launch request cannot change a
registered package's configuration, Python, deadlines or observation mode;
register a new selection for those changes. Synthetic test results establish the
software connection, not live driving or sensor performance.

The selected snapshot is returned as `simulation.agent_snapshot` and
`job.agent_snapshot` on run records. A terminal result retains it under
`result.simulation.agent_snapshot`. `result.agent_inputs` describes inputs
actually supplied during execution; it can be null when setup fails before
observations exist.

## List, inspect and deactivate registrations

| Method and route | Behavior |
| --- | --- |
| `POST /agents` | Register a local package and return its immutable selection |
| `GET /agents?limit=50&offset=0` | List persisted registrations; optional `active=true` or `active=false` filter |
| `GET /agents/{id}` | Read one registration and fingerprint snapshot |
| `GET /agents/{id}/readiness` | Repeat static declaration/file checks without executing code |
| `POST /agents/{id}/deactivate` | Prevent new launches using that registration |

For example:

```powershell
Invoke-RestMethod "$base/agents"
Invoke-RestMethod "$base/agents/$($agent.id)"
Invoke-RestMethod "$base/agents/$($agent.id)/deactivate" -Method Post
```

Every registration receives a new UUID, including another registration of the
same manifest version. Registrations are not edited or deleted. To change code,
weights, configuration or worker settings, create a new registration, then
deactivate the old one when it should no longer accept launches. Deactivation
preserves history and does not cancel already queued or active experiments.
Use the existing simulation cancellation route for that.

`agent_control_mode: "scripted"` is available for deliberate legacy ego-script
experiments, with interventions recorded by the runner. Authoritative mode lets
the agent choose the ego response and rejects conflicting scenario ego events.
Sensor runs require authoritative mode.

## Register a sensor agent or the learned reference

The same request works for a sensor package: set `package_path` to
`examples/agents/sensor_pilot`. For a live trial use the explicit task in
`examples/scenarios/sensor_pilot_town03.json`, CARLA mode and RGB rendering:

```powershell
# $sensorAgent is the registration response for examples/agents/sensor_pilot.
$sensorRecipe = Get-Content 'examples/scenarios/sensor_pilot_town03.json' -Raw | ConvertFrom-Json
$sensorLaunch = @{
    scenario = $sensorRecipe
    mode = 'carla'
    agent_id = $sensorAgent.id
    options = @{ allow_map_reload = $true; no_rendering_mode = $false; sensor_timeout_s = 5.0 }
} | ConvertTo-Json -Depth 100
Invoke-RestMethod "$base/simulations" -Method Post -ContentType 'application/json' -Body $sensorLaunch
```

Run one idle CARLA instance and start the backend with its matching CARLA Python
environment. Camera rigs require rendering; the manifest selects the agent's
input rig, while `options.camera`/`options.lidar` select optional legacy recording
streams. Sensor agents require an explicit driving task and authoritative
controls. Keep `agent_control_mode` at its default `authoritative`; scripted ego
events are rejected. See [the sensor guide](sensor-agents.md) for constraints and
the example's limitations.

For PlanT, first complete [the reference setup](reference-ai-pilot.md), then
register `examples/agents/reference_ai` with the Python environment containing
its ML dependencies. The maintained reference integration automatically resolves
and fingerprints its actual checkpoint, including its default checkpoint path.
For other agents, list any weights outside the package in `model_files` as
absolute local file paths without symlink or junction components. Keep in-package
assets declared in `agent.json`.
Undeclared external files are outside fingerprint coverage.

## What registration establishes

Manifest inspection validates SDK capabilities and declared package files
without importing the agent. The readiness response identifies itself as
`inspection: "static_only"`, with `inference_checked: false`, issues and explicit
limitations. A matching fingerprint establishes the registered file identity at
a check point. Neither inspection nor a fingerprint proves that dependencies
import, a model loads, an agent drives well or live CARLA meets a timing target.
Use an actual bounded execution to test that integration.

Simulator-state packages can receive privileged other-actor state. Sensor
packages declare synchronized RGB/LiDAR rigs and exclude those ground-truth
inputs while retaining labelled ideal ego localization and any declared
navigation assistance. Registering a package does not change its declared
observation mode.

## Queue and reproduction boundary

The durable launch record retains its resolved selected agent and runtime
settings through waiting, cancellation and process restart. Deactivating a
registration later does not select different code for a job already accepted.
Snapshots are checked again before process/native setup and in the worker before
agent import. If required registered content is missing or changed before
execution, the job fails explicitly. There is no fallback to the baseline or to
a newer agent version.

Fingerprints cover package regular files, declared/explicit model files,
first-party Poltergeist runtime Python files and the selected interpreter's
bytes. Cache, VCS and virtual-environment directories are excluded from the
package inventory. The snapshot includes per-file SHA-256 and size, aggregate
package identity, effective manifest/configuration, observation mode, worker
deadlines and control ownership.

The registry does not freeze or copy those files. Fingerprints are not a
container image, an installed third-party dependency lock, a copied CARLA
installation or a complete hardware/environment snapshot. Files imported by
third-party dependencies are not covered. Preserve the original package, model
assets and compatible worker environment if you need to execute that selection
again. Existing CLI package execution remains available; see the
[SDK commands](agent-sdk.md#run-an-agent-in-a-separate-process).

Agent-aware automated comparisons, reproduction/search and minimization remain
Phase 9. Automated reproduction or minimization of custom-agent failures is
rejected explicitly until that support exists. Retaining a snapshot makes the
selected controller inspectable; it does not by itself make every existing
batch or optimization workflow agent aware.
