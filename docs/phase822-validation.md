# Phase 8.2.2 validation

Follow-up on October 20, 2026: Phase 10's real Chromium workflow verified the
desktop/mobile dashboard, downloaded the enhanced HTML report, printed it to
three A4 PDF pages, and rendered/inspected every page. This closes the browser
and print recheck deferred below. See [Phase 10 validation](phase10-validation.md).

Phase 8.2.2 implements enhanced behavior reports and review/download polish.
Checks used saved synthetic evidence, temporary databases/artifacts and external
teaching-agent workers. CARLA, trained inference and the user's normal API server
were not started or modified. No new dependencies or database migration are
required.

## Automated checks

- **292 frontend tests passed.** New checks cover enhanced report flags, owned
  filenames, all three MIME types, schema-header compatibility, empty/incomplete
  transfers, proxy compression, sanitized errors, cancellation, timeout/retry,
  stale ownership and inactive workspaces. A review integration check verifies
  focus return to the original finding, fallback after its removal and
  reduced-motion scrolling.
- **143 focused backend/CLI/analysis tests passed**, with the two external
  registered-agent cases selected out because they had already run separately.
  **Both registered state/sensor export checks passed separately.** This covers
  the existing report and CLI suites, new behavior-report tests and existing
  behavior/route/fixture checks. The full Python suite was not repeated.
- Report tests compare default and explicit-settings analysis with JSON, CSV and
  the HTML snapshot. They preserve saved scores, identity, provenance, native
  frame/time spans, zero/unknown distinctions, legacy/failed/cancelled evidence,
  interventions and unavailable diagnostics. Exporting leaves database rows and
  evidence files unchanged and does not initialize execution services.
- Diagnostic prefix/byte caps and truncation counts are exercised. Agent text is
  escaped in HTML and JSON-encoded in CSV; user-supplied metadata cannot become a
  CSV unit/formula. Original observations and sensor bundles are not copied by
  the diagnostics collector. Corrupt or missing required evidence fails an
  enhanced export before new findings/diagnostics are returned.
- Registered-agent history still exports after current source is replaced with
  code that would fail on import. Sensor provenance remains explicit and sensor
  payloads remain separately referenced. The new tests allow 60 seconds for
  completion because the supported worker load deadline itself is 30 seconds;
  application inference/load deadlines were not changed.
- TypeScript/Vite production build, generated TypeScript-contract checks, shared
  JSON Schema checks and whitespace validation passed.

## Direct HTTP and CLI verification

An isolated API on localhost used a disposable synthetic braking run with
recorded decisions and a driving task. Real CLI transfers downloaded enhanced
JSON, CSV and HTML files. Their analysis agreed with GET analysis; configured
POST reports agreed with POST analysis and did not alter the default analysis.
Cross-origin response checks confirmed the version 2 report header, matching
allowed origin and exposed schema header needed by browser downloads.

Generated samples are local test artifacts under
`.pytest_cache/phase822-preview/downloaded.{json,csv,html}`. The preview services
were stopped and temporary frontend entry points removed after these checks.

## Visual verification limitation

The browser automation tool could not initialize: it reported that kernel assets
could not be written because a required path was missing. Resetting its session
did not resolve the tool error. Therefore the updated report/download workflow,
responsive HTML and print pagination were **not visually rechecked** in a browser
in this phase. The direct HTTP/CLI/content checks and component tests passed;
they do not replace visual layout verification. Phase 8.2.1's earlier visual
review remains documented separately.

No live simulator/model performance claim is made. Large saved recordings still
incur validation I/O and memory proportional to their evidence; diagnostics caps
do not bound the whole export. Broader hardware/integration checks remain Phase
10, and a browser layout/print recheck remains appropriate when the tool is
available.
