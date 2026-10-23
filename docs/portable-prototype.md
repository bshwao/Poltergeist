# S1 private Windows x64 portable prototype

Status: partial S1. A private runtime/build and typed [runner boundary](runner-boundary.md)
are implemented. Same-laptop relocation is useful evidence, not the completion
gate on an independent computer without developer tools. S2–S10 remain planned.
See [the recorded local checks and remaining gates](s1-validation.md).

The prototype packages core command-line workflows, not the dashboard, installer,
tray app or hosted service. Build machines use Python/developer tools; users run
`Poltergeist.cmd` through a real private interpreter without Git, Node, pip, system
Python or a repository. CARLA and trained models are not included.

## Build

On Windows x64 with Python 3.12 and the project's build environment:

```powershell
.\.venv\Scripts\python.exe -m scripts.build_portable --output validation-evidence/portable-dist/my-new-build
```

Use a new output path each time. `packaging/windows-x64/inputs.json` pins the
Python 3.12.15 standalone PGO archive, the CARLA 0.9.16 client, runtime/build wheels
and supplemental license text by URL/version/SHA-256. Inputs are checked before
use; `--offline` requires all of them in the cache. There is no runtime resolver
or unpinned build isolation. Build dependencies stay outside the user bundle.

The builder creates a fresh non-editable project wheel and fresh assembly,
retains upstream runtime metadata/licenses, installs the fixed wheel set, validates
dependency closure (including requested extras), and checks required resources.
It includes templates, PlanT's existing license resource, starter/sensor teaching
examples and a dedicated SDK interpreter probe. No source `.env`, user database,
registration, model, recording, `.venv` or editable `.pth` is copied.

The real `runtime/python.exe` uses an explicit `python312._pth`; entry commands
use `-I -B`. Workers use that same interpreter and ignore developer Python paths.
Frozen launchers are rejected as default SDK interpreters, avoiding accidental
recursive launcher startup. Native and Python dependencies retain their original
notices. See the bundled `DISTRIBUTION-NOTICES.md`, package metadata inventory,
PE import inventory and input hashes.

The archive has a per-file unsigned manifest and adjacent SHA-256 file. File
ordering/timestamps and installed records are normalized; rebuilding identical
source/input bytes can be compared byte-for-byte. An unsigned hash detects damage,
not publisher authenticity. This is not a signed public release or a native-loader
support matrix. Keep binaries and build outputs in ignored `validation-evidence`.

## User commands and data

Extract the whole archive, open Command Prompt in its folder, then run:

```bat
Poltergeist.cmd verify
Poltergeist.cmd doctor
Poltergeist.cmd capabilities
Poltergeist.cmd worker-smoke
Poltergeist.cmd validate --sensors
```

Default writable profile: `%LOCALAPPDATA%\Poltergeist\S1Prototype`. To isolate
another evaluation, put `--data-dir "C:\YourWritableFolder\S1Test"` before the
command. Data must be outside the bundle. `doctor`, `capabilities` and `verify`
do not initialize a database. Execution creates logs, temporary worker caches and
fresh evidence under that profile; it never imports/moves a development database.
Native client caches use a writable execution working directory. Config accepts
only a separate CARLA executable path and port, never public API bind settings.

`configure` creates `config.json` once and refuses to overwrite it. Subsequent
changes are deliberate edits to that profile file. It does not verify the version
from a filename and never starts CARLA. For an already running dedicated server:

```bat
Poltergeist.cmd configure --carla-executable "C:\YourCARLA\CarlaUE4.exe"
Poltergeist.cmd validate --mode carla --duration 2
```

The live probe requires matching versions, refuses an already synchronous world,
and uses the existing validator's actor checks and map/settings restoration.
There is no automatic simulator startup during validation. Stop other submissions
and use an idle dedicated world; no actors belonging to other work may be removed.
A startup/connection failure is retained separately from synthetic evidence.

## Relocation and external test handoff

The build-machine verifier must use a new directory outside the checkout:

```powershell
.\.venv\Scripts\python.exe -m scripts.verify_portable --archive validation-evidence/portable-dist/my-new-build/Poltergeist-S1-windows-x64.zip --workdir C:/YourWritableFolder/S1-relocation-check
```

It extracts, relocates to a path with spaces, runs from an unrelated working
directory with only Windows system tools on PATH, and challenges isolated mode
with invalid `PYTHONHOME` and a source-tree `PYTHONPATH`. It records actual native
imports, module paths, SDK worker interpreter/results, synthetic comparisons,
reports/reopening, `.cmd` entry and before/after bundle integrity. Add `--live`
only for a separately started dedicated server; optional `--carla-executable`
and `--carla-port` write only the new isolated test profile.

For the independent completion gate, obtain owner authorization for the private
evaluation, copy the archive/checksum to a separate Windows 10/11 x64 computer
without Python/Node/Git/developer tools, and record OS/GPU/driver/native runtime
versions. Extract it, verify its hash, run the five commands above, then install
or select a complete matching CARLA server and run the short live validation.
Return the fresh profile's evidence/logs, capability report and native-loading
failures. Do not send existing personal databases or registrations. A missing DLL
is a failed native prerequisite; do not patch it by copying arbitrary System32 DLLs.

Microsoft's x64 Visual C++ runtime and graphics/OS components may be prerequisites;
`notices/native-imports.json` names unbundled imports (including MSVCP140.dll).
The current laptop's successful native imports do not prove those components are
present on another computer. Linux/macOS/ARM64, clean-machine support and full
CARLA static-dependency notice review remain unverified. The project also has no
top-level distribution license; the owner must settle public distribution terms.
