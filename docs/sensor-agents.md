# Sensor agents (Phase 4)

SDK 1.3 delivers actual RGB camera and ray-cast LiDAR data to an external agent.
The runner waits for the exact declared sensor bundle for the current native
CARLA frame, then requests one control and advances the simulation once. Other
actors' state, ground-truth boxes, map rasters, signal states and evaluator
measurements are excluded from sensor observations.

The evaluator retains full telemetry separately. Sensor observations include
explicitly labelled **ideal simulated ego localization/kinematics**: the ego's
own pose, velocity and acceleration. This is assistance, not noisy GNSS/IMU or
visual odometry. Framework navigation tasks can also supply a finite route and
heading. Agent-owned tasks receive the destination without framework route
guidance, as in [Phase 3](driving-tasks.md).

## Run the supplied example

The sensor teaching controller uses LiDAR returns for corridor braking and RGB
brightness for a visibility guard. It follows the supplied route using ideal ego
localization. It is rule based, has no trained perception model, and does not
recognize traffic lights, classify objects or predict their motion. The existing
trained PlanT reference remains a **simulator-state** agent; this phase does not
convert it into a camera model.

First check the example without CARLA:

```powershell
.\.venv\Scripts\python.exe -m scripts.check_agent examples/agents/sensor_pilot --smoke-test
```

This executes trusted local code on clearly labelled deterministic sensor test
patterns. It tests interface compatibility, not rendering, sensing or driving
quality. Test-mode sensor fixtures deliberately do not reconstruct the synthetic
scene's actors or occlusion.

With one idle CARLA 0.9.16 instance running, use its Python 3.12 environment:

```powershell
$carlaPy = 'C:\Users\erich\.venvs\poltergeist-carla\Scripts\python.exe'
& $carlaPy -m simulator --scenario examples/scenarios/sensor_pilot_town03.json --agent-package examples/agents/sensor_pilot --agent-python .venv/Scripts/python.exe --allow-map-reload --rendering --sensor-timeout 5 --output storage/reports/sensor-result.json --decision-trace storage/reports/sensor-decisions.jsonl
```

The worker needs the normal Poltergeist installation and Python 3.12 or newer;
this example requires no ML library or model download. `--rendering` scopes RGB
rendering to this run and restores the previous setting. `--no-rendering` rejects
camera rigs; LiDAR-only agents can run without rendering. Offscreen rendering is
compatible with cameras. Use a new decision-trace filename for each run; traces
are never overwritten. RGB payloads make traces much larger than state-only logs.

For interface development without CARLA, use `--mode test` and a synthetic task
such as `examples/scenarios/goal_following.json` with the sensor package. Those
observations identify themselves as `deterministic_fixture`. A successful test
run cannot establish live camera/LiDAR operation.

## Declare the rig and consume inputs

The example's manifest illustrates the supported declaration:

```json
{
  "protocol_version": "1.3",
  "agent_id": "my-sensor-agent",
  "name": "My sensor agent",
  "version": "0.1.0",
  "entry_point": "agent:MyAgent",
  "observation_mode": "sensors",
  "navigation_mode": "provided_route",
  "sensors": [
    {"id": "front", "type": "sensor.camera.rgb",
     "attributes": {"image_size_x": 320, "image_size_y": 180, "fov": 90.0},
     "mount": {"position": {"x": 1.5, "z": 2.0}}},
    {"id": "roof", "type": "sensor.lidar.ray_cast",
     "attributes": {"channels": 32, "range": 60.0, "points_per_second": 56000,
                    "rotation_frequency": 20.0},
     "mount": {"position": {"x": 1.5, "z": 2.0}}}
  ]
}
```

The existing SDK lifecycle and dependency worker are unchanged. `reset.sensors`
contains the fully resolved calibration, including defaults and relative mount.
Camera FOV resolves to CARLA's native float precision.
Every observation's `sensors` list contains exactly those IDs and types, with
native frame, episode-relative timestamp, encoding, checksum and lossless base64
bytes. Use `sample.raw_data()` to decode them. Camera frames include width, height
and horizontal FOV; LiDAR frames include channel count. Byte formats and local
axes follow [CARLA's sensor reference](https://carla.readthedocs.io/en/latest/ref_sensors/):
BGRA uint8 pixels, and little-endian float32 XYZI points with x forward, y right,
z up in sensor coordinates. Apply the declared full mount transform when
interpreting returns in ego coordinates.

`observation_mode` is `sensors`, `sensor_source` is `carla_sensors` or
`deterministic_fixture`, and `ego_state_source` is `simulated_ego_kinematics`.
`actors`, `scene` and `traffic_light_red` are null. Ego boxes and speed limits are
also null. Task speed is advisory. Sensor runs require an explicit driving task
and authoritative control; scripted ego events are rejected before startup.

The sample controller requires one full LiDAR sweep per step. Other agents can
interpret partial scans or accumulate history themselves. The generic runtime
does not invent missing returns or provide semantic LiDAR actor IDs.

## Bounds and failure behavior

- One to four RGB/LiDAR streams, each with a unique ID and a mount within 10 m
  of the ego origin. IMU/GNSS agent streams are not implemented in this phase.
- `sensor_tick` must be zero: every physics tick produces a required sample.
  Unsupported attributes fail capability inspection. Numeric attributes use JSON
  numbers; see `simulator/agent_sensors.py` for canonical defaults and bounds.
- The complete bundle is limited to 2 MiB of decoded bytes, fitting the default
  4 MiB worker envelope after base64 encoding. Configurations over the declared
  budget fail before setup; runtime overflow also fails explicitly.
- Camera dimensions, FOV, LiDAR channels, relative mount, source, byte lengths,
  SHA-256, finite XYZI values, native frame and timestamp are checked. Missing,
  stale, duplicate or future samples cannot silently replace current data.
- Queues and sensor waits are bounded. Cancellation interrupts sensor waits.
  Setup, callback, worker and sensor failures stop and destroy owned resources;
  they do not substitute a baseline control. The normal final measured sensor
  bundle is validated even though no subsequent action consumes it.

GPU camera delivery may lag wall time; the runner waits within the configured
deadline rather than issuing extra physics ticks. CARLA's
[synchronous timing guide](https://carla.readthedocs.io/en/latest/adv_synchrony_timestep/)
explains the clock boundary. The state-only reference AI's Phase 2.5 throughput
measurements do not promise the same performance for rendered sensor workloads.

## Review evidence

Results now contain `agent_inputs`: observation mode, actual/fixture source,
resolved sensor calibration, ideal ego assistance and whether other-actor ground
truth was supplied. Legacy results can have null provenance. The optional local
decision trace records the exact sensor observations, diagnostics, controls and
resulting ego motion. Recorded evaluator telemetry stays separate from the agent
input object. Raw recording storage supports distinct streams of the same kind.
Saved sensor recordings are also checked against the declared rig and finalized
telemetry clock. Their source, mount and calibration cannot silently diverge
from the recorded input provenance.

These payload boundaries are for trusted local agent processes, not isolation
against code that independently reads files or opens a CARLA connection. Public
uploads still need isolated workers.

The [Phase 5 registry](agent-registry.md) supports selecting these packages for
queued API runs. Dashboard launch controls remain Phase 6; full persisted
action-timeline review remains Phase 7.
See [Phase 4 validation](phase4-validation.md) for measured live results and limits.
