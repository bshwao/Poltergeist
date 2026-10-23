# First run: from a new checkout to an agent comparison

The supported starting point is a local checkout, Python 3.12, and the Node
runtime declared in `frontend/package.json`. CARLA and trained-model dependencies
are optional. Start with the synthetic workflow; add the simulator afterward.
Use a local folder outside a cloud-sync directory for active databases and
recordings. These commands create new environments only on a new checkout.

For the private S1 command-line distribution prototype, see
[portable build and external tester instructions](portable-prototype.md).
That prototype does not bundle the dashboard or replace this development workflow.

## 1. Install

In Windows PowerShell, from the repository root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
Copy-Item frontend/.env.example frontend/.env
cd frontend
npm ci
cd ..
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m scripts.doctor --frontend
```

Reuse existing environments and environment files when updating. The explicit
Python path works without activating PowerShell scripts or changing execution
policy. On Linux/macOS, use `python3.12 -m venv .venv`, `.venv/bin/python`, and
`cp` instead. Full native CARLA checks are documented for this Windows setup;
macOS can run the core application and connect to a separately supported server
only if a compatible client is available.

The doctor prints machine-readable checks and exits nonzero when a required
check fails. The default command never imports CARLA, runs a model, modifies a
world, installs packages or writes application data. `--frontend` makes a
supported Node runtime required. `--hardware` adds a point-in-time hardware/GPU
snapshot; it is not a performance benchmark. Static version/permission checks
cannot prove that a model's dependencies or inference will work.

## 2. Prove the installed workflow

No running backend or simulator is needed:

```powershell
.\.venv\Scripts\python.exe -m scripts.validate_installation --mode test --sensors --output storage/reports/my-first-check
```

Choose a new output directory each time, under an existing parent. This starts
an API on an ephemeral loopback port with a private SQLite database and artifact
directory. It registers the bundled starter and sensor teaching controllers,
compares them with the baseline on matched seeds, reads applied decisions and
behavior analysis, downloads JSON/CSV/HTML behavior reports, then reopens the
saved evidence through a new API runtime. Your configured database is untouched.
This command executes the trusted bundled Python examples in external workers.

`validation.json` reports pass/fail, checks, versions, decision counts, worker
step timing and file hashes. `comparison.json`, registrations, recipes, database,
artifacts and per-run reports remain in the output directory. A failure returns
nonzero and retains accepted IDs and any evidence already saved. It never retries
an uncertain submission automatically. An existing output path is never replaced.

For a very short check use `--duration .15 --seeds 7`. A short synthetic check
does not prove live driving, learned perception, model quality or real-time
throughput. Worker step timings omit parts of simulator/persistence work.

## 3. Start the dashboard

Terminal one, repository root:

```powershell
.\.venv\Scripts\python.exe -m backend
```

Terminal two:

```powershell
cd frontend
npm run dev
```

Open `http://localhost:5173`. `/health` checks API liveness, not CARLA readiness.
On **Overview**, **Setup readiness** automatically checks local prerequisites for
synthetic mode and shows separate CARLA requirements. **Ready** means ready to
try under the reported static checks; it does not mean an experiment was run.
Storage permissions are estimates until a real run opens the database. Missing
CARLA dependencies do not block synthetic mode. **Refresh setup** repeats static
inspection; it does not start CARLA or run an agent.

After you separately start CARLA, **Check CARLA connection** performs an explicit
read-only probe and verifies reported client/server versions when a supported
client is installed. An executable file or a folder named `CARLA_0.9.16` is not
version verification. **Not running** means no listener accepted the connection;
timeouts and access errors remain **Unknown**. **Not checked** is displayed until
the relevant check is requested. Follow each check's next step and refresh after
configuration changes. Failed refreshes retain a clearly labeled prior snapshot.
Neither action installs packages, opens/migrates the database, launches models,
starts the simulator, or changes its world. Execution is tested only by a run.

The acceptance command's isolated runs do not appear in your ordinary dashboard
history. Create your own run in this workspace:

1. In **Agents**, register the absolute path to `examples/agents/starter`. Leave
   Worker Python blank to use the backend environment. Acknowledge the trusted
   local code and inspect the registration's static readiness.
2. In **Experiments**, keep **Synthetic test**, select the starter registration,
   and optionally retain the baseline as agent zero. Paste the recipe from
   `examples/scenarios/installation_check.json`. Use seed `7` and one repetition.
3. Start the comparison. Open **Review run** when it completes. Inspect the
   decision timeline, measured behavior and exported report. Unknown observations
   stay unknown; completion does not imply safe driving.

The starter is a rule-based SDK example, not a trained model. It now declares
SDK 1.3 and echoes the observation protocol in its response so explicit driving
tasks work. The sensor pilot is also rule-based. Use the maintained pretrained
PlanT integration for an actual learned policy.

## 4. Add CARLA

Use a Python 3.12 backend with the matching CARLA 0.9.16 client and server:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-simulation.txt
.\.venv\Scripts\python.exe -m scripts.doctor --frontend --hardware
```

Installing the Python client does not install the simulator executable. Extract
CARLA into its own local directory. On the **Scenarios** page, choose the
**CARLA map** before generating and validating the recipe. New drafts default
to **Town10HD_Opt**. The launch panel enables map switching by default; the
existing checks still refuse to destroy other vehicles, walkers or sensors.

For local backends, clicking **Launch simulation** starts CARLA when needed,
using a 640×360 window and no sound. Under **Execution settings**, choose
**CARLA graphics quality**: **Low**, **Epic**, or **Use configured default**
(`CARLA_QUALITY`, initially Low). A running instance is reused and keeps its
current quality. Close CARLA before launching to apply a different choice.
The backend waits for readiness before spawning actors; cancellation stops the
run while leaving the game open. Startup can take a minute or two.

Common local installation folders are discovered automatically. For a different
location, set the following in the backend's root `.env` and restart the backend:

```dotenv
CARLA_EXECUTABLE=C:/path/to/CARLA_0.9.16/CarlaUE4.exe
CARLA_AUTO_START=true
CARLA_STARTUP_TIMEOUT_S=120
CARLA_QUALITY=Low
```

On Linux, use the full `CarlaUE4.sh` path. `CARLA_AUTO_START=false` preserves
manual startup. Automatic startup opens the game on the computer running the
backend, not a remote dashboard visitor's computer. Remote CARLA hosts and
Docker-hosted backends require their simulator to be started separately.
Health checks, recipe generation, synthetic runs and installation diagnostics
never start CARLA. Direct simulator CLI calls also retain manual startup.
Once the game is running, add `--carla` to the doctor command for a read-only
connection check.

For the tested Windows
installation, Low quality on Town10HD_Opt works; Town03 Low quality has had native
shader crashes. See [setup](setup.md#carla-and-docker) and
[hardware profiles](hardware.md) before changing quality or maps.

With an idle dedicated world, run:

```powershell
.\.venv\Scripts\python.exe -m scripts.validate_installation --mode carla --seeds 7 --duration 2 --output storage/reports/my-live-check
```

This explicitly executes real CARLA. It uses the loaded map and spawn zero,
chooses a destination along forward road connections, and lets the normal route
resolver build framework guidance. It does not load a different world or remove
pre-existing actors. The state-only profile scopes no-rendering to each run and
checks map/settings restoration afterward. Use a dedicated simulator with no
other synchronous tick owner. `--sensors` includes the teaching sensor controller
and keeps rendering enabled, with a greater graphics/memory load.

## 5. Add a trained model

Prepare the [reference AI worker and pinned checkpoint](reference-ai-pilot.md)
separately. No ordinary install or doctor check downloads weights. Then register
its package through **Agents**, select its prepared worker Python, and declare
the external checkpoint file. In **Experiments**, compare its registration with
the starter using the same recipe, seeds and scoring settings.

The installation validator can also include a trusted additional package:

```powershell
.\.venv\Scripts\python.exe -m scripts.validate_installation --mode carla --seeds 7 --duration 2 --agent-package examples/agents/reference_ai --agent-python .agent-venv/Scripts/python.exe --model-file 'storage/models/plant2/epoch=029_final_1.ckpt' --output storage/reports/my-model-check
```

`--configuration path.json` supplies that additional registration's configuration
overrides. For example, `{"device":"cpu","torch_threads":2,"cuda_graphs":false}`
selects CPU inference and avoids adding model allocations to a busy GPU. This
does not promise real-time execution. Model/package/runtime changes require fresh
registrations; historical results remain readable. See [comparison interpretation](agent-comparisons.md).

The additional agent keeps the normal 30-second load deadline by default.
If cold model startup needs more time and that is acceptable for your experiment,
explicitly set `--agent-load-timeout 120`. The chosen deadline is frozen in its
registration; inference/reset deadlines are unchanged. A larger load allowance
does not establish real-time inference performance.

## Browser and release checks

The optional browser suite uses Chromium and a new isolated synthetic workspace,
never your default database or CARLA:

```powershell
cd frontend
npx playwright install chromium
$env:POLTERGEIST_TEST_PYTHON = (Resolve-Path ../.venv/Scripts/python.exe).Path
npm run test:e2e
```

On Linux/macOS use `POLTERGEIST_TEST_PYTHON="$(pwd)/../.venv/bin/python" npm run test:e2e`.
CI installs browser system dependencies with `npx playwright install --with-deps chromium`.
Ports 8049 and 5189 must be free. The suite refuses to reuse an existing service.
Screenshots, traces and a printed HTML report are saved in ignored
`frontend/test-results/`; backend evidence remains in `.pytest_cache/browser/`.
Normal `npm test` continues to run the component/contract suite without a browser.

The GitHub workflow covers Python 3.12/3.14 on Windows/Linux, the core regression
suite, schema checks, isolated workflows, a non-editable wheel install, and a
separate browser/build job. The checked configuration uses the maintained
[Python](https://github.com/actions/setup-python), [Node](https://github.com/actions/setup-node),
[checkout](https://github.com/actions/checkout) and [artifact](https://github.com/actions/upload-artifact)
actions. Hosted CI does not claim CARLA GPU or learned-model validation.

A wheel contains the core runtime, command entry points, templates and model
license notice. It does not bundle the dashboard source, example packages or
model weights. From outside a checkout, pass `--examples-dir /absolute/path/to/checkout/examples`
to the validator and configure absolute `DATABASE_URL`/`STORAGE_DIR` for an ordinary
installed backend. Source-checkout commands above are the supported complete
local workflow. See [troubleshooting](troubleshooting.md) for failures.
