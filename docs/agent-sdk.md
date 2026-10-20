# Autonomous agent SDK 1.0–1.3

Phases 1 and 2 provide a Python interface for user-supplied driving agents and an
external worker connected to the simulation runner. The current dashboard still
launches its original baseline controller; use Python, the CLI or the
[registered-agent API](agent-registry.md) for external agents. See the
[ten-phase integration plan](autonomous-agents-plan.md).

The [Phase 2.5 reference AI pilot](reference-ai-pilot.md) integrates a maintained
pretrained driving policy before expanding user-facing model registration. The
current starter remains rule based; the separate reference package uses actual
PlanT 2.0 weights. SDK 1.1 adds explicitly declared structured ground-truth scene
features and a map-derived route/raster for that integration. It does not enable
camera/LiDAR perception. See [the validation record](reference-ai-validation.md)
for live CARLA outcomes and [performance acceptance](reference-ai-performance.md)
for the measured 20 Hz profile.

Phase 3 adds SDK 1.2 driving tasks, route ownership, actual goals, authoritative
controls, persistent intervention audits and measured completion. See
[driving tasks](driving-tasks.md) for contracts and a runnable example.
SDK 1.0 remains the default; explicit tasks require 1.2 or newer.

Phase 4 adds SDK 1.3 camera/LiDAR observations with exact-frame synchronization,
declared calibration, explicit ego-localization assistance and persisted input
provenance. See [sensor agents](sensor-agents.md) for supported rigs and commands.

No CARLA installation, GPU, or ML framework is required to check the supplied
starter. The SDK uses the existing core dependencies and Python 3.12 or newer.

## Check the starter package

From the repository root, using its installed environment:

```powershell
.\.venv\Scripts\python.exe -m scripts.check_agent examples/agents/starter
.\.venv\Scripts\python.exe -m scripts.check_agent examples/agents/starter --smoke-test
```

The first command reads metadata and checks declared files. It does not import
agent code, load weights, install dependencies, or connect to CARLA. A successful
`compatible: true` describes those declaration checks only; inspect `checks` and
`code_executed` in the report.

The explicit `--smoke-test` imports trusted local Python code, calls its complete
lifecycle, and tests two synthetic observations. The output includes validated
controls for frames 0 and 1. Agent Python prints go to stderr so they do not
corrupt the JSON report on stdout. This demonstrates SDK compatibility, not driving
quality, perception accuracy, or CARLA performance. It has no process isolation
or inference deadline; use the process runner below for those. Exit code 0 means
success and 1 means failure.

An editable installation also provides `poltergeist-check-agent`. Reinstall with
`python -m pip install --no-deps -e .` if an existing environment needs the new
console command; the `python -m` form works directly from this checkout.

## Package layout

Copy `examples/agents/starter/` into a directory for your agent:

```text
my-agent/
  agent.json
  agent.py
  requirements.txt
  weights/             # optional, declare each required file in assets
```

The manifest identifies the agent independently from its scenario:

```json
{
  "protocol_version": "1.0",
  "agent_id": "my-driver",
  "name": "My driving agent",
  "version": "0.1.0",
  "entry_point": "agent:MyAgent",
  "observation_mode": "simulator_state",
  "control_mode": "vehicle_control",
  "sensors": [],
  "assets": [],
  "requirements_file": "requirements.txt",
  "configuration": {}
}
```

`entry_point` names a module inside this package and a zero-argument class that
inherits `AutonomousAgent`. Nested modules such as `driver.agent:MyAgent` work.
Use relative imports for sibling modules, for example `from .helpers import ...`.
Loading uses a unique module namespace per package and does not change `sys.path`
or the working directory. Resolve weights relative to your module's `__file__`.

`assets` and `requirements_file` must identify existing files inside the package.
Use portable relative paths with forward slashes; traversal, absolute paths,
and declared symlinks escaping the package are rejected. Inspection does not
execute or verify the meaning of model weights. Install your chosen dependencies
yourself; this phase never installs them automatically. A model file still needs
agent code that loads it, prepares observations, and interprets its predictions.

Loading/smoke-testing executes Python with the caller's permissions. This loader
is for trusted local packages; its path checks are not a sandbox.

## Lifecycle

Import `AutonomousAgent`, `AgentSession`, and the reference `BaselineSDKAgent`
from `simulator.agents`. Message models are public through `shared.models`.

| Operation | Responsibility |
| --- | --- |
| Constructor | Be lightweight; acquire model resources in load so failed initialization can be cleaned up |
| `load(configuration)` | Initialize model/runtime resources from a copied JSON configuration |
| `reset(context)` | Clear episode/recurrent state and initialize the supplied seed, map, ego, timing and optional route/destination |
| `step(observation)` | Return an `AgentStepResult` or matching dictionary with controls for that exact input frame |
| `close()` | Release resources, including those acquired before a failed load |

`AgentSession` enforces load before reset and reset before step. Entering its
context manager does not load automatically. Use:

```python
from simulator.agents import AgentSession
from simulator.agents.package import inspect_agent_package, load_agent_package

package = inspect_agent_package("examples/agents/starter")
agent = load_agent_package(package)  # explicitly execute trusted local code
with AgentSession(agent, package.manifest) as session:
    session.load()
    session.reset(reset_message)    # validated AgentReset from the caller
    result = session.step(observation_message)
    controls = result.control
```

The message variables above come from the caller; the smoke-test command creates
a complete synthetic example. A session may reset between episodes, but a failed
session must be closed and replaced. `close` is attempted once and is idempotent
at the session boundary. Context cleanup preserves an original agent exception
if cleanup also fails. There is no fallback controller.

Existing `BaseAgent.run_step(DrivingObservation)` and `CustomAgent` injection in
the Python simulation runner continue to work. `AutonomousAgent` is the new
versioned lifecycle; `ProcessAgent` connects it to the existing runner.

## Observations and responses

The runtime supports `simulator_state` and SDK 1.3 `sensors` observations with
`vehicle_control` outputs.
Simulator-state inputs contain true actor positions and velocities, including
objects hidden by obstacles. They test planning/control with privileged state;
they do not test camera perception or whether a pedestrian was actually seen.
SDK 1.3 sensor agents receive calibrated RGB/LiDAR bytes and ideal ego kinematics,
with other-actor and evaluator ground truth excluded. Older protocols remain
state-only. IMU/GNSS agent streams are still unsupported. Optional camera
recording alone does not select a sensor agent: the manifest declares the rig.

Agents declaring SDK 1.1 or 1.2 can additionally require `structured_scene`. Their
reset, observation, and result versions must match the manifest. Return
`AgentStepResult(protocol_version=observation.protocol_version, ...)` from a 1.1
agent; legacy models still default to protocol 1.0. The optional scene contains
explicitly sourced world routes, classified objects, map speed limits, and a
bounded semantic road raster. The reference pilot rejects unavailable required
inputs rather than substituting empty actors or a blank map.

`AgentObservation` includes native frame ID, episode-relative `time_s`, `dt_s`,
ego state, actor states, a requested target speed, optional route heading, and
optional red-light observation. Null means unavailable. An empty actor list
means observed with no other actors; do not replace null with an empty list.
The reference baseline rejects unavailable actor observations.

The first observation has time zero and may have any nonnegative native frame
ID. Subsequent frames must be consecutive, with time increments matching the
reset's fixed timestep. Ego identity and timestep must match the reset context.
Inputs, configuration, resets and outputs are defensively copied and revalidated,
including mutated or unchecked model objects.

```json
{
  "protocol_version": "1.0",
  "frame": 410,
  "control": {"throttle": 0.0, "brake": 0.8, "steer": 0.0},
  "diagnostics": {
    "maneuver": "stop",
    "reason_codes": ["pedestrian_ahead"],
    "hazard_actor_ids": ["pedestrian"]
  }
}
```

The response frame identifies the input that produced the decision, not the
future frame resulting from applying it. Throttle/brake must lie in [0, 1] and
steering in [-1, 1]. Non-finite/coerced values, unknown fields, unsupported
versions, invalid controls, stale frames, and mismatched response frames fail.
Missing controls are an error, not an implicit zero-control action.

Diagnostics are optional agent-reported evidence. Planned trajectories are
diagnostics only in this phase, not an alternative control output. Do not infer
decision reasons merely from brake/throttle values. The reference wrapper emits
no diagnostics because the existing baseline does not expose its branch reasons.

## Run an agent in a separate process

From the repository root, try the complete runner without CARLA:

```powershell
.\.venv\Scripts\python.exe -m simulator --template intersection --mode test --agent-package examples/agents/starter
```

By default the worker uses the same Python executable as the parent, in a
separate process. To give an agent its own dependencies, create and prepare a
Python 3.12-or-newer environment yourself:

```powershell
py -3.12 -m venv .agent-venv
.\.agent-venv\Scripts\python.exe -m pip install -e .
.\.agent-venv\Scripts\python.exe -m pip install -r examples/agents/starter/requirements.txt
.\.venv\Scripts\python.exe -m simulator --template intersection --mode test --agent-package examples/agents/starter --agent-python .agent-venv/Scripts/python.exe
```

Replace the starter path and requirements with your own package. An environment
can contain PyTorch, TensorFlow, or other dependencies your implementation needs;
Poltergeist does not install requirements, load arbitrary weight formats, or
convert a model into a driving policy. The selected environment must support
the SDK and have its dependencies installed. Paths containing spaces must be
quoted. Do not modify a package while its worker is running.

For a live experiment, start a matching CARLA server and use the CARLA-compatible
**parent** Python environment described in [setup](setup.md):

```powershell
& 'C:\path\to\carla-env\Scripts\python.exe' -m simulator --template intersection --mode carla --agent-package examples/agents/starter --agent-python .agent-venv/Scripts/python.exe
```

The built-in templates request Town03, so load Town03 first. Alternatively,
`--allow-map-reload` permits loading it only when the current world has no actors
that would be destroyed. See [map and world ownership](simulation.md#carla-execution).

The agent environment does not need CARLA, rendering libraries, or a connection
to the simulator. Only the parent controls the world and applies returned
vehicle controls. A synchronous tick waits for a validated response for its
observation frame. Slow inference therefore increases wall-clock runtime without
changing the configured simulated timestep. Exceeding a deadline fails the run;
no controller silently takes over.

The Python equivalent exposes timing and cancellation:

```python
from scenarios import load_template
from simulator import SimulationRunner
from simulator.agents import AgentProcessOptions, ProcessAgent

agent = ProcessAgent(
    "examples/agents/starter",
    python_executable=r".agent-venv\Scripts\python.exe",
    options=AgentProcessOptions(load_timeout_s=60, step_timeout_s=2),
)
runner = SimulationRunner()
output = runner.run(load_template("intersection"), mode="test", agent=agent)
print(output.result.simulation.status, output.result.simulation.error)
print(agent.stats)
# From another thread, runner.cancel() also interrupts a blocked agent request.
```

The runner loads the agent before world setup, then resets it with the episode
seed, map, ego identity, and timestep. It closes it after any attempted run,
including partial setup, cancellation, and failure. A failed/closed instance
cannot be loaded again; create a new `ProcessAgent` for the next run. Direct users
must call `load(agent.manifest.configuration)`, `reset`, `step`, and `close` in
order and close in a `finally` block.

| Deadline | Default | Override |
| --- | --- | --- |
| Startup and load together | 30 s | `--agent-load-timeout` / `load_timeout_s` |
| Episode reset | 10 s | Python `reset_timeout_s` |
| Each observation/control exchange | 2 s | `--agent-step-timeout` / `step_timeout_s` |
| Graceful close and exit | 2 s | `--agent-close-timeout` / `close_timeout_s` |
| Forced cleanup wait | 1 s | Python `terminate_timeout_s` |

These are wall-clock deadlines, independent of experiment duration. Increase
load time for large weights and step time for slower inference as appropriate.
Cancellation is checked while waiting for agent replies; existing blocking
CARLA RPCs and caller callbacks must still return before the runner can cancel.
A worker that fails, hangs, or is cancelled is terminated along with its child
processes, with bounded waits for cleanup. Cancellation returns the runner's
cancelled status; agent exceptions, malformed replies, and timeouts return failed
status and a phase-specific error.

Transport protocol 1.0 uses UTF-8 newline-delimited JSON, with a default limit of
4 MiB per message. Startup verifies worker identity before loading agent code;
each response must match its request ID and operation. SDK validation on both
sides rejects invalid controls and mismatched observation frames. Agent Python
stdout is redirected to stderr; the parent drains a bounded 64 KiB stderr tail
so logging does not block control delivery. Code that writes directly to the
protocol file descriptor can corrupt the stream and fail the run.

`agent.last_timing` contains the last successful frame's worker step duration and
parent round-trip duration. `agent.stats` is an immutable summary containing
successful step count, total/maximum step and round-trip times, worker PID, and
Python version. Step time includes worker-side SDK validation. Round-trip time
includes serialization, transport, scheduling, and inference. CLI summaries are
JSON on stderr; stdout (or `--output`) remains `SimulationResult` JSON. Agent
diagnostics/timings are not yet persisted as a decision timeline.

This process boundary provides dependency separation and failure cleanup for
**trusted local code**. It does not restrict filesystem, network, or GPU access
and does not make public uploads safe.

## Current integration limits

Phase 3 implements explicit task/route ownership, authoritative agent controls,
recorded scripted interventions and measured goal outcomes. SDK 1.2 agents
default to authoritative mode; older no-task packages retain the legacy heading,
speed and event behavior. The starter remains a rule-based controller. See
[driving tasks](driving-tasks.md) for the current contract and examples.

Sensor inputs and provenance are implemented in Phase 4. Phase 5 adds immutable
trusted local registrations, fingerprint checks and queued API agent selection;
see [the registry guide](agent-registry.md). The [dashboard](dashboard.md) supports
registered agent selection, and [Phase 7](decision-timeline.md) adds persisted
decision review. [Phase 8.1](behavior-analysis.md) provides backend behavior
measurements; dashboard presentation and behavior exports remain Phase 8.2 in the
[integration plan](autonomous-agents-plan.md).
