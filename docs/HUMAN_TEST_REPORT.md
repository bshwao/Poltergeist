# Poltergeist human testing report

Session opened November 2, 2026. **Acceptance is in progress; release readiness
is not established.** Manual results below come only from the tester's explicit
answers. Automated and source-inspection evidence are recorded separately.

## Application and preserved data

- Source: GitHub `main` at `a6f1044dad81028dcc33eaa8d1c5931995bb830c`, checked
  out on `codex/human-acceptance-validation`. The three incoming commits add
  managed-model UI polish, optional OpenAI assistance and GitHub regression checks.
  A subsequent fetch found `894d826` on `main` (dashboard loading/integration
  polish). Its overlap was reviewed; application code remains at `a6f1044`
  during this first experiment. The recorded preflight and manual observations
  do not establish acceptance of that newer frontend. Update and relevant
  rechecks follow the current experiment checkpoint.
- Dashboard: [http://localhost:5174/#overview](http://localhost:5174/#overview).
  API: [http://127.0.0.1:8001](http://127.0.0.1:8001).
- Checkout: `C:\Users\glass\.codex\worktrees\087f\Poltergeist`.
- Dedicated persistent acceptance storage:
  `validation-evidence/human-acceptance-20261102/data`; SQLite is
  `data/database/poltergeist.db` under that session. Both storage and database
  settings were overridden in process environment, with exact dashboard CORS
  origins. Existing `.env` files, normal storage, model assets and recordings
  were preserved. This fresh history intentionally excludes previous experiments.
- Starts use `.venv\Scripts\python.exe -B -m backend` and frontend
  `npm.cmd run dev -- --host 127.0.0.1 --port 5174 --strictPort` in hidden
  background processes. Launch identity, commands and logs are retained in
  `validation-evidence/human-acceptance-20261102/session.json`. Saved process IDs
  are historical identities, not permission to stop a reused PID.
- No browser automation or native window control was used for this session.

## Verified environment and feature boundaries

| Item | Agent-observed state | Acceptance implication |
| --- | --- | --- |
| OS / CPU / RAM | Windows 11 Home 10.0.26300; Ryzen 7 260, 8 cores / 16 logical processors; 15.31 GiB RAM | This laptop only; no independent-machine acceptance |
| Python | Existing project `.venv`, Python 3.12.15; SQLite 3.53.1 | Core environment available; no venv replacement |
| Node / npm | 24.22.0 / 11.19.1 | Meets frontend engine requirements |
| GPU | RTX 5060 Laptop, 8151 MiB VRAM, NVIDIA 582.05; integrated Radeon 780M also present | Inventory only; no live simulator performance conclusion |
| Core prerequisites | `pip check` and `scripts.doctor --frontend --hardware` exit 0 | Static/dependency preflight only |
| Synthetic | Static readiness says ready to try; separate installation validator passed | User execution/evidence acceptance still required |
| CARLA | Client package 0.9.16 present; launcher exists at `C:\Users\glass\Downloads\poltergiest\CARLA_0.9.16\CarlaUE4.exe`; acceptance backend has no configured/discovered executable | Current dashboard may correctly report missing installation configuration; native client/server match, idle world and live execution remain untested in this session |
| Optional simulation/search packages | NumPy present; OpenCV, Optuna and SciPy absent | Do not advertise the full simulation/optimization extras as installed. Random search is separate from optional Optuna |
| Baseline / starter / sensor pilot | Implemented rule-based controllers | Do not describe these as pretrained perception |
| S16 AIM / S17 Hub | Implemented and previously locally validated, with separate managed CPU environments and pinned assets | Current acceptance installation/inference/driving/account checks remain NOT RUN; no trained LiDAR or managed CUDA support |
| OpenAI / GitHub | Explicitly disabled for this session | Local deterministic Analyst/Regression/Discovery remain separate; no live provider call or publication claimed |
| Replay | Saved telemetry frames and raw sensor sample inspection | No encoded video player or reconstructed-world playback is implemented |
| Companion / hosted / distribution | Separate application and release surfaces | This local browser session does not accept the installer, account pairing, public hosting, signing or independent hardware |

GPU readings in static tools and the automated synthetic validator are
device-wide point snapshots. They are not attributable CARLA load, peak VRAM,
perception throughput or a driving benchmark.

## Automated supporting evidence

All paths in this table are under the ignored local evidence directory
`validation-evidence/human-acceptance-20261102/`. They contain no manual verdicts.

| Check | Result | Receipt and scope |
| --- | --- | --- |
| Dependency consistency | PASS, exit 0 | `dependency-check.log`, `diagnostics.json` |
| Static doctor | PASS, exit 0 | `doctor.log`; no world changes, inference or package installation |
| Initial API/frontend HTTP probes | PASS, HTTP 200 | `http-preflight.json`, `health.json`, `readiness.json`; not browser acceptance |
| Connection investigation | Health/templates HTTP 200; correct CORS origin and served API URL | `a1-connection-diagnostics.json`; browser's initial failure is retained below |
| Isolated synthetic installation workflow | PASS | `automated-installation/validation.json`; registration, matched executions, sensor fixtures, decisions, analysis, report exports and persistence reopening; separate private data |
| Frontend existing tests | 393 PASS, 0 fail/skip | `frontend-tests.log`; Node tests, not browser or human acceptance |
| Contract consistency | PASS, exit 0 | `frontend-contracts.log`, `frontend-checks.json` |
| Frontend production build/typecheck | PASS, exit 0 | `frontend-build.log`, `frontend-checks.json` |
| Focused backend tests | 516 PASS, 2 skipped, exit 0 | `backend-preflight.log`, `backend-preflight.xml`, `backend-checks.json`; core/API/evidence/agent/model/provider boundary tests; no native GUI or live CARLA acceptance |
| Latest-main integration CI (`af964a1`) | All five jobs PASS: dashboard and Windows/Linux on Python 3.12/3.14 | [Workflow run](https://github.com/bshwao/Poltergeist/actions/runs/41506657613), `github-ci-jobs-af964a1.json`; includes `894d826` in a separate review checkout; automated evidence only, not current human-browser acceptance |

The backend skips are the optional Optuna case (package absent) and a native
symbolic-link case (privilege unavailable). Neither is counted as a pass. The
selected 22 files cover configuration/database/readiness, scenario/simulation
APIs, final integration, registered execution, reports, optimization service,
guided experimentation, Analyst/Regression, AIM camera, Hub/model lifecycle,
sensor runner/manager and optional provider transport boundaries. This is a
focused subset, not a claim that the entire repository test suite ran.

The automated synthetic validator's operation/run IDs and full settings are in
its `validation.json`, `accepted-operation.json` and `comparison.json`; they
are not the tester's first experiment and do not appear in acceptance history.

Disposable U8/U15 inputs are now prepared under this session's `fixtures/`:
`missing-dependency`, `missing-model` and `invalid-suite.json`. Static preflight
confirmed valid dependency-fixture package metadata and absent intentional import,
the missing-model asset rejection, and malformed suite JSON (`fixtures/preflight.json`).
No fixture was registered or launched through the acceptance API. These checks
prepare the negative paths; their human outcomes remain NOT RUN. Installed
dependencies and existing model files were not altered.

## Human observations and current checkpoint

The tester has not supplied a browser name/version or screenshots. No screenshot
or precise user-event timestamp is fabricated. Observations below were reported
in chat on November 2, 2026; assistant diagnostics have their own UTC timestamps.

| Checkpoint | Tester observation | Manual result |
| --- | --- | --- |
| A1 initial open | “Page loads but API is disconnected” | FAIL for the initial connection checkpoint; page rendered |
| A1 retry | After clicking Retry connection: “It now says API connected” | PASS on retest; initial failure retained as H01 |
| A2 readiness | After Refresh setup: “Synthetic is ready to try” | PASS for visible static readiness only |
| Navigation to Scenarios | “Yes, the templates are visible” | PASS for this navigation checkpoint |
| Select Sudden Braking | “Yes, all five fields appear” | PASS for template field visibility only |
| A3–A4 test input entry | “All values are entered and retained” | PASS for the specified form values |
| A5 generation | “Yes, the generated recipe matches” | PASS for the requested map/duration/actors/event preview |
| A6 named-recipe validation | “It says Recipe validated” after the specified rename/validation action | PASS for displayed validation |
| A7 save | “The recipe saved successfully” | PASS for displayed save confirmation |
| A8 synthetic launch | “Run accepted appears” | PASS for visible admission receipt only |
| A9 run detail/status | Tester reports “Completed” after Track this run | PASS for opening the completed run |
| A9 frozen settings | “All recorded settings match” for the requested name, seed, duration, mode and controller | PASS |
| A10 safety metrics and coverage | Tester compared the cards against the saved result and answered “All values and coverage match”: collisions 0, minimum TTC 0.98 s, minimum clearance 1.85 m; observation counts 201/201 and no gaps | PASS for displayed metrics/coverage consistency; synthetic only |
| A11 measured motion and decisions | Next checkpoint: inspect a saved telemetry frame and the decision timeline | NOT RUN |
| A12–A14 behavior review through export/reopen | No observation yet | NOT RUN |

Confirmed input values: Town10HD_Opt, seed 101, duration 10 s, ego/lead speeds
12 m/s, lead distance 20 m, brake delay 2 s, lead deceleration 6 m/s²,
precipitation/fog 0 and sun altitude 70. Name: `HUMAN-A Emergency braking seed101`.
After the tester's save confirmation, an agent read of the acceptance API found
saved scenario `33199f39-1a7b-42c0-8995-e07d29774b98`, revision 1, with that
name and seed (`human-scenarios.json`). This ID is independently collected
metadata, not an invented user observation. After the launch confirmation, an
agent API read identified run `ae0d8087-8b5f-41d4-a85e-26e4ea0ee26c` with the
same named recipe and expected inputs, `test` mode and `baseline` controller.
The stored record is completed with 201 telemetry frames and 10 simulated
seconds (`human-run-history.json`). These supporting API observations do not
mark the user's run-detail, timeline or report tests as passed. The stored
start/finish timestamps span about 1.95 seconds; this is a synthetic execution
record, not CARLA/GPU performance or the user's perceived UI latency.

The tester confirmed the frozen name, seed 101, duration 10 s, Synthetic test
and baseline controller, then personally confirmed the displayed metrics and
201/201 coverage against the saved record. This is not a real-world safety
conclusion. Next pending checkpoint: inspect **Measured motion**, then the
**Inputs, decisions & outcomes** timeline. Ask for the observed result before
advancing a manual verdict.

## Required manual coverage

Use the [concrete checklist](HUMAN_TEST_CHECKLIST.md), one meaningful action at a
time. Its expected outcomes are test criteria, not observations.

| Requirement / case | Planned evidence | Current human status |
| --- | --- | --- |
| A emergency braking / complete first journey | Saved recipe/revision, run ID, progress, recorded settings, controls/motion, safety coverage, JSON/CSV/HTML exports, reopen | In progress; saved recipe and launch receipt confirmed; run detail/evidence/export observations pending |
| B pedestrian crossing | Event timing, proximity, controls and measured outcome | NOT RUN |
| C intersection conflict | Actor interactions, timing, controls and measured outcome | NOT RUN |
| D occluded pedestrian | Synthetic plumbing separately from rendered CARLA visibility/capture evidence | NOT RUN |
| E repeated identical conditions | Three synthetic trials, equal frozen settings/seeds, trace/metric comparison; CARLA variation assessed separately | NOT RUN |
| F two supported configurations | Two fresh trusted starter registrations, eight trials/four matched cells, honest coverage and paired differences | NOT RUN |
| Guided lab / galleries / suites / import / feedback | Explicit save/run/consent, durable suite revision, safe import and export | NOT RUN |
| Discovery / search / reproduction / minimization | Reviewed frozen plan and separate approval, bounded trial accounting, genuine failure evidence or honest no-confirmation result | NOT RUN |
| Analyst / regression review | Exact evidence links, frozen versions, compatible pairs, unavailable coverage | NOT RUN |
| Invalid inputs / missing packages/models | Actionable rejection, preserved draft/identity, no controller fallback | NOT RUN |
| Cancellation / repeat clicks / refresh | Actual active run IDs, terminal state and retained evidence, duplicate-admission observations | NOT RUN |
| Backend loss / restart recovery | Deliberate stop/restart of owned acceptance service only, same database and reopened IDs | NOT RUN; initial incidental retry is not this test |
| CARLA execution / connection failure | Verified matching server, idle world, recorded adapter/placement/settings, useful failure without synthetic fallback | NOT RUN |
| S16 / S17 managed-model surfaces | Separate acceptance model profile, lifecycle, inference and real camera inputs/control; optional account gates | NOT RUN |
| Optional OpenAI / GitHub | Configuration-dependent deliberate disclosure/provider/publication observations | NOT RUN / disabled |
| Performance and repeated-run stability | Real user-run wall time, UI observations, per-process CPU/RAM samples; GPU only during relevant actual workload | NOT RUN |

## Prioritized findings

### H01 — Initial dashboard could not connect, recovered on retry

- **Severity:** Medium. The first workflow was blocked until retry; recovery was
  successful. Cause remains open, rather than claiming a fixed product defect.
- **Environment/test:** This Windows laptop, acceptance URL, initial A1.
- **Steps:** Open the acceptance dashboard; inspect the API indicator. The
  tester reported a loaded page with a disconnected API. Click Retry connection.
- **Expected:** Available backend is connected, or a useful recoverable error
  explains why it is unavailable.
- **Actual:** Initial disconnected indicator; connected after one explicit retry.
- **Evidence:** The two direct tester answers above; local subsequent health and
  template requests returned 200 with matching CORS; served Vite module points
  to `http://127.0.0.1:8001` (`a1-connection-diagnostics.json`). No browser console
  error or screenshot was supplied.
- **Suspected cause:** Undetermined. These checks rule out a currently dead
  backend or an obvious configured-origin mismatch; they do not establish the
  original browser request failure.
- **Fix/retest:** No code change justified yet. Human retry PASS. If it recurs,
  capture the visible error, URL/footer and browser/network details before a
  targeted fix. Do not restart healthy services solely from a stale indicator.

### H02 — Dashboard guide omitted implemented navigation/workflows

- **Severity:** Low; source/documentation finding, not a human test failure.
- **Steps/environment:** Compare `docs/dashboard.md` introduction on source
  `a6f1044` with `frontend/src/App.tsx` and current experiment routes.
- **Expected/actual:** Guide should list implemented pages and workflows. It
  listed five pages and said optimization controls were unavailable; source
  implements eight pages and experiment controls.
- **Cause/fix:** Stale documentation. Updated the page list, implemented workflow
  overview and reproduction navigation without changing application behavior.
- **Evidence/retest:** Local documentation diff and source inspection. Human
  navigation acceptance remains limited to the observed pages above.

### H03 — Agent guide retained obsolete Phase 9 restrictions

- **Severity:** Low; source/documentation finding, not a human test failure.
- **Steps/environment:** Read the end of `docs/agent-registry.md` on `a6f1044` and
  compare with implemented agent-aware experiment support.
- **Expected/actual:** Document current support and boundaries. The obsolete
  restriction contradicted implemented comparisons, reproduction/minimization.
- **Cause/fix:** Stale paragraph. Replaced it with implemented support while
  retaining frozen-identity, file/dependency and reproducibility limitations.
- **Evidence/retest:** Local documentation diff/source inspection; manual agent
  workflow and human retest NOT RUN.

No Critical or High defect has been established by this session's available
evidence. This is not proof that none exist. Retain failed receipts and original
observations when a later retest passes.

## Release assessment and remaining work

### November 5 human S15 comparison check

This is a separate engineering-results workspace on source `be11f1d`, served at
`http://localhost:5175` with API `http://127.0.0.1:8011` and preserved storage
`validation-evidence/s10-s19-20261105/data`. It does not replace the earlier
assisted journey or establish first-time Guided lab acceptance.

Suite run: `97d512f2-8505-4e9c-a7f0-190c33bc3ff2`.

| Manual action | Tester observation | Result and scope |
| --- | --- | --- |
| F-N1 open the permanent trained-model suite comparison | “Completed suite and both cases are visible” after opening the supplied link, expecting completed status, Clear lead braking, Wet dusk lead braking and AIM/TransFuser comparison | PASS for opening the completed suite and case visibility only |
| F-N2 select Peak deceleration in Regression Agent | “Peak deceleration stays selected” after the instruction to retain the 1 m/s² threshold and minimum 3 matched pairs | PASS for checkbox selection retention only; report values not yet manually verified |
| F-N3 review displayed regression values and missing measurements | Awaiting observation | NOT RUN |
| F-N4 export and reopen the saved comparison | Awaiting observation | NOT RUN |

Native execution, independent metric/report audits and CI are engineering
evidence recorded separately. They do not mark F-N3–F-N4 or earlier A11–A14 as
manually passed.

### November 5 engineering findings under the adjusted S10–S19 plan

These are engineering reproductions and checks, not new personal PASS results.
The [S10–S19 record](validation/s10-s19-acceptance-20261105.md) retains exact
plans, failed attempts, source/trace fingerprints and regression receipts.

- **E04 — High: native localhost connection failure.** The owned Epic CARLA
  server answered at `127.0.0.1` but timed out at `localhost`, causing an admitted
  discovery attempt to fail with zero frames. The isolated acceptance runtime
  now uses the working address, with a passing native readiness check and an
  actual completed 201-frame CARLA run. Ordinary environment files were retained.
  Human retest: NOT RUN.
- **E05 — High: observed braking rejected because overall severity is missing.**
  Run `593baf0c-23b6-4a57-87eb-dbe32472360e` has four hard-braking episodes and
  201/201 braking observations, while an unchecked initial traffic-rule frame
  makes severity null. Discovery previously discarded the positive named evidence.
  `failure-policy-v2` now permits an explicit kind with no severity filter to use
  its own observations, preserves null scores through saved reports/UI and
  still requires valid severity for a positive severity threshold. The original
  journal is unchanged. Automated regression and the approved six-run Epic
  batch pass: two candidates, two matching confirmations each, within budget.
  Human retest: NOT RUN.
- **E06 — Medium: peak-deceleration link opened the review-window start.**
  The native peak occurs at frame 59275 / 0.20 s; the original Analyst linked
  frame 59271 / 0 s. Braking/behavior evidence now retains the actual witness,
  including both frames for a velocity-derived interval. Regression cases and
  the same saved native trace verify the corrected link; source and telemetry
  fingerprints are unchanged. Human retest: NOT RUN.

**Not accepted for release.** Local preparation, automated synthetic evidence
and the first human navigation/readiness checks support continuing manual QA.
They do not yet prove the requested complete scenario-to-export journey,
agent comparison, recovery, real CARLA/sensor behavior, performance or all
implemented feature gates. Six realistic cases are prepared; their unobserved
outcomes remain NOT RUN. Broader hardware, independent users, installed visual
acceptance, real provider accounts and public distribution have separate gates.

For a confirmed reproducible product issue, retain exact inputs/IDs and actual
error, apply the smallest relevant fix, run its meaningful regression checks,
then request the tester's affected action again. Only the tester's report can
close its manual retest.
