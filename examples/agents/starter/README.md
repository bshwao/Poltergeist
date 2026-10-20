# Starter driving agent

The maintained starter declares SDK 1.3 and version 0.2.0. It supports explicit
framework driving tasks and preserves the input protocol in every response.
After updating the package, create a new registration because executable
fingerprints have changed. [First-run walkthrough](../../../docs/first-run.md).

This editable package demonstrates the agent SDK and external worker. It wraps
Poltergeist's deterministic, rule-based baseline. It is **not a trained driving
AI**, a perception system, or a destination/path planner. It follows the supplied
route heading, regulates speed, and brakes for red lights or nearby hazards.

Its `simulator_state` observations contain simulator ground truth, including
actors hidden behind obstacles. This example cannot measure learned perception
or realistic occlusion response. `actors: null` means unavailable and is rejected;
`actors: []` means the road was observed with no other actors.

## Check the package

From the repository root, using the Python environment with Poltergeist installed:

```powershell
.\.venv\Scripts\python.exe -m scripts.check_agent examples/agents/starter
```

The default check validates the manifest, declared files, and supported
capabilities without importing agent code or installing dependencies. To exercise
the agent's lifecycle with a synthetic observation, explicitly run:

```powershell
.\.venv\Scripts\python.exe -m scripts.check_agent examples/agents/starter --smoke-test
```

The smoke test executes the package's Python code in the current environment.
Use it only for code you trust. It does not start CARLA, launch a dashboard run,
or prove driving competence. To execute a scenario through a separate worker:

```powershell
.\.venv\Scripts\python.exe -m simulator --template intersection --mode test --agent-package examples/agents/starter
```

Use `--agent-python C:/path/to/agent-env/Scripts/python.exe` for a separately
prepared dependency environment. Use `--mode carla` with a compatible parent
environment and running server for live execution. The worker is closed on
completion, failure, and cancellation, and hung operations have deadlines. See
the [SDK guide](../../../docs/agent-sdk.md#run-an-agent-in-a-separate-process)
for installation, timing, and timeout settings. Register the package in **Agents**
and select it in **Scenarios** or **Experiments** for the dashboard workflow.

## Adapt it to your model

1. Copy this folder, then change `agent_id`, `name`, and `version` in `agent.json`.
2. Keep `entry_point` pointed at your Python module and class. The entry class must
   implement the SDK's `load`, `reset`, `step`, and `close` methods.
3. Replace the reference `step` call in `agent.py` with preprocessing, inference,
   and conversion to `AgentStepResult(protocol_version=observation.protocol_version, frame=observation.frame, control=...)`.
   Throttle and brake must be between 0 and 1; steer must be between -1 and 1.
   Optional diagnostics should reflect information your agent actually produces.
4. Initialize weights in `load`, clear recurrent/episode state in `reset`, and
   release resources in `close`. Declare weight paths in `assets`, relative to
   the package folder. Resolve them from `Path(__file__).resolve().parent`, not
   the current working directory. Add extra dependencies to `requirements.txt` and install
   them yourself in the intended environment.
5. Run the checks again. The Phase 1 checker supports simulator-state agents with
   direct vehicle-control outputs. Sensor inputs and trajectory-to-control
   adapters are not delivered by this example.

The baseline configuration accepts `target_speed_mps`, `emergency_ttc_s`, and
`minimum_clearance_m`. If `target_speed_mps` is omitted, the controller uses the
observation's requested speed. A reset recreates the controller from the loaded
configuration. The baseline does not emit decision diagnostics; a missing
diagnostic field must not be interpreted as evidence of any particular reason.
