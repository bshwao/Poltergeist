# Exporting run reports

Export a finalized run from its result page under **Export this run**, or use the
command below. Completed, failed, and cancelled runs can all have reports once
their terminal result has been persisted. Exporting reads stored evidence; it
does not launch a run or perform a reproduction.

## Command line

From the repository root, with the API running:

```powershell
python -m scripts.export_report --simulation-id <RUN_UUID> --api-url http://127.0.0.1:8000 --format json --output report.json
python -m scripts.export_report --simulation-id <RUN_UUID> --format csv --output report.csv
python -m scripts.export_report --simulation-id <RUN_UUID> --format html --output report.html
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
  `poltergeist-run-report-v1`. Top-level keys are `schema`, `generated_at`,
  `record`, `failures`, `interpretation`, `telemetry_included`, and `telemetry`.
  `telemetry` is `null` when omitted and an array, possibly empty, when included.
- **CSV** is a flattened, lossless snapshot with columns `section`,
  `json_pointer`, `value_json`, and `unit`. Each row identifies a value with a
  JSON Pointer, for example `/record/result/metrics/min_ttc_s`. Decode
  `value_json` with a JSON parser to recover its exact type and value. Strings
  retain their JSON quotes; `null`, `false`, `0`, and `[]` remain distinct. User
  text is JSON encoded so it cannot become a spreadsheet formula. UTF-8 with a
  byte-order mark supports common spreadsheet imports. Explicit metric units
  include meters, seconds, meters per second squared, and episode counts;
  settings also retain their unit-bearing field names.
- **HTML** is a self-contained printable report with a metric table,
  interpretation notes, and the complete escaped JSON snapshot. It contains no
  scripts or remote assets. Open the downloaded file in a browser to inspect
  it or use the browser's print dialog. It is not a generated PDF.

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
```

`format` defaults to `json`; `include_telemetry` defaults to `false`. Successful
responses are downloadable attachments named
`poltergeist-{simulation_id}.{format}`, with the corresponding JSON, CSV, or
HTML content type. They are sent with `Cache-Control: no-store`. HTML also uses
a restrictive content security policy. Response filenames come from the run
UUID, never scenario names or user-supplied file paths.

The endpoint returns `404` for a missing run, `409` for a run without finalized
output, and `422` for invalid IDs or report options. Persistence failures return
`503`. Including telemetry reads and validates the finalized artifact, so an
unavailable or invalid required artifact also fails the export. A report without
telemetry can still export the persisted result metadata and artifact
references; it does not certify that every referenced artifact is accessible.
