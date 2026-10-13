# Poltergeist

CARLA-based driving-safety simulation and failure-discovery platform.

## Project layout

- `simulator/` contains CARLA clients, world and actor management, sensors, and agents.
- `scenarios/` defines, validates, and generates driving scenarios.
- `evaluation/` calculates safety metrics from simulation telemetry.
- `optimization/` searches for severe and reproducible failures.
- `backend/` exposes the REST API and orchestration services.
- `frontend/` contains the React dashboard.

See [`docs/setup.md`](docs/setup.md) for local development instructions.