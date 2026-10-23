# Private desktop companion

Status: S2 private Windows x64 prototype, built on the S1 portable runtime.
The companion executes local work and preserves it across restarts. Hosted
pairing, an installer, managed model downloads and public release are later
milestones. The S1 independent-machine and redistribution gates remain open.

## Open the application

Extract the complete private archive to a writable local directory, then
double-click **Poltergeist Companion.vbs**. It invokes the bundled `pythonw.exe`
with isolated imports; it needs neither a console nor system Python/Node/Git/pip.
Windows Script Host must be available under the machine's existing policy.
If it is disabled, an alternative using the same private runtime is:

```cmd
runtime\pythonw.exe -I -B -m scripts.companion
```

Use a short extraction path. A CLI relocation under the deeply nested Codex
AppData virtualization directory failed to load SQLAlchemy's native extension
with `The filename or extension is too long`; this is a recorded native path
limit. The desktop relocation with spaces passed. Installer path selection and
broader native validation remain S3/S10 work.

The archive retains its original `Poltergeist-S1-windows-x64` folder name for
compatibility with the S1 verifier. Its new manifest identifies S1/S2 and includes
the companion, real GUI interpreter and Tcl/Tk resources. The original S1
validation archive and its recorded checksum are unchanged.

For source development only:

```powershell
.\.venv\Scripts\python.exe -m scripts.companion --development
```

The default profile is `%LOCALAPPDATA%\Poltergeist\CompanionPrototype`.
`--data-dir` selects an absolute profile outside the installed bundle. No `.env`,
ambient backend settings, user-site Python packages or development database are
used. Settings are `companion.json`; SQLite is in `database/`; artifacts and
recordings are in `storage/`; temporary native caches, exports, diagnostics,
imported reports and rotated logs remain in the same profile.

The window holds an OS instance lock for the profile even while its runtime is
stopped. Another window cannot use the same settings/database concurrently.
Stale lock files are harmless: ownership is the OS lock, not a saved PID. The
existing database worker lease additionally excludes a second queue owner.
Different profiles are not yet coordinated around a shared CARLA endpoint;
use one active companion per CARLA server until S6 adds that ownership boundary.

## Run and review

The window starts its local runtime. **Run recipe** executes the selected
scenario through the existing `SimulationService`, `SimulationRunner`, agent
workers, evaluator and persistence implementation. There is no internal HTTP
server. Each profile has one active worker and a bounded durable waiting queue.

Start with **Synthetic installation check** and **Synthetic test**. Completed
synthetic work is labelled accordingly and does not establish CARLA execution
or driving safety. The twelve collision presets are also available; ordinary
live runs keep the server's normal signal behavior, so their danger labels have
the scoped calibration limits documented in [collision-stress.md](collision-stress.md).

**Import scenario** accepts a validated standalone scenario JSON. Detailed
scenario editing remains in the dashboard. The recent run table shows mode,
status and progress. Select a finalized run to export an HTML or JSON report to
the profile's `exports/` folder. An error remains attached to its failed run.
Restarting the app reopens the same database and finalized evidence.

**Register trusted agent** statically inspects a local folder containing
`agent.json` and records its immutable source/interpreter identity. Approving it
is explicit because selecting it executes that Python code with user permissions.
The private core interpreter is used; registration does not install dependencies,
prove inference works or sandbox the package. The bundled starter works with the
core runtime. Managed environments/weights and richer model choices belong to S7.

**Cancel selected** requests cooperative cancellation. **Stop local runtime**
stops acceptance and requests cancellation of active work; waiting jobs stay
durable and resume on Start. Closing the window follows the same cleanup path.
If a native call outlasts the short shutdown wait, the window stays open with
`stopping` status and keeps its profile lock. Retry Stop/Close after it returns.
CARLA remains open. A successful stop is reported only after the worker exits.
Abrupt process/PC loss is reconciled by the existing durable worker: claimed work
is failed as interrupted rather than blindly rerun. Broader fault testing is S6.

## Configure and diagnose CARLA

Stop the local runtime, browse to the complete CARLA 0.9.16 installation's root
`CarlaUE4.exe`, select a local RPC port and Low/Epic startup graphics, then save.
Windows layout checks reject a standalone shipping executable without the root
`Engine/Content/Internationalization` and `CarlaUE4/Binaries/Win64` resources.
File layout cannot establish the server version. Automatic startup is off by
default and requires a selected installation; Low is the default quality.

**Check prerequisites** performs static local checks. **Check CARLA RPC** makes
an explicit bounded connection/version check in a subprocess; it starts no
simulator and runs no experiment. Both show readable findings and next actions.
**Save diagnostics** writes their structured local report for deliberate review
or sharing. Nothing is uploaded automatically. Logs rotate at 2 MiB with two
backups. Existing log/error messages retain their local paths.

Choose **CARLA** explicitly to execute live work. Use an idle asynchronous server
with no other tick owner. The existing launcher reuses a listener; it starts the
configured installation only for a live run when automatic startup is enabled.
An existing server keeps its graphics settings. Map reload is not enabled by the
desktop, so start the intended map beforehand or let a new server start with the
recipe's map. Setup detection/wizard/repair and hardware guidance expand in S3.

## Bring existing evidence deliberately

No existing database, registrations or recordings are silently moved or trusted.
Export a finalized JSON run report from the development dashboard, then choose
**Imported reports → Import run report**. The companion validates its versioned
record and archives a separate copy under `imported-reports/`; selecting it shows
its original JSON. Imports are bounded at 10 MiB and duplicate content is reused.
The original source is unchanged. Importing does not restore execution jobs,
register code, resolve artifact paths or copy raw recordings. Original storage
and recordings remain available through the original installation. Scenario JSON
can be imported separately to run a fresh experiment.

## Validation and limits

The [local S2 validation receipt](validation/s2-desktop.json) records the final
47,892,405-byte archive, its SHA-256, 148 matching packaged Python sources,
baseline/starter desktop runs, preserved results, private Tcl/Tk imports,
final CLI checks and 39 passing focused tests on each of Python 3.12/3.14.
The earlier full integration run passed 2,705 tests with 35 optional skips;
final startup/path/cleanup refinements were covered by the focused reruns.

`tests/test_companion.py` exercises real SQLite/artifacts/synthetic execution,
restart, exports, static diagnostics, trusted SDK execution, cross-process
instance exclusion and a native-call shutdown timeout that retains ownership.
`tests/test_companion_ui.py` invokes real Tk controls and checks background
responsiveness, execution/export/restart and settings/diagnostic callbacks.
Machines without a Tk/display skip only the GUI checks.

The packaged check uses a new profile and invokes the actual application's Tk
callbacks, runs baseline and starter synthetic experiments, closes/reopens the
window/controller, verifies persisted results and imports/diagnostics, and checks
all first-party/Tk imports and Tcl/Tk resource paths stay inside the bundle:

```cmd
runtime\python.exe -I -B -m scripts.verify_companion --data-dir "C:\NewCompanionCheck"
```

It writes `companion-validation.json`, including exact interpreter/module paths,
run identities, cleanup and diagnostic evidence. This is application-level
automation on the same computer. The native window was visually inspected, but
hardware mouse automation could not be verified because the Windows input helper
returned `SendInput sent 0 of 1 events; GetLastError=87` twice. That limitation is
separate from the tested Tk callbacks. Independent no-developer-tools testing,
broader Windows/display/hardware coverage, signing, release licensing and safe
updates are still required. Website connection status explicitly remains
unavailable; the S1 runner DTOs are not an authenticated hosted dispatcher.
