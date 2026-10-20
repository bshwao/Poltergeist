# Phase 8.1 validation

Phase 8.1 implements backend behavior measurements and read-only analysis API
contracts. It does not add the Phase 8.2 dashboard panel or behavior report exports.
Checks used saved synthetic telemetry, temporary SQLite/artifact stores and real
external teaching-agent workers. CARLA and trained neural inference were not started.

## Automated checks

- **Full Python regression: 2,532 passed, 10 skipped in 396.86 s.** The optional
  skips require Torch, SciPy, OpenCV or explicitly enabled reference-weight tests.
  The full run includes all final implementation changes and the checks below.

- **61 behavior/route tests passed**, covering input versus resulting-state clocks,
  nonzero native frame IDs, true zero latency versus already-active braking,
  steering/heading wraparound, velocity-derived deceleration, script overrides and
  guidance, complete/truncated windows, gaps, unresolved SDK inputs, hazard onset
  coverage, scheduled-event alignment, stopping integration, original scoring,
  task completion evidence, unknowns, caps and deterministic JSON.
- **19 new API tests passed**, also exercised in a 120-test focused
  API/persistence/timeline/report run. These include repeated GET/default POST equality,
  custom/invalid settings, finalized/unknown IDs, file/row immutability, queue-free
  reads, legacy/failed/cancelled results and corrupted timeline/sensor rejection.
- Registered state and sensor controller runs retain their immutable identity and
  input source. Historical analysis still succeeds after the original agent source
  is replaced with code that would fail if imported. Failed inference produces
  partial/unresolved evidence without inventing a proposal.
- **264 existing frontend tests passed**. TypeScript/Vite production build and
  generated frontend-contract drift checks passed with the new analysis types.

The route checks cover finite endpoints, slopes clipped to the ego-height band,
overpasses, missing reference height, empty/single-point/vertical paths, rigid
coordinate transformations, reversed spatial-tree ordering and finite arithmetic.
No test interprets evaluator hazards as known sensor-agent perception.

Tests use temporary data under `.pytest_cache`. API/process suites run in the host
environment because Windows sandbox socket/temp-cache restrictions do not support
those worker tests reliably. Unit geometry/measurement checks do not need CARLA,
additional dependencies, a model checkpoint or an API server.

## Route calculation timing

A synthetic 200-frame analysis with a 4,000-point reference route measured 2.280 s
with a per-pose full route scan. Preparing a spatial reference once per analysis
reduced the same check to 0.089 s, with maximum deviation unchanged at 0 m. These
are single local checks, not a CARLA, sensor-I/O or neural-inference benchmark.

Telemetry and compact controls remain proportional to the recorded episode.
Response/stop output caps bound returned findings, while counts/truncation flags
preserve their scope. Analysis still validates persisted raw input checksums and
loads finalized telemetry, so large recordings incur additional memory and I/O.
No real-time performance or model-quality claim is made.
