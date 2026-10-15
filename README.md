# Poltergeist

CARLA-based driving-safety simulation and failure-discovery platform.

Sections 1–4 of 10 are implemented: shared models, configuration, API bootstrap,
frontend build/types, a scenario engine with seeded generation, and safety
evaluation with observation coverage and severity scoring. A synchronous runner
now connects the CARLA adapter or a deterministic synthetic test adapter to
baseline/custom agents, timed events, telemetry, cancellation, and cleanup.
Persistence, feature APIs, optimization, and dashboard features remain planned.
See [the implementation plan](docs/implementation-plan.md),
[scenario engine usage](docs/scenarios.md), [running simulations](docs/simulation.md),
and [evaluation methodology](docs/methodology.md). Real CARLA execution has not yet
been validated; bundled spawn layouts still require a suitable map anchor.

## Project layout

- `simulator/` contains CARLA clients, world and actor management, sensors, and agents.
- `scenarios/` defines, validates, and generates driving scenarios.
- `evaluation/` calculates safety metrics from simulation telemetry.
- `optimization/` searches for severe and reproducible failures.
- `backend/` exposes the REST API and orchestration services.
- `frontend/` contains the React dashboard.
- `shared/models/` defines the Python data contracts used across the application.
- `contracts/models.schema.json` and `frontend/src/types/generated.ts` are generated
  from those contracts.

See [`docs/setup.md`](docs/setup.md) for local development instructions.
