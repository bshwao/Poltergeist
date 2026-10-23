# Private S1/S3 distribution inventory

This is an unsigned private prototype. The repository currently has no top-level
project license; no new distribution license is granted by this build. The owner
must establish project distribution terms before external/public distribution.
This notice is not a substitute for the licenses retained in this archive.

- Python 3.12.15 is the pinned Astral python-build-standalone Windows x64 PGO
  distribution. Its upstream `PYTHON.json`, available component license texts and
  runtime `LICENSE.txt` are retained under `notices/python` and `runtime`.
  See https://docs.astral.sh/python-build-standalone/distributions/ and
  https://github.com/astral-sh/python-build-standalone/blob/main/docs/running.rst.
- Dependency names, exact versions, upstream metadata and retained license paths
  are in `notices/python-packages.json`. Their original `.dist-info` notice/license
  files remain under `runtime/Lib/site-packages`. The input URLs and SHA-256 values
  are in `notices/inputs.json`.
- The CARLA 0.9.16 Python wheel identifies its license as MIT but omits its license
  text. The version-tagged upstream license is included as `notices/CARLA-LICENSE.txt`:
  https://github.com/carla-simulator/carla/blob/0.9.16/LICENSE.
  A complete review of statically linked CARLA client dependencies/notices is
  still required before public redistribution; wheel metadata alone is not that review.
- The CARLA server, Unreal Engine, maps/assets, trained-model code/weights and
  their separate distribution terms are outside this archive. The PlanT license
  already shipped as a project resource remains present; no model weights are included.
- Native `.pyd`/DLL/executable import names are inventoried in
  `notices/native-imports.json`. Python/NumPy and other wheels carry native code.
  Included runtime DLLs retain upstream notices. Unbundled imports include Windows
  system components and may require the Microsoft Visual C++ 2015–2022 x64 runtime:
  https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist.
  No arbitrary DLL was copied from this build machine's System32. Static PE import
  inventory does not cover every delayed/dynamic load or establish redistribution rights.

The prototype targets Windows 10/11 x64 only. This laptop is the initial evidence
machine, not a supported OS/hardware matrix. Linux, macOS, Windows ARM64, other
Python ABIs and clean-machine native loading remain unvalidated. A suitable GPU,
driver and a separately installed matching CARLA server are required for live tests.
The portable archive contains no installer. The separate private S3 installer
adds per-user setup, repair and uninstall. Signing, updates, hosted transport
and secure pairing remain unimplemented.
