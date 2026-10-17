# Running simulations

Section 4 provides a synchronous simulation runner with two adapters. CARLA mode
uses an optional 0.9.16 client/server; test mode runs deterministic synthetic
kinematics without either. Both return the same shared result and telemetry
contracts. Section 5 adds explicit [local persistence](persistence.md) for results,
finalized telemetry, and optional raw sensor recordings. Section 6 adds an API
worker queue for durable baseline-agent launches, progress/cancellation, and
result/artifact access. See the [API walkthrough](api.md#generate-save-launch-and-read-a-test-run).
Encoded video and replay generation remain later work.

## Run locally

From the repository root, with the development environment activated:

```text
python -m simulator --template sudden_braking --mode test
python -m simulator --scenario scenario.yaml --mode test --output result.json
```

Choose exactly one of `--template` or `--scenario`. The four template choices are
`intersection`, `pedestrian_crossing`, `occluded_pedestrian`, and `sudden_braking`.
`--mode` defaults to `carla`; use `test` explicitly for synthetic runs. The CLI
prints `SimulationResult` JSON or writes it to the explicitly supplied output
path, whose parent must exist. It does not save the telemetry trace. Exit codes
are 0 for completed, 130 for cancelled, and 1 for failed/invalid runs.

The Python API returns both the result and finalized in-memory telemetry:

```python
from scenarios import load_template
from simulator import SimulationRunner, run_simulation

output = run_simulation(load_template("sudden_braking"), mode="test")
print(output.result.simulation.status)
print(output.result.metrics)
print(output.result.runtime.resolved_spawns)
frames = output.telemetry

runner = SimulationRunner()
output = runner.run(
    load_template("pedestrian_crossing"),
    mode="test",
    on_progress=lambda simulation: print(simulation.status, simulation.progress),
    on_frame=lambda frame: print(frame.frame, frame.time_s),
)
```

Create a new runner for every run. A runner rejects repeated or concurrent
execution. It accepts a normalized `Scenario`, scenario mapping, or queued
`Simulation`. A queued record retains its UUID and mode; an explicit mode or
agent must agree with that record. Scenario inputs and frame/progress callback
values are copied so subscribers cannot alter the recorded recipe or trace.

`runner.cancel()` or an injected `threading.Event` requests cooperative
cancellation. The runner checks between ticks; a blocking client call or callback
must return before that request is processed. `KeyboardInterrupt` also cancels.
Validation failures before setup raise an exception. Setup, execution, callback,
finalization, and cleanup failures return a failed result with diagnostics and
any trace collected so far. Cleanup is attempted after partial setup as well.
To save a run, create its queued record through `PersistenceService`, pass that
record's `simulation` to the runner, then persist the returned output with the
expected database revision. The [persistence example](persistence.md#save-run-and-reload)
also shows reloading its finalized trace. Execution does not hold a database
transaction open.

## Timing and events

The runner records an initial frame at elapsed time zero, then advances by
`fixed_delta_seconds`. CARLA uses one initialization tick before that initial
sample so newly attached sensors can deliver data. Native CARLA frame indices
are preserved; elapsed telemetry time is relative to the initialized world.
Every subsequent frame must be consecutive and match the configured step.

The requested number of integration ticks is the ceiling of duration divided by
step, so a nonintegral duration may finish up to one step later. The default
`max_ticks` limit is 100,000 and is checked before opening resources.

Events are sorted by trigger time with original list order retained for ties.
They apply before the first integration interval whose starting elapsed time is
at least the trigger. A time-zero event affects the first interval. A trigger
between ticks rounds up to the next boundary; if that is the terminal boundary,
there is no later interval in which to apply it. When several commands target
the same actor at one boundary, the last command wins.

`set_speed` replaces the actor's target and clears a prior braking command.
`brake` requests a zero target with the specified deceleration. NPC controls
approximate these requests in CARLA. Ego braking events override throttle/brake
while retaining the agent's steering, reverse, and handbrake flags, with brake
`min(1, deceleration_mps2 / 8)`. This control mapping does not guarantee actual
vehicle deceleration. Telemetry records the resulting measured motion.

## Agents

`BaselineAgent` follows the adapter's heading, regulates target speed, and brakes
for a known red light or observed clearance/TTC hazard. Missing critical ego pose
or velocity produces a brake command. It is a development controller with no
perception pipeline, complete route planning, or intersection negotiation. In
CARLA, the adapter follows a deterministic nearby waypoint continuation and
requests a stop when no valid continuation is available.

Implement `BaseAgent.run_step(DrivingObservation)` or wrap a callable:

```python
from shared.models import ControlInput
from simulator.agents import CustomAgent

agent = CustomAgent(lambda observation: ControlInput(brake=1.0))
output = run_simulation(load_template("sudden_braking"), mode="test", agent=agent)
assert output.result.simulation.agent == "custom"
```

Custom control mappings are validated against `ControlInput`. Agent/controller
exceptions fail the run and still trigger cleanup.

Custom agents are available through the Python runner only. API launch requests
use the baseline agent and accept no code or custom controller selector. They
persist `SimulationOptions` and `EvaluationSettings` with the queued recipe and
run each job through a fresh runner. Options live in `shared.models.runtime` and
remain re-exported from `simulator` and `simulator.protocols` for Python callers.

## CARLA execution

Use Python 3.12 with the optional simulation requirements and a matching CARLA
0.9.16 server. Both reported versions are checked. Importing `simulator`, models,
or agents does not import CARLA or connect. Host/port/timeout come from backend
settings, and adapters can also accept explicit connection settings. See
[setup](setup.md) and the official
[Python API](https://carla.readthedocs.io/en/0.9.16/python_api/).

Use a dedicated world with one tick owner. The adapter configures synchronous
fixed-step execution and physics substeps, applies scenario weather, and captures
the prior settings/weather. A concurrent client advancing the world fails the
frame check. CARLA's
[synchrony guidance](https://carla.readthedocs.io/en/0.9.16/adv_synchrony_timestep/)
describes the server assumptions; a seed alone does not guarantee deterministic
physics.

The loaded map must match the scenario by default. `SimulationOptions(
allow_map_reload=True)` or CLI `--allow-map-reload` permits map changes only when
the existing world has no vehicles, walkers, sensors, or controllers that would
be destroyed. Cleanup attempts to restore the original map, settings, and weather.
RPC or restoration failures are reported as run failures rather than hidden.

Actor setup validates blueprint/type, dependency-ordered absolute/map-index/
relative poses, ground and lane placement, heading, relevant built-in approach/
junction/crossing geometry, and overlap using actual bounding boxes. Unspecified
placements use seeded candidate ordering. Explicit invalid placements fail;
they are not silently moved to a different road. The bundled spawn index 0 and
relative layouts remain symbolic and are not certified Town03 anchors. Provide
a map-valid configuration and validate it on the server.

For Town03, [the mapped intersection example](../scenarios/examples/town03_mapped_intersection.json)
provides explicit driving-lane placements obtained from live map geometry. It is
a custom recipe (`template: null`): its ego approach is 60 m instead of the
generic template's 30 m, while speeds, crossing distance, event timing, and
weather retain the defaults. This changes the encounter timing and does not
guarantee a collision. Load its JSON in the dashboard scenario editor, or run:

```text
python -m simulator --scenario scenarios/examples/town03_mapped_intersection.json --mode carla
```

On October 17, 2026, this recipe completed a live CARLA 0.9.16 Town03 API run:
30 simulated seconds, 601 telemetry frames, and 1,202 sensor samples. Other map
versions, occupied spawn locations, and changed recipes require fresh validation.

Native snapshots provide SI velocity/acceleration, true world bounding-box center
and orientation including local offsets, and available speed limits converted
from km/h to m/s. Existing foreign vehicles and pedestrians are observed under
stable `world:<native_id>` identities, with collision-safe naming where necessary;
the adapter never takes ownership of or destroys them. Missing snapshot state or
unavailable speed limits remain unknown. Cleanup stops/destroys owned sensors,
destroys owned actors, then restores the world; all stages are attempted even
when an earlier stage fails. Failed resource handles remain available for retry.

## Sensor observations and raw capture

CARLA mode always attaches an ego collision sensor and a periodic IMU heartbeat.
Optional camera and LiDAR sensors are mounted above the ego roof and can be
enabled with `--camera`/`--lidar` or `SimulationOptions`. Defaults are:

| Option | Default | Meaning |
| --- | --- | --- |
| `sensor_timeout_s` | 2 | Wall-clock wait for each frame's periodic sensors |
| `sensor_settle_s` | 0.02 | Additional bounded wait for independent collision callbacks |
| `camera_width`, `camera_height` | 640, 360 | RGB sensor dimensions, each at most 4096 |
| `camera`, `lidar` | false | Enable raw capture streams |

Camera capture requires world rendering to be enabled. A camera request against
`no_rendering_mode=True` fails before sensors attach; disable that mode before
running. The adapter preserves the configured rendering setting.

Queues are bounded: eight pending frames per periodic stream, 4,096 collision
callbacks, and at most 16 raw samples/64 MiB awaiting drain. Timeouts, duplicate or
stale periodic samples, overflow, and callbacks without a telemetry frame fail
the run. An empty collision list is emitted only after periodic frame delivery
and the settling window. The IMU does not prove collision-stream completion:
independent callback delivery has no perfect cross-stream barrier here.

Finalization waits for in-flight callbacks, stops listeners, waits again, and
merges late collision observations into their original frames before evaluation.
Live `on_frame` subscribers can therefore see an earlier provisional collision
list; use the returned `output.telemetry` as the finalized trace. These bounded
timing assumptions still need validation on a real CARLA installation.

Raw capture is delivered in memory through `on_sensor(SensorSample)`:

```python
from simulator import SimulationOptions

output = run_simulation(
    scenario,  # A map-valid Scenario for the running server.
    mode="carla",
    options=SimulationOptions(camera=True, lidar=True),
    on_sensor=lambda sample: print(sample.kind, sample.frame, len(sample.data)),
)
```

Samples contain native frame/time, immutable bytes, and metadata. Camera encoding
is `bgra8` with width/height; LiDAR is `float32_xyzi` with channels. The runner
drains samples every frame and after finalization, even without a subscriber.
It stores their count in runtime metadata and does not retain/encode their bytes
or write videos, replay files, telemetry, or database records. Consumers that need
capture persistence can pass a storage capture writer's `write` method as
`on_sensor`, then submit that writer with finalized output to the persistence
service. See [capture persistence](persistence.md#capture-raw-sensor-samples).
Test mode rejects optional capture requests because it has no sensor renderer.

The API worker wires enabled captures to the storage writer automatically.
Finalized results expose artifact indexes for downloading the raw manifest and
individual sample files. Live frame streaming and encoded playback are not
provided by these endpoints. See [artifact access](api.md#results-and-artifacts).

## Rule coverage, results, and limitations

The CARLA rule monitor checks measured speeding, mapped lane direction/sidewalk
motion, red stop-line crossings, and stop-sign trigger entry/exit. Red-light
events require an associated lane crossing under a stable known signal; waiting
near a red signal alone is not a violation. Stop checks require an observed
continuous 0.5 second stop at no more than 0.1 m/s within the trigger. Missing
APIs, unreadable map state, ambiguous transitions, or an unknown prior entry leave
the affected family unchecked. This is a project detector definition, not a
regulatory driving assessment.

`rule_checks` explicitly lists observed families; known violations can remain
present under partial coverage. The evaluator does not interpret unavailable
checks as compliance. See [contracts](data-contracts.md) and
[methodology](methodology.md) for the legacy null convention and score coverage.
Failed/cancelled runs retain measured metrics, but noncollision severity stays
null. A known collision still scores 100. Completed runs can also be unscored
when observation coverage is incomplete.

`SimulationResult.runtime` preserves adapter, actual map, client/server versions
when applicable, fixed step, seed, resolved spawns, and drained raw sample count.
The normalized scenario remains the requested recipe. Setup failures may leave
runtime metadata unavailable. `RunOutput.telemetry` is the finalized trace; the
result contains its frame count and measured duration.

The synthetic adapter maps spawn index `i` to `(80*(i//4), 12*(i%4), 0)` at zero
yaw, with seeded fallback placements and composed relative transforms. This is
a virtual coordinate system independent of the named CARLA map. Vehicles use
3 m/s² acceleration and 8 m/s² braking unless an NPC deceleration is requested;
ego steering uses a simple bicycle approximation. Ego-to-other oriented box
overlap is sampled at tick end with an inelastic stop response and zero synthetic
impulse. It has no collision meshes, continuous/substep contact detection,
friction model, perception, or real road network. Its virtual map has a 13.9 m/s
limit and no lights, stop signs, sidewalks, or opposing lanes, so those rule
families are explicitly observed as inapplicable. Synthetic completeness and
scores validate the software path, not real-world safety.

Automated tests exercise the runner, synthetic adapter, and CARLA components with
fake objects. The mapped intersection example above also has a completed live
CARLA run. This limited check does not establish coverage of every template,
map anchor, sensor configuration, or driving behavior.
