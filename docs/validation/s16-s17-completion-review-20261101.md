# S16/S17 completion and usability review — November 1, 2026

Reviewed source on `main` at `86d89f0`, the roadmap's acceptance criteria, the
[recorded packaged validation](s16-s17-sensor-hub-20261101.json), managed model
acquisition, native controls and sensor-policy guards.

**Both integrations are implemented and locally validated. Neither should be
marked unconditionally finished or release accepted.** S16 delivers the AIM
front-camera CPU policy; there is no trained LiDAR policy. S17 delivers one
curated public Hugging Face model, rather than an arbitrary Hub browser.

| Milestone | Delivered | Still open |
| --- | --- | --- |
| S11 | Shared dashboard design, responsive workflows and native companion improvements | Complete workflow visual/accessibility matrix and independent first-run acceptance |
| S12 | Guidance, galleries, saved suites, comparisons and exact-frame highlights | Independent tester feedback and verified feedback-driven improvements |
| S13 | Approved, frozen, bounded discovery plans with durable trial accounting | Representative live failure-discovery acceptance beyond the documented startup-fault checks |
| S14 | Saved-evidence analysis with exact links and explicit coverage | Independent factual review and representative live-evidence acceptance |
| S15 | Frozen-model matched-scenario reviews with coverage-specific regressions | Broader representative live and hardware comparisons |
| S16 | Pinned AIM camera policy, restricted checkpoint loading, declared inputs and actual CARLA control evidence | Broader hardware/driving coverage, installed-app visual acceptance and upstream checkpoint-terms review; trained LiDAR and managed CUDA are absent |
| S17 | Pinned PlanT Hub revision, bounded verified resume, repair/removal and separate protected account configuration | Actual gated-account and independent-machine acceptance |
| S18 | Planned | Optional bounded OpenAI planning/analysis integration |
| S19 | Planned | Exact-commit regression publishing and CI checks with explicit publishing authorization |

The existing S16 record establishes 80 real camera/control decisions, 1.3087 m
of measured ego motion and policy p95 of 92.35 ms on the recorded CPU. Its S17
record establishes public-source pause/reopen/resume/full verification/removal.
Those are earlier same-laptop measurements, not new driving or public-Hub
downloads performed in this review. They do not establish broad driving quality,
20 Hz wall-time operation, checkpoint redistribution rights or a new machine's
compatibility. The private raw artifacts referenced by that record are not
present in this checkout; their hashes were not independently rechecked here.

## Changes from this review

- The AI models panel uses the same keyboard-aware scroll surface as Local
  execution. Tab reveals focused controls; the canvas accepts Page Up/Down,
  Home/End and arrow keys, and selectors keep their native wheel handling.
  The new native regression exposed ancestor focus notifications scrolling a
  container over its focused child. The shared handler now scrolls only the
  widget that actually holds focus.
- Weight, provider and environment actions occupy two-column groups, keeping
  controls inside the supported 700 × 600 and 960 × 800 windows.
- Next-step guidance explains installation, readiness and enabling. Controls
  that require a stopped runtime are disabled while it runs; source checks,
  ordinary weight downloads and weight inspection retain their existing policy.
- Removing an environment reports that saved weights are retained. It clears
  inference readiness without certifying those retained bytes, and resets
  progress to the selected checkpoint's byte scale. Subsequent weight inspection
  still rejects a corrupt checkpoint.

## Verification

Focused source and native checks are recorded separately under the ignored
`validation-evidence/` directory. Initial sandbox attempts could not create
pytest's default temporary directories or initialize Git OpenSSL's MSYS named
objects. The TLS and GUI checks therefore run outside that sandbox using
disposable local profiles. No production deadlines or assertions were relaxed.

The first new state test expected a hash error for a deliberately undersized
checkpoint. The existing size guard correctly rejected it earlier; the assertion
now checks that precise rejection. This was a test expectation correction.

The focused source run passed **261 tests**; the two added environment-removal
cases then passed with that corrected assertion. Together these cover all
**263 selected source cases**, with warnings treated as errors. Receipts are
`validation-evidence/s16-s17-review-final-20261101.xml` (the initial run, including
the two assertion failures) and `s16-s17-polish-state-20261101.xml` (their passing
recheck). The subset includes camera calibration/pixel guards, sensor timing,
Hub metadata/credential boundaries, real localhost TLS transfer faults,
resume/repair/removal, environment integrity, selection persistence and CLI
sensor contracts. No pretrained inference or CARLA run was repeated.

Native checks passed **seven distinct cases**: GUI-variable cleanup, close/profile
ownership, weight preparation/removal, model selection/source checks/runtime
locks, model-panel layout and focus at both window sizes for both models,
protected provider save/forget, and existing Local execution keyboard scrolling.
Four passed in `s16-s17-polish-gui-20261101.xml`; that receipt also retains the
ancestor-focus failure discovered by the new layout test. After correcting the
shared handler, that layout test, provider callback and Local execution test
all passed in `s16-s17-polish-scroll-20261101.xml`. These use real Tk callbacks
and disposable synthetic model fixtures, not downloaded model execution.

The computer-use visual review was stopped by the user. Its captures did not
establish a usable panel view, so no new visual acceptance is claimed.
The temporary review processes were stopped; their disposable profile remains
under the ignored validation directory. No normal Companion profile was used.

The review does not close independent-user, installed-package, screen-reader,
high-DPI/contrast, LiDAR, checkpoint-terms or actual gated-account gates.
