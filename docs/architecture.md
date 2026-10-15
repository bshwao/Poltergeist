# Architecture

Poltergeist generates CARLA scenarios, runs agents through them, evaluates telemetry,
and exposes results through the backend API and dashboard.

The shared boundary is `shared.models`: Pydantic models validate scenarios,
telemetry, run lifecycle records, results, and failures. A deterministic schema
export generates the frontend types. See [data contracts](data-contracts.md).

The API factory `backend.main.create_app()` builds the application with validated
settings and CORS. `python -m backend` uses configured host/port. Bootstrapping
does not open a CARLA connection, initialize a database, or create storage.

The section 2 scenario engine safely loads and normalizes YAML/JSON, validates
template parameter bindings, and produces seeded configurations with typed
events and symbolic actor-relative geometry. It is independent of CARLA. See
[scenario engine usage](scenarios.md).

The execution pipeline, database, and feature routes are placeholders until their respective
[implementation sections](implementation-plan.md).
