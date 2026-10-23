POLTERGEIST S1 PRIVATE WINDOWS X64 PROTOTYPE

This archive is a command-line packaging proof, not the S2 companion or an
installer. No dashboard, cloud pairing, trained model, or CARLA server is bundled.
Use only in a private evaluation authorized by the project owner.

Extract the entire folder to a local directory. Do not run from inside the ZIP.
Open a Windows Command Prompt in the extracted folder and run:

  Poltergeist.cmd verify
  Poltergeist.cmd doctor
  Poltergeist.cmd capabilities
  Poltergeist.cmd worker-smoke
  Poltergeist.cmd validate --sensors

No Git, Node, pip, system Python or developer checkout is needed to run these.
The real private interpreter is runtime\python.exe. Keep the entire folder intact.
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
