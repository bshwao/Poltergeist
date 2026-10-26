# Private Windows installer and first-run setup

Status: **private prototype under validation**, October 26, 2026. This builds on the
[private desktop companion](companion.md) and its pinned private runtime. It is
unsigned and has not passed the independent no-developer-tools machine gate or
the external-tester S3 completion gate. See [progress notes](progress-notes.md).

## Install and set up

Run `Poltergeist-Companion-Private-Setup.exe` under the Windows account that will
use the companion. Installation is per-user, without administrator elevation.
The default location is a short user application folder; an overly long or linked
path is refused because native Python extensions have a measured path limit.
Windows x64 is the prototype target; this is not an ARM64 or broad support claim.
Keep application files and the companion data profile in separate folders.

Setup creates Start menu entries for the companion, repair and uninstall.
Desktop and login-startup shortcuts are optional and unchecked initially.
Rerunning setup with either option disabled removes its previously created
shortcut. Setup checks installed private Python, application/native imports,
Tcl/Tk resources and complete bundle integrity before reporting success.

Open the companion and use **Setup**. Detection searches bounded known local
locations and reports incomplete installations separately. Browse to the complete
CARLA 0.9.16 installation's root `CarlaUE4.exe` if it is elsewhere. Detection
checks file layout; the connection check establishes client/server compatibility.
Hardware and free-space observations are informational, without a CARLA/model
performance qualification.

Choose the local RPC port and Low/Epic startup graphics. Low is the default.
Automatic startup is an explicit option. **Start / reuse CARLA** starts the
selected complete installation on the selected map, or reuses an existing server
without changing its map. Stop the local runtime before explicit startup.
**Check CARLA connection** reads versions, current map, available maps and
synchronous status; it does not tick or reload the world. Collision presets
require `Town10HD_Opt`. Use an idle asynchronous server with one execution owner.

**Save setup choices** persists configuration; it does not claim an experiment
has run. **Use synthetic for now** explicitly completes synthetic-only setup
without changing saved CARLA choices. Then start the local runtime and run the
synthetic installation check. Choose **CARLA** explicitly for a live run.

## Repair and uninstall

The repair shortcut runs a local copy of the same installer, without downloading
dependencies. Its version directory is identified by the complete bundle manifest,
so a dependency-only change receives a different runtime directory.

For same-build repair, setup moves the old runtime into the install folder's
`recovery/` directory before extracting a clean, exact copy. This permits repair
of both damaged and unexpected files. The preserved copy is not recursively
deleted. A failed repair attempts to put that copy back and records any inability
to do so in the setup log. Recovery copies are retained for deliberate inspection;
they consume disk space and may remain after uninstall. This prototype mechanism
does not establish the signed update/rollback workflow required by S10.

Close the companion before repair or uninstall. Setup checks the packaged-app
mutex and refuses an in-use application rather than terminating it. Settings,
SQLite, logs and original recordings stay in the separate companion profile.

Uninstall defaults to retaining all user data. The interactive removal option
deletes only a profile created and marked by this installer, bound to its exact
absolute path. A pre-existing or imported profile is preserved. Links/junctions,
foreign ownership, active companion/database leases and unsafe roots prevent
removal. A structured removal report records the outcome. Silent uninstall also
keeps data unless `/REMOVEOWNEDDATA=1` is explicitly supplied.

## Build and evidence

On a Windows x64 build machine with the project build environment:

```powershell
.\.venv\Scripts\python.exe -m scripts.bootstrap_installer --destination validation-evidence/installer-tools/inno-7.1.0
.\.venv\Scripts\python.exe -m scripts.build_portable --offline --output validation-evidence/portable-dist/new-build
.\.venv\Scripts\python.exe -m scripts.build_installer --bundle validation-evidence/portable-dist/new-build/Poltergeist-S1-windows-x64 --output validation-evidence/installer-dist/new-build
```

Use fresh output directories. Bootstrap verifies the pinned Inno Setup archive
hash, then extracts its build tool privately. The pinned upstream installer was
also checked manually for a valid Pyrsys B.V. Authenticode signature during local
toolchain validation; bootstrap does not repeat that certificate check.
It does not install a compiler globally. The generated installer is **unsigned**;
a verified compiler does not authenticate the resulting product.

`--validation-profile` compiles a distinct validation application identity and an
isolated profile ending in `CompanionPrototype`. It must not be distributed as the
normal private installer. Build metadata records this distinction, exact profile,
manifest identity and installer checksum. Test installation is not performed by
the builder.

The installed native check is `scripts.install_check`; it supervises its own
bounded subprocess and creates no experiment database or CARLA world. GUI tests
run each real callback case in its own application process, matching the native
app's interpreter lifetime. Same-laptop native lifecycle receipts and the full
regression result are recorded in the
[sanitized S3 receipt](validation/s3-installer.json) and
[progress notes](progress-notes.md).

The originally delivered S3 installer predates accounts and remote execution.
Later validation builds carry the current S4–S6 code; their evidence and payload
identities are recorded separately. They do not replace that original installer
or establish a public distribution.

The [installed Runtime I recovery record](validation/s6-installed-recovery.json)
uses the fresh compiled validation profile and normal first-run synthetic callback.
Native setup, pairing/recovery, consent, cancellation, suspension and reopening
pass on this laptop. Default uninstall preserves all profile file bytes. The
Start menu shortcut's target/arguments are verified, without actually invoking it.

External clean-machine/native support testing, visual onboarding inspection,
redistribution terms, code signing and external beta remain open. CARLA and model
weights are separate installations. Public hosting remains deferred.
