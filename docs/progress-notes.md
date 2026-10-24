# S0–S10 progress notes

Updated October 24, 2026. Current checkpoint: **S4 private workflow validated on
the development laptop; S5 website integration is next**.
Branch: `codex/s4-hosted-pairing`.

This branch includes the earlier S0 collision presets, S1 private runtime and
S2 desktop companion and S3 installer/setup flow. The latest changes add the
account/pairing foundation and desktop consent controls for S4.
The full [roadmap](scalability-plan.md) retains all completion gates; a working
private prototype does not close independent-machine or release validation.

| Step | Current state | Evidence or remaining work |
| --- | --- | --- |
| S0 — Collision stress scenarios | Implemented and locally validated | Twelve presets; 36 calibration and 60 held-out CARLA trials with the unchanged baseline. See [measurements and limits](collision-stress.md). |
| S1 — Standalone packaging | Private prototype validated on the development laptop | Pinned private Python/core dependencies, relocated synthetic/CARLA/SDK checks. An independent computer without developer tools, redistribution review and a native support matrix remain open. |
| S2 — Companion application | Private desktop prototype | Native application, isolated profile, durable local queue/results, configuration, diagnostics and deliberate imports/exports. S4 adds explicit relay connection and consent controls. See [desktop guide](companion.md). |
| S3 — Installer and onboarding | Private prototype; locally validated, external gate open | Per-user installer, CARLA discovery/setup, compatibility/hardware checks, opt-in shortcuts/startup, repair and data-preserving uninstall. External clean-machine/workflow testing and visual onboarding review remain open. |
| **S4 — Hosted backend and pairing** | **Private security workflow locally validated; broader gates open** | Accounts, approved ownership, authenticated relay, atomic remote receipts, scoped commands/results and desktop session consent. Portable and installed-runtime proofs pass on this laptop; all five code validation jobs pass. Public deployment, visual/default-shortcut and supported-machine acceptance remain open. See [scope and evidence](coordinator.md). |
| S5 — Website integration | Pending | Paired-device dashboard transport and a real HTTPS deployment. |
| S6 — Reliable remote execution | Pending | S4 now supplies durable remote receipts and initial reconnect/cancellation proofs. The wider crash/sleep/timeout matrix and cross-profile CARLA endpoint ownership remain open. |
| S7 — Managed AI installations | Pending | Supported environment/weight installation and lifecycle through the app. |
| S8 — Evidence delivery | Pending | Authorized bounded telemetry/recording transfer and retention. |
| S9 — Multi-user capacity | Pending | Monitoring, quotas, device routing and load tests. |
| S10 — Distribution and release | Pending | Signing, updates/rollback, supported-machine validation and external beta. |

## Latest work

- Added desktop Connect/Disconnect and an unchecked consent box beside the
  approved account, device, permissions and limits. The controller checks that
  exact protected binding before enabling the session. Disconnect/Stop/Close,
  reopening and changed pairing clear consent; startup and polling never connect.
- Moved each TLS connection onto one event-loop owner. Native checks retain
  strict certificate/hostname verification and normal session-ticket negotiation.
  Stop/write deadlines abort only the captured connection; slow DNS cleanup
  retains the runtime/profile lease. All 37 transport/lifecycle cases pass on
  Python 3.12 and 3.14, including failed startup and cancelled opening races.
- Private build G passed the combined native Tk consent, DPAPI and HTTPS/WSS
  proof through the normal controller, with two accounts and two process launches.
  It checks all 168 first-party sources, 6,079 manifest files and 99 bundled
  import paths per process. Unchecked sessions deny all new companion operations;
  Stop/Start and reopening require fresh consent. Lost acknowledgements keep one
  run; queued/active cancellation, durable readback and revocation pass.
  The first bootstrap deadline failure and successful instrumented repeat are
  both retained in the [desktop consent receipt](validation/s4-desktop-consent.json).
- An isolated validation installer for G also passed native setup, the same
  two-account consent proof from its installed version directory, and default
  data-preserving uninstall. The proof used a fresh custom data profile; it did
  not launch the installed shortcut or exercise the compiled default profile.
  Both companion processes exited cleanly, the validation registration/runtime
  were removed, and profile/evidence data remain available. See the
  [installed consent receipt](validation/s4-installed-consent.json).
- Controller tests pass 43 cases on each Python version; native Tk tests pass
  14 on each. The updated normal-controller two-companion TLS proof also passes
  on Python 3.14. Hidden callback checks make no visual or physical-input claim.
- The additional full local run passed 3,045 tests with 35 optional skips and
  three registered-agent integration failures. Original records show two valid
  Close acknowledgements followed by process-exit deadline exhaustion, and one
  history observer expiring before its intended failure result became visible.
  The integration registrations now request a ten-second close budget and that
  observer has sixty seconds with last-state diagnostics. Production deadlines
  and dedicated timeout/termination assertions are unchanged. This earlier run
  remains recorded as failed; all 142 affected comparison, behavior, registered
  execution and lifecycle tests passed on Python 3.12 and 3.14 after the fixture changes.
  Final focused and GitHub results are separate.
- Added the private S4 relay and command boundary: closed message types,
  authenticated/fenced connections, owner and scope checks at each boundary,
  bounded queues/heartbeats, durable command outcomes and revocation.
- Added migration 5 for local remote receipts and independent public projection
  revisions. Submit and cancel receipts share the worker transaction; a retry
  cannot commit a second tentative simulation/job.
- Added explicit baseline-only result summaries with null/coverage preservation,
  fixed errors and evidence availability. Local paths, snapshots and arbitrary
  diagnostics never form the public response.
- The source TLS execution proof passes with two accounts and two started
  companions, an intentionally lost acknowledgement, one original accepted run,
  scoped results, cooperative cancellation, swapped-identity denials and revoked
  credentials after coordinator restart. Alice's worker executes; Bob's stays
  empty. Normal startup keeps remote requests disabled. See the
  [relay validation receipt](validation/s4-relay-prototype.json).
- Private build F passed the packaged DPAPI/TLS/relay/worker proof across two
  process launches with one profile and two account sessions. Lost acknowledgements
  retain one job, results survive reopening, and both queued/active cancellation
  and revocation denials pass. All 167 first-party sources and 6,078 manifest files
  match the frozen bundle. This earlier proof uses a constructor-only private
  switch; build G above exercises the normal controller and native controls.
- The prior relay implementation passed 205 focused checks on Python 3.14.
  Dashboard code is unchanged in this follow-up; its prior 329 tests, contracts,
  typecheck and production build passed.

## Account/pairing foundation evidence

- Added a separate coordinator with explicit storage/configuration, bounded
  account authentication, revocable browser sessions and CSRF boundaries.
- Added local pairing approval, durable owner/grant creation, hashed server
  credentials, current-user/profile protected Windows enrollment, retry/restart
  recovery and owner-scoped revocation. Browser claim alone grants no access.
- Added desktop Connection controls for the saved HTTPS server, pairing code,
  account/permission review, approval/denial, resume and revocation. Existing
  CARLA/setup configuration preserves the pairing origin, and public snapshots
  omit protected credentials. Remote execution is still disabled.
- Recorded foundation test/TLS/package scope in the
  [sanitized S4 receipt](validation/s4-pairing-foundation.json). This does not
  establish a public deployment or complete S4 remote execution acceptance.
- The current full Python 3.12 suite passed **2,802 tests with 35 optional skips**;
  five new coordinator-verifier cases passed separately on both interpreters.
  Python 3.14 passed 81 account/pairing/controller cases and all ten desktop cases.
  Dashboard tests passed 329/329 and the production build passed.
- A fresh non-editable wheel passed core workflows before installing its hosted
  extra. The separate coordinator then passed real Argon2/two-owner/revoke checks.
  The private companion bundle excludes hosted dependencies, matches all 160
  first-party runtime sources, and passed real TLS/DPAPI account approval and
  revocation through hidden native callbacks across two process launches.
- Final address-validation review found that rejected userinfo/port strings
  could appear in errors. Fixed parser errors and hidden rejected configuration
  input now keep them out of messages and logs. Follow-up checks passed 66 cases
  on Python 3.12 and 41 on 3.14. Rebuilt private bundle C passed the full native
  pairing/TLS/reopen/revoke check, plus three rejected-address callbacks using
  the normal companion log; configuration stayed unchanged and no sentinel leaked.

## Prior S3 work

- Added first-run CARLA detection and browsing, Low/Epic configuration, explicit
  start/reuse, strict 0.9.16 client/server checks, map listing and read-only
  hardware/disk observations. Synthetic-only setup is an explicit choice.
- Added a pinned Inno Setup build, private per-user install, native runtime checks,
  repair/uninstall shortcuts and optional desktop/login startup choices.
- Preserved profile data through repair and ordinary uninstall. Explicit data
  removal requires an installer-owned, exact-path profile with no linked content
  and no active profile/database lease.
- Isolated GUI regression cases into individual application processes after a
  full-suite Tcl lifetime failure. The subsequent full suite passed **2,730 tests
  with 35 optional skips**. The earlier failure remains in local evidence.
- Native validation on this laptop has exercised installed synthetic execution,
  persisted results after reopening, damaged-file repair, refusal while the app
  is running, default data retention, explicit owned-profile removal, and shortcut
  opt-in/opt-out. These are separate from the external-machine gate.
- The installed setup/start/run/export callbacks completed a two-second CARLA
  baseline experiment on `Town10HD_Opt` with Low graphics and 41 telemetry frames.
  The finalized result survived reopening, world settings/weather were restored,
  and the supervisor verified cleanup of only its newly created simulator tree.
  The first application check passed but its supervisor could not observe an exit
  code; that failed receipt was retained. A native-handle observer was tested with
  exit codes 0 and 7 before the complete live check passed with exit code 0.
- Review found that runtime identity must cover all bundled dependencies and
  repair must handle unexpected files. Identity now uses the full bundle manifest;
  repair preserves the previous runtime separately before extracting a clean copy.
  Native verification passed: damaged files were restored, unexpected files were
  retained outside the exact active runtime, and every profile file was preserved.

The [sanitized S3 validation receipt](validation/s3-installer.json) records exact
private installer/bundle identities, test scope and remaining gates. The normal
private installer is separate from the isolated native-test installer.

## GitHub validation

The consent and single-owner TLS implementation
`26331fbcb6ada614baf0059d669aeec7cb6289f0` passed all five jobs in
[run 40140867479](https://github.com/bshwao/Poltergeist/actions/runs/40140867479),
including each full Python suite and the isolated non-editable core/coordinator
wheel checks. This run follows the scoped fixture corrections described above.

The relay implementation `ba8c6ddd625aa1c294b5bf4c5e72c3ab706691c8`
passed all five jobs in
[run 40064187930](https://github.com/bshwao/Poltergeist/actions/runs/40064187930).
An additional local full run passed 3,015 tests with 35 optional skips and one
native TLS handshake failure. Further instrumentation reproduced a completed
client write with no HTTP request received, while both TLS reader threads
waited. Controlled ticket/TLS comparisons and upstream threading guidance point
to concurrent SSL I/O, so the follow-up moves TLS onto one event loop. This
local failure remains recorded; the green GitHub run does not erase it.

The earlier Windows 3.14 functional failures were cold-start deadlines in the
test harness. Docker configuration now has a 90-second test deadline; API worker
polling has 30 seconds and includes the last observed state on failure. Production
deadlines were unchanged. All five jobs passed for `2318b3d` in
[run 39883641192](https://github.com/bshwao/Poltergeist/actions/runs/39883641192).
The complete S3 commit `7cda315179f9829a133ae8d80655d988acb5a816` also passed
all five jobs in [run 39923801579](https://github.com/bshwao/Poltergeist/actions/runs/39923801579).
Each run validates its recorded commit; the S4 branch requires its own checks.

The first S4 run for `2ce3e42`,
[39954176546](https://github.com/bshwao/Poltergeist/actions/runs/39954176546), passed
the dashboard and both Ubuntu jobs. Its two Windows failures were in existing
test harnesses: the first Compose configuration invocation exceeded 90 seconds
(the five later Compose checks passed), and a synthetic desktop completion wait
exceeded 30 seconds. The latter case passed unchanged locally on Python 3.14;
the generic worker-close message did not establish a CARLA/native call.

The follow-up keeps those checks and assertions enabled, allows bounded Windows
Compose startup and desktop completion time, and adds stage/state/thread
diagnostics for a future GUI timeout. Production deadlines are unchanged.
All eleven isolated desktop cases passed again on Python 3.12 and 3.14 after
the harness change. This local success does not identify the CI stall's cause;
the next runner failure, if any, must be investigated using its captured state.
Docker is absent locally, so its six parser checks remain explicitly skipped
here and require the actual CI runners. Follow the
[S4 branch workflows](https://github.com/bshwao/Poltergeist/actions?query=branch%3Acodex%2Fs4-hosted-pairing)
for each newer commit's result.

## Remaining practical gates

The private installers are unsigned. Clean-machine installation, native platform
support, licensing/redistribution decisions, visual first-run inspection and an
external tester remain open. Windows sign-in obscured the latest desktop preview,
so automated callback tests do not constitute a completed visual inspection.
No public hosted service is available. Authenticated remote control has been
validated only on private test infrastructure; the delivered S3 installer is
unchanged.

Visual design, guided workflows, experiment agents and candidate API connections
are recorded separately as [product impact proposals](product-impact-proposals.md).
They are proposals; the active S0–S10 scope and gates above remain intact.
