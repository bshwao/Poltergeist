# S7 managed CPU environment validation — October 28, 2026

The Windows x64 CPU implementation is delivered. Independent-machine acceptance
and public release remain open. This record separates real learned inference,
CARLA execution, packaging and GUI evidence from the earlier weight-only record.

## Delivered and checked

- A pinned CPython 3.12.10 embeddable runtime and 35 pinned package assets install
  through the Companion. Total environment archives: 310,437,548 bytes, separate
  from the 446,559,352-byte PlanT checkpoint. Every asset uses exact size/SHA-256,
  bounded resumable HTTPS transfer and owned storage. No runtime pip resolver,
  setup script or developer environment is used to assemble the capsule.
- The interpreter excludes core/user/developer third-party site-packages. A
  narrow finder exposes the existing first-party packages, preserving their
  registered source paths. The imported Torch, NumPy, SciPy, transformers, timm,
  OmegaConf and Pydantic modules must come from the capsule. Runtime import
  checks validate the complete installed dependency closure.
- Readiness actually loads the hash-pinned pretrained model, resets the SDK
  agent and executes an observation. The successful source check used Torch
  `2.6.0+cpu`, Python `3.12.10`, and measured approximately 0.101 s inference.
- Explicit local trust enables the verified pack in the Agent selector. Repeated
  enabling reuses an unchanged registration. Integrity manifests are included in
  registered model-file fingerprints. OS leases exclude repair/removal through
  local execution. Baseline and the included teaching starter need no download.
- The combined regression run passed **124 tests**, covering model transfers,
  archive paths, source archive installation without setup execution, integrity,
  unknown ownership, hardlinks, cancellation, use leases, trusted registration,
  real starter workers, Companion persistence, portable tooling and native Tk
  callback lifecycles. Evidence: `.pytest_cache/s7-final-regressions.xml`.
- A visible native Companion window was inspected at 860×740. All model controls
  were readable, scrolling exposed the enable/starter controls, and the test
  window closed normally. This was a separate isolated profile, not the user's
  ordinary Companion profile. It was a layout review, not an installed shortcut
  or full hardware-input model-installation acceptance.

## Actual CARLA execution

The source Companion workflow completed a 0.2 s CARLA experiment on
`Town10HD_Opt`, using Low graphics and the actual road raster. All four decisions
were applied unchanged. Diagnostics recorded CPU inference, positive timing,
`carla_ground_truth` inputs and the pinned checkpoint hash. JSON, CSV and HTML
exports succeeded; the saved result matched after closing and reopening the
runtime. The model environment use lease was reacquired on reopening.

Source receipt:
`validation-evidence/s7-local-1/managed-ai-carla-validation.json`, run
`c2f446df-f691-490a-822e-f4ecf6693cf7`. The validator records `passed: true`,
`reopened: true` and `closed: true`. This short trial establishes the installation
and control/persistence path; it is not a driving-quality or 20 Hz benchmark.

## Packaged acceptance

The corrected private Windows archive was built with the pinned OpenCV simulation
dependency included, alongside the existing CARLA client and NumPy. OpenCV is
needed by the existing measured-road-raster generator; it is built into the core
simulation package rather than installed into the core by model preparation.
The separate model pack retains its own pinned NumPy and other dependencies.

Archive: `validation-evidence/s7-cpu-complete/Poltergeist-S1-windows-x64.zip`.
Size: 87,526,671 bytes. SHA-256:
`f6d88a5b3c85883e021b6d88bf92ff8d88bc7f9af17135ffea0375144153b24c`.
Initial and post-run bundle integrity verified all 6,181 files unchanged. A fresh local temporary profile
outside the checkout uses only Windows tools on PATH, invalid developer Python
paths, isolated mode and the actual bundled interpreter. Verified cached upstream
downloads are copied into the new profile; interpreter/dependencies are assembled
from those assets, not copied from a developer environment. This workflow passed
actual pretrained inference and a 0.2 s CARLA run, applied all four decisions
unchanged, exported JSON/CSV/HTML and reopened the identical saved result. Both
the core and model used their own dependency sets: the packaged core has NumPy
2.5.4, while the checked model worker imports its pinned NumPy 2.2.4 and Torch
2.6.0+cpu. The runtime/profile closed successfully after its use lease was
reacquired on reopening.

Packaged run: `e2064faf-5e68-4d5a-9d46-87db52196043`. Execution and post-run
integrity evidence are retained under `validation-evidence/s7-packaged-complete.*`.
The [machine-readable summary](s7-environment.json) records receipt hashes,
source hashes, package versions, measured decision timings and the passing
regression count. Every listed shipped Python source/catalogue/license resource
matches the current source checkout.

## Retained failures and scope

- Initial upstream wheel names containing `+cpu` exposed an overly restrictive
  asset filename validator. These names are now accepted; path separators and
  unsafe names remain rejected.
- ANTLR's source archive contains two PKG-INFO files. Only its top-level original
  metadata/notices and import sources are installed; setup.py is never run.
- The first inference call passed an empty configuration to an SDK worker whose
  manifest declared model settings. It correctly rejected the mismatch. Readiness
  now passes the inspected manifest's exact configuration.
- Initial Windows enumeration used cached DirEntry hardlink counts, which can be
  zero. The guard now uses a real non-following stat; the hardlink regression
  verifies refusal before use or removal. Redundant ancestor checks were removed.
- A synthetic trial and the initial CARLA trial without parent-side OpenCV failed
  explicitly for missing road rasters. No baseline or invented raster was used.
  Synthetic mode is now rejected before queueing for this pack. The corrected
  source CARLA run passed after adding the existing OpenCV simulation dependency.
- Early sandboxed tests could not create the native OpenSSL, hardlink and junction
  fixtures. The final native regression run executed outside that restriction.
  Two initial GUI failures were missing optional coordinator test dependencies;
  the locked development dependencies were installed before the passing run.
- Earlier private build attempts and the stopped obsolete package validation are
  retained alongside the corrected build. No failure is counted as a pass.

The CPU pack does not claim CUDA installation, learned camera perception,
real-time throughput, general driving safety, independent-machine portability,
installed shortcut acceptance or public release/signing. Hosted execution policy
is unchanged. Native Windows/CARLA prerequisites remain the app's system
requirements; import failures cannot be treated as model readiness.
