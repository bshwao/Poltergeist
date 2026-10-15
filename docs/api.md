# API

The FastAPI application is exposed from `backend.main:app` and can be created with
`create_app(settings)` for tests or alternate configuration. Launch it with
`python -m backend`. Interactive docs are at `/docs` and the OpenAPI document at
`/openapi.json`.

`GET /health` returns HTTP 200 with:

```json
{"status": "ok", "service": "poltergeist"}
```

This is a process-liveness check; it does not contact CARLA or a database. CORS
allows configured origins from `CORS_ORIGINS`, defaulting to local Vite origins.

Scenario, simulation, failure, and analytics routes will be implemented in
section 6. Shared data models already exist under `shared.models`, but those
feature endpoints are not available yet.
