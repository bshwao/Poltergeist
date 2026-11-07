# Poltergeist human acceptance checklist

Prepared from the current local dashboard source on November 2, 2026. This is
the test procedure; current observations and verdicts live in
[HUMAN_TEST_REPORT.md](HUMAN_TEST_REPORT.md). An unobserved manual check is
**NOT RUN**. Source inspection and automated checks are not human acceptance.
Only your explicit observation can change a manual result to PASS or FAIL.

**Application:** [http://localhost:5174/#overview](http://localhost:5174/#overview).
This acceptance instance uses a fresh, persistent profile under
`validation-evidence/human-acceptance-20261102/data`; existing experiments and
configuration are preserved. See the report for current prerequisite checks.

Use one numbered action at a time. After each meaningful checkpoint, stop and
tell the assistant what you saw, including the displayed status and any error.
Do not execute the whole checklist before reporting back. The assistant must
not operate your browser, manufacture observations, or mark a result from a
successful API request alone.

## Session rules and prerequisites

- Use the acceptance instance and its reported data directory. Preserve existing
  environment files, databases, saved experiments, model weights and recordings.
  Do not reset a database or delete existing history to get an empty screen.
- Give new items the prefix `HUMAN-` and retain their full scenario, run,
  registration, experiment and failure-report IDs. Existing records are not
  acceptance fixtures and must not be edited, deactivated or deleted.
- Start with **Synthetic test** and the **Baseline controller**. This requires
  the local backend/frontend, writable storage and their installed dependencies;
  it does not require CARLA, a GPU, model weights or an API key.
- CARLA, sensor agents, pretrained models, OpenAI and GitHub are separate gates.
  The assistant's environment report must say which are actually available.
  Record an unavailable gate as NOT RUN with its prerequisite, not as a working
  feature or a failed driving experiment.
- A completed run proves execution ended, not that the controller avoided a
  hazard. A collision can be valid recorded evidence. `Unknown`, missing values
  and partial coverage are not zero collisions, zero reaction time or safety.
- Synthetic actor motion and deterministic sensor fixtures do not establish
  physical map placement, occlusion, perception, real-time performance or
  real-world road safety. Record synthetic and CARLA observations separately.

### What the local browser interface offers

All rows are source-identified capabilities; **human status: NOT RUN**.

| Navigation/surface | What to exercise | Dependency or limit |
| --- | --- | --- |
| **Overview** | Setup readiness, explicit CARLA connection check, persisted analytics | **API connected** means API liveness only. A static readiness snapshot is not a successful run. |
| **Scenarios** | Four templates, stress presets, recipe JSON, validation, saved library, launch settings | Generation neither saves nor runs. Map validation at recipe time does not prove live geometry. |
| **Agents** | Trusted local package registration, frozen identity/configuration, static checks, deactivation | Paths refer to the backend computer. No browser upload, dependency install or weight download occurs here. |
| **Simulations** | History, progress/cancel, recorded recipe, metrics, telemetry, decisions, behavior and exports | Missing artifacts and legacy evidence must remain explicitly unavailable. |
| **Experiments** | Matched-seed comparisons, repeats, random search, reproduction and minimization | Optional Optuna needs its own installation. Failures can remain unconfirmed or unreproduced. |
| **Guided lab** | First experiment, scenario gallery, saved suites, import/export, optional feedback | A gallery illustration is not a recording of a run. Feedback is saved only with explicit consent. |
| **Discovery** | Bounded deterministic search/confirmation plan and separate execution approval | A description is an evaluation question; typed policies and budgets control execution. OpenAI is optional. |
| **Evidence Analyst / Regression Agent** | Evidence-linked observations and matched-version review | These do not invent private agent reasoning, missing measurements or statistical safety guarantees. |
| **Optional OpenAI assistance** | Configuration status, disclosure preview, deliberate provider call | Disabled/unconfigured is a legitimate state. Sending requires your explicit approval and configured limits. |
| **GitHub reporting** | Commit-bound disclosure preview and deliberate check publication | Optional local GitHub App configuration; a live publication is a separate acceptance gate. |

The Windows Companion/installer and its managed-model controls are separate
surfaces. Opening this local dashboard does not validate an installer, remote
account pairing, automatic model installation, signed updates or public hosting.

## First complete journey — Test A: emergency braking

**Status: NOT RUN.** Goal: create, save, execute, inspect, export and reopen one
ordinary experiment. Use this as the guided first session before the later tests.

| Step | Your exact action/input | Expected behavior and failure signal |
| --- | --- | --- |
| A1 | Open [the acceptance dashboard](http://localhost:5174/#overview). | The Overview workspace and main navigation appear. The top indicator should eventually say **API connected**. A blank page, broken layout or indefinite spinner is a finding. **Stop and report what appears.** |
| A2 | In **Setup readiness**, click **Refresh setup**. Read both **Synthetic test** and **CARLA live simulation**. | A dated snapshot explains required checks and next steps. CARLA can be missing/not checked without blocking synthetic work. An API liveness badge alone must not claim CARLA is ready. **Report the synthetic status and any failed check.** |
| A3 | Click **Scenarios**, then the **Sudden Braking** template card. Set **CARLA map** to **Town10HD_Opt (recommended)**, **Random seed** `101`, and **Experiment duration** `10`. | The selected card and fields reflect your choice. This map name remains recipe metadata during synthetic mode; no simulator should start merely from editing. |
| A4 | Set **Ego Speed** `12` m/s, **Lead Speed** `12` m/s, **Lead Distance** `20` m, **Brake Delay** `2` s and **Lead Deceleration** `6` m/s². Set **Precipitation** `0`, **Fog Density** `0`, **Sun Altitude** `70`. | Fields retain the values. Distances express intended actor positioning; a live built-in template resolves a suitable road layout later. Values outside physical constraints should show a useful field error. |
| A5 | Click **Generate recipe** (or **Regenerate recipe**). In **Recipe name**, enter `HUMAN-A Emergency braking seed101`. | **Recipe preview** shows the map, `10` s, `0.05` s timestep, ego/lead actors and a lead braking event at `2` s. Renaming requires validation again. No run exists yet. |
| A6 | Read **Full recipe JSON**, then click **Validate JSON**. | The badge becomes **Recipe validated** and the current normalized JSON is shown. Actor/event references must be consistent. A success badge for older edited JSON is a failure. **Report the badge or full visible error.** |
| A7 | Click **Save new recipe**. Copy its saved scenario ID and revision into the human report. | A saved record/revision appears. Generation/validation alone must not have silently saved an extra scenario. Saving errors must preserve the draft. |
| A8 | Under **Launch a simulation**, select **Synthetic test** and **Ego controller → Baseline controller**. Keep camera/LiDAR capture off. Click **Launch simulation** once. | An acceptance receipt identifies one run and the selected mode/controller. A real CARLA window should not be needed. If acceptance is uncertain, inspect **Simulations** history before retrying; do not blindly click Launch again. **Report the accepted run ID or error.** |
| A9 | Open the receipt's run link, or click **Simulations** and the `HUMAN-A…` name in **Saved executions**. | The run shows status/progress and eventually a terminal result or actionable error. Open **Recorded recipe and execution settings**: name, seed `101`, duration `10`, mode `Synthetic test`, controller and settings must match. A later editor change must not rewrite this snapshot. |
| A10 | Read **Safety metrics**, including collisions, minimum TTC/clearance, maximum deceleration, hard braking, stopping measurements and observation coverage. | Measurements have units and coverage. A result can legitimately contain collisions or unavailable measures; inspect the trace rather than expecting a preset score. Wrong mode, missing recorded settings without explanation, or unknown values shown as zero is a failure. **Report status, collision result and coverage.** |
| A11 | In **Measured motion**, move **Inspect frame on this page** and use **Next frames**. In **Inputs, decisions & outcomes**, select a **Frame …** near the braking event. | Speeds, brake/throttle/steer, frame/time, agent proposal, applied control and measured result remain tied to the selected frame. The lead's scheduled command is not proof the ego braked or perceived a hazard. Record the actual ego response, including no response if observed. |
| A12 | Open **Behavior review** when its review control is offered; inspect **Events & response intervals** and **Completed stopping episodes**. Follow an **Inspect anchor frame …** link. | Scheduled braking should be reviewable at its recorded event time, subject to the `0.05` s sampling resolution. Response intervals describe saved controls/motion, not perception latency or causal reasoning. Missing evidence must be explained. |
| A13 | In **Export this run**, click **Download JSON report**, **Download CSV evidence**, then **Download HTML report**. Use unique download names; open the HTML file yourself. | Each nonempty file identifies this run, mode, recipe/controller, settings and measured results. HTML should be readable offline. Raw recordings remain separate artifacts. A reported success with no file, corrupt file or another run's contents is a failure. **Report which files you could open.** |
| A14 | Refresh the browser. Reopen the same run from **Simulations**. Then go to **Scenarios → Open library**, find the saved name and click **Load**; if asked, choose **Load saved recipe**. | The original finalized result and saved recipe remain readable with the same IDs. Loading may replace only the current draft after confirmation. Reopening history must not start another simulation. **Report what persisted.** |

For A's outcome, record actual brake-control frames, observed collision count,
minimum TTC/clearance with coverage, any completed stopping episode, run ID and
exports. Do not pre-fill a braking time, collision result or safety verdict.

### A-CARLA extension — NOT RUN

Only after the assistant confirms the installed client/server prerequisites:
click **Overview → Check CARLA connection**. This contacts an existing server;
it does not start one. For a live launch, load A's saved recipe, explicitly select
**CARLA**, expand **Execution settings**, choose **Low — faster rendering** under
**CARLA graphics quality**, and inspect **Allow map reload if the CARLA map
differs** before deciding whether to enable it. Do not approve a map reload that
would destroy another experiment's actors. A running instance retains its quality.

Click **Launch simulation** once, follow the live run and repeat A9–A14. Initial
startup can take up to two minutes. A refused map/placement/version/connection
must be an actionable CARLA failure, never a silent synthetic fallback. Record
the actual adapter, map, resolved placement, client/server versions and outcome.
Do not assume synthetic and CARLA brake times or collision counts will match.

## Test B — pedestrian crossing

**Status: NOT RUN.** Use **Scenarios → Pedestrian Crossing**. Set map
`Town10HD_Opt`, seed `102`, duration `10` s, **Ego Speed** `8` m/s,
**Pedestrian Speed** `1.4` m/s, **Pedestrian Distance** `18` m,
**Pedestrian Lateral Offset** `4` m and **Crossing Delay** `1` s.
Keep precipitation/fog `0`, sun altitude `70`. Generate, name it
`HUMAN-B Pedestrian seed102`, validate and **Save new recipe**.

1. Select **Synthetic test → Baseline controller**, launch once and report its ID.
2. Follow A9–A13. Inspect the pedestrian's recorded start command near `1` s,
   ego control/motion, proximity/TTC coverage and collision outcome. Record what
   actually happened; do not require a collision or a successful avoidance.
3. If CARLA is available, repeat explicitly in **CARLA** and inspect recorded
   actor interactions and resolved placement. Save that different run ID/mode.

Success means the selected recipe executes or reports a useful failure and its
evidence matches its recorded inputs. Wrong pedestrian timing/identity, fabricated
proximity or a CARLA-labelled synthetic result is a failure. Synthetic success
does not prove a real pedestrian was visible or detected. **Stop for your result.**

## Test C — intersection conflict

**Status: NOT RUN.** Choose **Scenarios → Intersection**. Set map
`Town10HD_Opt`, seed `103`, duration `10` s, **Ego Speed** `8` m/s,
**Crossing Speed** `6` m/s, **Intersection Distance** `20` m,
**Crossing Distance** `15` m and **Crossing Delay** `0.5` s.
Use clear weather as in B; generate, name `HUMAN-C Intersection seed103`,
validate and save.

1. Launch **Synthetic test** with the baseline; then inspect saved actor/event
   identities, scheduled crossing command, applied controls, measured proximity,
   contact outcome and traffic-rule coverage. Keep the run ID and an HTML report.
2. Run the CARLA variant only when available. A suitable actual intersection and
   recorded transforms must support the live interpretation. A clear placement
   rejection is preferable to pretending invalid geometry was measured.

No particular right-of-way decision, stop or collision count is promised by this
development controller. Distinguish a controller weakness from a broken launch,
wrong recipe or inconsistent report. **Report your observed interactions and
coverage before assigning a result.**

## Test D — occluded pedestrian and sensor evidence

**Status: NOT RUN.** Choose **Scenarios → Occluded Pedestrian**. Use map
`Town10HD_Opt`, seed `104`, duration `10` s, ego `8` m/s, pedestrian `1.4` m/s,
pedestrian distance `18` m, lateral offset `4` m, crossing delay `1` s and
**Occlusion Gap** `4` m. Set fog/precipitation `0`, sun altitude `70`.
Generate, name `HUMAN-D Occlusion seed104`, validate and save.

1. Run the synthetic baseline first to check the three actors, crossing event,
   recorded controls and outcome. **Visibility/occlusion acceptance remains NOT
   RUN in synthetic mode**, even if the run completes.
2. For live visual evidence, select **CARLA**. Under **Execution settings** set
   **CARLA rendering → Enabled for this run**, **Camera width (px)** `320`,
   **Camera height (px)** `180`, and check **Capture camera frames (CARLA)**.
   Keep LiDAR off for this first recording. Launch once and record its live ID.
3. In the finalized run's **Artifacts & sensor frames**, click **Load capture
   manifest**, choose **Sample number**, then **Inspect sample**. Inspect samples
   before and after the recorded crossing event with **Next sample**. Expect
   explicit frame/time, encoding/size and a verified sample or an actionable error.
   These are individual raw camera frames, not an automatically playable video.
4. Describe what the camera actually shows: occluder, pedestrian, and first frame
   where you can distinguish the pedestrian. Optional recording uses a roof
   camera; its visibility is not automatically the same as an agent's input rig.
   If visibility cannot be judged, record it as unverified rather than inventing
   an emergence frame.
5. Inspect that run's decision/behavior timeline around the corresponding time.
   The baseline/starter uses privileged simulator state, including occluded
   actors. Early braking does not demonstrate visual perception. Testing a
   sensor agent requires a separately prepared registration, its explicit
   `driving_task`, rig and authoritative-control compatibility.

Failures include corrupted/misbound samples, claiming rendered perception from
fixture inputs, or presenting missing capture data as observed visibility.
Controller behavior and camera visibility are separate observations. **Stop and
report both, including missing data.**

## Test E — matched-seed repeatability

**Status: NOT RUN.** Complete A first and keep its exact normalized recipe JSON.

1. Click **Experiments → Experiment type → Agent comparison / repeated trials**.
   Select **Execution mode → Synthetic test — no CARLA**. Leave only **Built-in
   baseline** checked under **Agents, in reference order**.
2. Paste A's full validated recipe into **Scenario recipe JSON**. Enter **Matched
   seeds (comma separated)** `105` and **Repetitions per seed / candidate** `3`.
   Under **Simulation and scoring settings**, leave both JSON objects `{}` for
   this test. Inspect the Start button: it must indicate three executions in
   `test` mode. Click **Start compare · …** once.
3. Open the experiment's **Trial evidence → Review run** links. Verify all three
   runs use seed `105`, identical scenario parameters, baseline, mode, timestep
   and options/evaluation settings. IDs/timestamps will intentionally differ.
4. Compare recorded controls, frame counts, metrics and coverage, then click
   **Download comparison JSON**. Check the saved trial journal and observed
   denominators. Unknown or failed trials must not become zero-valued successes.

Synthetic repeats are intended to be deterministic under identical inputs; any
numerical/trace discrepancy needs the actual values and run IDs, not an assumed
explanation. Repeating in CARLA is a separate NOT RUN subtest: matching seeds
alone do not prove identical road layout, inference, frame identifiers or physics.
Record runtime/version/placement differences and measured variation before
deciding whether it is expected. **Report the three IDs and comparison result.**

## Test F — two supported controller configurations

**Status: NOT RUN.** This uses a rule-based SDK package, not a trained model.

1. Click **Agents**. In **Package directory**, enter
   `C:\Users\glass\.codex\worktrees\087f\Poltergeist\examples\agents\starter`.
   Leave **Worker Python (optional)** blank to use the prepared backend Python.
   Leave external-model paths empty and choose **Authoritative agent control**.
2. For configuration A, paste
   `{"target_speed_mps":8,"emergency_ttc_s":1.5,"minimum_clearance_m":2}`
   into **Configuration overrides (JSON object)**. Read and deliberately check
   **I trust this local agent code…**, then click **Register local agent**.
   Save registration A's full UUID.
3. Inspect that registration and click **Check static readiness**. It should
   identify the package/configuration and any file problem; even a pass does not
   establish successful dependency imports or inference.
4. Register the same known package again with configuration B:
   `{"target_speed_mps":5,"emergency_ttc_s":2.5,"minimum_clearance_m":3}`.
   Recheck the trust box and record B's distinct UUID. Do not edit existing
   registrations or source files to create this variant.
5. In **Experiments**, select **Agent comparison / repeated trials**, synthetic
   mode and A's recipe JSON. Uncheck **Built-in baseline**. Select registration A
   first, then B, matching their UUIDs: A must show **agent 0**, B **agent 1**.
   Enter seeds `106, 107` and repetitions `2`; keep settings `{}`. Inspect the
   Start button for **8 executions** before starting once.
6. Read all trial identities/configurations, completion/coverage counts and the
   **Agent 1 minus agent 0** paired-difference section. Open a matched pair's
   **Reference run** and **Compared run**, inspect controls, and download the
   comparison JSON.

Success means frozen configurations and matched cells are represented faithfully.
Differences are descriptive; identical outcomes are allowed. No automatic
winner, superior perception or safety certification should be invented. Missing
registration/dependency files must cause a clear error and preserve selection,
never silently substitute the baseline. **Report UUIDs, operation ID, one matched
pair and the displayed denominators.**

## Other implemented workflows

### November 5–6 native trained-model comparison follow-up

Open [the saved AIM/TransFuser suite](http://localhost:5175/#lab/suite-run/97d512f2-8505-4e9c-a7f0-190c33bc3ff2).
The tester explicitly confirmed completed status and both named cases, retained
Peak deceleration selection, the clear-case worsening result (3/3 pairs, mean
about +3.667 with threshold 1 m/s² / minimum 3), unavailable severity under the
default policy, the JSON download and the saved comparison after refresh.
The report records each scoped PASS; downloaded file contents and recovery
after restarting the backend have not been inspected by the tester. These
observations do not pass the separate synthetic Test F procedure above.

The tester also opened native candidate
`1033955b-efe0-4003-9682-2a01296909dc`, confirming completed/CARLA/121 frames,
then confirmed its Analyst summary and peak citation (about 26.4 m/s²,
frame 815261 / 1.200 s), then confirmed that following the link selects that
frame/time and about 26.351 m/s² in Measured motion. The tester also confirmed
the first native decision, the verified camera preview and the verified LiDAR
point count. This workspace contains engineering runs and is separate from
first-time Guided lab acceptance and the earlier synthetic journey.

Each item is a separate **NOT RUN** test. Perform it only after the preceding
core journey and report its result before continuing.

| ID | Concrete manual action | Expected behavior / failure condition |
| --- | --- | --- |
| G1 — guided journey | **Guided lab → First experiment**; choose a starter scene, choose **Synthetic check**, then use its explicit run button. Follow **Open full run evidence**. | Scene browsing starts no execution. A real run ID, recorded outcome/coverage and functioning evidence link appear. The illustration is not presented as run footage. |
| G2 — gallery/suites | **Guided lab → Scenario gallery**; add two chosen scenes to **Saved suites**. Set **Suite name** `HUMAN-G2 Two scenes`, synthetic mode, baseline, matched seeds `111`, repetitions `1`; inspect a total of two executions, then **Save experiment suite** and its explicit launch control. | Save/launch are distinct; the launch freezes that saved revision. Reopening **Past results** retains each scene and its own run links/denominators. |
| G3 — suite import/export | Export only G2's suite JSON, then **Import suite JSON** and select that file; when replacing an unsaved draft, first choose **Keep current draft**, then repeat and deliberately **Replace unsaved draft**. Save as `HUMAN-G3 Imported copy`. | Import preserves recipe/settings and exposes unavailable registrations; it does not execute or overwrite existing suites automatically. The browser does not advertise general run-database restoration. |
| G4 — feedback | In a finished guided run, choose **Can you explain the recorded outcome?**, write `The frame links helped me find the recorded braking event.`, and submit only if you deliberately choose **Save this feedback with the experiment**. | No feedback is saved before consent. This text is a suggested input, not a recorded human observation. You should replace it with your actual opinion. |
| H — bounded discovery | **Discovery**: goal `Find repeatable hard braking in a synthetic sudden-braking scene`; failure **Measured hard braking**; family **sudden braking**; mode **Synthetic workflow check**; baseline; duration `10`, seed `120`, minimum severity `0`. Budget: total executions `3`, search candidates `1`, candidates to confirm `1`, confirmation repetitions `2`, required matches `2`, wall-clock `180`, simulated-time `30`. Keep parameter/task objects empty. Click **Build a plan for review**. | Building a plan starts no simulations. Review frozen settings/limits and fingerprint. Only after your separate checkbox approval and **Approve and start discovery** may executions begin. It may honestly end **No failure confirmed**; no forced positive result is required. |
| I — random search | **Experiments → Search for failures**, synthetic, baseline only, sudden-braking template, **Random**, search seed `121`, maximum executions `2`; fixed parameters `{"brake_delay_s":2}`; other JSON `{}` and no driving task. Inspect and click its **Start search · …** button once. | At most two ordinary saved trials, with outcomes and coverage. A best score is not automatically a confirmed failure. Do not select Optuna unless diagnostics confirm the optional package. |
| J — reproduce/minimize | Only if you observed an eligible positive failure in A–D: save it via **Save a failure report**, choose the actual **Observed failure kind**, and describe the run ID/frame. In **Failures**, open it and use **Run reproduction trials** with repeats `2`/required matches `2`; then **Minimize this failure**, repeats `2`, maximum executions `6`. | Source controller/settings are inherited and retained. Reproduction may fail or remain unknown; reduction is not global minimality. If no eligible failure exists, leave J NOT RUN rather than inventing a report. Changing **Recorded reproduction evidence** alone does not run a trial. |
| K — evidence review | In a finalized run click **Analyze recorded evidence** under **Evidence Analyst**, inspect a cited frame, then **Download evidence analysis**. | Every claimed observation points to stored evidence; missing coverage and unverified hypotheses remain explicit. Agent diagnostics do not become claimed internal reasoning. |
| L — regression review | In G2/G3, use two prepared frozen registrations in reference order, collect four matched pairs (seeds `106, 107`, repetitions `2`), then **Regression Agent → Review version regression**. | The default policy requires at least three observed pairs. Only observed compatible pairs support a verdict. Insufficient evidence should remain inconclusive; no new execution starts merely from reviewing. |

### M — managed camera and Hugging Face controls — NOT RUN

The current source also includes **TransFuser RGB + LiDAR (CPU)**. Use a verified
current QA bundle/source build for that check; an older installed Companion is
not updated by pulling Git. Its separate [policy guide](transfuser-policy.md)
documents the genuine checkpoint, assistance, timing and unresolved distribution
terms. Manual follow-up, one prompted action at a time, should verify:

- Selecting the fusion model persists the choice and starts no download/run.
- Preparation reports progress and verifies actual inference after warmup before
  **AI ready**. Missing weights/dependencies remain actionable failures.
- Explicit enablement creates the selected registered controller. A 20 Hz,
  framework-route run retains the released camera and 64-channel LiDAR rig.
- Native input frames/hashes, learned/held-prediction origins and proposed/applied
  controls are visible in recorded decisions. Synthetic fixture inputs remain
  clearly distinguished from native CARLA measurements.
- Export and reopening retain the same model/run identity and unknown metrics.

These are prepared checks, not manual passes. No model-internal reasoning,
general driving quality, 20 Hz wall-time performance or checkpoint redistribution
rights follow from an engineering run.

These controls live in the Windows Companion, not this browser's Agents page.
Before testing, the assistant must identify a compatible current build and a
separate acceptance profile; never remove an existing user's model environment.

1. In that prepared Companion, open **Manage AI…**, select **AIM camera**, and
   inspect the next-step guidance. **Install / resume AI** prepares the supported
   CPU environment and pinned weights; downloading weights alone must not claim
   inference readiness. Record actual installation/readiness messages and errors.
2. Once prerequisites pass, a separate rendered CARLA run needs the documented
   front-camera calibration, framework driving task and `0.05` s timestep.
   Record its run ID, delivered input, controls, measured motion and deadlines.
   Synthetic fixtures cannot establish camera perception. Trained LiDAR and
   managed CUDA are not delivered by this integration.
3. Select **PlanT 2.0**, then **Check pinned source**. Inspect the immutable
   revision, declared size/hash/license and any actionable network error. A
   source check must not claim saved weights or successful inference.
4. Only in the disposable acceptance model profile, use **Download / resume**,
   **Pause download / check**, close/reopen after pausing completes, and resume.
   Use **Check saved weights** after completion. Record whether the same owned
   partial resumes and verification completes. Test **Repair weights…** and
   **Remove weights…** only on those newly acquired acceptance assets.
5. Public PlanT downloads need no account. **Configure account…** and **Forget
   account** are optional human gates; never put tokens in chat or evidence.
   Anonymous download success does not validate gated-account access. PlanT uses
   privileged structured simulator data and does not establish camera perception.

Record each meaningful result before moving to the next action. Installation,
inference, real driving, account access and independent-machine acceptance are
separate observations. Earlier local validation is not a result for this session.

## Usability, invalid inputs and recovery

All cases are **NOT RUN**. Use only new acceptance drafts/records. Save or copy
the original JSON before deliberate invalid edits. Expected rejection is a
successful negative-path test only after you personally observe it.

| ID | Action | Expected result and recovery |
| --- | --- | --- |
| U1 | In a fresh sudden-braking draft, empty **Random seed**, then enter `-1`, then `1.5`; try Generate each time. | Useful whole-number/range error and no run/save. Restore `101`. |
| U2 | Set **Experiment duration** `1` with **Brake Delay** `2`; generate/validate. | Actionable event-after-end rejection. Restore duration `10`; old validated JSON must not be falsely marked current. |
| U3 | Set **Lead Distance** `0`, then restore `20`; set **Precipitation** `101`, then restore `0`. | Field/domain errors prevent accepting impossible/out-of-range inputs without erasing other edits. |
| U4 | Remove the final `}` from a copied **Full recipe JSON**, then **Validate JSON**. Restore the original complete JSON and validate. | Clear syntax error; no false validated badge or enabled launch for the invalid draft. |
| U5 | Edit only **Recipe name** after validation; inspect save/launch state before revalidating. | The edit invalidates current validation; revalidating restores a usable draft. |
| U6 | Save a fresh draft, change its name, then **Update saved recipe**. Make a separate **Save a copy**. Try loading another record with unsaved edits and choose **Keep current draft**. | Explicit actions preserve the intended record/revision and unsaved draft. Never use a preexisting user record for this test. |
| U7 | **Agents**: try a missing package `C:\Poltergeist-Acceptance-Missing\agent`, or configuration text `[]`; leave trust unchecked for one submission. | An actionable validation/registration rejection; no agent code executes or silently becomes registered. Restore the known starter path and `{}`. |
| U8a | Register `C:\Users\glass\.codex\worktrees\087f\Poltergeist\validation-evidence\human-acceptance-20261102\fixtures\missing-dependency`, after deliberately approving trust for this known fixture only. Select its registration for a new synthetic A run. | Static package checks may pass; loading must fail on the intentionally absent `poltergeist_acceptance_missing_dependency_20261102` import. The selected identity and useful failure must be retained, without baseline fallback. Do not install that dependency. Manual result remains NOT RUN until observed. |
| U8b | Try registering the sibling `fixtures\missing-model` package. | A missing `missing-weights.bin` asset error should reject registration. No existing model file needs to be renamed, removed or downloaded. |
| U9 | Start a new acceptance run with duration `300` s and **Maximum ticks** `10000`; while still queued/running click **Cancel run** once. | **Cancellation requested**, then eventual terminal cancellation/partial evidence after cleanup. If the run finished before the click, cancellation was not exercised; do not label that a failure. |
| U10 | During a new active run, refresh the browser at its `#simulations/<id>` URL. | The same saved run/progress reconnects; refresh must not launch a duplicate. Record before/after IDs and state. |
| U11 | Click Launch twice rapidly for a new acceptance draft. | Pending submission should be disabled and accepted identities should be clear. A later deliberate click after completion is a new request, not a promised idempotent replay. On an uncertain response check history before another submission. |
| U12 | Arrange with the assistant to stop only its acceptance backend. Then click **Refresh API connection** and try a history/report read. After the assistant restarts that same data-backed instance, click **Retry connection** and reopen the recorded ID. | Clear connection error, preserved draft/history after recovery, no fabricated stale-success state. You report both sides; the assistant must not reset storage or stop unrelated processes. |
| U13 | Attempt a CARLA launch only when the assistant has confirmed the isolated acceptance CARLA server is unavailable and auto-start is disabled for that test. | Connection/startup error with retained CARLA mode; no synthetic fallback. Do not kill an unrelated simulator or alter shared configuration yourself. |
| U14 | In **Simulations**, filter **Status → running** when no acceptance run is active; then restore **All statuses**. | Honest empty-filter message, not erased global history. A truly empty-history test needs a separately supplied fresh data profile; never clear the existing database. |
| U15 | In **Guided lab → Saved suites → Import suite JSON**, select `C:\Users\glass\.codex\worktrees\087f\Poltergeist\validation-evidence\human-acceptance-20261102\fixtures\invalid-suite.json`. | Useful import error; current saved suite/history untouched. The fixture is deliberately incomplete JSON. For scenarios, invalid JSON is tested in the recipe editor, not a nonexistent upload button. |
| U16 | Start a report download, then **Cancel download** if still active; retry the format. For an offline download failure use the controlled U12 outage. | Truthful cancellation/error, retry path, no success notice for missing or truncated files. Browser download-directory permissions must be distinguished from server export failure. |
| U17 | At normal browser zoom use keyboard Tab/Shift+Tab through navigation, fields, launch and report controls; resize the browser yourself to a narrow viewport. | Focus is visible, labels remain readable, controls reachable and wide evidence tables scroll instead of covering actions. Report the viewport/zoom and exact obscured control if broken. |

## Optional external connections — never required for the core journey

**O — OpenAI: NOT RUN.** In **Discovery**, click **Open language assistance**, then
**Refresh OpenAI configuration**. If disabled/unconfigured, expect an explanation
and working deterministic discovery; do not enter keys in the browser, JSON or
report. Only if the assistant confirms private configuration and you choose to
test a provider call: enter the question `Suggest a sudden-braking experiment
that checks recorded hard braking.`, output-token limit `256`, then **Preview
OpenAI disclosure**. Inspect the exact outgoing payload, model and usage limits.
Preview must not make a provider call. Check the explicit disclosure approval
and click **Send approved OpenAI request** only if you intend that external
request and possible usage cost. A suggested plan still needs its own discovery
execution approval. Invalid/provider failures must remain failed records, not
invented model responses. Download the saved assistant record if tested.

**P — GitHub: NOT RUN.** Only after a saved suite contains two frozen registered
versions, a regression review exists, and a separately configured GitHub App is
confirmed: open **GitHub reporting** in that review. Use the reference/candidate
full 40-character commit SHAs and package/model pin paths supplied for this
acceptance case. Leave disclosure categories unchecked until you deliberately
select them. Click **Verify commits and preview GitHub check**, then inspect
**Exact outgoing GitHub check JSON (no token)**. Preview must not publish.
Publishing requires your separate approval and **Publish approved GitHub check**;
record its actual external check link only if you personally verify it. Missing
configuration or unavailable commit binding keeps this test NOT RUN. No secret,
issue comment, repository edit or PR mutation is part of this checklist.

## Performance and stability observations

**PERF — NOT RUN.** Do this while performing real accepted runs; do not infer
numbers from synthetic duration or previous automated receipts.

1. Before Test E, open Windows Task Manager yourself. Record date/time, backend
   and browser process memory/CPU, and whether a real CARLA process is present.
   Record GPU utilization/VRAM only when the relevant real GPU process is active;
   otherwise write **not exercised**.
2. For each of E's repeated runs record wall-clock launch/finish times separately
   from simulated seconds, observed UI responsiveness, memory after completion,
   terminal status and run ID. Inspect hover/frame controls and navigation while
   a run is active. Repeat the batch only deliberately, keeping every operation.
3. Note sustained growth, frozen controls, long queues or failures with their
   intervals/process names. A transient peak is not established memory leakage.
   Browser Network timing can be captured by you if useful; avoid screenshots
   containing secrets. CARLA/GPU throughput needs a separate real CARLA run and
   its measured settings. A responsive synthetic dashboard is not a GPU benchmark.

## Record each result and decide readiness

For each completed human checkpoint, give the assistant:

- Case/step ID, timestamp, exact URL/page, mode and browser/version.
- Exact inputs plus full scenario, run, registration or operation IDs.
- Expected behavior and your actual observation, including any visible error.
- Screenshot/log/download reference if you chose to capture one; never invent
  evidence that was not captured. Keep secrets out of report attachments.
- Your explicit result: PASS, FAIL or NOT RUN/BLOCKED with the missing prerequisite.

For confirmed issues, the assistant records reproduction steps, severity
(Critical/High/Medium/Low), evidence-backed suspected cause, the smallest proposed
fix, automated regression results separately, and **human retest: NOT RUN** until
you repeat the affected action. Data loss, false evidence or unintended disclosure
need priority over cosmetic findings.

Release acceptance remains **unproven** until the human report contains your
observations across the applicable installation, synthetic, agent, evidence,
export/persistence and recovery gates. CARLA, sensor/perception, external-provider,
independent-machine and distribution claims stay separate and explicitly limited
when they have not been exercised. No checkbox in this document is a completed test.
