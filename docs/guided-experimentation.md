# Guided experimentation and failure discovery

S12's application workflows and S13's bounded discovery engine are implemented
in the local dashboard and the connected website. S12's independent new-tester
acceptance gate remains open. These source changes do not update an archived
installer or publish the website.

## Start with one experiment

Open **Guided lab** in the dashboard. **First experiment** offers four illustrated
starting scenes. **Scenario gallery** includes all sixteen starter/stress entries,
with family and stress filters, complete recipes, seeds and learning goals.
Choosing or browsing a scene starts no execution.

Choose **Synthetic check** to learn the application workflow, or **CARLA** to
measure driving in the simulator. Then explicitly run one experiment. The result
explains recorded contacts, minimum TTC and contact-observation coverage.
Unknown measurements remain unknown. Completion and a low heuristic score do
not establish road safety.

**Replay highlights** point to actual saved telemetry frames: recorded contacts,
rule events, peak deceleration, evaluator conflict thresholds and the final
recorded frame. Local links focus the existing telemetry/decision viewer. The
connected website reads the selected frame through the approved companion.
Recordings and printable reports remain in the existing run evidence workflow.
Highlights validate telemetry separately from camera/LiDAR recording integrity.
Review is bounded to 100,001 frames and 128 MiB of telemetry; larger evidence
remains available through paged telemetry and local exports.

The final guidance step invites an answer about understanding the outcome,
confusing steps and reasons to return. Feedback is saved only after the user
answers and explicitly selects **Save this feedback with the experiment**.
It is stored in the workspace, scoped to the paired owner when submitted from
the website. Automated fixture responses are test data, not human feedback.

## Keep a reusable suite

Add gallery scenes to **Saved suites**, name the suite, select controllers in
reference order, and choose matched seeds and repetitions. The local dashboard
supports baseline and registered local agents. A definition includes exact
recipes, simulation options, scoring settings and a failure policy. It supports
up to twenty scenes, eight controllers, twenty distinct seeds and ten repetitions,
with at most 200 executions in total.

Save before launching. Every launch freezes the saved revision and receives its
own operation identity. An explicit retry after a lost launch response preserves
that identity. Later edits and deletion of the reusable definition preserve
existing run evidence. Draft replacement and deletion require deliberate UI
actions. JSON import/export retains the simulation and scoring settings;
unavailable agent registrations remain visible so they can be removed or restored.
Each saved suite's **Past results** lists its durable launches. Comparison links
survive reloads and work in a fresh browser session with the same device approval.

Comparison reports keep scenarios separate. Each controller has completed/planned
counts, observed/unavailable metric denominators, sample spread, input sources,
coverage and links to every execution. Paired differences use matched
scene/seed/repetition cells and require completed, fully observed results with
compatible recorded runtimes. Failed or missing results do not become zero.
JSON downloads include the complete frozen operation and trial journal.

## Review and approve a discovery plan

Open **Discovery**. Describe the evaluation question, select an observed failure
policy and scenario family, choose baseline or a registered local agent, then
set the seed, duration, fixed parameters and optional parameter ranges/task.
The typed selections determine execution; the description records the question.
No language-model service or arbitrary shell operation is used.

Budgets are explicit:

| Budget | Allowed range |
| --- | --- |
| Total submitted executions | 3–200 |
| Search candidates | 1–100 |
| Confirmation repetitions per matching candidate | 2–10 |
| Matching repetitions required | 2–10, no greater than repetitions |
| Candidates investigated by confirmation | 1–20 |
| Absolute wall-clock allowance | 1–7,200 seconds |
| Reserved scenario time | Positive, at most 36,000 simulated seconds |
| Duration of each candidate | Positive, at most 120 simulated seconds |

**Build a plan for review** validates every sampled candidate and any selected
agent without executing model code or starting CARLA. The saved plan freezes all
recipes, agent identity, simulation/scoring settings, limits, steps and a SHA-256
fingerprint. Reviews expire after 24 hours. Changing the draft invalidates its
displayed approval. Starting requires the checkbox and **Approve and start
discovery**, bound to the saved fingerprint and revision.

Approval starts the absolute deadline, including queue time, shutdown and
interruptions. Repeating the same approval returns the original operation.
Resume retains its original deadline, candidates and all spent executions.
Every linked child consumes an execution and its full reserved scenario window,
including failed, cancelled and unknown trials. Before starting another candidate,
the engine reserves enough executions and scenario time for all its confirmations.
Deadline expiry requests cancellation; a blocking native call and cleanup may
finish later, and the engine submits no further work.

A matching search observation is a candidate. Only separate executions of its
exact recipe, seed, frozen agent and settings count toward confirmation. All
approved repetitions must finish assessment before a candidate is confirmed.
Negative confirmations can establish failure to reproduce; missing observations
can leave the decision inconclusive. Cancellation preserves incomplete work.
The report retains positive, negative, failed, unknown and unassessed trials.
An empty search says **No failure confirmed** and does not invent a failure.

Confirmed scenarios can be downloaded with their identity/settings and source
evidence, or saved directly as reusable suites for later execution and comparison.
Reports describe measured heuristic outcomes and repeatability under these
conditions; they do not infer model reasoning or general safety.

## Connected website boundary

When a previous evidence read is finishing, guided actions wait briefly for the
computer's transfer slot. Only an explicit `transfer_busy` refusal before relay
admission is retried automatically: at most four attempts within twelve seconds,
honoring Retry-After with one frozen request identity and payload. Cancellation,
lost approval or a retired workspace prevents further dispatch. Network failures,
uncertain writes and other refusals still require deliberate review/retry.

Guided actions use `POST /v1/devices/{device_id}/experimentation`, an ephemeral,
negotiated HTTPS/WSS channel. It requires the current account/device generation,
browser CSRF protection, the relevant local execution/read/cancel grant and local
evidence consent. Older companions continue to support the original workflow and
show an update/consent prompt for the lab. Experiment documents, approval receipts
and evidence stay on the companion; the coordinator does not persist lab payloads.

The connected prototype supports baseline only and at most twelve executions or
search candidates per plan, within the computer's duration/tick/queue limits.
It disallows remote map reloads, graphics changes, raw capture options and local
package paths. Local consent, mode and limits are rechecked before every new
autonomous child. Stop/Unpair prevents subsequent trials. Evidence and suites
are scoped to origin, account, device and pairing generation, including evidence
whose reusable suite definition was deleted.
Guided-run acceptance, its native command receipt and its lab acknowledgement
commit in one worker transaction. A receipt-storage failure rolls back admission;
a lost response can replay the original acknowledgement without executing again.

## Local API and storage

| Method and path under `/experimentation` | Purpose |
| --- | --- |
| `GET /gallery` | Pure illustrated-scenario catalogue; no persistence/worker |
| `GET, POST /suites` | Page or create reusable definitions |
| `GET, PUT, DELETE /suites/{id}` | Read, revision-check update or delete |
| `GET, POST /suites/{id}/runs` | Page durable launches or launch a saved revision with an explicit request UUID |
| `GET /suite-runs/{operation_id}` | Per-case matched comparisons and full evidence |
| `POST, GET /discovery/plans` | Preflight/save or page plans |
| `GET /discovery/plans/{id}` | Read the exact review/approval snapshot |
| `POST /discovery/plans/{id}/approve` | Explicit digest/revision-bound approval |
| `GET /discovery/plans/{id}/report` | Audited complete discovery report |
| `GET /highlights/{simulation_id}` | Recorded frame highlights |
| `POST, GET /feedback` | Deliberately save or read workspace feedback |

Suite/report GETs support `download=true`. Lists accept `limit=1..100` and a
nonnegative offset. Existing optimization cancel/resume endpoints control suite
and discovery operations. The public optimization creation endpoint excludes the
internal discovery request, so execution must pass through saved plan approval.

Migration 7 adds typed experiment documents and scoped durable acceptance
receipts. Prior migrations remain unchanged. Definitions, approvals and receipt
hooks share existing transactional child admission; there is still one serial
simulation worker. Generated Python/JSON/TypeScript contracts describe both
dashboards. Back up a closed profile before upgrading. An older binary that
supports only schema 6 cannot reopen an upgraded profile; signed release
compatibility checks preserve that boundary.

## Acceptance record

Automated checks cover the first-run journey, no execution on browse/preview,
explicit approval, feedback consent, exports, protected drafts, exact frame
links, saved-suite comparisons, cancellation, interruption, absolute budgets,
frozen candidates, changed agent assets, unknown and negative confirmations,
registered external agent execution, scoped durable retries and remote revocation.
Browser checks exercise desktop and 390-pixel layouts with WCAG accessibility
audits. See [progress notes](progress-notes.md) for the dated results and live
CARLA limitations.

To close S12's remaining adoption gate, give a new tester the app with no verbal
walkthrough. Ask them to choose and run one scene, explain its recorded outcome
and missing evidence, inspect a highlight, save a suite, and explain why they
would return. Capture their consented feedback and the exact app version. Record
each usability change and repeat the relevant task with the tester. Automated
browser acceptance alone does not satisfy this human gate.
