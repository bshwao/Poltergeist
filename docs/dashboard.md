# Dashboard workspace

Sections 8 and 9 provide Overview, Scenarios, Simulations, and Failures pages.
Build and launch a validated recipe, follow its saved run, inspect actual
observations, and compare recorded failure evidence. Search, automated
reproduction, and minimization remain available through the section 7 APIs;
the dashboard does not yet expose optimization controls.

## Start the workspace

From the repository root, start the API:

```powershell
.\.venv\Scripts\python.exe -m backend
```

In another terminal, start the frontend:

```powershell
cd frontend
npm run dev
```

Open <http://localhost:5173>. The frontend uses
`VITE_API_URL=http://localhost:8000` by default; configure a different absolute
HTTP or HTTPS base URL in `frontend/.env` and restart Vite. The backend allows
`http://localhost:5173` and `http://127.0.0.1:5173` by default. If Vite chooses a
different port, include its exact origin in root `.env`'s `CORS_ORIGINS` JSON
array. See [setup](setup.md) for installation and configuration details.

The connection indicator reads `/health`. Connected means the API process
answered; it does not establish CARLA, database, or worker readiness. The refresh
button retries health and template metadata. A failed or empty catalog refresh
preserves an already-loaded workspace and its draft.

## Build and validate a recipe

1. Open **Scenarios** and choose a template. The four built-in templates are
   intersection, pedestrian crossing, occluded pedestrian, and sudden braking.
   Their recipes, parameter descriptions, bounds, defaults, units, and integer
   requirements come from `/scenarios/templates`.
2. Enter a seed from 0 to 2,147,483,647, choose **Experiment duration**, and set
   parameter values for the experiment. Manual values can exceed the normal
   template sampling ranges; for example, ego speed can be 40 m/s. Field errors
   explain the remaining physical or practical constraints. Duration accepts custom seconds
   or **30 s**, **1 min**, **2 min**, and **5 min** presets. It measures simulated
   time, so actual runtime depends on CARLA performance. The duration must allow
   at least one tick and extend past the final configured event.
   **Reset defaults** restores parameter defaults, the template's duration, and
   seed 0. Selecting a different template starts a new draft.
3. Select **Generate recipe**. The backend's `/scenarios/generate` endpoint
   returns a full scenario recipe. The same template, seed, and fixed values
   produce the same recipe. Generation does not save or execute it.
4. Inspect the map, timing, actor summary, and full recipe JSON. Change **Recipe
   name**, edit the JSON directly, or paste a complete scenario object, then select
   **Validate JSON** before saving or launching.
   The `/scenarios/validate` endpoint checks the recipe and returns normalized
   JSON, including deterministic actor identifiers where needed. Validation
   errors show field paths and actionable reasons.

Changing parameters or duration marks the previously generated recipe as stale; regenerate
to apply those changes to JSON. Regeneration preserves the current recipe name.
Validating JSON instead keeps that recipe and synchronizes the template form's
parameters, duration, and seed to the normalized result. Custom recipes provide
the same duration control alongside their JSON editor without displaying an
unrelated template form. Their duration edits update the JSON and require validation
again. A validated badge applies to the current normalized JSON, not to later
edits. Superseded requests are aborted and their responses cannot replace newer
edits.

The overview uses one consistent 24 px gap between its heading, scenario hero,
Build/Run/Review guidance, and recorded analytics. Duplicate template-count,
connection-status, and validation cards have been removed; template count and
connection state remain in the sidebar and top bar.

Validation checks structure, actor references, parameter bindings, and domain
constraints. It does not certify real map geometry or driving behavior. During
CARLA execution, built-in templates automatically select a suitable complete
road layout and validate it against the loaded map. Custom coordinates stay fixed.

## Save and load scenarios

Select **Open library** to read saved scenarios from the configured backend.
Opening the library opts into local persistence initialization. It shows eight
records per page, with **Previous page**, **Next page**, and **Refresh saved
scenarios** controls. **Load** fetches the current record from the server. If the
workspace contains unsaved changes, choose **Load saved recipe** to replace them
or **Keep current draft** to retain them. Loaded recipes are already validated.

After validating a new draft, select **Save new recipe**. A loaded or newly saved
recipe exposes **Update saved recipe** for edits and **Save a copy** for a separate
record. Updates include the revision that was loaded. If another request changed
the saved record, HTTP 409 leaves the draft intact: choose **Load latest revision**
(with draft replacement confirmation), or save your draft as a copy. A delayed
save response cannot overwrite JSON edited while that request was in progress.

**Delete** opens an in-page confirmation and checks the listed revision before
deleting. The API may reject deletion if the record changed or a run references
it; refresh the library before retrying. Deleting the record currently associated
with the workspace leaves its draft available and makes it a new, unsaved draft.

Saved records live in backend SQLite and survive browser reloads. They are
separate from unsaved page edits; saving is always an explicit action.

## Configure and launch a run

1. Generate a recipe and validate its JSON, or load a saved recipe. Changed
   template parameters must be regenerated or replaced by validating the JSON
   before launch is available.
2. Choose **CARLA** or **Synthetic test** explicitly. No mode is preselected.
   CARLA uses the configured server and requested map. Synthetic test exercises
   the pipeline with approximate telemetry and cannot certify real map placement
   or driving safety. Test mode disables camera/LiDAR capture and clears capture
   selections made in CARLA mode.
3. Expand **Execution settings** to adjust map reload permission, sensor timing,
   maximum ticks, camera dimensions, and optional CARLA camera/LiDAR capture.
   Timing values must be positive; tick counts/dimensions must be whole numbers.
   Camera dimensions remain validated even when capture is disabled.
4. Expand **Evaluation settings** to adjust episode gaps, thresholds, and severity
   weights saved with the run. The TTC horizon must cover its warning time;
   full-severity braking must exceed the hard-braking threshold. The four severity
   weights must total 100%. Field errors explain the required bounds. Editing
   execution/evaluation settings does not invalidate the recipe's JSON validation.
5. Select **Launch simulation**. The request freezes a copy of the validated
   recipe and selected settings. Loading or editing a different recipe afterwards
   cannot change that submission. A launch uses inline JSON rather than its saved
   scenario ID, so a concurrent server edit cannot change the submitted recipe.
   The run stores the recipe snapshot without a link to the saved library record.

When a CARLA run initializes successfully, the CARLA window's spectator camera
automatically moves to an overhead view of the scenario actors. This uses the
first measured world snapshot and adds no camera sensor or recording overhead.
The view is positioned once, so you can then fly it manually. Vehicles are
removed at the end of the run; watch while it is active. Spectator positioning
does not require the **Capture camera frames** checkbox. A server launched with
`-RenderOffScreen` has no visible spectator window, and no-rendering mode has no
3D scene. Restart the backend after installing this change to load the new code.

HTTP 202 displays **Run accepted**, its ID, mode, status, and available queue
position at acceptance. This receipt does not track subsequent progress or prove
the run completed. Select **Track this run** to open its live detail view.

**Check queue** requests an advisory snapshot of waiting/active work and whether
the API is accepting submissions. This explicit action initializes persistence
and starts/recovers the single simulation worker. It does not prove that CARLA
is reachable or that the recipe will execute successfully. Queue state is not
polled automatically. Opening a different recipe keeps the selected execution
settings for the workspace.

Launch errors, including capacity limits and worker unavailability, appear in the
panel; there are no automatic launch retries. If a network failure leaves
acceptance uncertain, the server may already have queued the run. Open
**Simulations**, refresh history, and identify the run by creation time, recipe
name, seed, and mode before retrying to avoid duplicate runs.

## Follow saved runs

Open **Simulations** to read ten saved executions per page. Filter by execution
mode or status, move between pages, and use **Refresh history** for a new
snapshot. The list reports real saved data, with unscored results labeled
**Unscored**. History is a snapshot; it does not poll every listed run.

Select a run name, or use the launch receipt's **Track this run** link. The URL
ends with `#simulations/<run UUID>`, so a run can be bookmarked or reopened after
a browser reload. The selected run polls serially while queued/running,
approximately every 1.2 seconds after a successful response. Terminal progress
triggers a finalized result read and stops polling. Requests have timeouts;
temporary errors retain the last confirmed observation and retry with a bounded
delay. A missing run requires an explicit refresh. Leaving the Simulations page
aborts its requests and polling; reopening it reads the current saved state.

The detail view shows mode, agent, progress, queue position, timestamps, recipe
seed/map, revision, and the frozen recipe/execution/evaluation settings.
**Cancel run** requests cooperative cancellation for API jobs. A confirmed
request is displayed separately from a terminal cancelled result: native calls
must return before cleanup and partial-result persistence can finish. An
uncertain cancellation response directs you to refresh before retrying. Manually
stored records without an API job have no cancellation control.

Reading simulation history/detail starts or recovers the backend's single
worker. It does not establish a CARLA connection until a CARLA run executes.
Existing queue/restart behavior is described in [the API guide](api.md#simulation-jobs).

## Inspect finalized observations

Once a result is available, safety cards show severity, observed collision/rule
counts, minimum TTC/clearance, braking, and completed-stop metrics. **Unknown**
remains distinct from an observed zero. Expand observation coverage for each
measurement's frame and actor-pair counts, and inspect scoring settings/runtime
provenance before comparing runs. Failed/cancelled traces and observation gaps
remain visible. A completed run alone does not establish safety or full coverage.

**Measured motion** loads at most 200 finalized telemetry frames per request.
Use **Previous frames** and **Next frames** to inspect longer traces. Chart labels
state which page and simulation-time window are displayed; the page is not
presented as a downsampled whole run. Ego speed uses the measured velocity
magnitude. Longitudinal deceleration projects measured acceleration against
velocity; rest or missing vectors stay unknown. Aggregate braking evaluation may
also use adjacent velocities and its recorded stopped-speed policy, so its
summary can differ from this direct-observation chart. Missing measurements and
skipped frames break chart lines. The frame slider shows exact time, speed,
deceleration, controls, and event counts. Aggregate TTC/clearance cards use the
backend evaluator rather than an approximate chart-side geometry calculation.

Artifact links download this run's indexed telemetry, reports, and raw capture
manifests. Sensor bytes load only after **Load capture manifest** and **Inspect
sample**. Navigate by sample number or previous/next controls; each sample shows
its sensor, recorded frame, timestamp, encoding, and byte count. The inspector
checks SHA-256 and byte length before displaying decoded data. A sample download
is limited to 64 MiB. Integrity verification requires HTTPS or localhost.

Camera samples decode actual BGRA8 bytes to a canvas. LiDAR inspection reports
finite XYZI point counts and sensor-frame XYZ bounds, with float32 values
explicitly decoded as little endian. These are individual raw sensor samples,
not encoded video, a reconstructed world, or a rerun. Synthetic runs have no
CARLA camera/LiDAR captures. CARLA runs without successful enabled captures show
an explanatory empty state. Navigating to another run/sample or leaving the page
aborts superseded loads and clears stale sample content.

## Browse and compare failures

Open **Failures** to list eight recorded reports per page. Filter reproduction
evidence as **Confirmed**, **Not reproduced**, **Unknown**, or all states, and
optionally apply a parent simulation UUID. Refresh and pagination read stored
reports across history. Search operations can create reports automatically.
In a finalized run, **Save a failure report** records a selected failure kind
and summary using the exact recipe and measured severity. Only scored positive
evidence is eligible; near-miss eligibility uses the recorded TTC/clearance
thresholds, or backend defaults when settings are unavailable. Reproduction
starts as **Unknown**. This action creates a report without launching a run.
A high severity alone does not create a report from every manual launch.

Open a report for its original recipe/seed, parent run link, execution mode,
metrics and coverage, execution/evaluation settings, and available runtime
provenance. Opening a report does not rerun it. **Save reproduction observation**
records an explicit manual flag only; it does not execute or verify reproduction.
Use **Unknown** when evidence is insufficient. Automated repeat trials use the
[reproduction API](optimization.md).

Select up to two reports, including across pages, for a side-by-side comparison.
Actual observations and nulls are retained. Different modes, agents, execution
options, scoring settings, incomplete runs, and partial coverage receive explicit
compatibility notes; changed scoring settings are listed. Even compatible
results describe two individual observations, with no inferred risk reduction.
Comparison selections remain available while browsing the current page session.

## Export finalized evidence

In a selected finalized run, **Export this run** downloads a JSON report, CSV
summary, or printable HTML report. All formats preserve the actual mode,
recipe, metrics, coverage, settings, runtime, and failure observations. Check
**Include finalized telemetry in the JSON report** only when you need the full
trace; it can substantially increase download size. Raw sensor samples remain
separate artifact downloads. Exporting does not rerun the scenario.
See [report formats and CLI](reports.md).

## Read historical analytics

On **Overview**, select **Load analytics** or **Refresh analytics** for a timestamped
snapshot of all persisted history, beyond the current run page. CARLA and
synthetic test groups remain separate. Counts include status, finalized/scored/
unscored results, reports, and collision/rule observations with their observed
denominators. Mean severity includes scored terminal results under their saved
settings; it is not a controlled safety comparison or a certification. Unknown
means and empty history remain explicit. Analytics load only on request.

## Draft lifetime

Dashboard navigation retains the recipe draft within the current page session.
A browser reload closes that session and discards unsaved edits. There is no
autosave or browser storage. Save a validated recipe or copy its JSON before
reloading. Template loading, generation, and validation are pure API calls; they
create no database, artifacts, or simulation jobs. Saved-data and queue actions
initialize their corresponding backend features only when requested.

Built-in CARLA layouts are resolved and validated against the running map;
runtime metadata preserves their resolved spawn transforms. Raw sensor captures
are stored as samples/manifests rather than encoded video playback. See
[live execution checks and limits](simulation.md#rule-coverage-results-and-limitations).

## Verify frontend changes

```powershell
cd frontend
npm test
npm run contracts:check
npm run build
```

The tests use Node's built-in runner, TypeScript, and a React test renderer
matching the application's React version. They do not need a running backend.
They cover typed request behavior, safe
error formatting and abort handling, parameter validation, seed rules, JSON form
parsing, saved-record reconciliation, immutable launch payloads, and execution/
evaluation field validation. Component interaction tests cover loading and
deleting saved recipes, revision conflicts, draft replacement, delayed saves,
explicit execution modes, queue checks, and launch receipts. Section 9 adds typed
run/failure/analytics requests, serial polling and cancellation races, deep links,
bounded telemetry pagination, unknown-preserving metrics, raw sample integrity
and decoding, lazy loading and aborts, and evidence comparisons. The build also
type-checks React components. Final section 9 verification counts are recorded in
the [implementation handoff](implementation-plan.md#verification). Browser
interaction and responsive layout require a separate visual check.
Section 9's browser check was blocked by unavailable browser tooling; section 10
restored browser access and records its desktop/mobile checks and limits
in the implementation handoff.
