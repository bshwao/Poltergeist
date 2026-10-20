# Recorded decision evidence (Phase 7)

New API executions save a decision timeline alongside their measured telemetry.
In **Simulations**, open a finished run and use **Inputs, decisions & outcomes**.
Select an input frame to review its observation, agent proposal, applied control,
script interventions and following measured frame together. Pages contain ten
inputs, with controls to move through the episode.

Agent-reported maneuver, reason codes, hazard IDs, target speed, trajectory and
metadata appear separately from measurements. Diagnostics are optional statements
from agent code; they do not establish internal reasoning or causality.
[Phase 8.1](behavior-analysis.md) adds a read-only behavior-analysis API. Its
dashboard presentation and behavior report exports remain Phase 8.2.

## What is retained

- The successful SDK reset message, including the resolved route, goal and sensor
  calibration actually delivered to the worker.
- The frame-associated observation before each agent call, including structured
  scene inputs when supplied. Unavailable and withheld fields remain explicit.
- Successful SDK response and optional diagnostics, proposed/applied controls,
  scripted intervention kinds, inference/round-trip/step wall time, and the next
  measured ego state. Baseline runs have no fabricated SDK response or reset;
  their legacy driving observation is represented using the SDK field names.
- Sensor wire headers, byte lengths, SHA-256 and indexes into the run's raw
  recording. Raw camera/LiDAR bytes are stored once, outside the decision JSON.

The dashboard can fetch a selected sensor input on demand and check its byte
length and SHA-256 before displaying camera pixels or LiDAR statistics. Synthetic
fixture inputs remain labelled. Measured evaluator telemetry is displayed
separately and is never substituted for withheld sensor-agent inputs.

An applied decision at input frame **f** is associated with resulting frame
**f + 1**. Times are episode-relative seconds; sensor recording manifests retain
native timestamps too. Worker inference measures agent execution; round-trip
includes process communication. Recorded step time is the runner's interval up
to its decision callback and excludes writing that callback's evidence. Actual
control-loop timing includes evidence I/O. These values are observations rather
than a real-time performance guarantee.

## Gaps, failures and compatibility

Coverage describes the decision chain, independently of safety and task success:

| Coverage | Meaning |
| --- | --- |
| `complete` | Every measured transition has an associated applied decision |
| `partial` | An input is unresolved or a measured transition lacks a complete decision record |
| `empty` | The new execution ended before any decision input or transition |
| `unavailable` | The result has no timeline artifact, including older results and legacy persistence callers |

Input capture happens before inference. A timeout, invalid agent response,
cancellation or simulator/capture failure may leave a final `unresolved` input.
It makes no claim about an action, diagnostics or resulting state. If an error
occurs after the world advances but before its decision callback, synchronized
telemetry can contain a next frame while the decision remains unresolved.
Valid partial sensor recordings are retained for decision-enabled failed runs.
If raw-capture storage itself fails, its transaction rolls back. The failed run's
telemetry is still finalized; a sensor-agent decision timeline referencing those
lost bytes is discarded and explicitly unavailable. State-agent decision evidence
can remain available independently of an optional recorder failure.

The queue publishes finalized artifacts and then commits their references with
the result. Failed publication removes only that attempt's unreferenced files;
ambiguous database commits preserve files for reconciliation. Mid-run evidence
is staged, not exposed through the API. A process crash or machine failure before
finalization is not a resumable or continuously durable recording session.
Restart recovery retains its existing failed-job behavior.

Historical results are not backfilled: telemetry cannot recover an agent's
original inputs, diagnostics or internal state. Existing CLI `--decision-trace`
and reference-pilot evidence commands continue to work independently; automatic
artifact-backed review applies to API queue executions.

## API and artifact format

`GET /simulations/{id}/decisions?offset=0&limit=25` returns `DecisionPage`.
`limit` is 1–100; offset counts decision inputs, not native frame IDs. Unfinished
runs return 409. A finalized older result returns HTTP 200 with
`available: false` and `coverage: "unavailable"`.

The response includes run identity, coverage, counts, delivered reset,
`recording_artifact_index`, `records` and the synchronized `telemetry` window.
That window includes each returned input's measured frame and, when available,
one following frame. There are no inline sensor byte payloads. A sensor reference
contains `header` (the exact wire header without `data_base64`), `byte_length`
and `sample_index`. Retrieve those bytes using:

`GET /simulations/{id}/artifacts/{recording_artifact_index}/samples/{sample_index}`

To reconstruct a sensor observation, preserve all saved observation fields and
replace its empty `sensors` list with the ordered headers plus each downloaded
sample encoded as canonical base64 under `data_base64`. This reconstructs the
delivered input message, not the worker's hidden memory or model activations.

The `decisions` artifact is `decisions/{run_uuid}/{artifact_uuid}/timeline.ndjson`.
Its first line is a `decision-timeline-v1` header with reset, recording index,
counts and SHA-256 of the subsequent UTF-8 record lines. Later lines are
`DecisionRecord` objects. Downloads use the artifact's index in the result,
just like telemetry. Import no arbitrary artifact paths.

The writer spools records to disk and retains one pending input; it never holds
an episode's camera payloads or an in-memory list of decisions. Publication
validates the full trace against telemetry, controls, interventions, registered
identity and raw sensor metadata/checksums. Readback checks clocks, transitions
and the timeline checksum. Pagination bounds returned decision records; page
validation still loads finalized telemetry and scans the decision trace. Raw
capture validation/downloads can read multiple samples. Very long recordings may
therefore take additional memory and I/O time.

No new dependency or database migration is required. Restart the backend and
reload the dashboard after updating. Re-register existing agents: Phase 7 changes
the fingerprinted Poltergeist runtime, so old registrations correctly fail their
drift check. Keep those registrations for historical identity, and select the new
registration for new runs. Execute a new run to create decision evidence.
