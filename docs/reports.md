# Exporting run reports

Export a finalized run from its result page under **Export this run**, or use the
command below. Completed, failed, and cancelled runs can all have reports once
their terminal result has been persisted. Exporting reads stored evidence; it
does not launch a run or perform a reproduction.

The dashboard enables **Include behavior analysis and recorded agent diagnostics**
by default. These enhanced version 2 reports include the same default analysis
and effective thresholds shown in the behavior review panel. Download state,
errors, cancellation and retry appear in the panel; browser requests time out
after 60 seconds. Larger recordings can use the CLI with a longer timeout.

## Command line

From the repository root, with the API running:

```powershell
python -m scripts.export_report --simulation-id <RUN_UUID> --api-url http://127.0.0.1:8000 --format json --output report.json
python -m scripts.export_report --simulation-id <RUN_UUID> --format csv --output report.csv
python -m scripts.export_report --simulation-id <RUN_UUID> --format html --output report.html
python -m scripts.export_report --simulation-id <RUN_UUID> --format html --include-behavior --output behavior-report.html
```

Replace `<RUN_UUID>` with the run ID shown in the dashboard. An installed package
also provides `poltergeist-report` with the same arguments. The CLI uses Python's
standard library and requires no development dependencies. The API URL defaults
to `http://127.0.0.1:8000`; HTTP(S) base URLs with a path prefix are supported.
URLs with embedded credentials, a query, or a fragment are rejected. Redirects
are rejected so an incorrect API URL cannot silently download another page.

The output's parent directory must already exist. An existing output file is
preserved unless you explicitly add `--force`. The download streams to a
temporary file in the output directory and publishes the completed file
atomically. Without `--force`, publication also rejects a destination created
by another process during the transfer. With `--force`, the previous file stays
in place until the complete replacement is ready. An incomplete transfer is
never published as the destination report.

Add `--include-telemetry` to a **JSON** export to include the full finalized trace:

```powershell
python -m scripts.export_report --simulation-id <RUN_UUID> --format json --include-telemetry --output report-with-telemetry.json
```

Telemetry can make reports substantially larger and slower to generate. It is
omitted by default. `--include-telemetry` is rejected for CSV and HTML. The flag
includes recorded telemetry frames, not raw camera or LiDAR bytes. Raw captures
remain separate owned artifacts available from the run detail page.

Add `--include-behavior` for enhanced JSON, CSV or HTML. It can be combined with
`--include-telemetry` for JSON. The command-line and GET API options are opt-in so
existing metadata-only exports retain their version 1 shape and can be used when
trace artifacts are missing. The dashboard checkbox controls the same flag.

Use `--timeout 60` to increase the default 30-second HTTP socket timeout. Success
returns exit code `0` and prints the final file path. A download or file error
returns `1`; invalid command arguments return `2`; interruption returns `130`.
Expected failures print an actionable message without a Python traceback.

## Formats and evidence

Each format represents one run and preserves its immutable scenario recipe,
seed, mode, agent, lifecycle timestamps, saved scenario ID, record revision,
recorded execution options and evaluation settings, safety metrics, evaluation
coverage, runtime provenance, and associated failure reports. The export time is
separate from the run's timestamps. Missing legacy job, runtime, or settings
metadata remains `null`; it is not filled with current defaults. Historical
queue position is unavailable and remains `null`.

- **JSON** is the complete machine-readable snapshot. Its `schema` is
  `poltergeist-run-report-v1` for metadata-only exports. Top-level keys are `schema`, `generated_at`,
  `record`, `failures`, `interpretation`, `telemetry_included`, and `telemetry`.
  `telemetry` is `null` when omitted and an array, possibly empty, when included.
  Enhanced exports use `poltergeist-run-report-v2` and add `behavior_analysis`,
  `agent_reported_diagnostics` and `qualified_interpretations`.
- **CSV** is a flattened, lossless snapshot with columns `section`,
  `json_pointer`, `value_json`, and `unit`. Each row identifies a value with a
  JSON Pointer, for example `/record/result/metrics/min_ttc_s`. Decode
  `value_json` with a JSON parser to recover its exact type and value. Strings
  retain their JSON quotes; `null`, `false`, `0`, and `[]` remain distinct. User
  text is JSON encoded so it cannot become a spreadsheet formula. UTF-8 with a
  byte-order mark supports common spreadsheet imports. Explicit metric units
  include meters, seconds, meters per second squared, and episode counts;
  settings also retain their unit-bearing field names.
- **HTML** is a self-contained printable report with metric tables,
  interpretation notes, and an expandable complete escaped JSON snapshot. Version
  2 adds readable behavior, diagnostics and qualified-description sections. It contains no
  scripts or remote assets. Open the downloaded file in a browser to inspect
  it or use the browser's print dialog. It is not a generated PDF.

Enhanced exports preserve response statuses, actual zero versus unknown values,
thresholds/units, original safety metrics, task outcomes, route measurements,
evidence spans, input provenance and immutable agent snapshots. Braking/route
measurements are copied from the same analysis engine as the dashboard. Reports
with explicit settings retain those settings; they do not alter historical safety
scores. Response/event/stop caps and coverage remain part of the analysis.

Recorded diagnostics are agent-supplied statements, not verified internal reasoning.
Their export retains an earliest prefix of diagnostic-bearing inputs, bounded by
**200 records** or **1 MiB of compact UTF-8 JSON record objects**, whichever is
reached first. Counts of total decisions, unresolved inputs, diagnostic-bearing
decisions, retained records, retained bytes and truncation are explicit. A first
oversized record can leave an empty retained prefix with a nonzero omitted count.
Pretty-printing and the report envelope add bytes beyond that accounting. Each
retained record carries its native input frame/time, control authority,
interventions and exact diagnostics. Observations and raw sensor bundles are not
copied by the diagnostics collector. The original timeline retains later records.

Qualified interpretations describe measured threshold onsets and windows; they
do not infer intent, perception time or causality. Script assistance stays
explicit. Missing legacy diagnostics or incomplete runs do not gain invented
agent statements. HTML separates these categories and preserves the complete
enhanced snapshot in an expandable appendix. Expand the appendix before printing
if you also want its full JSON on paper; the readable findings print directly.

Test-mode reports are labeled **Synthetic test execution**. They are not CARLA
or real-driving evidence. CARLA mode is identified independently. A report
preserves the recorded runtime; the export does not verify that a new CARLA
server is available.

Null metrics never become zero. Null minima can mean unavailable observations
or no relevant interaction, so inspect the accompanying coverage. Coverage is
labeled `complete`, `partial`, or `unobserved`. A collision can produce severity
`100` even with partial coverage; otherwise missing observations can leave the
result unscored. Severity is a search heuristic, not crash probability or a
safety certification. A completed run does not imply complete measurement.

Failure reports retain their observed kinds, summaries, severity, and nullable
manual reproduction flags. `true` means reproduction was marked observed,
`false` means it was marked not reproduced, and `null` means unknown. These
flags reflect the stored manual observations at export time. They do not
indicate that exporting reran the scenario. The report contains no encoded video
or simulator replay.

## API

```http
GET /reports/simulations/{simulation_id}?format=json
GET /reports/simulations/{simulation_id}?format=csv
GET /reports/simulations/{simulation_id}?format=html
GET /reports/simulations/{simulation_id}?format=json&include_telemetry=true
GET /reports/simulations/{simulation_id}?format=html&include_behavior=true
POST /reports/simulations/{simulation_id}?format=json
```

`format` defaults to `json`; `include_telemetry` defaults to `false`. Successful
responses are downloadable attachments named
`poltergeist-{simulation_id}.{format}`, with the corresponding JSON, CSV, or
HTML content type. They are sent with `Cache-Control: no-store`. HTML also uses
a restrictive content security policy. Response filenames come from the run
UUID, never scenario names or user-supplied file paths.

`include_behavior` defaults to `false` on GET. POST always includes behavior and
requires a settings JSON body; `{}` uses the same defaults as GET analysis. Pass
the same settings object to POST analysis and POST report to obtain identical
`behavior_analysis` values. For example:

```powershell
$runId = "YOUR_FINISHED_RUN_UUID"
$settings = @{ response_window_s = 2.0; brake_threshold = 0.2 } | ConvertTo-Json
Invoke-WebRequest "http://127.0.0.1:8000/reports/simulations/${runId}?format=html" `
  -Method Post -ContentType "application/json" -Body $settings -OutFile behavior-report.html
```

The `X-Poltergeist-Report-Schema` response header identifies version 1 or 2. The
backend exposes it through CORS; reverse proxies must forward it. The dashboard
checks schema, content type and the completed transfer before saving a file, and
ignores obsolete requests after a run changes. Compressed responses are checked
using the browser's decoded body rather than comparing compressed byte lengths.

The endpoint returns `404` for a missing run, `409` for a run without finalized
output, and `422` for invalid IDs or report options. Persistence failures return
`503`. Including telemetry reads and validates the finalized artifact, so an
unavailable or invalid required artifact also fails the export. A report without
behavior or telemetry can still export the persisted result metadata and artifact
references; it does not certify that every referenced artifact is accessible.

Enhanced exports validate telemetry, the complete decision checksum and raw
sensor references before returning new measurements or diagnostics. Corrupt
evidence is not silently downgraded into an enhanced report. They never import
current agent code, check current model weights or start a simulator/worker.
Behavior plus optional JSON telemetry share one validated artifact read. Large
recordings can incur additional disk I/O and memory proportional to saved
telemetry/compact controls. The diagnostics byte cap is not a cap on the entire
report or analysis workload.
