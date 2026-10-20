# Local development

For a new checkout, start with the [first-run walkthrough](first-run.md), which
includes `python -m scripts.doctor --frontend` and an isolated agent/sensor
validation command. See [hardware profiles](hardware.md) and
[troubleshooting](troubleshooting.md) for execution choices and errors.

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
Random search uses only core dependencies. To enable optional Optuna search:

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[optimization]'
```

Optuna 4.x is required; 4.9.0 has been exercised locally, and resuming a search
requires its recorded exact engine version. See [optimization usage](optimization.md).
The adapter requires a matching CARLA 0.9.16 client/server. The
[reference performance record](reference-ai-performance.md) and
[Phase 10 validation](phase10-validation.md) distinguish tested native Windows
workflows from unverified hardware/container combinations.

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
| `CARLA_AUTO_START` | `true` | API live runs start a local loopback CARLA server when closed; reuse existing listeners |
| `CARLA_EXECUTABLE` | auto-discover | Full local `CarlaUE4.exe` or `CarlaUE4.sh` path; server configuration, never accepted from browser requests |
| `CARLA_STARTUP_TIMEOUT_S` | `120` | Readiness deadline, 1–600 seconds; cancellation remains available |
| `CARLA_QUALITY` | `Low` | Default startup graphics quality: `Low` or `Epic`; dashboard/API `options.carla_quality` can override it for a new instance |
| `DATABASE_URL` | `sqlite:///./storage/database/poltergeist.db` | Local SQLite database; initialized by persistence features or the explicit CLI |
| `STORAGE_DIR` | `storage` | Root for telemetry, recordings, and reports |
| `API_HOST` | `0.0.0.0` | Address used by `python -m backend` |
| `API_PORT` | `8000` | Port used by `python -m backend` |
| `CORS_ORIGINS` | `["http://localhost:5173","http://127.0.0.1:5173"]` | JSON array of allowed browser origins |
| `SIMULATION_QUEUE_SIZE` | `100` | Maximum waiting simulations and, separately, optimization operations, excluding each active item; 1–1000 |
| `SIMULATION_PROGRESS_INTERVAL_S` | `0.25` | Minimum interval between persisted running progress updates; greater than 0, at most 60 seconds |
| `SIMULATION_SHUTDOWN_TIMEOUT_S` | `15` | Bounded wait for each controller/worker shutdown; greater than 0, at most 60 seconds |
| `VITE_API_URL` | `http://localhost:8000` | Frontend HTTP(S) API base or root-relative proxy path such as `/api`; configure in `frontend/.env` |

Vite reads its own environment file in `frontend/`; it does not read root `.env`.
If Vite selects a different port, add that exact origin to `CORS_ORIGINS`.
`STORAGE_DIR` and `DATABASE_URL` are independent settings: changing the storage
root does not rewrite an explicitly configured database URL.
Docker builds the dashboard with `/api`, forwarded by nginx to the API on the
same origin. An existing build must be rebuilt after changing `VITE_API_URL`.

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
<http://localhost:8000/docs>. Open the dashboard at <http://localhost:5173>.
Its Overview and Scenarios pages load real API metadata, generate seeded recipes,
validate edited JSON, and manage saved scenarios with revision checks. Select
CARLA or synthetic test mode explicitly, configure execution/evaluation settings,
and submit the validated recipe to the worker. Follow the launch receipt's
**Track this run** link for live progress, cooperative cancellation, finalized
metrics, paged telemetry charts, artifact downloads, and available raw camera/
LiDAR sample inspection. Simulations provides saved history and mode/status
filters; Failures provides evidence details, manual reproduction flags, and
two-report comparisons. Overview loads mode-separated historical analytics on
request. See [dashboard usage](dashboard.md).
Final results also provide JSON, CSV, and HTML report downloads, with finalized
telemetry optionally included in JSON. See [report exports](reports.md).
No CARLA server or database is required for health, pure scenario generation,
evaluation, or direct synthetic CLI runs. `/health` reports process liveness,
not simulator/database readiness. Saved-data routes initialize persistence lazily;
queue/launch/list/detail/progress/cancel routes also start and recover the single
simulation worker. Optimization routes start its controller and recover previous
operations as interrupted, requiring explicit resume. The [API walkthrough](api.md#generate-save-launch-and-read-a-test-run)
shows how to generate and save a recipe, launch explicit `mode: "test"`, poll it,
and download finalized telemetry. API mode defaults to `carla`; it never falls
back silently to synthetic execution.

Run a synthetic scenario from a separate terminal:

```powershell
.\.venv\Scripts\python.exe -m simulator --template sudden_braking --mode test
```

The command prints a result JSON document and needs neither CARLA nor a database.
It uses a deterministic virtual layout and approximate kinematics. It does not
represent the real Town03 spawn-point geometry. `--scenario path/to/scenario.yaml`
loads a saved scenario instead of a template; `--output path/to/result.json` writes
the result to an explicit existing directory. This simulator CLI does not persist
telemetry or raw sensor data. See [running simulations](simulation.md) for Python
callbacks and timing behavior.

Initialize local persistence directly, if desired:

```powershell
.\.venv\Scripts\python.exe -m backend.persistence init
```

This opt-in command creates the configured SQLite database/artifact directories
and applies versioned migrations. It supports plain local SQLite file URLs or
`sqlite:///:memory:`; other database engines and URL query options are rejected.
Imports, service construction, API startup, and `/health` do not initialize it.
Feature routes initialize it when first needed; the explicit command is useful
for checking configuration or using the Python service directly. Use
`PersistenceService` to create a queued run, execute it with `SimulationRunner`,
and save finalized output. See [local persistence](persistence.md) for a runnable
synthetic example, raw capture integration, transactions, and recovery limits.

## Experiment batches and reports

With the API running, submit repeatable batches through its existing worker:

```powershell
.\.venv\Scripts\python.exe -m scripts.run_experiment --template pedestrian_crossing --mode test --count 2 --seed 1 --output experiment.json
.\.venv\Scripts\python.exe -m scripts.export_report --simulation-id <RUN_UUID> --format json --include-telemetry --output report.json
```

Replace `<RUN_UUID>` with a confirmed run ID in the receipt or dashboard. Output
directories must exist; existing files are preserved unless `--force` is given.
The experiment command requires an explicit mode and records immutable recipes,
settings, accepted IDs, terminal results, coverage, and runtime provenance. It
does not create another worker. Failed/cancelled runs and interrupted/uncertain
submissions remain explicit in the receipt. A wait timeout leaves accepted jobs
running; Ctrl+C requests cooperative cancellation of the invocation's confirmed
active job. See [experiment usage](experiments.md) for deadlines and recovery.

Installing the package also creates `poltergeist-experiment` and
`poltergeist-report` commands. The module commands work from the checkout. If
upgrading an existing editable install, rerun
`.\.venv\Scripts\python.exe -m pip install --no-deps -e .` to register new entry points.

Use one backend process per SQLite database. The queue owns an exclusive worker
lease, persists launch options/evaluation settings, and restores unclaimed waiting
jobs after restart. Interrupted claimed jobs become failed rather than rerun.
Shutdown requests cooperative cancellation and waits for the configured timeout;
a blocked native call can retain its lease until it returns. See
[queue behavior](api.md#simulation-jobs) before running CARLA through the API.

## Verify

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m scripts.export_contracts --check
cd frontend
npm test
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
checks through fake CARLA objects. Section 5 covers explicit initialization,
migration history/schema checks, SQLite transactions and stale revisions,
scenario/result/failure round trips, contained artifact paths, raw sample hashes,
and persistence failure cleanup. Section 6 covers scenario/failure APIs, bounded
FIFO scheduling, persisted progress, cancellation/restart, exclusive worker leases,
artifact downloads, analytics with unknown values, and lazy initialization.
Section 7 covers deterministic random/Optuna sampling and replay, conservative
objectives, repeated reproduction, valid local minimization, durable operation
journals, cancellation, and interrupted resume without duplicate child runs.
The section 8 and 9 frontend tests use Node's built-in test runner, a TypeScript loader,
and the matching React test renderer development dependency. They check typed API requests,
safe validation errors, abort handling, parameter bounds/defaults, strict seeds,
JSON form parsing, saved-recipe reconciliation, and launch payload/settings
validation, plus real component transitions for saved recipes and launch forms.
Section 9 adds run history/deep links, serial progress polling and cancellation
races, paged telemetry/unknown metrics, raw camera and LiDAR integrity/decoding,
request cancellation, failure comparisons, manual flags, and mode-separated
analytics. They need no browser or running backend. Browser interaction and
responsive layout require separate checks. Section 10 adds report/service/API/CLI,
atomic output and transport handling, container configuration, packaging, and a
real synthetic worker/API/export/storage-reopen integration check. Final
automated and browser verification is recorded in the
[implementation handoff](implementation-plan.md#verification).
These checks do not validate a live server's
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

The four bundled templates automatically find a complete map-valid layout at
execution time. Generate a template in the dashboard and launch it in CARLA mode,
or use `python -m simulator --template intersection --mode carla`. Ground,
lane, heading, and overlap validation still apply; unsuitable maps or infeasible
distance combinations receive a placement error. Absolute ego coordinates pin
a built-in layout, and custom recipes retain strict placement semantics.
`--camera` and `--lidar` enable optional raw captures in
CARLA mode; test mode rejects them. Raw samples are available to Python callbacks,
and the CLI result records their count. The persistence service can save those
samples with a manifest, and the dashboard can inspect individual recorded
camera/LiDAR samples. Encoded video and reconstructed replay generation remain
later work. The mapped Town03 example has a completed live camera/LiDAR capture;
that check does not cover every sensor configuration.
See [live execution requirements](simulation.md#carla-execution) and
[capture persistence](persistence.md#capture-raw-sensor-samples).

On the Windows installation checked on October 17, 2026, loading Town03 in Low
quality crashed CARLA before any scenario initialized (a DirectX 12 shader error,
and a separate DirectX 11 access violation). Town10HD_Opt ran all four templates
in Low quality for short live checks. A small Epic-quality window successfully
loaded Town03 and completed full 30 s runs of all four default templates. If the
same native crash occurs, close all CARLA instances and launch from the extracted
CARLA directory with:

```powershell
.\CarlaUE4.exe -quality-level=Epic -windowed -ResX=640 -ResY=360
```

Keep camera/LiDAR capture off when unnecessary. This is an observed workaround
for that installation, not a requirement that every CARLA system use Epic quality.
Only run one CARLA server on the default ports 2000 and 2001. Choose the map in
the Scenarios tab before generation, then validate the recipe. Dashboard launch
settings allow switching to that selected map by default. Saved/custom recipes
retain their map; their explicit coordinates and routes must match it. See the
[automatic local startup guide](first-run.md#4-add-carla) for installation discovery
and the distinction between the browser's computer and the backend's computer.

The default Docker Compose stack starts the core API and a built dashboard with
a same-origin nginx proxy. A named volume retains the SQLite database and owned
artifacts across container recreation. It does not start CARLA automatically.
The optional `carla` profile pins the server to 0.9.16; connecting to it also
requires a backend image built with the simulation extra. See [Docker setup](docker.md)
for exact commands, configuration, health checks, persistence, and prerequisites.
Run one API process per database/server. Compose configuration is tested, while
actual image builds, GPU integration, and live CARLA remain environment checks.
