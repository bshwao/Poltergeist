# Phase 7 validation

Phase 7 adds persisted input/action/diagnostic/intervention evidence and
frame-synchronized dashboard review. Validation used synthetic simulator adapters,
real external Python teaching-agent workers, SQLite and the local artifact store.
CARLA and trained neural inference were not started for these checks.

## Automated checks

- **262 affected Python tests passed** in the final rerun across decision
  evidence, queue service, registered execution, simulation API, persistence and
  artifact storage. The new evidence suite contributes 14 end-to-end checks.
- **264 frontend tests passed**, including eight new API/component checks for
  exact frame associations, malformed evidence, pagination, stale requests,
  unavailable/partial history, retries and sensor integrity.
- TypeScript checks, Vite production build, frontend generated-contract drift
  check and whitespace validation passed.

The Python checks verify exact worker inputs/reset context and diagnostics across
restart; controls and measured transitions; baseline script interventions;
camera/LiDAR byte references; retained failed inputs; cancellation; a missing
post-transition packet; legacy compatibility; query bounds; corruption detection;
and rollback of rejected evidence publication. No test claims to validate driving
quality, learned-model accuracy or CARLA rendering performance.

Tests were run with temporary data under `.pytest_cache`. API/process checks used
the normal host environment because Windows sandbox temp/cache cleanup and local
async sockets do not support the process-worker suite reliably. A first affected
run overlapped source edits and correctly rejected a changed runtime fingerprint;
the stable-source rerun passed. These environment-related failures did not
require weakening any runtime identity or worker cleanup checks.

The broad run finished with **2,449 passed, 10 optional skips and two failures**.
It found two recorder-service regressions after the initial 231-test check:
an old artifact-list assertion and a raw-write error that prevented finalization.
The latter was fixed to preserve failed telemetry, discard sensor references to
rolled-back raw bytes, and keep the queue usable. A new regression test covers
that error after successful prior sensor inputs and verifies subsequent recovery.
The final 262-test rerun includes both formerly failing tests and the additional
regression, all passing. The unchanged remainder was covered by the broad run;
the entire broad suite was not repeated after this targeted correction. Optional
skips require Torch, SciPy, OpenCV or explicitly enabled reference weights tests.

## Browser verification

A temporary backend on `127.0.0.1:8017` and Vite on `127.0.0.1:5177` used a separate
database and artifact root under `.pytest_cache/phase7-browser-data`. The bundled
camera/LiDAR teaching controller executed a 0.6 s synthetic task:

- Run `0125ced2-367f-4bbe-9634-fde7471f9966` completed with 12 actions and 13
  measured frames; evidence coverage displayed `complete`.
- Selecting input frame 3 showed input time 0.150 s and result frame 4.
- The selected 320 × 180 BGRA camera input loaded on demand; its SHA-256 and
  230,400-byte length verified before pixels appeared. Its fixture source remained
  explicit. The matching LiDAR reference had the same input clock.
- Next-page navigation displayed frames 10 and 11, disabled further navigation,
  and associated input 11 at 0.550 s with result 12 and its following measured
  speed. Proposed/applied control, diagnostics and unavailable actor inputs stayed
  separate in the review.

The desktop layout and camera preview were visually inspected. Screenshot:
`.pytest_cache/phase7-decision-review.png`. The owned browser tab and both servers
were closed afterward; ports 8017 and 5177 were confirmed closed. Existing user
servers and CARLA were not stopped or reconfigured.

## Instrumentation timing and limits

A single 200-step baseline synthetic episode on this machine measured median
step time of 0.510 ms without timeline callbacks and 4.760 ms with callbacks;
95th percentile was 0.929 ms and 6.807 ms respectively. This includes synchronous
decision serialization/spooling under the workspace's OneDrive directory. It
does not include a neural model, CARLA/GPU, sensor payload capture or finalized
publication checks, and is not a hardware benchmark or real-time guarantee.
The ignored measurement file is `.pytest_cache/phase7-timing/timing.json`.

The writer retains one pending input and streams completed records; sensor bytes
are not duplicated in its JSON. Publication/readback validates evidence, which
adds I/O. Page validation still scans the trace and raw recording downloads can
validate multiple samples. Evidence is published only at finalization; recovery
of a process killed before that point is not implemented. Behavior analysis,
reasoning claims, agent comparisons and new report analysis remain later phases.
