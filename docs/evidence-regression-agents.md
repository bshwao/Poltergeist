# S14–S15: evidence analysis and version regression

The Evidence Analyst reviews a saved run. The Regression Agent reviews a saved
suite run. Both use deterministic rules over recorded evidence; opening a page
does not execute a controller, call a language model or launch another simulation.
Choose **Analyze recorded evidence** in a run or guided outcome, or **Review
version regression** in a completed suite comparison. Each review exports JSON.

## S14 — Evidence Analyst

The report separates recorded observations from explicitly **unverified
hypotheses**. It describes execution status, frames containing contact/rule
events, measured peak deceleration and saved scripted intervention records.
Contact-frame counts are labelled as frame counts, not distinct collision
episodes. Frame references retain the recorded frame number and time, including
frame zero. The run viewer opens those saved frames; control-audit references
open its separate decision/control view.

An intervention immediately preceding measured braking may produce a question
for review. Temporal proximity does not establish causation, and the report never
claims to know what a model perceived or why it acted. Proposed/applied controls
remain distinct from model intent. Camera/LiDAR and decision traces require their
existing separate viewers; this report does not claim to validate them.

Every report includes coverage, unknowns, limitations and SHA-256 fingerprints of
the reviewed result and telemetry. Missing observations are null/unknown, rather
than zero events. Pending runs produce no finalized-outcome claims. Missing,
corrupt or oversized saved telemetry produces an unavailable analysis; partial
coverage and timeline gaps remain visible. Synthetic mode is explicitly labelled
and does not demonstrate CARLA driving performance.
The saved scoring settings must reproduce the stored safety metrics from the
reviewed trace. A structurally valid changed trace cannot retain an old coverage
claim when it contradicts that evidence.

## S15 — Regression Agent

Create a local saved suite with at least two registered controller versions, the
reference first, and matched seeds/repetitions. Launch that suite deliberately.
Its frozen journal retains recipes, ordered controller snapshots, execution and
scoring settings independently of later edits to the reusable suite definition.
Reviewing that journal starts no additional execution.

Each candidate is compared with the reference on the same case, seed and
repetition. Completed, assessed runs need validated saved telemetry and complete
recorded safety coverage. Recorded simulator runtime, resolved spawns, agent
input sources/sensors/assistance, control authority, navigation and scoring must
also be compatible. Missing, failed, cancelled, corrupted or incompatible results
remain unavailable pairs, with their reason and both run links.

Controller identity uses frozen code/model/configuration fingerprints. Changing
only a human-readable name, version label or registration ID does not create a
distinct executed artifact. The built-in baseline has no frozen artifact version
and therefore cannot establish a version-regression decision. Its ordinary suite
comparison remains available. Python/dependency fingerprints are disclosed;
missing inventories or changed dependencies prevent attribution to model weights
alone, even when a measured stack difference is observable.

The explicit review policy selects measurements, native-unit thresholds and a
minimum of 2–200 observed pairs (default **3**). Defaults select heuristic severity
with a **5-point** worsening threshold and contact episodes with a **0-episode**
threshold. Other selectable measurements are minimum TTC, minimum clearance,
peak deceleration, hard-braking episodes and rule-event episodes.

Differences retain the raw sign: **candidate minus reference**. Lower TTC and
clearance are worse; higher values of the other selected measurements are worse.
A change must **exceed**, rather than equal, its threshold.

| Per-case/controller/measurement decision | Required evidence |
| --- | --- |
| Worsening pattern observed | At least the minimum observed pairs, and every observed change exceeds the worsening threshold |
| No selected regression observed | Every planned pair is observed, at least the minimum count is met, and no change exceeds the worsening threshold |
| Inconclusive | Insufficient evidence, mixed patterns, identical artifacts or incompatible/unavailable observations |

A positive pattern can coexist with other unavailable planned pairs; their
denominators and reasons remain visible. The overall report clears selected
regressions only for a completed suite with all selected comparisons fully
observed. It does not clear unselected measurements or assert safety. Means,
ranges and sample standard deviations describe the observations; they are not
statistical significance, failure probabilities or causal estimates.

The JSON retains policy, controller fingerprints, raw reference/candidate
measurements, matched run IDs, unavailable reasons and source/plan hashes. Download
the existing **suite comparison evidence** alongside it for the full frozen
reproduction journal. The interface pages long assessment and pair lists.

## Local API and connected computers

| Route | Result |
| --- | --- |
| `GET /experimentation/analysis/{simulation_id}` | Saved-run evidence analysis; `?download=true` supplies a JSON attachment |
| `GET /experimentation/regressions/{suite_run_id}` | Saved-suite regression review using the default policy |
| `POST /experimentation/regressions/{suite_run_id}` | Read-only review using the supplied `RegressionPolicy` JSON |

For example, the POST body below requires three observed matched pairs and a
minimum-TTC decrease greater than half a second:

```json
{"minimum_pairs": 3, "thresholds": {"min_ttc_s": 0.5}}
```

These routes use persistence without creating a simulation worker. Responses use
`Cache-Control: no-store`. Unknown IDs retain the existing not-found behavior;
invalid policy inputs return 422. Each review shares a cumulative **128 MiB /
100,001-frame** telemetry budget, with bounded line reads, including protection
against files growing after their initial size check. A multi-run regression
review shares this budget across its runs. Limit-excluded evidence stays unknown;
the existing paged evidence and exports remain available for manual review.

The connected dashboard uses negotiated `X-Poltergeist-Analysis: v1` support in
addition to the existing lab capability. False capability fields are omitted on
the WebSocket protocol so older strict peers can still connect. An unsupported
companion receives an upgrade message; new actions are not forwarded to it.

Remote `analysis_get` and `regression_report` are owner/generation-scoped reads,
require existing result/evidence permission and retain post-read consent checks.
They create no execution receipts. Remote controller execution remains limited
to the built-in baseline under the current device policy. Its suite review
honestly returns inconclusive for missing frozen version identities. Registered
version comparisons use the local trusted-package workflow; this addition does
not authorize remote arbitrary Python packages or grant new device scopes.

## Validation scope

The automated checks cover exact frame-zero links, unavailable/corrupted traces,
bounded reads, actual registered SDK packages, introduced synthetic regressions,
threshold boundaries, minimum counts, renamed identical artifacts, matched-input
checks, owner isolation, revoked consent, older-companion negotiation, explicit
review actions, exports and responsive browser accessibility. Reports are
deterministic and synthetic results are labelled. Independent human review,
current packaged-client acceptance and representative live CARLA/hardware
comparisons remain separate acceptance work; these checks do not complete them.
