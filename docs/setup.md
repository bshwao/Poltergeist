# Local development

Use **Python 3.12** for full CARLA development and **Node.js 24 LTS** for the
dashboard. The core API, shared models, synthetic simulation, and tests also work
without CARLA and are checked in the existing Python 3.14 environment. CARLA 0.9.16 publishes Python
3.10, 3.11, and 3.12 wheels, so do not install its extra into the Python 3.14
environment. [CARLA release notes](https://carla.org/2025/09/16/release-0.9.16/)
and [published wheels](https://pypi.org/project/carla/0.9.16/#files).

Vite requires Node 20.19+ or 22.12+; Node 24 meets this requirement.
[Vite setup requirements](https://vite.dev/guide/).

## Install the core application

Run from the repository root. On Windows PowerShell, for a new checkout:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
Copy-Item frontend/.env.example frontend/.env
cd frontend
npm ci
cd ..
```

If `.venv` already exists, reuse it for core development. Do not recreate or
overwrite it just to run these checks. Activate it if you prefer bare `python`
commands: `.\.venv\Scripts\Activate.ps1`. Using the explicit interpreter path
works without changing the PowerShell execution policy.

On macOS/Linux, create the environment with `python3.12 -m venv .venv`, use
`.venv/bin/python`, and copy the example environment files with `cp`.

`pyproject.toml` declares package metadata, dependency ranges, and optional extras.
`requirements.lock` pins core and development dependencies across supported
platforms; `requirements.txt` installs those pins and the local editable package.
`frontend/package-lock.json` locks frontend dependencies. To intentionally refresh
the Python lock with [uv](https://docs.astral.sh/uv/pip/compile/):

```text
uv pip compile pyproject.toml --extra dev --universal --output-file requirements.lock
```

Simulation/optimization extras are declared but are not covered by the core lock.
The adapter requires a matching CARLA 0.9.16 client/server; live GPU integration
and the optimization dependencies remain to be validated.

## Configuration

Backend settings load the repository-root `.env` regardless of the current
working directory. Process environment variables take precedence. `get_settings()`
caches settings for the process; restart the server after editing configuration.
Relative storage and SQLite database paths resolve against the repository root.
Configuration does not create directories or connect to external services.

| Variable | Default | Purpose |
| --- | --- | --- |
| `CARLA_HOST` | `localhost` | Simulator hostname |
| `CARLA_PORT` | `2000` | Simulator RPC port |
| `CARLA_TIMEOUT` | `10` | Connection timeout, seconds |
| `DATABASE_URL` | `sqlite:///./storage/database/poltergeist.db` | Future persistence connection |
| `STORAGE_DIR` | `storage` | Root for telemetry, recordings, and reports |
| `API_HOST` | `0.0.0.0` | Address used by `python -m backend` |
| `API_PORT` | `8000` | Port used by `python -m backend` |
| `CORS_ORIGINS` | `["http://localhost:5173","http://127.0.0.1:5173"]` | JSON array of allowed browser origins |
| `VITE_API_URL` | `http://localhost:8000` | Frontend API base URL; configure in `frontend/.env` |

Vite reads its own environment file in `frontend/`; it does not read root `.env`.
If Vite selects a different port, add that exact origin to `CORS_ORIGINS`.
`STORAGE_DIR` and `DATABASE_URL` are independent settings: changing the storage
root does not rewrite an explicitly configured database URL.

## Run

Start the API in one terminal and the dashboard in another:

```powershell
.\.venv\Scripts\python.exe -m backend
```

```powershell
cd frontend
npm run dev
```

API liveness: <http://localhost:8000/health>. Interactive API docs:
<http://localhost:8000/docs>. The dashboard currently displays its starter screen.
No CARLA server or database is required for the API starter, evaluation, or
synthetic test runs. `/health` reports process liveness, not simulator/database
readiness. The API does not yet expose simulation launch routes.

Run a synthetic scenario from a separate terminal:

```powershell
.\.venv\Scripts\python.exe -m simulator --template sudden_braking --mode test
```

The command prints a result JSON document and needs neither CARLA nor a database.
It uses a deterministic virtual layout and approximate kinematics. It does not
represent the real Town03 spawn-point geometry. `--scenario path/to/scenario.yaml`
loads a saved scenario instead of a template; `--output path/to/result.json` writes
the result to an explicit existing directory. Telemetry/raw sensor persistence is
not included. See [running simulations](simulation.md) for Python callbacks and
timing behavior.

## Verify

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m scripts.export_contracts --check
cd frontend
npm run contracts:check
npm run build
```

Tests cover scenario templates, model validation, wire round trips, lifecycle
consistency, settings, CORS, health, and the CARLA connection helper with a fake
client. Section 2 adds file parsing/normalization, actor/event references, parameter
bounds, repeatable generation, and saved-scenario round trips. Section 3 covers
known 3D clearance/TTC cases, braking/stopping calculations, event deduplication,
observation coverage, and severity thresholds. Section 4 covers synthetic runs,
event timing, cancellation/failures, callback isolation, CARLA adapter boundaries,
resource restoration, spawning, bounded sensor synchronization, and map rule
checks through fake CARLA objects. These checks do not validate a live server's
driving behavior or sensor delivery timing. See [scenario engine usage](scenarios.md),
[simulation usage](simulation.md), and [evaluation methodology](methodology.md).

After changing a Python contract, regenerate both tracked artifacts:

```powershell
.\.venv\Scripts\python.exe -m scripts.export_contracts
cd frontend
npm run contracts:generate
```

See [data contracts](data-contracts.md) for units and wire conventions. With an
activated Python environment and Make available, `make check` and `make contracts`
run the equivalent checks/regeneration.

## CARLA and Docker

In a Python 3.12 environment, `python -m pip install -r requirements-simulation.txt`
adds CARLA 0.9.16, numerical/video dependencies, and optimization libraries. Use a
matching CARLA 0.9.16 server. Installing its client does not install the simulator
server. The runner checks both client and server versions before changing the
world. Configure `CARLA_HOST`, `CARLA_PORT`, and `CARLA_TIMEOUT` in root `.env`.

Use a dedicated server world with no other synchronous tick owner. The scenario's
map must already be loaded by default. `--allow-map-reload` explicitly permits
changing maps only when no existing vehicle, walker, sensor, or controller would
be destroyed. The adapter restores the original map/settings/weather after use.
An unavailable original map or failed RPC can still make restoration fail; such
cleanup errors are surfaced in the run result.

```powershell
.\.venv\Scripts\python.exe -m simulator --scenario path/to/map-valid-scenario.yaml --mode carla
```

The four bundled templates use symbolic spawn index 0 plus relative offsets.
They have not been certified against Town03 roads/junctions, and strict runtime
layout validation can reject them. Supply a suitable map anchor/layout and verify
it on the actual server. `--camera` and `--lidar` enable optional raw captures in
CARLA mode; test mode rejects them. Raw samples are available to Python callbacks,
and the CLI result records their count; section 4 does not encode videos or save
sensor data. See [live execution requirements](simulation.md#carla-execution).

The Docker image installs the core API only. Compose pins the CARLA image to
0.9.16 and points the backend at the `carla` service. GPU/runtime integration,
storage volumes, and a worker that launches runs through the API remain later
work. Container builds and real CARLA execution have not been validated.
