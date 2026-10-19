# Explicit goal example

This deterministic teaching controller demonstrates SDK 1.2 destinations,
route ownership, advisory target speeds, and measured completion. It is **not
a trained AI model** and has no obstacle avoidance or traffic compliance.

From the repository root, run:

```powershell
.\.venv\Scripts\python.exe -m simulator --mode test --scenario examples/scenarios/goal_following.json --agent-package examples/agents/goal_following --output storage/reports/goal-following.json
```

The recipe starts at 1 m/s, advises 4 m/s, and asks the controller to stop near
an explicit destination and remain below 0.3 m/s for 0.2 simulated seconds.
The run ends when those criteria are measured. Its result includes a separate
`driving_outcome`; execution completion alone is not goal completion or safety.

With `route_owner: "framework"`, the example follows the complete finite route
delivered once through `reset.route`. In synthetic mode a destination-only
framework task resolves to a virtual straight segment. CARLA resolves actual
connected driving-lane routes; synthetic coordinates do not identify CARLA roads.

With `route_owner: "agent"`, the framework supplies no route or heading. This
example then steers directly toward the goal **only when observations identify
the synthetic test world**. It rejects this mode in CARLA because it has no
road planner. A genuine navigation agent must implement that planner itself.

The target speed is advisory. If it is absent, this controller explicitly uses
its own `cruise_speed_mps` configuration; it never infers a goal speed from the
actor's initial speed. Diagnostics state which source it used. Dependencies
are the existing Poltergeist installation; no model download is required.
