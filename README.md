# Poltergeist

CARLA-based driving-safety simulation and failure-discovery platform.

All ten implementation sections now provide shared models, configuration, API bootstrap,
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
mode-separated historical analytics. Experiment batches run through the same API
worker, and finalized evidence exports as JSON, CSV, or printable HTML from the
dashboard or command line. The Docker stack includes the API, dashboard,
same-origin proxy, persistent storage, and an optional CARLA GPU profile.
See [the implementation plan](docs/implementation-plan.md),
[scenario engine usage](docs/scenarios.md), [running simulations](docs/simulation.md),
and [local persistence](docs/persistence.md). See
[search and reproduction](docs/optimization.md) to discover and reduce failures. Use the
[API walkthrough](docs/api.md#generate-save-launch-and-read-a-test-run) to launch
a synthetic run through the backend, or [dashboard usage](docs/dashboard.md) to
build, save, launch, and inspect a recipe in the browser. See
[experiment batches](docs/experiments.md), [report exports](docs/reports.md), and
[Docker setup](docs/docker.md). See [evaluation methodology](docs/methodology.md)
for scoring assumptions. Built-in CARLA templates resolve their placements against
the running map; custom coordinates retain strict placement validation.
Container configuration is checked; container builds/runtime require a working
Docker engine. Synthetic execution exercises the complete local workflow
without a CARLA installation.

For local development, start `.venv/Scripts/python.exe -m backend`, then run
`npm run dev` from `frontend/`. Open [the dashboard](http://localhost:5173).
For a container setup, run `docker compose up --build -d` with Docker running
and open [the container dashboard](http://localhost:8080). Choose **Synthetic
test** explicitly to try the full workflow without CARLA.

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
