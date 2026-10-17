# Scenario engine

Section 2 provides file loading, structural and template validation, parameter
discovery, seeded generation, and saving. All operations are independent of CARLA.
Public functions and error types are exported from `scenarios`.

## Create and reproduce a scenario

Use template defaults for a controlled configuration, or sample variations:

```python
from scenarios import (
    build_scenario,
    generate_scenario,
    generate_scenarios,
    get_parameter_specs,
    load_scenario,
    load_template,
    save_scenario,
)

baseline = load_template("pedestrian_crossing")
configured = build_scenario(
    "pedestrian_crossing",
    seed=42,
    parameters={"ego_speed_mps": 15, "crossing_delay_s": 2},
)
variation = generate_scenario(
    "pedestrian_crossing",
    seed=42,
    parameters={"ego_speed_mps": 15},
    ranges={"pedestrian_speed_mps": (1, 2)},
)
save_scenario(variation, "scenario.yaml")
assert load_scenario("scenario.yaml") == variation

batch = generate_scenarios("sudden_braking", 5, seed=100)
assert batch[0] == generate_scenario("sudden_braking", seed=100)

for name, spec in get_parameter_specs("intersection").items():
    print(name, spec.minimum, spec.maximum, spec.default, spec.unit)
```

`build_scenario` uses defaults for unspecified parameters; `generate_scenario`
samples them uniformly from declared bounds. Fixed `parameters` override sampling
and must satisfy the accepted input bounds, which are wider than sampling bounds.
For example, `parameters={"ego_speed_mps": 40}` preserves a 40 m/s speed rather
than clamping it to the original 25 m/s sampling maximum. `ranges` narrows sampling bounds only;
fixed values take precedence, including when outside a narrowed range. All range
definitions are validated even if the corresponding parameter is fixed.

`build_scenario`, `generate_scenario`, and `generate_scenarios` also accept an
optional `duration_s` keyword. Omitting it preserves the template duration.
Supplied values must be finite and positive, allow at least one fixed step, and
extend past all configured event triggers. The saved recipe and simulation runner
use this duration; it is not an extra template parameter or a wall-clock timeout.

Sampling uses a local `random.Random(seed)` and never changes global RNG state.
The same template version, seed, overrides, and ranges reproduce the same complete
payload. Save the complete scenario as the durable record: changing templates or
the generator can change later output for a seed. This does not guarantee CARLA
physics determinism, which also needs synchronized simulator settings.

Batch seeds are consecutive and wrap from 2,147,483,647 to 0. Each item can be
regenerated independently, and increasing batch size preserves the earlier
prefix. A count of zero returns an empty list; negative/fractional/boolean counts
and invalid seeds are rejected.

## Templates and parameter bounds

Every bundled template defaults to `Town03`, a 30 second duration, a 0.05 second fixed
step, stable actor IDs, and explicit blueprint choices. Generic custom scenarios
retain the model's 60 second default duration. Blueprint names are drawn from the
[CARLA 0.9.16 blueprint library](https://carla.readthedocs.io/en/0.9.16/bp_library/).

All four templates share:

| Parameter | Normal sampling range | Accepted manual input | Default | Unit |
| --- | --- | --- | --- | --- |
| `ego_speed_mps` | 1–25 | 0–80 | 12 | m/s |
| `precipitation_pct` | 0–100 | 0–100 | 0 | % |
| `fog_density_pct` | 0–60 | 0–100 | 0 | % |
| `sun_altitude_deg` | -10–90 | -90–90 | 70 | degrees |

Precipitation sets both rain intensity and road wetness. Cloudiness retains its
template value. Normal sampling ranges keep seeded generation and automated
searches focused on the original experiments. Wider manual input bounds are
application guardrails for experiments, not CARLA maximum capabilities or a
safety classification. Weather percentages and sun altitude use CARLA's
[documented domains](https://carla.readthedocs.io/en/0.9.16/python_api/#carla.WeatherParameters).
The dashboard displays descriptions without appending the old sampling ranges.

`pedestrian_crossing` starts a stationary pedestrian to the ego's right, facing
across its path; a timed event starts the crossing:

| Parameter | Normal sampling range | Accepted manual input | Default | Unit |
| --- | --- | --- | --- | --- |
| `pedestrian_speed_mps` | 0.5–3 | 0–12 | 1.4 | m/s |
| `pedestrian_distance_m` | 15–45 | 1–500 | 18 | m forward of ego |
| `pedestrian_lateral_offset_m` | 2–8 | 0.5–20 | 4 | m right of ego |
| `crossing_delay_s` | 0–8 | 0–3600 | 1 | seconds |

`occluded_pedestrian` uses the same bounds and adds `occlusion_gap_m` (normal
sampling 1–8 m, manual input 0.5–50 m, default 4). The gap must be smaller than
`pedestrian_distance_m`. A parked van is placed this distance closer to the ego along the
forward axis, with its lateral position projected onto the initial ego-to-person
sightline. This defines intended occlusion geometry; actual visibility depends
on actor dimensions, map geometry, and sensor placement in the simulator.

`intersection` starts a crossing vehicle to the ego's left, facing right across
a conflict point ahead of the ego:

| Parameter | Normal sampling range | Accepted manual input | Default | Unit |
| --- | --- | --- | --- | --- |
| `crossing_speed_mps` | 1–20 | 0–80 | 10 | m/s |
| `intersection_distance_m` | 10–60 | 1–500 | 30 | m along the ego approach to the conflict |
| `crossing_distance_m` | 5–40 | 1–500 | 25 | m along the crossing approach to the conflict |
| `crossing_delay_s` | 0–8 | 0–3600 | 0 | seconds |

`sudden_braking` starts a moving lead vehicle directly ahead and triggers braking:

| Parameter | Normal sampling range | Accepted manual input | Default | Unit |
| --- | --- | --- | --- | --- |
| `lead_speed_mps` | 1–25 | 0–80 | 12 | m/s |
| `lead_distance_m` | 5–50 | 5–500 | 20 | m forward of ego |
| `brake_delay_s` | 0.5–8 | 0–3600 | 2 | seconds |
| `lead_deceleration_mps2` | 1–10 | 0.1–20 | 6 | m/s² magnitude |

Delays must also precede the selected experiment duration. Larger distances still
require suitable map geometry, mapped crossings, and non-overlapping actors.
Vehicle physics and available grip determine the speed and deceleration actually
achieved; accepting a requested value does not guarantee the vehicle can hold it.

## Placement and timed behavior

An actor can specify one placement source: absolute `spawn`, map
`spawn_point_index`, or `relative_spawn`. With none, the adapter selects a
map spawn. A relative offset is in the anchor's local
frame: x forward, y right, z up, with a relative yaw in degrees. Relative actor
references must exist, cannot reference themselves, and must form an acyclic
dependency graph.

Built-in layouts express scenario intent and are resolved together by the CARLA
adapter when execution starts. A built-in ego `spawn_point_index` is an anchor
hint, rather than a requirement to force the scenario onto that road. Braking
and pedestrian templates first try the hinted anchor, then search sorted map
waypoints in a seeded order for a complete valid layout. Their configured local
distances and occluder geometry are retained. Pedestrians stop after traversing
the configured crossing instead of walking indefinitely beyond mapped ground.

Intersection templates find two connected driving lanes that cross within the
same junction and at the same elevation. Ego and crossing distances are measured
backwards along those lanes from their shared conflict point. Real lane headings
replace the symbolic right angle. The controller follows the selected paths
through the junction so overlapping lanes do not produce an ambiguous heading.
Speeds, weather, event timing, and the requested distance parameters are retained.

Every candidate still undergoes ground, lane/heading, and actual bounding-box
validation. An occupied layout is cleaned up and another candidate is tried,
without removing foreign actors. If no suitable layout exists, execution reports
an actionable placement error; every distance combination is not feasible on
every map. Exact results also depend on the CARLA map version and existing traffic.
Runtime metadata records the resolved spawn transforms without modifying the
saved recipe. Loading, generation, and JSON validation do not connect to CARLA.

To pin a built-in layout, supply an absolute ego `spawn` instead of an index.
Custom recipes (`template: null`) retain strict placement semantics for all
actors: invalid explicit coordinates are never silently relocated. Test mode
continues to use its own virtual coordinates and cannot establish map validity.

Events reference actor IDs. `set_speed` requests a target speed at an elapsed
simulation time; `brake` requests positive deceleration until stopped. Triggers
must be nonnegative and strictly before scenario end. The runner dispatches
events against the simulation clock; adapters control the resulting physical motion.
Events now execute before integration at the first tick boundary at or after
their trigger. Equal-time events retain saved list order, and the last command
for an actor wins. A non-grid trigger can round to the terminal boundary and have
no remaining interval to affect. Speed/deceleration requests become controls;
actual measured motion can differ in CARLA. See [simulation timing](simulation.md#timing-and-events).

## File and validation behavior

`load_scenario` accepts `.yaml`, `.yml`, and `.json` paths. `loads_scenario` accepts
text with explicit `format="yaml"` or `format="json"`. Parsing uses safe YAML,
rejects multiple documents, duplicate keys at every level, unsafe tags, and
non-finite JSON numbers. Files are read as UTF-8 with optional BOM. Errors include
their source path or supplied source name.

`validate_scenario` accepts a mapping or existing `Scenario`, returns a fresh
validated record, and leaves its input untouched. Missing actor IDs use roles
such as `ego`, `pedestrian`, `lead`, `crossing`, or `vehicle`, with `_2`, `_3`, etc.
for collisions. Explicit IDs are reserved first. Default blueprints are chosen
by actor type; explicit IDs and blueprint selections are preserved. Spawn
locations are never invented during normalization.

For records with a built-in `template`, omitted parameter values are filled from
the registry. Parameters must agree with materialized weather, actor roles,
speeds, relative geometry, and the single primary event; contradictions are
rejected. Use `build_scenario` to change parameters and materialize those changes
together. Extra background actors are allowed, but arbitrary event programs and
nonstandard layouts should use a custom record with `template: null`, where
generic model validation applies and scalar parameters can be application-specific.

`save_scenario` validates before writing, emits full defaults and parameters, and
overwrites only the explicitly supplied file. Its parent directory must already
exist. `ScenarioLoadError`, `ParameterValidationError`, and
`ScenarioGenerationError` are `ValueError` subclasses for caller-facing errors.
