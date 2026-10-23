# S1 validation record: October 20, 2026 (America/Toronto)

Status: **partial S1**. These are same-laptop checks, not independent clean-machine
verification. The private command-line prototype does not implement S2–S10.

| Check | Recorded outcome |
| --- | --- |
| Offline reproducibility | Two fresh builds from identical source/pinned inputs produced identical ZIP bytes |
| Archive | 47,865,065 bytes; SHA-256 `5747958789d850ab1853dc234d8ac8da4b5f141f6111a2cc69f1cd256406fe7a` |
| Relocation | Extracted then moved outside the checkout to a path with spaces; commands ran from an unrelated working directory |
| Developer environment | PATH contained only Windows system tools; invalid PYTHONHOME and source-tree PYTHONPATH were ignored |
| Imports | Private Python 3.12.15; CARLA 0.9.16, NumPy, SSL and SQLite loaded; recorded first-party module paths stayed inside the relocated bundle |
| SDK process | Actual worker reported the bundled interpreter/module paths and completed load/reset/two frames/close |
| Synthetic | Baseline, starter and sensor teaching agent completed on seed 7; JSON/CSV/HTML behavior exports and persistence reopen passed |
| User entry point | Poltergeist.cmd completed without system Python, Node, Git or pip lookup |
| Live | Baseline and starter each completed a 2-second CARLA run on Town10HD_Opt, with matching client/server 0.9.16 |
| Live ownership/restoration | Fresh idle asynchronous server, Low quality, scoped no-rendering; map/settings before and after matched; no vehicles/walkers/sensors/controllers remained |
| Bundle integrity | All file hashes matched before and after the complete workflow |
| Regression checks | 306 focused tests passed with six host symlink skips; final changed-code selection passed 216 tests; shared schema/types and frontend build passed |

The machine reported Windows 11 build 26300. The precise native loader/driver
environment is evidence for this laptop only. The run directories use UTC
timestamps dated October 21; the local validation date above is October 20.
Raw evidence remains outside source-controlled application data; delivery copies
include an integrity/reproducibility summary and the relocated validation report.

The first simulator startup attempt used an incomplete duplicate installation.
Unreal's native crash context reported `ICU data directory was not discovered`
at zero seconds; that copy lacked the Engine internationalization directory.
It was not a scenario/model/GPU diagnosis. A complete separate installation
contained the Engine resources and answered version/state probes. An intermediate
packaged live attempt found no listener and refused to start an experiment.
The final dedicated full-installation Low-quality attempt succeeded. No entire
simulator or model was downloaded, and no development database was imported.

Remaining gates:

- Run the archive's synthetic, SDK and short live checks on a separate Windows
  computer without developer tools, recording native prerequisites/failures.
- Validate a supported OS/GPU/driver matrix, including Visual C++ runtime and
  any delayed/dynamic native dependencies; static PE imports are not a loader proof.
- Establish the project's distribution license and finish the CARLA client's
  statically linked dependency/notice review before public redistribution.

See [portable instructions](portable-prototype.md) and
[the runner boundary](runner-boundary.md). Nothing here is a signed release,
installer, hosted transport, safety assessment or real-time benchmark.
