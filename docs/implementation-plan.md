# Implementation plan and handoff

Work is split into ten sections. Each section should deliver usable code,
appropriate tests, updated documentation, and a handoff here. A checked item means
that section's scope is complete, not that the whole application is functional.

| Section | Scope | Status |
| --- | --- | --- |
| 1 | Foundation, configuration, dependency setup, shared data models, frontend build | Complete |
| 2 | Scenario loading, validation, parameter constraints, seeded generation, full starter templates | Complete |
| 3 | Collision, clearance, TTC, braking, rule metrics, severity scoring | Next |
| 4 | CARLA world/actors/sensors, baseline agent, telemetry, lifecycle, cancellation, cleanup, test adapter | Planned |
| 5 | Database models, migrations, scenario/run/failure persistence, artifact storage | Planned |
| 6 | Scenario APIs, single-worker simulation queue, progress/cancellation, results/failures/analytics | Planned |
| 7 | Random/Optuna search, severity objectives, reproduction, failure minimization | Planned |
| 8 | Dashboard navigation, scenario forms, validation, launch controls | Planned |
| 9 | Live status, telemetry/metrics charts, failure browsing, comparisons, available recording playback | Planned |
| 10 | Experiment/report CLI, exports, Docker integration, end-to-end checks, setup completion | Planned |

Target: a local application with one simulation worker. Authentication, public
hosting, and training a custom ML agent are separate extensions.

## Section 1 handoff

- Python contracts live in `shared/models/`; public imports use `shared.models`.
  See [data contracts](data-contracts.md) for units, lifecycle rules, and defaults.
- Python schemas and TypeScript are generated and tracked. Run the export and
  frontend generation after model changes; both have nonmutating drift checks.
- Root `.env` config is validated in `backend.config.Settings`. Relative paths
  are anchored to the repository. The CARLA connection helper reads those
  settings lazily or accepts explicit connection settings.
- The API has `/health` and CORS, with no simulator/database side effects.
- Core dependencies are locked separately from optional CARLA/search extras.
  Existing Python 3.14 works for core; use Python 3.12 for CARLA 0.9.16.
- Frontend dependencies are installed/locked, React/Vite types are configured,
  and type checking plus production build work. The UI is still the starter.
- Docker dependency-copy ordering is fixed and the CARLA image version is pinned;
  container builds and GPU/runtime integration remain section 10 validation.

## Section 2 handoff

- Public scenario APIs are exported from `scenarios`: file/text/template loading,
  validation, saving, parameter metadata/validation/sampling, and default/seeded
  scenario construction. See [scenario engine usage](scenarios.md).
- YAML/JSON parsing rejects duplicate keys and unsafe/invalid document forms.
  Normalization fills deterministic IDs and blueprints without mutating inputs.
- Parameter specs define bounds, defaults, units, and descriptions. Built-in
  parameter values are bound to actual actor/weather/event fields; loaded records
  with contradictory values are rejected.
- All four templates now contain explicit actors, map-relative placement data,
  parameters, and typed timed events. Shared contracts and generated frontend
  types include map-index/relative spawns and speed/braking events.
- Seeded generation uses isolated RNG state, permits fixed values and narrowed
  ranges, and produces self-contained save/load records. Batch seeds are unique
  consecutive values with wraparound, and preserve prefix stability.
- Spawn-point 0 and relative paths are symbolic starter layouts. Actual map,
  lane/junction, ground/overlap, visibility, and physics validation remain for
  the section 4 adapter. No CARLA execution is claimed.

## Resume with section 3

Implement the `evaluation/` modules using `shared.models.TelemetryFrame` and
`SafetyMetrics`. Specify collision/violation event deduplication, bounding-box
clearance conventions, closing-speed TTC assumptions, signed acceleration versus
deceleration magnitude, and missing-data/null semantics. Document severity weights
and thresholds. Test known numeric cases, nonclosing motion, absent actor data,
and repeated sensor events; do not infer safety from unmeasured default values.

Do not implement fake driving results in the API. The lightweight simulation
adapter belongs in section 4 and must identify runs with `mode: "test"`.

## Verification

See [setup](setup.md) for exact commands. Run Python tests, schema drift check,
frontend contract drift check, and frontend production build before handing off.
The tests use a fake CARLA client; real simulation behavior remains unverified.

Section 1 validation: 117 Python tests passed on Python 3.14. After updating the
HTTP test client to match Starlette, all 35 backend/configuration/CARLA-helper
tests also passed with warnings treated as errors. Schema drift check, generated
TypeScript drift check, TypeScript compilation, Vite production build, Python
dependency consistency, and Git whitespace checks passed. CARLA execution and
container builds were not run.

Section 2 validation: 284 Python tests passed with warnings treated as errors.
Schema and frontend contract drift checks, TypeScript compilation, Vite
production build, and Git whitespace checks passed. The new checks cover safe
parsing and actor normalization, placement/event references, parameter bounds,
seeded generation, binding consistency, and saved-scenario round trips. Real
CARLA map validity and event execution remain section 4 work.
