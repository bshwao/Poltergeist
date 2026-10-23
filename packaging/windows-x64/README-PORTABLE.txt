POLTERGEIST S1/S3 PRIVATE WINDOWS X64 PROTOTYPE

This archive includes the private desktop companion and packaging checks.
No installer, dashboard, cloud pairing, trained model or CARLA server is bundled.
Use only in a private evaluation authorized by the project owner.

Extract the entire folder to a local directory. Do not run from inside the ZIP.
Double-click "Poltergeist Companion.vbs" to open the desktop window without
a console. Keep the entire extracted directory together. If Windows Script Host
is disabled by your computer's policy, do not bypass it; launch the private
runtime from a Command Prompt instead:

  runtime\pythonw.exe -I -B -m scripts.companion

On first run the companion shows Setup. Detect or browse for CARLA, inspect local
hardware/disk facts, and explicitly check the running client/server versions and
maps. Choose Use synthetic for now if CARLA is not installed. Save the choices,
then Start local runtime; later launches reopen the saved local queue. Hardware
inspection does not establish simulator/model performance. The companion stores
results across restarts, imports
scenario JSON, exports HTML/JSON reports, and provides CARLA settings/diagnostics.
Synthetic test uses synthetic physics. Select CARLA explicitly for a live run.
Use an idle asynchronous world with no other tick owner. Automatic CARLA startup
is off until you select the complete CARLA folder and enable it in Settings.
Default graphics are Low. Stop the runtime before saving settings. Closing the
window stops/cancels its worker; CARLA itself remains open. If a native call is
still returning, keep the window open and retry Stop/Close. Waiting jobs remain
saved and resume when the local runtime starts again.

Desktop data is separate from the CLI prototype:
  %LOCALAPPDATA%\Poltergeist\CompanionPrototype
No development database is moved. Use Imported reports to archive an existing
dashboard JSON export, or Import scenario for an existing recipe. Raw recordings,
queued jobs and local code trust are not migrated by report import. Hosted pairing
and the full website connection are not implemented; no local HTTP server opens.

For the original command-line packaging checks, open Command Prompt and run:

  Poltergeist.cmd verify
  Poltergeist.cmd doctor
  Poltergeist.cmd capabilities
  Poltergeist.cmd worker-smoke
  Poltergeist.cmd validate --sensors

No Git, Node, pip, system Python or developer checkout is needed to run these.
The real private interpreters are runtime\python.exe and runtime\pythonw.exe.
Windows 10/11 x64, suitable graphics drivers and the native Microsoft Visual C++
runtime may be required by native dependencies. See DISTRIBUTION-NOTICES.md.

Configuration, logs and new evidence use:
  %LOCALAPPDATA%\Poltergeist\S1Prototype
Existing development databases and registrations are never imported or moved.
For a separate test profile, use an absolute directory outside this bundle:
  Poltergeist.cmd --data-dir "C:\YourWritableFolder\S1Test" validate --sensors
All report commands print the evidence path. Logs are in the profile's logs folder.

CARLA 0.9.16 remains a separate installation. The prototype never starts it:
  Poltergeist.cmd configure --carla-executable "C:\YourCARLA\CarlaUE4.exe"
  Poltergeist.cmd validate --mode carla --duration 2
Configure creates config.json once; edit that file deliberately to change it.
Use a dedicated idle asynchronous CARLA world with no vehicles/walkers/sensors
and no other tick owner. Stop other run submissions while testing. The check
refuses existing actors and preserves map/world settings; it does not reload maps.
A folder name is not version verification. The live check requires matching
client/server versions and records evidence separately from synthetic results.

File hashes detect accidental changes but this archive/manifest is unsigned.
A successful same-computer relocation test is not a clean second-machine test.
The S1 completion gate remains open until independent no-developer-tools testing
and the required native/redistribution review are recorded.
