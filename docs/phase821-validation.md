# Phase 8.2.1 validation

Phase 8.2.1 implements dashboard behavior review and native-frame evidence
navigation. Behavior report exports remain Phase 8.2.2. Validation used recorded
synthetic evidence; CARLA, trained inference and the user's API services were not
started or modified.

## Automated checks

- **283 frontend tests passed**, including 19 new behavior review checks. These
  exercise the analysis client against a backend-generated fixture, nested
  contract rejection, zero versus unknown intervals, already-active signals,
  incomplete windows, script influence, evaluator hazards, legacy/failed runs,
  output caps, event/stop pagination, request cancellation, stale ownership,
  timeouts, retries and integrated run-detail evidence links.
- Native frame tests use IDs starting at 100 rather than zero. Links locate a
  decision beyond the first page, inspect a terminal telemetry packet without
  inventing a subsequent action, reject missing frames and retain manual paging.
  Sparse-ID lookup uses binary search; contiguous-ID candidates are verified
  before use. Obsolete decision and telemetry lookups cannot replace a newer
  selection.
- **62 backend tests passed**: existing behavior analysis/route checks plus a new
  fixture consistency test. The fixture stores synthetic source telemetry,
  decision records and the finalized result; recomputing it with the real engine
  reproduces its complete analysis exactly.
- TypeScript/Vite production build, generated TypeScript-contract drift checks
  and shared JSON Schema drift checks passed. No dependencies or schema changes
  were required for 8.2.1.

The full Python regression was not repeated for these frontend changes. The
previous Phase 8.1 validation remains documented separately.

## Browser verification

The real run-detail and review components were mounted against the saved
synthetic fixture in an isolated local preview. Desktop and a 390 px wide review
container were inspected. Summary cards wrapped without overflowing; response
tables retained horizontal scrolling and evidence disclosures remained usable.

Opening a proposed-braking finding's frame 103 link moved focus to the existing
decision timeline and selected **input 103 → measured result 104**. A legacy
example explicitly displayed missing decision evidence, no recorded driving
outcome and no available response anchors. Model reasoning was not inferred.

The preview used fixture responses rather than the user's running backend. Its
temporary HTML/TSX entry points were removed after verification; they are not an
application route or a production dependency. Test data remains under
`frontend/tests/fixtures/behavior-review.json` for automated regression checks.

Large saved recordings still incur the existing API's evidence validation I/O.
This phase does not claim a CARLA or neural-inference performance improvement.
