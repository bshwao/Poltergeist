# Repository reviews

## October 30, 2026

This follow-up reviewed evidence storage and startup, dashboard request handling,
repository hygiene and Docker build contexts.

- Failed or interrupted evidence writes now release their unwritten quota
  reservations. Partial writes are completed before publication; a write that
  makes no progress rolls back without publishing incomplete evidence.
- Evidence initialization now serializes its scan, rejects unreadable or linked
  namespaces and records usage only after the whole scan succeeds. A failed scan
  leaves the store unavailable until a successful retry.
- Cancelling a hosted response stream retains its cancellation state instead of
  reporting invalid evidence.
- Eleven generated decision timelines (4,050,264 bytes) were removed from Git
  tracking while their local copies were preserved. The decision directory now
  follows the other ignored artifact directories, with a `.gitkeep` placeholder.
- Docker build contexts exclude the agent environment, hosted builds, browser
  results and local validation/runtime output.

Regression tests reproduce the write-accounting, startup-retry and hosted
cancellation failures. Additional fault-injection checks cover short writes and
unreadable directories. No database migration or dependency change is required.

### Verification

- Complete Python suite: **3,366 passed, 10 optional checks skipped** in
  1,371.70 seconds. Skips require Torch, SciPy, OpenCV or explicitly enabled
  reference-weight tests. The initial run's eight failures passed on targeted
  reruns and in the complete final run.
- Dashboard unit tests: **341 passed**.
- Chromium integration: **19 local and 2 hosted tests passed**, including
  desktop/mobile accessibility, agent experiments, reports, account isolation,
  pairing, cancellation and interrupted-response recovery.
- Both dashboard production builds, Python/TypeScript contract checks, dependency
  consistency and Git whitespace checks passed.

The JUnit run emitted five warnings about existing `record_property` diagnostics
and pytest's default `xunit2` format. The test result XML is retained locally at
`validation-evidence/repository-review-tests.xml`.

Live CARLA, learned-model inference and Docker image execution were not part of
this follow-up. Browser fixtures used isolated synthetic storage.

## October 20, 2026

This review covered backend execution and persistence, agent registration and
worker boundaries, scenario/evaluation code, experiments, reports, dashboard
request lifecycles, installation tools and packaging/container configuration.
Existing regression tests supplement the targeted code review; this is not a
claim that every possible runtime or hardware fault has been excluded.

### Corrected issues

| Issue | Result after the fix |
| --- | --- |
| The report CLI could silently accept a legacy response to `--include-behavior`. | JSON, CSV and HTML behavior exports require the v2 schema acknowledgement before publishing a file. A rejected download preserves an existing output, including with `--force`. |
| Installation diagnostics accepted database drivers and URL options the backend cannot use. | The doctor uses the backend's inert database configuration validation. Unsupported databases fail the configuration check without opening storage or contacting CARLA. |
| A delayed experiment mutation could navigate back to a previously selected experiment. | Successful mutations navigate only while the user remains on the original route; accepted work still refreshes history. |
| History pagination retained the previous page's records during loading and offered an empty next page after exactly ten records. | Loading clears stale records, disables paging, and fetches one extra record to establish whether another page exists. |
| A registry request failure hid otherwise available experiment history. | Registry and history requests finish independently and display separate errors. |
| The launch panel classified every HTTP 503 as a definite rejection. | A 503 after POST prompts the user to inspect run history. A backend regression demonstrates that receipt construction can fail after a launch is already durable. |
| The Python API client marked lost mutation responses as uncertain only for simulation POSTs. | The same conservative handling applies to experiment/agent POSTs and PUT/PATCH/DELETE operations, with no automatic retries. |

### Verification

- The initial complete Python suite passed 2,594 tests with 10 optional checks
  skipped. Focused regression tests reproduced the export, configuration,
  navigation, registry and mutation issues before their fixes.
- The final dashboard suite passed 305 tests. Production build, TypeScript
  checking and generated API contract checking passed.
- Both Chromium integration tests passed: registered comparison execution,
  behavior/evidence review, downloadable/printable reports, mobile layout and
  keyboard navigation. These used private synthetic storage; temporary servers
  stopped afterward.
- `pip check` reported no broken requirements in the core environment.
- The complete Python suite against the final changes passed **2,611 tests with
  10 optional checks skipped in 460.69 seconds**. The skips require
  Torch/SciPy/OpenCV or explicitly enabled reference-weight tests. This run
  includes the new HTTP 503 receipt regression and isolated installation flow.

No database migration or new dependency is required. Live CARLA and learned
model inference were not started for this review. Their hardware and performance
results remain those recorded in [Phase 10 validation](phase10-validation.md).
Docker images and remote CI were inspected but not executed during this review.
