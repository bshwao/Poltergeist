# Phase 10: supported workflow and validation

Validated October 20, 2026. This phase adds installation diagnostics, an isolated
end-to-end acceptance command, maintained browser checks, release/CI gates and
a current first-run/hardware/troubleshooting path. It also fixes the starter
package's outdated SDK declaration. No database migration is required.

## Delivered

- `python -m scripts.doctor` / `poltergeist-doctor`: core package/configuration
  checks; optional frontend requirement, bounded read-only CARLA probe, and
  explicitly requested hardware point snapshot. Default diagnostics do not
  initialize storage, import CARLA or execute agents/models.
- `python -m scripts.validate_installation` / `poltergeist-validate`: a private
  loopback API, database and artifact directory; registered comparisons,
  decision/control checks, behavior analysis, JSON/CSV/HTML exports and a fresh
  persistence reopen. Test mode is the default. Live CARLA, sensors and additional
  executable agent packages are explicit options. Existing output is preserved;
  failures retain evidence and never silently fall back to another controller.
- SDK 1.3/version 0.2.0 starter, with protocol-matched step responses and updated
  example instructions. Explicit driving tasks now work in the new-user path.
- A short labelled synthetic recipe and a live destination helper that uses the
  loaded map's road network. The normal lane/connectivity checks remain active.
- Real Chromium tests for registered dashboard execution, behavior review,
  enhanced HTML download, printing, all six mobile pages and keyboard skip
  navigation. Browser tests seed isolated synthetic data and never use CARLA or
  the user's database.
- CI configuration for Python 3.12/3.14 on Windows/Linux, core regressions,
  schema checks, isolated agent/sensor acceptance, wheel installation, frontend
  tests/build and a separate Chromium job. CI artifacts retain test evidence.
- [First run](first-run.md), [hardware profiles](hardware.md) and
  [troubleshooting](troubleshooting.md), linked from README/setup/roadmap.

## Installation and automated checks

The local core environment is Python 3.14.0; CARLA and the clean installation use
Python 3.12.10. Node 24.19.0 and the locked frontend dependencies were used.

The first broad regression run passed 2,582 cases with ten failures and ten
optional-dependency skips. Three failures were obsolete pre-Phase-9 expectations:
the optimization union still expected three variants, and two registered failure
tests expected reproduction/minimization to reject supported agents. They now
check the four request variants and preservation of the registered identity.
The remaining failures were short startup/poll deadlines under overlapping test
and model workloads. All affected cases passed on an isolated recheck without
changing production worker/inference deadlines. The final uncontended full run
passed **2,594 tests with 10 optional-dependency skips in 515.67 seconds**.
Those skips require the optional Torch/SciPy/OpenCV or opt-in weight-test
environment; the prepared reference environment separately completed the live
learned-policy integration below.

Other completed gates:

- 301 frontend component/contract tests passed; production build and generated
  schema/type freshness checks passed.
- The installation/example/package focused run passed 68 tests; subsequent
  installation-tool checks cover explicit failures, malformed plans, absent
  servers, native CARLA log ordering, SDK compatibility and private storage.
- Both Chromium end-to-end tests passed. Screenshots were inspected at desktop
  and 390 px phone width. The downloaded enhanced HTML printed to three A4 PDF
  pages; every page was rendered with Poppler and visually checked. Tables retain
  headers across page boundaries and no clipping was found. This closes the
  browser/print work deferred in Phases 8.2.2 and 9.
- A wheel was built, installed non-editably with only `requirements-core.lock`
  dependencies into a new Python 3.12 environment, and checked with `pip check`.
  From outside the checkout, isolated Python (`-I`) successfully ran the doctor
  and the complete starter/sensor/report/reopen workflow with an explicit
  source `--examples-dir`. No editable-source fallback or dev HTTP client was
  needed. The wheel intentionally does not bundle examples, dashboard or weights.

Local evidence is retained under ignored `.pytest_cache/phase10-*`,
`.pytest_cache/browser/` and `frontend/test-results/`. A nonzero CLI result keeps
its failure receipt; the initial failed attempts are retained alongside retries.

## Fresh native CARLA checks

One CARLA 0.9.16 server was started for this validation, using Low quality,
640×360 and no sound on Town10HD_Opt. State-only runs scoped no-rendering to
execution. The original asynchronous/no-rendering-false settings were restored
afterward. No user's saved database was used. The test instance was closed when
the live checks finished.

The machine reports Windows 11, 12 logical CPU cores, approximately 64 GiB RAM
and an RTX 2060 with 6 GiB VRAM. GPU snapshots are device-wide point observations,
not model peaks or a minimum-hardware specification.

1. A one-second, seed-7 baseline/starter comparison completed, with 20 applied
   decisions per child, behavior analysis, all three exports and persistence
   reopen. Its total operation wall time was approximately 5.03 seconds.
2. A fresh comparison added the maintained pretrained PlanT 2.0 policy through
   its separate Python 3.12 worker. Baseline, starter and PlanT all completed
   the same one-second seed-7 recipe, each with 20 applied decisions. The PlanT
   registration declared the real checkpoint as an external model file.
3. Every reference decision retained its original proposed/applied control,
   without scenario interventions. All 20 diagnostics recorded the pinned
   checkpoint hash and positive inference duration, and matched the declared
   model fingerprint. Input provenance remained `carla_ground_truth`; this was
   a structured-state learned controller, not camera perception.
4. JSON/CSV/HTML behavior exports and comparison evidence were saved, reopened
   through another API runtime, and matched their persisted identities. The
   final read-only CARLA probe confirmed the same map, versions and tick/render
   settings as before execution.

Checkpoint SHA-256:
`2f896d8d7640d4b7b68bcef8615a73e9761e850870a24f914c4c908f0f2199f4`.

The reference check explicitly used **CPU**, two Torch threads, CUDA graphs off
and a **120-second cold-load allowance** for that additional registration.
The ordinary 30-second load attempt failed earlier and remains recorded. Its
step/reset deadlines were not enlarged. The successful comparison's overall
operation wall time was approximately 131.47 seconds, including setup, serial
children and persistence work. The reference worker-step p95 was approximately
0.912 seconds over 20 samples. That is far above the 50 ms nominal timestep;
this CPU integration check does **not** pass or claim real-time throughput.
It is separate from the earlier GPU performance acceptance profile.

Live evidence directories:

- `storage/reports/phase10-live-starter-v2-20261020/`
- `storage/reports/phase10-live-reference-v2-20261020/`

An initial hand-sampled junction route was rejected as a disconnected shortcut.
The helper now requests a destination and delegates route construction to the
existing map-network resolver. Road validation was not relaxed to pass the test.

## Scope and remaining boundaries

The ten-phase implementation is delivered for the documented trusted local
workflow. These checks do not certify driving safety, generalization, independent
global planning, learned sensor perception or every user-supplied model. A short
run validates the connection and persistence path, not a benchmark distribution.

Native Windows core/CARLA and a clean installed wheel were actually exercised.
The new Linux/Windows hosted CI matrix is configured but was not dispatched from
this local task. Linux/macOS installs, GPU containers, other hardware/driver
combinations and larger sensor rigs still require their own measured validation.
No public model upload, untrusted-code isolation, multi-user hosting or production
deployment is introduced by this phase. Registration remains explicit execution
of trusted local Python code.
