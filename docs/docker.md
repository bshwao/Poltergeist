# Containers

The default stack runs the API and a built dashboard. It supports saved scenarios,
explicit synthetic test runs, metrics, failures, artifacts, and random searches
without installing or starting CARLA. Both services bind only to localhost.
Use Docker with Linux containers and the Compose plugin.

From the repository root:

```text
docker compose up --build -d --wait
docker compose ps
```

Open [the dashboard](http://localhost:8080). The browser uses the same origin's
`/api` proxy; it never tries to resolve the internal Docker hostname `backend`.
The direct API is at [localhost:8000](http://localhost:8000/docs), and
[API liveness through the proxy](http://localhost:8080/api/health) returns JSON.
No `.env` file is required. The normal root `.env` is read by Compose for
interpolation but is not copied into the image or loaded as a container env file.
Docker-specific simulator overrides use a `POLTERGEIST_` prefix so a host setting
such as `CARLA_HOST=localhost` does not point a container at itself.

The backend runs one process as an unprivileged user. Its health check verifies
HTTP liveness, not database or simulator readiness. Nginx starts after that check
passes. Both containers have a read-only root filesystem and writable temporary
storage. Runtime dependencies are pinned in `requirements-core.lock`; the
frontend uses `npm ci` and its committed lock. Base images use version tags and
can receive upstream patch updates.

## Durable storage and shutdown

The `poltergeist-data` named volume mounts at `/data`, containing both
`database/poltergeist.db` and telemetry, recordings, and reports. Results survive
container recreation and ordinary `docker compose down`:

```text
docker compose down
docker compose up -d --wait
```

Do not add `--volumes` when preserving results. Stop the stack before copying
the entire named volume for a consistent database and artifact backup. The
existing persistence format validates migrations and artifact paths on reopening.
Do not share one writable volume between multiple running backend instances.

Docker forwards termination to the single API process. The stack allows 75
seconds for cooperative cancellation and the two bounded worker shutdown waits.
An interrupted native CARLA call can still require process termination; queued
work is reconciled by the persisted worker recovery rules on the next startup.

Change host ports without changing the browser API URL. In PowerShell:

```powershell
$env:POLTERGEIST_DASHBOARD_PORT = '18080'
$env:POLTERGEIST_API_PORT = '18000'
docker compose up -d --build --wait
```

On a POSIX shell, prefix the command with
`POLTERGEIST_DASHBOARD_PORT=18080 POLTERGEIST_API_PORT=18000`.

## Optional CARLA server and client

The `carla` profile starts `carlasim/carla:0.9.16` with off-screen rendering,
sound disabled, and an NVIDIA GPU reservation. A compatible GPU, graphics
drivers, Vulkan support, and NVIDIA Container Toolkit are required by
[CARLA's Docker instructions](https://carla.readthedocs.io/en/latest/build_docker/).
This profile targets Linux NVIDIA hosts. Windows Docker/WSL graphics support must
be checked on the actual machine; a native CARLA server is also an option.
Off-screen rendering retains GPU sensor output; it is different from disabling
rendering. See [CARLA rendering options](https://carla.readthedocs.io/en/latest/adv_rendering_options/).

Enable the matching Python client and optional numeric/Optuna extras explicitly.
The extra package versions follow `pyproject.toml` ranges and are constrained by
the core lock; they are not fully locked themselves. The CARLA client is fixed at
0.9.16, and the API image uses Python 3.12 for its published wheel.

```powershell
$env:POLTERGEIST_INSTALL_SIMULATION = 'true'
docker compose --profile carla up --build -d
docker compose --profile carla logs -f carla
```

Wait for CARLA to finish loading before selecting CARLA mode in the dashboard.
The backend connects to service `carla:2000` by default. A connection/version/map
failure is reported as a failed CARLA run; it never changes that run into test
mode. Camera capture requires rendering enabled in the run options.

To connect the client-enabled API image to an existing host CARLA 0.9.16 server,
omit the profile and set the Docker-specific host:

```powershell
$env:POLTERGEIST_INSTALL_SIMULATION = 'true'
$env:POLTERGEIST_CARLA_HOST = 'host.docker.internal'
docker compose up --build -d --wait
```

`POLTERGEIST_CARLA_PORT` and `POLTERGEIST_CARLA_TIMEOUT` override the client RPC
port and connection timeout. For a remote server, set its reachable hostname.
The profile's own RPC ports remain 2000/2001 and bind only to localhost.

## Verification and troubleshooting

`docker compose config` validates the resolved configuration without contacting
the engine. `tests/test_container_config.py` also checks the core/profile split,
durable storage, localhost ports, shutdown boundaries, and Docker overrides.

When an engine is available, verify the actual images and HTTP proxy:

```text
docker compose build
docker compose up -d --wait
docker compose exec backend python -m pip check
docker compose exec backend python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health').read().decode())"
```

Then use the dashboard to generate a recipe, launch **synthetic test** mode,
follow its receipt to a terminal result, inspect telemetry, and reload the same
run after `docker compose down` followed by `docker compose up -d --wait`.
Confirm `/api/health` returns JSON in the browser and that downloads contain
telemetry, not HTML. A failed health check can be investigated with
`docker compose logs backend dashboard`; a container restart does not prove
the simulator or persisted data are ready.

If the CLI reports a missing `dockerDesktopLinuxEngine` pipe or cannot connect to
the Docker daemon, start/configure the local Docker engine before running these
checks. Compose parsing alone does not verify image builds, nginx startup,
volume permissions, HTTP proxying, or GPU execution.

Refresh the runtime lock intentionally alongside the development lock:

```text
uv pip compile pyproject.toml --universal --constraint requirements.lock --output-file requirements-core.lock
```

The section 10 implementation environment could parse both Compose profiles,
but its Docker engine was unavailable. Image builds, container runtime checks,
and live CARLA/GPU behavior therefore remain unverified there.
