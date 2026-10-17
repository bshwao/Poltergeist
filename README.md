# Poltergeist

CARLA-based driving-safety simulation and failure-discovery platform.

Sections 1–9 of 10 provide shared models, configuration, API bootstrap,
frontend build/types, a scenario engine with seeded generation, and safety
evaluation with observation coverage and severity scoring. A synchronous runner
now connects the CARLA adapter or a deterministic synthetic test adapter to
baseline/custom agents, timed events, telemetry, cancellation, and cleanup.
Local SQLite persistence now stores scenarios, runs, failures, finalized telemetry,
and optional raw sensor captures. Feature APIs now expose saved scenarios, a
durable single-worker simulation queue, progress/cancellation, results and artifact
downloads, failures, and analytics. Durable random/Optuna search, repeated failure
reproduction, and bounded local minimization now use that same worker. The
dashboard provides navigation and a scenario workspace for template parameters,
seeded recipe generation, JSON editing, server validation, and saved-scenario
management. Explicit CARLA or synthetic mode, execution and evaluation settings,
and launch receipts complete section 8. Section 9 adds saved run history,
live progress/cancellation, finalized safety metrics and paged telemetry charts,
raw camera/LiDAR sample inspection, failure evidence and comparisons, and
mode-separated historical analytics. Experiment/report CLI, exports, Docker
integration, and final setup checks are next in section 10.
See [the implementation plan](docs/implementation-plan.md),
[scenario engine usage](docs/scenarios.md), [running simulations](docs/simulation.md),
and [local persistence](docs/persistence.md). See
[search and reproduction](docs/optimization.md) to discover and reduce failures. Use the
[API walkthrough](docs/api.md#generate-save-launch-and-read-a-test-run) to launch
a synthetic run through the backend, or [dashboard usage](docs/dashboard.md) to
build, save, launch, and inspect a recipe in the browser. See [evaluation methodology](docs/methodology.md)
for scoring assumptions. Real CARLA execution has not yet
been validated; bundled spawn layouts still require a suitable map anchor.

## Project layout

- `simulator/` contains CARLA clients, world and actor management, sensors, and agents.
- `scenarios/` defines, validates, and generates driving scenarios.
- `evaluation/` calculates safety metrics from simulation telemetry.
- `optimization/` searches for severe and reproducible failures.
- `backend/` contains the REST API, SQLite repositories, artifact storage, and services.
- `frontend/` contains the React dashboard.
- `shared/models/` defines the Python data contracts used across the application.
- `contracts/models.schema.json` and `frontend/src/types/generated.ts` are generated
  from those contracts.

See [`docs/setup.md`](docs/setup.md) for local development instructions.
