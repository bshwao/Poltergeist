# Driving tasks and control ownership (Phase 3)

Phase 3 separates a scenario's starting state from the driving objective.
SDK 1.3 retains these task contracts for [sensor agents](sensor-agents.md).
Scenarios determine actor placement, initial speed, weather and NPC events.
An optional `driving_task` determines the destination, navigation ownership and
measured goal criteria. The agent returns each evolving control action.

## Run the complete example

From the repository root, with the core environment installed:

```powershell
.\.venv\Scripts\python.exe -m simulator --scenario examples/scenarios/goal_following.json --mode test --agent-package examples/agents/goal_following --output storage/goal-result.json
```

This uses synthetic kinematics and a rule-based teaching agent. It needs neither
CARLA nor weights. The maintained trained model is the separate
[reference AI](reference-ai-pilot.md).

## Define a goal independently of initial speed

For example, an ego actor can start at `speed_mps: 2.0`, while its task requests
a cruising speed of 4 m/s and stopping near a destination:

```json
{
  "route_owner": "framework",
  "destination": {"position": {"x": 12.0, "y": 0.0, "z": 0.0}},
  "target_speed_mps": 4.0,
  "completion_radius_m": 1.5,
  "completion_height_tolerance_m": 2.0,
  "completion_speed_mps": 0.5,
  "completion_hold_s": 0.2,
  "stop_on_completion": true
}
```

These coordinates illustrate the synthetic world. CARLA coordinates must belong
to the actual loaded map. A CARLA spawn index has different synthetic coordinates.
Save a task as JSON and use `--driving-task path.json` with either
`python -m simulator` or `python -m scripts.run_reference_ai`; the override becomes
part of the saved recipe. Alternatively embed it directly in scenario JSON/YAML.

`target_speed_mps` is advisory, not a forced velocity. It may be null for SDK 1.2
agents that choose their own speed. The actor's speed is only an initial
condition. Baseline/legacy controllers need a numeric task target and framework
route. Units are metres, seconds and metres/second; rotations use degrees.

## Choose who plans the route

| Task | Agent declaration | Responsibility |
| --- | --- | --- |
| `route_owner: "framework"` | `navigation_mode: "provided_route"` or `"agent_planner"` | Framework supplies a finite route; agent chooses its evolving path and controls |
| `route_owner: "agent"` | SDK 1.2 and `navigation_mode: "agent_planner"` | Only the destination is supplied; agent must plan its own route |

Framework tasks can supply a world-coordinate `route` of 2–4096 points. The last
position must match the destination; omitting the destination uses the last point.
CARLA validates driving-lane membership, elevation, directed connectivity and
interpolated segments. Sparse segments cannot cut across grass between valid
endpoints. Alternatively, supply only a destination: a bounded directed search
over CARLA waypoints resolves a route from the measured ego pose. Invalid or
disconnected goals fail explicitly. Routes exceeding the bounded search or
4096-point limit need a shorter task or an agent planner. This planner follows
forward driving lanes; it does not plan lane changes.
If the first supplied point is ahead of the spawn, the actual directed approach
is retained. A bounded progress cursor keeps guidance on the current traversal
through loops and repeated intersections.

SDK 1.2 reset receives the full resolved route once in `reset.route` and the actual
goal in `reset.destination`. Observations retain an immutable task descriptor
with `route: []`. Structured scenes contain a finite local route slice ending at
the destination. An already-at-goal reset may contain one terminal point.
Agent-owned tasks receive no framework route or heading. Nearby traffic controls
remain observable when the agent owns navigation.

The synthetic adapter has no road graph: framework destination-only tasks use a
direct virtual segment. The teaching agent's direct-to-goal planner is restricted
to this synthetic world. Neither demonstrates road-network planning.

## Control authority and evidence

`--agent-control-mode auto` is the default. SDK 1.2 agents use authoritative
control; older agents without tasks retain scripted mode. Explicit tasks require
SDK 1.2 for external agents. Authoritative mode rejects scripted ego speed/brake
events before loading the worker or spawning actors. NPC events still run. The
runner applies validated agent controls without a baseline substitution or map
failure stop command. Task guidance bypasses legacy target-speed fallback logic.

For deliberate scripted experiments, use `--agent-control-mode scripted`. Active
ego speed events change the requested speed; braking events replace throttle and
brake. `result.control_audit` records the controller, control mode, navigation
source and each affected observation frame, time, proposed control, applied
control and scripted target. Task outcomes retain matching interventions.
An intervention requires a valid measured transition. These records describe
framework influence; they do not explain a model's internal reasoning.

Scenarios without tasks retain their existing behavior and are labelled
`legacy_assisted`. A rolling scene horizon is no longer misrepresented as a
global goal: `reset.destination` is null without an explicit task. Old saved
records with no audit remain readable with unknown control authority.

## Measure completion

`driving_outcome` is separate from execution status and safety metrics:

- `reached`: measured XY distance and height satisfy the goal, plus optional
  measured speed continuously for the requested hold time.
- `timed_out`: execution completed its time window without reaching the goal.
- `unavailable`: final pose is unavailable and no earlier reach was established.
  Missing velocity cannot establish a speed-qualified goal.
- `failed` / `cancelled`: execution failed or was cancelled. A failure before
  telemetry can have no outcome, and an earlier goal visit does not erase failure.

Reach is latched. `stop_on_completion: true` ends after the first qualifying
measured frame, without an extra tick or invented braking action. Otherwise the
full experiment continues, retaining first reach time and final distance.
The default is false. Without a speed criterion, driving through the goal can
satisfy it. Specify `completion_speed_mps` for a stop. A separate height check
prevents completing a destination by driving underneath it on another level.

Outcomes record actual initial speed, final distance/height error, resolved route,
first reach time and progress. Progress is the furthest observed horizontal
polyline coordinate within an 8 m route corridor and the task's height tolerance;
it is not a lane-keeping score. Agent-owned tasks have no framework route progress.
Persistence replays saved telemetry and accepts early completed traces only when
their opt-in goal completion is independently verified.

## Reference AI behavior

The reference package now declares SDK 1.2 with `provided_route` navigation.
For explicit tasks, finite route samples are clamped to the actual endpoint when
less than the model's 21.5 m input horizon remains. No road is extrapolated past
the goal. Without a task, the existing full route requirement remains.

The neural model still runs every decision. Its learned path and desired speed
feed the declared deterministic tracking controller. Task cruising-speed caps and
goal approach caps, when a completion-speed criterion is specified, are declared
assistance within the reference stack. They only reduce learned desired speed.
Diagnostics retain raw neural speed, the upstream startup-adjusted speed,
tracking target, caps and assistance labels. Standstill startup limits are also
declared separately.
This tests the model-plus-controller stack, not learned global routing or learned
goal stopping.

```powershell
& $carlaPy -m scripts.run_reference_ai --scenario examples/scenarios/reference_ai_goal_town03.json --agent-python .agent-venv/Scripts/python.exe --performance --output storage/reports/my-goal-trial
```

Use a new report directory. Recipes/results preserve the task, audit and outcome;
decision diagnostics preserve tracking assistance. [Phase 7 decision review](decision-timeline.md)
is available for queued API runs. Sensor support is described in [Phase 4](sensor-agents.md), and
[Phase 5 registration](agent-registry.md) supports queued API agent selection.
The [dashboard launch selector](dashboard.md#configure-and-launch-a-run) can use
a registered agent. Paste a recipe containing the required `driving_task` into
the scenario JSON editor and validate it before launching.

The mapped example was verified on CARLA 0.9.16 Town03. Start one idle CARLA
instance with that map loaded, or add `--allow-map-reload` to load it. The reference
worker and weights must be installed as described in the pilot guide.
See [Phase 3 validation](phase3-validation.md) for measured results.
