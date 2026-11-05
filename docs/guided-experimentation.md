# Guided experimentation and failure discovery

S12's application workflows and S13's bounded discovery engine are implemented
in the local dashboard and the connected website. S12's first-time tester
acceptance can run on the owner's current PC. These source changes do not update an archived
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

Either scenario entry point shows gallery loading and offers a retry on failure.
If a saved guided run is unavailable in the current workspace, refresh the run
or choose another experiment. That action clears the browser's reference to the
unavailable run. A temporary service error keeps the reference for retry, and
missing result evidence keeps the recorded run available for review.

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

The guide saves the exact launch recipe and request ID in the current browser
tab before submitting. After a connection error or reload, **Retry saved
submission** uses the same ID and recipe. The browser waits for an explicit
retry. Discarding the saved submission clears it from the tab while preserving
any run the worker already accepted. Hosted sign-out clears saved paired
submissions from the tab.

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

The local guide submits through `PUT /simulations/requests/{request_id}`. The
worker saves the request receipt with the run and queue job. A repeat with the
same UUID and recipe returns the original run's current record, including after
a worker restart. Reusing that UUID with a different recipe returns HTTP 409.

Migration 7 adds typed experiment documents and scoped durable acceptance
receipts. Prior migrations remain unchanged. Definitions, approvals and receipt
hooks share existing transactional child admission; there is still one serial
simulation worker. Generated Python/JSON/TypeScript contracts describe both
dashboards. Back up a closed profile before upgrading. An older binary that
supports only schema 6 cannot reopen an upgraded profile; signed release
compatibility checks preserve that boundary.

## Acceptance record

S14 adds an explicit **Analyze recorded evidence** action to run outcomes. S15
adds **Review version regression** to saved-suite results, using frozen registered
versions and a selected measurement policy. Neither review starts more runs.
See [evidence and regression agents](evidence-regression-agents.md) for frame links,
coverage, unknowns, exports and the connected baseline-only restriction.

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

The tester workspace uses a separate loopback profile on this PC. The current
build was restored and verified on November 5.
Its production dashboard build uses source base `ec363fc` plus the current
working-tree recovery and usability changes. The served index SHA-256 is
`6df6eb78f96e9b12b030429bcbdfe8f9fe01234dacc8cc35f549cb665c0d8dd1`.
The previous build assets were retained. The workspace had empty run, feedback
and suite histories when the current build was verified. In a fresh private
browser session, open the [test dashboard](http://127.0.0.1:8069/) so the tester
can find the lab unaided. The tester may submit feedback in the app and explain
the result to the person observing the session.

The recovery checks reproduced both issues before the fix. After the fix, all
398 frontend unit tests, 50 local browser checks and the connected guided-lab
journey passed. The local browser tests verified that a service error retains
the saved run reference, a missing record can be cleared without launching a
replacement, and the gallery retry returns keyboard focus to its heading.

The subsequent durable-launch build passed 25 backend tests, 398 frontend unit
tests, 62 local browser checks and three hosted browser checks. The two local
checks that require the optional assistant fixture were skipped. The guided
browser tests dropped the response after acceptance, reloaded the dashboard,
and verified that the deliberate retry used the original request and recovered
the single admitted run. The backend tests also cover concurrent replay, a
changed recipe, a worker restart and a failed receipt write. The hosted browser
verified that sign-out clears a saved submission.

The follow-up focus and forced-colors fixes passed 51 local accessibility and
guided browser checks and three hosted checks. The restored tester workspace
serves these fixes and supports the durable launch endpoint. A browser checked
the production first-run, gallery and empty suite views at 320 pixels, with
forced colors, zero axe violations and zero writes. All sixteen production
assets verified over HTTP. The run, job and experiment-document histories were
still empty after the check.

The same-day engineering check used a separate workspace and the current
production dashboard build. **Sudden Braking** completed in CARLA 0.9.16 on
`Town10HD_Opt` at Low graphics, with 161 recorded frames over eight simulated
seconds. The browser labeled the result as CARLA evidence and opened the
selected replay highlight at exact native frame 1579. The original world
settings and dynamic actor counts matched after the run. Run ID
`f355a246-f6e3-41dd-b0c2-2614040df914`. This was an engineering check. The
first-time tester workspace remains separate.
