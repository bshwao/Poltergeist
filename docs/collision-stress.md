# S0 collision-stress catalogue and baseline calibration

Status: **implemented and live-validated for the protocol below**. Twelve fixed
presets exist in `scenarios/stress_presets.py`, three per existing family. Their
low/medium/high tiers describe measured stress under this protocol. The medium
braking preset collided in one held-out trial; a tier is not a collision-free
guarantee. These are simulated stress tests, not road-safety ratings.

The families remain intersection, pedestrian crossing, sudden braking and
occluded pedestrian. Presets reuse the existing parameter validation, template
materialization and map-placement checks. They contain no scripted ego-control
events and use the default `BaselineAgent` without changing its source or tuning
its parameters. The baseline sees CARLA ground-truth actors, including pedestrians
behind the occluder; this does not test camera perception or visibility inference.

## Protocol

- CARLA client/server 0.9.16, Town10HD_Opt, Low graphics, dry daylight, 0.05 s
  simulation steps and an 8 s observation window. No cameras or trained models.
- Serial execution on a separately started idle asynchronous server. The CLI
  refuses existing vehicles, walkers, sensors/controllers or a synchronous world;
  it never starts CARLA, reloads maps or uses the development database.
- `--signals green` freezes all lights green to isolate collision avoidance from
  signal timing. Natural signals remain the CLI default for separate experiments.
  The tool requests the previous colors and restores the original global frozen
  flag afterward. CARLA has no setter for elapsed signal-cycle time; that phase
  cannot be restored. Settings/weather restoration and remaining actors are
  checked after every trial, and signal cleanup is checked before batch completion.
- Calibration seeds: 7, 11, 19. The frozen recipes were benchmarked separately on
  23, 29, 31, 37, 41. No recipe was retuned using those benchmark outcomes.
- Retain the baseline and execution/evaluation/scenario source hashes, normalized
  recipe, resolved spawns, accepted simulation UUID, finalized telemetry, original
  metrics, timing, and world state before/after every trial. Initial stream files
  remain separate because late collision callbacks may change the finalized trace.

The primary interacting actor is `crossing`, `lead` or `pedestrian`, depending on
the family. The report keeps contacts with other actors/roadside geometry in the
original result and reports all-actor collision runs separately. A roadside crash
after passing the junction is not evidence of a crossing-vehicle contact.

Operational outcome bins use complete collision/clearance/TTC channels:

| Observed bin | Rule |
| --- | --- |
| Collision | At least one recorded contact with the primary interacting actor |
| Critical avoidance | No primary contact; minimum predicted TTC at most 1.5 s or primary box clearance at most 1 m |
| Clear | No primary contact and neither critical-avoidance condition observed |
| Unclassified | Failed/cancelled execution or missing physical observation coverage |

These bins do not replace the existing safety score. Missing traffic-rule coverage
continues to leave overall noncollision severity unknown. Braking, roadside
contacts and rule metrics remain available; a brake spike alone does not imply an
imminent collision. Failed/missing/unverified-cleanup trials never enter the
collision-rate denominator as safe runs. Rates include Wilson 95% intervals;
small samples do not establish general collision probabilities.

## Commands

In the dashboard's **Scenarios** workspace, use **Collision stress presets**,
select an entry and choose **Load stress preset**. The complete seed-7 recipe is
loaded without saving or executing it. Existing unsaved edits require an explicit
replacement choice. Subsequent edits require validation as usual. The catalogue
is also available as `GET /scenarios/stress-presets?seed=7`; the read initializes
no storage and does not contact CARLA. It returns ordinary `Scenario` objects.

Start or reuse a dedicated matching CARLA server on Town10HD_Opt. For a server on
port 2100, use new output directories:

```powershell
.\.venv\Scripts\python.exe -m scripts.benchmark_stress --output validation-evidence/s0-calibration --phase calibration --signals green --carla-port 2100 --seeds 7 11 19
.\.venv\Scripts\python.exe -m scripts.benchmark_stress --output validation-evidence/s0-benchmark --phase benchmark --signals green --carla-port 2100 --seeds 23 29 31 37 41
```

`--presets` selects named catalogue entries. `--summarize --output <directory>`
reads retained results without contacting CARLA. `--resume` requires identical
source/configuration and skips only terminal trials with verified world cleanup.
An interrupted/uncertain trial is retained and requires inspection; it is never
blindly executed a second time. Each directory is a private evidence set, not an
import or migration of existing application data.

Before committing changes, fetch upstream and reconcile textual and behavioral
overlap with the current implementation. Do not put large raw traces, map caches,
runtime distributions or user databases in Git.

## Verified calibration, October 22, 2026

The final twelve recipes have 36 selected live calibration trials (seeds 7, 11,
19). Their finalized target metrics were recomputed from retained telemetry. All
selected recipes match the frozen v1 catalogue, the baseline/execution/evaluation
sources match, and world/signal cleanup passed. Eleven unchanged recipes reuse
their candidate-3 evidence; the revised pedestrian medium uses three candidate-4
trials. Earlier exploratory candidates/failures remain outside this selected set.
The separate five-seed benchmark is recorded below.

| Preset | Target-contact runs | Any-contact runs | Median target TTC (s) |
| --- | --- | --- | --- |
| `intersection-low` | 0/3 | 0/3 | 4.037 |
| `intersection-medium` | 0/3 | 0/3 | 0.681 |
| `intersection-high` | 3/3 | 3/3 | 0.000 |
| `pedestrian_crossing-low` | 0/3 | 0/3 | Not predicted |
| `pedestrian_crossing-medium` | 0/3 | 0/3 | 0.364 |
| `pedestrian_crossing-high` | 3/3 | 3/3 | 0.000 |
| `sudden_braking-low` | 0/3 | 0/3 | Not predicted |
| `sudden_braking-medium` | 0/3 | 0/3 | 0.101 |
| `sudden_braking-high` | 3/3 | 3/3 | 0.004 |
| `occluded_pedestrian-low` | 0/3 | 0/3 | 6.431 |
| `occluded_pedestrian-medium` | 0/3 | 1/3 | 1.376 |
| `occluded_pedestrian-high` | 3/3 | 3/3 | 0.011 |

The occluded medium had one contact outside the primary pedestrian interaction;
intersection high also had secondary contacts. Target avoidance must not be read
as absence of other collisions. The [calibration receipt](validation/s0-calibration.json)
retains both counts, per-trial measurements and evidence hashes. Seeds do not
guarantee independent road layouts; resolved spawns remain part of each result.
These repeated controlled trials and their statistical intervals are descriptive,
not population crash-probability estimates.

Export usable scenario recipes without CARLA:

```powershell
.\.venv\Scripts\python.exe -m scripts.export_stress_presets --output validation-evidence/stress-recipes --seed 7
```

The new directory contains twelve ordinary scenario JSON files plus a checksum
index. Existing output is refused. Python callers can use
`get_stress_preset(id).scenario(seed=7)` from `scenarios.stress_presets`.

## Held-out baseline benchmark

All 60 trials completed with complete collision/clearance/TTC observations and
verified cleanup. The baseline implementation and its defaults remained unchanged:
`target_speed_mps=None`, `emergency_ttc_s=1.5`, `minimum_clearance_m=2.0`.
The source SHA-256 was
`bc62cc570dfbd8f3f8372538eb90305b2fd2be3d67970a67f8f5ad4dba0f1a4d`.
This measures collision outcomes, not real-time throughput or a learned policy.

| Preset | Target-contact runs | Any-contact runs | Median target TTC (s) | Distinct resolved layouts |
| --- | --- | --- | --- | --- |
| `intersection-low` | 0/5 | 0/5 | 4.037 | 1 |
| `intersection-medium` | 0/5 | 0/5 | 0.681 | 1 |
| `intersection-high` | 5/5 | 5/5 | 0.000 | 1 |
| `pedestrian_crossing-low` | 0/5 | 0/5 | Not predicted | 5 |
| `pedestrian_crossing-medium` | 0/5 | 0/5 | 0.364 | 1 |
| `pedestrian_crossing-high` | 5/5 | 5/5 | 0.000 | 1 |
| `sudden_braking-low` | 0/5 | 0/5 | Not predicted | 1 |
| `sudden_braking-medium` | 1/5 | 1/5 | 0.101 | 1 |
| `sudden_braking-high` | 5/5 | 5/5 | 0.004 | 1 |
| `occluded_pedestrian-low` | 0/5 | 0/5 | 6.431 | 5 |
| `occluded_pedestrian-medium` | 0/5 | 0/5 | 0.711 | 5 |
| `occluded_pedestrian-high` | 5/5 | 5/5 | 0.000 | 5 |

The low tier had 0/20 target-contact runs, medium 1/20, and high 20/20. The medium
contact was sudden braking, seed 31, simulation
`1886aa7d-c7bd-4b13-aaa9-16e1d47206c0`. It remains in the published denominator.
The other medium runs entered the critical-avoidance bin. The three calibration
occluded-medium trials include one occluder contact, which is retained separately.

Most presets resolved to the same layout despite different seeds. These repetitions
therefore measure that layout's behavior, rather than independent spatial samples.
The [full measurement receipt](validation/s0-baseline-benchmark.json) includes
per-trial source/result/telemetry identities, target and other contacts, coverage,
resolved spawns, and descriptive Wilson intervals. For example, 0/5 has an upper
interval endpoint about 43.4%; the sample does not establish a zero population risk.
Overall noncollision severity remains null where road-rule coverage was incomplete.

All 96 selected calibration/benchmark traces were decoded and their full safety
and target metrics recomputed exactly. A final analysis guard rejects a missing
target instead of treating it as an empty safe scene. Every target was present in
every recorded frame, so that guard changed no measured result; the receipt records
both the executed tool's hash and the final analysis hash. The exact executed tool
is retained with the private raw evidence.

After the benchmark, a fresh RPC check matched the original map/settings/weather,
found no trial actors and confirmed all lights unfrozen. Only the owned dedicated
test server was then stopped. Raw streams, native map caches and exploratory
attempts remain in ignored `validation-evidence`, rather than the application DB.

## Software checks

The final focused scenario/preset/API checks passed 172 tests. All 329 frontend
tests, schema/type freshness checks and the production build passed. Six Chromium
end-to-end tests passed, including the new real catalogue load, unsaved-draft
protection and existing workspace flows. The preset panel was inspected at
1440 px and 390 px widths with no clipping or horizontal overflow. Loading a
preset made no simulation submission; runs remain explicit actions.
