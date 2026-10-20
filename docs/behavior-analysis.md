# Recorded behavior analysis (Phase 8.1)

Phase 8.1 adds a backend measurement engine and read-only HTTP endpoints. It
analyzes finalized saved evidence, without starting a simulation, loading an
agent or importing a model. Phase 8.2.1 adds dashboard review and evidence
navigation; see the [review guide](behavior-review.md). Phase 8.2.2 adds
[behavior report exports](reports.md) containing the same measurements and
effective settings, plus separately labelled recorded diagnostics.

Restart the backend after updating. No new dependencies or database migration
are required. Existing saved runs can be analyzed without re-registering their
agent. For future executions, re-register agents because the new evaluation and
contract files change their recorded runtime fingerprints.

## Read an analysis

The endpoint uses the same server root as other simulation routes:

```powershell
$base = 'http://localhost:8000'
$runId = 'YOUR-FINALIZED-RUN-UUID'
$analysis = Invoke-RestMethod "$base/simulations/$runId/analysis"
$analysis.coverage
$analysis.braking
$analysis.route
$analysis.events | ConvertTo-Json -Depth 100
```

For explicit analysis settings, POST a settings object to the same URL:

```powershell
$settings = @{
    response_window_s = 2.0
    brake_threshold = 0.2
    steering_change_threshold = 0.15
    hazard_ttc_s = 3.0
    max_response_events = 100
} | ConvertTo-Json
$analysis = Invoke-RestMethod "$base/simulations/$runId/analysis" `
    -Method Post -ContentType 'application/json' -Body $settings
```

Both operations return HTTP 200 and make no changes to results, artifacts,
registrations or scoring settings. POST is a read-only calculation with a body,
not a new experiment. Empty settings `{}` equal the GET defaults. Unfinished
runs return 409, unknown runs 404, and invalid IDs/settings 422. Artifact
corruption fails validation instead of yielding a plausible analysis. The
endpoints depend only on persistence and do not start queue/recovery services.

The response is a generated shared contract, `BehaviorAnalysis`, with
`schema_version: "1.0"`. It includes run ID/mode/execution status, immutable agent
snapshot, actual input provenance, control authority, effective settings,
coverage, unchanged stored safety metrics, the recorded task outcome, braking
and route measurements, response events and interpretation notes. It contains
no generated timestamp or random ID: identical saved evidence/settings produce
identical analysis JSON. It does not query the current registry to relabel history.

## Response events and clocks

Each `events` item contains an anchor and six response channels:

- `proposed_braking`: validated recorded controller proposal.
- `applied_braking`: applied control from the measured transition, corroborated
  by a decision record when available.
- `proposed_steering_change` and `applied_steering_change`: absolute change in
  signed normalized steering relative to the preceding input control, or the
  anchor's control when no preceding sample exists. A steady turn is not a new
  steering response. Reference frame/value are explicit.
- `measured_deceleration`: acceleration projected against measured velocity, or
  adjacent-speed interval deceleration when acceleration is unavailable.
- `measured_heading_change`: circular heading change relative to the anchor's
  measured heading, correctly handling wraparound at 0/360 degrees.

Anchors have explicit sources:

| Anchor | Definition |
| --- | --- |
| `scenario_event` / `scenario_script` | An immutable scheduled speed/brake command, aligned to its observed input tick. Scheduled time, action, original event index, actor ID and whether it targets ego are retained. This is a command reference, not proof of physical hazard onset. |
| `measured_hazard` / `evaluator_ground_truth` | First observed per-actor box clearance at/below the configured threshold or constant-velocity box TTC within the configured horizon. `threshold_crossing` requires a preceding continuously observed safe state; otherwise onset is `first_observed`. |

Unavailable actor/geometry/velocity data never establish a safe reset between
hazards. Empty observed actor lists do differ from unavailable feeds. Hazard
anchors use evaluator state even for sensor agents; they do not imply that the
agent received or perceived those objects. The result preserves that input
provenance separately. TTC reuses existing oriented-box evaluation and its
constant-velocity/orientation assumptions.
Zero returned hazard anchors is not a hazard-free assessment: inspect pair
coverage, the saved safety coverage and the source telemetry before interpreting
an empty event list. Unreached scheduled events remain explicit unavailable anchors.

For a recorded input at frame **f**, its control is applied over the transition
to measured frame **f + 1**. Control response times use the input clock, not the
following packet's time. Physical response times use the measured state clock.
All times are simulated episode seconds, not inference wall time. Native frame
IDs are retained, including episodes whose first native frame is not zero.

A response is the first inclusive threshold crossing in the requested window.
`latency_s` is the elapsed time from the aligned anchor to that observed crossing;
it is not known perception latency or evidence that the event caused the action.

| Response status | Meaning |
| --- | --- |
| `observed` | A continuously observed threshold onset has frame/time/value evidence. A genuine zero interval remains `0.0`. |
| `already_active` | Braking/deceleration was already present at the anchor, or present in the first sample without a preceding baseline. Its onset latency is unknown (`null`). |
| `not_observed` | No crossing in the observed continuous window/prefix. `window_complete` distinguishes a full window from a truncated one. It does not mean the vehicle never responded. |
| `unavailable` | The anchor or a necessary signal/continuous prefix is missing. No response latency is inferred across gaps. |

An established response is retained if later samples are missing; the full
window can still be incomplete. The terminal packet has no subsequent applied
action, so a control window reaching the recording boundary may be incomplete.
Proposals missing from legacy/unresolved inputs stay unavailable, even if applied
control or measured motion can still be analyzed.

`attribution` identifies control source/assistance, not causal intent. It separates
SDK agents, baseline/legacy controllers, script-influenced controls, unknown source
and measured motion. Ego-script anchors and interventions in the relevant input
prefix (including a steering reference) prevent credit as an independent SDK
response. Measured motion alone is never credited as an agent decision. Agent
diagnostics remain separate, self-reported evidence in the Phase 7 timeline.

## Braking, navigation and evidence

Braking analysis reuses `summarize_braking`: projected/interval deceleration,
continuous hard-braking episodes, and completed stopping episodes whose distance
integrates measured speed. Completed stops include their exact telemetry span,
duration, distance and known/unknown script influence. An unfinished or broken
stopping sequence is not invented as a completed stop.

Thresholds come from the saved evaluation settings, or explicitly labelled
`legacy_defaults` when absent. Continuity uses the tighter of saved braking gap
policy and the requested analysis gap. Effective braking settings are returned.
The result's `stored_safety_metrics` and its scoring settings are copied unchanged;
new analysis does not overwrite historical severity or change its definition.

Route deviation is planar distance to the **recorded resolved finite framework
route**, with segment fractions clipped to the configured ego-height band. It
respects endpoints and elevated roads; it is not a lane-compliance measurement.
The mean is over observed compatible pose samples, not time-weighted distance.
An indexed reference avoids scanning every segment for every pose. Recorded
progress, length and completion fraction come from the saved task monitor;
analysis does not guess progress using nearest points at route crossings.

Agent-owned navigation without an independently recorded reference, missing
resolved routes and missing/height-incompatible poses remain unavailable. The
scenario's requested route and an agent's predicted trajectory are not substituted
for the resolved executed framework route. Zero route length has no completion
fraction. The recorded task outcome and its final/reached frame evidence remain
separate from execution status and safety metrics.

Available measurements carry units, observed/eligible sample counts, coverage
and `EvidenceSpan` references. Each span identifies source and inclusive native
frame/time bounds. Aggregate spans describe the source window, which may contain
missing samples; counts and coverage describe which observations were usable.
Response references include baselines and following measured packets for controls.
Use the existing telemetry/decision endpoints to inspect those source frames.

## Settings and limits

| Setting | Default | Meaning |
| --- | --- | --- |
| `method_version` | `"1.0"` | Measurement definitions; unsupported versions are rejected |
| `response_window_s` | 3 | Inclusive observation window; greater than 0 and at most 60 s |
| `brake_threshold` | 0.1 | Normalized brake threshold; hand brake counts as 1 |
| `steering_change_threshold` | 0.15 | Absolute normalized steering change; greater than 0, at most 2 |
| `deceleration_threshold_mps2` | 0.5 | Positive measured deceleration threshold |
| `heading_change_deg` | 5 | Positive circular heading change, at most 180 degrees |
| `hazard_ttc_s` | 3 | Positive predicted-contact horizon/threshold, at most 60 s |
| `hazard_clearance_m` | 2 | Nonnegative oriented-box surface clearance threshold |
| `max_sample_gap_s` | 0.5 | Maximum continuous adjacent-sample gap; skipped frame IDs always break continuity |
| `route_height_tolerance_m` | 2 | Positive height band around the ego pose for route reference points |
| `max_response_events` | 200 | Earliest events returned; 1–2000 |
| `max_stopping_episodes` | 200 | Completed stops returned; 1–2000 |

Output caps preserve total counts and explicit truncation flags. Earliest events
are chosen deterministically; scenario events precede hazard events at the same
time. Bounds limit returned findings rather than discarding the source evidence.

Failed/cancelled runs and old results retain useful measured windows. Missing
decision artifacts never become invented proposals or diagnostics. Analysis
validates the complete persisted trace and sensor references before returning;
only compact controls are retained from the decision stream, not all scene or
camera payloads. Telemetry is still loaded and raw recordings are integrity
checked, so long recordings can take additional memory and I/O time. It is
post-run analysis, not a real-time or model-quality guarantee.
