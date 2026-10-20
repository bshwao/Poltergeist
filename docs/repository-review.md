# Repository review — October 20, 2026

This review covered backend execution and persistence, agent registration and
worker boundaries, scenario/evaluation code, experiments, reports, dashboard
request lifecycles, installation tools and packaging/container configuration.
Existing regression tests supplement the targeted code review; this is not a
claim that every possible runtime or hardware fault has been excluded.

## Corrected issues

| Issue | Result after the fix |
| --- | --- |
| The report CLI could silently accept a legacy response to `--include-behavior`. | JSON, CSV and HTML behavior exports require the v2 schema acknowledgement before publishing a file. A rejected download preserves an existing output, including with `--force`. |
| Installation diagnostics accepted database drivers and URL options the backend cannot use. | The doctor uses the backend's inert database configuration validation. Unsupported databases fail the configuration check without opening storage or contacting CARLA. |
| A delayed experiment mutation could navigate back to a previously selected experiment. | Successful mutations navigate only while the user remains on the original route; accepted work still refreshes history. |
| History pagination retained the previous page's records during loading and offered an empty next page after exactly ten records. | Loading clears stale records, disables paging, and fetches one extra record to establish whether another page exists. |
| A registry request failure hid otherwise available experiment history. | Registry and history requests finish independently and display separate errors. |
| The launch panel classified every HTTP 503 as a definite rejection. | A 503 after POST prompts the user to inspect run history. A backend regression demonstrates that receipt construction can fail after a launch is already durable. |
| The Python API client marked lost mutation responses as uncertain only for simulation POSTs. | The same conservative handling applies to experiment/agent POSTs and PUT/PATCH/DELETE operations, with no automatic retries. |

## Verification

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
