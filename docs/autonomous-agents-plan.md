# Autonomous driving integration: ten phases

This extension builds on the existing scenario engine, CARLA adapter, simulation
queue, telemetry, evaluation, and dashboard. It is separate from the original
ten implementation sections in [implementation-plan.md](implementation-plan.md).

| Phase | Deliverable | Completion checkpoint | Status |
| --- | --- | --- | --- |
| 1 | Versioned agent SDK, manifests, lifecycle, direct controls, reference/starter package, compatibility checks | A local example loads, receives synthetic observations, and returns validated frame-associated controls | Implemented |
| 2 | External agent process and dependency environment, inference timing, cancellation, deadlines, cleanup | An external agent controls a Python-launched CARLA experiment; failures are explicit | Implemented; live verified through the reference AI pilot |
| 2.5 | Poltergeist reference AI pilot: one pretrained learned driving policy, live control verification, and hardware timing | A pinned model drives a bounded CARLA route through the worker, with evidence that its predictions affect the vehicle and measured real-time performance | Complete; live control and 20 Hz throughput verified with the documented performance profile |
| 3 | Initial conditions versus agent goals, route/planner ownership, authoritative agent controls and recorded interventions | The agent chooses its evolving response without silent scripted overrides | Implemented; see driving task guide |
| 4 | Declared camera/LiDAR inputs, synchronized observation delivery, separation of evaluator ground truth | One compatible sensor-based driving agent runs end to end | Implemented; see sensor agent guide |
| 5 | Agent registry, API selection, durable job configuration and code/model fingerprints | Queued API runs retain the exact selected agent and observation mode | Implemented; see agent registry guide |
| 6 | Agents page, registration/readiness checks, dashboard launch selection | A user registers and launches a compatible agent from the dashboard | Implemented; see dashboard guide |
| 7 | Persisted input/action/diagnostic/intervention timeline and synchronized review | A recorded action can be traced to its preceding observation | Implemented; see decision timeline guide |
| 8.1 | Behavior measurement engine and analysis API | A finalized run produces reproducible response/route measurements with settings, coverage and supporting frame references | Implemented; see behavior analysis guide |
| 8.2.1 | Dashboard behavior review and evidence navigation | Users can inspect measurements and open their supporting recorded frames | Implemented; see behavior review guide |
| 8.2.2 | Behavior report exports and final review polish | Exported reports carry the same findings, identity, settings and evidence limits as the dashboard | Implemented; desktop/mobile and printed pages rechecked in Phase 10 |
| 9 | Agent/version comparisons, repeated trials, agent-aware reproduction/search/minimization | Comparisons retain traceable experiment conditions and agent versions | Implemented; see [guide](agent-comparisons.md) and [validation](phase9-validation.md) |
| 10 | Integration/failure validation, installation, supported examples and hardware guidance | A new user can complete the supported workflow from documentation | Implemented; see [first run](first-run.md) and [validation](phase10-validation.md) for tested scope |

Public uploads and hosted execution need a separate isolated-worker design. A
local Python process alone is not a security boundary for untrusted agents.

Phase 2.5 comes before user-facing model registration/import workflows. Keep the
existing trusted-package worker as the internal integration mechanism. Prove one
supported reference model first, then use that working integration to guide the
general registry and dashboard in Phases 5 and 6. The original ten phase numbers
remain unchanged.

## Phase 1 handoff

- Shared SDK 1.0 contracts live in `shared/models/agent.py`. They are exported
  from `shared.models`, JSON Schema, and generated frontend types.
- `simulator.agents.AutonomousAgent` defines `load`, `reset`, `step`, and `close`.
  `AgentSession` validates/copies messages and associates results with exact
  observation frames. Errors terminate a session without substituting controls.
- `BaselineSDKAgent` exposes the existing baseline as a reference. The editable
  package in `examples/agents/starter/` is a rule-based example, not a trained
  driving model. Existing `BaseAgent`, `CustomAgent`, dashboard launches, and
  simulation behavior retain their original interfaces.
- `python -m scripts.check_agent PACKAGE` checks manifest declarations and files
  without importing code. `--smoke-test` explicitly executes trusted local code
  against two synthetic observations and always attempts cleanup.
- Phase 1 supports simulator-state observations and direct vehicle controls.
  Simulator state includes hidden actors. Sensor declarations are representable
  but rejected by this runtime; no sensor-driving capability is implied.
- Package loading is in-process, with package-relative modules and no automatic
  dependency installation. It is not the phase 2 worker or phase 5 registry.
- See [the SDK guide](agent-sdk.md) for runnable commands, lifecycle semantics,
  package layout, and the phase 2 integration boundary.

## Phase 2 handoff

- `ProcessAgent` runs a trusted local package in an explicitly selected Python
  environment. The parent and worker both validate SDK messages. Bounded JSON
  messages carry transport versions, request IDs, operations, and native frames;
  invalid or stale replies fail the experiment without fallback controls.
- `SimulationRunner` owns CARLA ticks, applies returned controls, resets episode
  state, and closes the worker on completion, cancellation, or failure. The worker
  receives simulator-state observations and never connects to CARLA.
- Load/reset/step/close deadlines use wall time. A hung worker is terminated with
  its process group, including children; Windows workers enter a kill-on-close
  job before their initially suspended launcher resumes. Model-step time and
  transport round-trip time are measured separately from simulated time.
- Use `python -m simulator --agent-package PACKAGE --agent-python PYTHON` with a
  template or scenario and `--mode carla` or `--mode test`. The selected Python
  environment must already contain Poltergeist and the agent's dependencies.
  Nothing is installed automatically. See [commands and limits](agent-sdk.md#run-an-agent-in-a-separate-process).
- Timing summaries are available through `ProcessAgent.stats` and CLI stderr.
  Full persisted decision/diagnostic traces are implemented in Phase 7. Queued API agent
  selection is described in Phase 5 below; the dashboard workflow is described in Phase 6.

Validation on October 18, 2026: 1,790 Python regression tests passed. The final
180 focused worker/process/runner/SDK cases passed with warnings treated as
errors, including three configuration cases added after the broad run started.
A Python 3.12.10 parent successfully controlled a Python 3.14.0
worker, and the starter completed a 30 s synthetic experiment with 600 control
exchanges. Tests cover hangs, crashes, stale/invalid controls, bounded messages
and logging, cancellation, cleanup failures, and actual Windows descendants.
The live CARLA API was unavailable at `127.0.0.1:2000`; no live external-agent
CARLA execution is claimed by these checks.

## Phase 2.5 — Poltergeist reference AI pilot

The maintained PlanT 2.0 integration, setup command, authoritative control mode,
SDK 1.1 scene delivery, and evidence/benchmark harness are implemented. The
verified checkpoint is installed in the local ignored model cache. Twelve live
hazard/changed-condition trials completed, alongside matched baseline and control
ablation runs. Subsequent optimization passed the full 20 Hz throughput target
on the RTX 2060 test machine across ten repeated live template trials, including
evidence I/O in the cycle budget. See [setup and launch](reference-ai-pilot.md),
[functional validation](reference-ai-validation.md), and
[performance acceptance](reference-ai-performance.md). The specification below
retains the intended scope. Rendering and different hardware need their own
measured timing results.

The goal is to demonstrate a genuinely trained policy controlling the live ego
vehicle, and measure whether it can keep up with the simulator on the test
machine. The current `StarterAgent` and `BaselineSDKAgent` are rule based and do
not satisfy this checkpoint. Start with a supported pretrained model rather
than adding a training project to this integration subphase. "Poltergeist
reference agent" means our maintained integration of that model; retain credit
for the model's original authors and training.

The first pilot uses structured simulator state and a supplied route. This
tests learned planning/behavior and the control connection. Camera perception,
occlusion awareness, and independent selection of a destination/global route
are separate capabilities. Observing hidden actors through ground truth must
remain explicit in every pilot result.

| Step | Tangible work | Reviewable result |
| --- | --- | --- |
| 2.5a | Select a pretrained policy and verify checkpoint, input requirements, dependency compatibility, and hardware use | A pinned model/checkpoint and successful local inference from a documented input fixture |
| 2.5b | Supply required route/scene features and make the pilot agent's outputs authoritative | Validated observations and an explicit control-ownership mode, with recorded assistance/interventions |
| 2.5c | Package inference behind the existing SDK and external worker | A reference agent that loads real weights, resets state, and produces controls through model inference |
| 2.5d | Run controlled live CARLA driving and hazard trials | Reproducible runs and a small evidence trace proving model outputs reach the vehicle |
| 2.5e | Measure and tune performance alongside CARLA | Latency percentiles, deadline misses, memory use, and end-to-end real-time factor on the test hardware |
| 2.5f | Deliver the supported local demonstration | Documented setup, one launch command, and a functional/performance validation report |

### 2.5a: select and verify the reference model

The selected reference is [PlanT 2.0](https://github.com/autonomousvision/plant2),
whose authors provide an object-centric CARLA planning model and pretrained
[checkpoints](https://huggingface.co/SimonGer/PlanT2). This is a candidate for
adaptation rather than an unchanged dependency. Its upstream
[environment](https://github.com/autonomousvision/plant2/blob/main/environment.yml)
uses Python 3.10 and CARLA 0.9.15; Poltergeist currently uses Python 3.12+ and
CARLA 0.9.16. Verify the inference components on the supported local environment
before committing to it. Do not replace the user's CARLA installation to match
an upstream demonstration.

Separate the neural inference code from the upstream CARLA/Leaderboard agent.
The worker must continue to receive observations from Poltergeist and must not
open another CARLA client or tick the world. Inventory every required input,
including route points, object classes/boxes/motion, and relevant traffic/map
context. Choose a checkpoint whose actual feature requirements can be supplied;
do not fabricate missing features. Record upstream revision, checkpoint hash,
configuration, preprocessing, license/attribution, and pinned dependencies.

If this compatibility spike fails, document the concrete blocker and choose a
compatible pretrained structured-state policy before continuing. A renamed
rule-based controller or a network with random weights is not an alternative
completion path. Camera-based policies require Phase 4 inputs first.

### 2.5b–c: connect learned decisions to vehicle controls

Pull forward only the Phase 3 prerequisites needed for an honest pilot:

- Separate initial ego speed from desired route/speed goals. Provide a bounded,
  map-valid route and update its local context as the ego moves. State whether
  the model plans a local path along that supplied route or merely selects
  longitudinal behavior; do not call supplied headings independent pathfinding.
- Provide required scene features through validated, versioned contracts. Extend
  compatibility checks/schema/types if the current observation cannot represent
  them. The current reset supports a route but the runner does not populate it;
  a heading and actor list alone are not assumed sufficient for PlanT.
- Add an explicit reference-AI execution mode in which scenario events control
  NPCs and conditions, while the agent controls ego responses. Reject conflicting
  scripted ego-control events, or expose them as deliberate recorded interventions.
  Preserve existing baseline-mode behavior.
- Implement the model's actual preprocessing and inference in `load/reset/step`.
  If it predicts waypoints and desired speed, use a documented tracking controller
  to convert those outputs into steering/throttle/brake. Record that controller's
  contribution; it is part of the stack being tested.
- Keep model errors explicit, with no hidden baseline fallback. Record any
  warm-up or initialization controls and exclude them from claims about learned
  driving. Set goals and scenario conditions, not a prerecorded ego action list.

### 2.5d: verify live control and response

Begin with one short, mapped route on Town03 and a simple road-following trial.
Then test sudden braking and pedestrian hazards, followed by crossing traffic
and the occluded-pedestrian template. Start with bounded speeds/durations, then
vary hazard timing, placement, and seeds, including held-out conditions.

Save a small local verification trace rather than implementing all Phase 7 UI:
observation frame/time, model/checkpoint identity, model path/speed or direct
action, proposed controls, applied controls, interventions/assistance, measured
ego motion, inference duration, round-trip duration, and full tick wall time.
Record trial recipe, actual map, runtime settings, and hardware for reproduction.

Require evidence that model inference actually affects the vehicle: controlled
observation changes must produce appropriate measurable action/path changes,
and a diagnostic model-output ablation must change applied controls or motion.
Run this ablation only in a dedicated simulator experiment. The normal pilot
must fail if model inference is unavailable. Report route progress, collisions,
lane departures, stopped/stuck behavior, and failures. A dangerous trial ending
in a collision can still validate the integration; it must remain a driving
failure in the results. Finishing a run is not proof of driving competence.

### 2.5e: verify real-time performance separately

Successful synchronous control means the model receives the current observation
and supplies the next action while CARLA waits. This can work even when a run
takes much longer than simulated time. Real-time performance requires a separate
measurement; raising the worker timeout does not make inference faster.

At a 0.05 s simulation step the nominal budget is 50 ms for the complete step,
including observation preparation, worker exchange, inference, world advancement,
and telemetry. After separately recorded loading/warm-up, report:

- Inference and complete-step p50/p95/p99 wall times and missed step budgets.
- Real-time factor: simulated execution seconds divided by execution wall
  seconds. Record setup/loading separately. A factor below 1 is slower than
  real time; a factor at least 1 supports the overall throughput claim only.
- Peak process memory and, when measurable, GPU memory with CARLA and the model
  running together. Test the actual machine; no GPU fit or frame rate is promised.
- A matched rule-based run to reveal how much time CARLA itself consumes.

For a claimed 20 Hz demonstration, target real-time factor at least 1 and at
least 95% of complete steps within 50 ms over repeated measured trials; publish
all misses and tail latency. This is an empirical demonstration target, not a
hard real-time guarantee. If it misses, report functional control and performance
results separately and investigate the measured bottleneck before making a
real-time claim. Test supported precision/device settings without silently
changing the observation stream or skipping model decisions.

### 2.5f: handoff before general model imports

Deliver the maintained reference package, explicit weight/dependency setup,
documented CLI/Python launch, reproducible test recipes, and the evidence/timing
report. The report must identify which parts are learned, which use ground
truth, and which receive route/controller assistance. No trained model is
integrated or verified merely by adding this plan.

Functional control and the documented performance profile passed the live
2.5e control and throughput checks. Use
this known reference integration to shape later user import/registration and
dashboard selection. Continue the broader Phase 3 work afterward; the small
prerequisites pulled forward here do not mark all of Phase 3 complete.

## Phase 3 handoff

SDK 1.2 adds explicit driving tasks, route ownership, actual destinations,
optional desired speed and measured completion criteria. The runner supplies a
finite validated framework route or leaves planning to a declared agent planner.
New SDK agents default to authoritative controls and reject scripted ego events.
Deliberate scripted mode records affected controls in a typed audit, including
legacy runs. Goal outcomes are separate from execution status; early completion
is opt-in and independently verified against saved telemetry.

The reference AI accepts finite framework tasks and declares endpoint input
padding and deterministic goal/tracking assistance. The network still runs each
decision. A complete synthetic teaching example is included. See
[driving tasks and commands](driving-tasks.md) for ownership, measurements and
limitations. Dashboard agent selection is described in Phase 6; sensor support is
described in Phase 4 below.

## Phase 4 handoff

SDK 1.3 delivers declared, calibrated camera/LiDAR payloads through the existing
external worker. Exact-frame barriers, bounded queues/deadlines, checksum and
calibration validation, cancellation and cleanup protect the observation/action
clock. Sensor observations exclude privileged other-actor and evaluator state.
Ideal ego localization and framework navigation are explicitly disclosed.

The sensor teaching pilot reacts to actual LiDAR returns and camera visibility;
it is not a trained perception policy. PlanT remains the separate structured-state
reference AI. Results retain input provenance, multiple raw streams can be
recorded, and a local CLI trace preserves exact inputs/actions. See
[sensor agents and launch commands](sensor-agents.md). Registration, queued agent
selection are described in Phase 5 below; dashboard controls are described in Phase 6.

## Phase 5 handoff

The persistent local registry records a trusted package, its selected worker
Python, SDK manifest and code/model fingerprints. API launches can select a
registered agent, and queued jobs retain their resolved selection rather than
looking up a mutable current default when execution starts. Changed or missing
package content fails explicitly instead of silently substituting an agent.
Existing baseline launches remain available.

Registration and inspection do not install dependencies or import agent code.
Execution uses the same bounded external worker and observation contracts as
the CLI. Sensor and simulator-state inputs retain their distinct provenance;
registration alone is not proof of driving quality or CARLA readiness. See
[registration, API launch and provenance](agent-registry.md) for the supported
workflow and fingerprint limits, and [Phase 5 validation](phase5-validation.md)
for measured test coverage.

The Agents page and dashboard launch selector are implemented in Phase 6;
persisted decision review is implemented in Phase 7. Behavior reports and
agent-aware comparisons remain Phases 8–9.


## Phase 6 handoff

The dashboard now provides an Agents page for trusted backend-local package
registration, optional worker Python/configuration/model paths, advanced execution
settings, paged active/inactive history, saved manifest and fingerprint inspection,
static readiness checks, and deactivation. Registration does not execute code or
install dependencies. Duplicate display names retain distinct registration IDs.

Scenario launches offer the baseline or an explicit registered ego controller.
The selection survives recipe edits and navigation; current static checks are
repeated before launch. SDK/task/planner/control conflicts and RGB rendering
requirements are shown before submission without rewriting the recipe. Sensor
fixtures remain explicitly labelled in synthetic mode. Missing or deactivated
selections remain visible until the user chooses another controller.

Acceptance receipts, run history, run detail and failure evidence show the saved
agent identity. They do not consult a mutable registry to relabel old results.
Existing side-by-side failure views flag different or unknown custom identities;
agent-aware experiment comparisons remain Phase 9. Hidden-page requests are
cancelled, late responses are ignored, and uncertain mutations are not retried
automatically.

See [dashboard usage](dashboard.md) and [Phase 6 validation](phase6-validation.md).
Persistent decision review is implemented in Phase 7. Behavior analysis and
agent-aware optimization remain Phases 8–9.

## Phase 7 handoff

New API queue executions retain a streamed decision artifact. Each applied
action links its preceding observation, optional agent diagnostics, proposed and
applied control, interventions, timing and following measured frame. Successful
SDK reset context is retained; sensor bytes are referenced in the existing raw
capture rather than duplicated in decision JSON. Inputs interrupted by failure
or cancellation are explicitly unresolved, and coverage describes missing links.

The Simulations page provides paged, frame-synchronized review and on-demand
sensor input verification. Legacy results remain readable and explicitly lack
decision evidence. Publication checks the chain against finalized telemetry,
authority/intervention audits, registered identity and sensor recording metadata.
No internal model reasoning is inferred. Phase 8 remains behavior analysis and
evidence-based reports; Phase 9 remains agent-aware comparisons and optimization.

See [decision timeline usage and format](decision-timeline.md) and
[Phase 7 validation](phase7-validation.md).

## Phase 8 plan: two subphases

Phase 8 is complete when 8.1, 8.2.1 and 8.2.2 are implemented. Phase 8.2 is split
into dashboard review first and report exports second. Phases 9 and 10 keep
their existing numbers and scope. Both subphases build on the finalized telemetry,
driving outcomes, immutable agent identity and decision evidence already saved by
Phases 3–7. Analysis runs on recorded data and does not start another simulation
or invoke the driving model.

### 8.1 — Behavior measurements and backend analysis

Build the analysis engine and API first, with a defined result contract that
8.2 can consume without reimplementing calculations in the browser.

- Reuse existing safety and braking evaluation. Add measured behavior summaries
  for braking/steering response, stopping episodes, task completion, and route
  progress/deviation where the recorded route and pose support them. Keep
  agent-owned navigation without an independently recorded route explicitly
  unavailable for route-adherence measurements.
- Define event anchors, thresholds and observation windows before calculating
  response intervals. Identify whether an anchor is a scripted scenario event
  or a measured hazard condition. An interval after that anchor is an observed
  response measurement, not proof of when the agent perceived a hazard. If the
  input, anchor or continuous measurements are missing, report unavailable or
  no response observed within the measured window, as appropriate.
- Distinguish proposed controls, applied controls, script interventions and
  measured vehicle response. Script-driven braking must not be credited as an
  autonomous agent response. Preserve sensor/state input provenance and keep
  evaluator ground truth separate from what the agent actually received.
- Return a versioned analysis with explicit settings/units, coverage, run and
  agent identity, and supporting frame/time references for each finding. Retain
  useful measurements from partial and legacy runs without inventing missing
  decision inputs, diagnostics or outcomes. Preserve existing safety scores and
  their evaluation settings rather than silently changing their definitions.
- Add an on-demand endpoint for finalized-run analysis and meaningful tests for
  complete, failed, cancelled, partial, legacy and script-influenced runs.
  Identical saved evidence and settings must produce identical analysis.

Completion checkpoint: the API can analyze a saved run and explain exactly which
recorded frames support each available measurement, with missing evidence and
script assistance explicit. A new dashboard analysis panel and behavior report
presentation belong to 8.2.

### 8.2.1 — Dashboard behavior review and evidence navigation

Use the analysis contract from 8.1 to make the measurements useful to someone
reviewing an experiment.

- Add a behavior summary to finished-run detail, with response/route findings,
  appropriate charts or event markers, units, analysis settings and coverage.
  Selecting a finding should open its supporting frames in the Phase 7 decision
  timeline when those records exist; legacy findings can refer to telemetry.
- Present measured facts, agent-reported diagnostics and qualified
  interpretations separately. Generate concise descriptions from the saved
  evidence, such as when braking began relative to a defined event, while
  avoiding unsupported claims about internal model reasoning or causality.
- Handle zero versus unknown values, incomplete evidence, old results, loading,
  request failures and stale responses. Add frontend tests and visually verify
  the review workflow with recorded synthetic examples.

Completion checkpoint: a user can open a finished run, understand its measured
response and inspect the supporting evidence. The dashboard uses the existing
8.1 analysis API; behavior exports belong to 8.2.2. This subphase includes its
own tests and usable loading/error states.

### 8.2.2 — Behavior report exports and final polish

- Extend the existing JSON, CSV and HTML report exports with behavior analysis,
  supporting frame references, agent/version identity, input provenance,
  settings and evidence limits. Keep the dashboard and exports consistent by
  using the same backend analysis and effective settings rather than separate
  calculations.
- Preserve the dashboard distinctions between measured facts, agent-reported
  diagnostics and qualified interpretations in every export. Include partial,
  unavailable and script-influenced evidence without inventing values.
- Test export contents and dashboard/report consistency for complete, partial
  and legacy runs. Finish accessibility, responsive layout and report formatting
  polish, and document the complete review/download workflow.
- Verify the combined workflow with recorded synthetic examples. CARLA and
  trained-model execution are not prerequisites for either 8.2 subphase.

Completion checkpoint: a user can open a finished run, understand its measured
response, inspect the supporting evidence and download a report carrying the
same findings and limitations. Cross-agent comparisons, repeated trials and
agent-aware search/minimization remain Phase 9; broader installation and live
hardware validation remain Phase 10.

### Phase 8.1 handoff

The versioned `BehaviorAnalysis` contract and deterministic evaluation engine
analyze finalized telemetry, compact validated decision controls, saved task
outcomes and original safety settings. Explicit event/hazard anchors drive six
control/motion response channels with frame/time evidence, complete/truncated
windows, already-active signals and unavailable-data reasons. Script-influenced
controls remain distinct from independent controller proposals and measured motion.

Braking analysis reuses existing episode calculations. Finite height-aware route
distance uses a reusable spatial index; progress/completion are the recorded task
monitor values rather than inferred route shortcuts. Input provenance, immutable
agent identity, partial/legacy history and unchanged safety scoring remain explicit.

`GET /simulations/{id}/analysis` uses default settings; `POST` to the same path
accepts a `BehaviorSettings` body for a read-only calculation. Neither starts
the queue, CARLA or a model, changes historical results, or verifies mutable
current agent code. Response/stop findings have configurable caps and total counts.

See [analysis definitions and API usage](behavior-analysis.md) and
[Phase 8.1 validation](phase81-validation.md). Dashboard review is implemented in
8.2.1; behavior report exports are implemented in 8.2.2.

### Phase 8.2.1 handoff

Finished-run detail now requests the 8.1 analysis API and displays measured
braking/stopping, route progress/deviation and task outcomes. Event markers and
paged event selection expose all six response channels, their thresholds,
attribution, observation windows and supporting frames. Coverage, effective
analysis settings, legacy/partial results and interpretation limits stay explicit.

Evidence links open an exact native frame in the existing decision timeline or
telemetry inspector. They verify a contiguous-frame candidate before falling back
to binary search for sparse recordings; missing frames are never substituted.
Requests are cancelled when run/selection ownership changes, with stale results
ignored and timeout/retry states available. Agent diagnostics remain separately
labelled in the synchronized timeline. No model or simulator is invoked.

See [dashboard behavior review](behavior-review.md) and
[Phase 8.2.1 validation](phase821-validation.md). JSON/CSV/HTML behavior exports
and the final report/review polish are implemented in Phase 8.2.2.

### Phase 8.2.2 handoff

Dashboard downloads include version 2 JSON/CSV/HTML behavior reports by default.
They use the same validated analysis engine and effective default settings as
the review panel. Read-only POST exports accept the same explicit settings as
POST analysis. Measurements, bounded recorded agent diagnostics and qualified
descriptions occupy separate report sections, each retaining native-frame
evidence, immutable identity, input provenance and evidence limits.

Behavior and optional telemetry share one validated artifact read. Diagnostics
retain the earliest prefix up to 200 records or 1 MiB of compact UTF-8 record
JSON, with exact counts, byte accounting and truncation flags. Reports never
load current agent packages or invoke a model/simulator. Missing/corrupt required
artifacts fail the enhanced export; metadata-only version 1 exports remain
available through the existing route or dashboard checkbox.

Downloads check the report schema, MIME type and completed transfer, support
cancel/retry, and ignore stale run responses. Review tables support keyboard
scrolling, evidence links provide a return-to-finding control, and reduced-motion
preferences are respected. Offline HTML separates the three evidence categories
and supplies responsive/print styles and an escaped complete snapshot.

See [report formats and usage](reports.md) and
[Phase 8.2.2 validation](phase822-validation.md). Phase 8 is implemented;
cross-agent comparison/repeated trials remain Phase 9, and broader installation
and hardware integration checks remain Phase 10.
