# Poltergeist

CARLA-based driving-safety simulation and failure-discovery platform.

Sections 1 and 2 of 10 are implemented: shared models, configuration, API bootstrap,
frontend build/types, and a validated scenario engine with seeded generation.
Driving simulation, metrics computation, persistence, and dashboard features
remain to be implemented. See [the implementation plan](docs/implementation-plan.md)
and [scenario engine usage](docs/scenarios.md).

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
