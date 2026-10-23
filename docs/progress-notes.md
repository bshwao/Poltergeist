# S0–S10 progress notes

Updated October 23, 2026. Current work: **S4 — hosted backend and pairing**.
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
| S2 — Companion application | Private desktop prototype | Native application, isolated profile, durable local queue/results, configuration, diagnostics and deliberate imports/exports. Hosted connection remains unavailable. See [desktop guide](companion.md). |
| S3 — Installer and onboarding | Private prototype; locally validated, external gate open | Per-user installer, CARLA discovery/setup, compatibility/hardware checks, opt-in shortcuts/startup, repair and data-preserving uninstall. External clean-machine/workflow testing and visual onboarding review remain open. |
| **S4 — Hosted backend and pairing** | **In progress; account/pairing foundation** | Separate coordinator, Argon2 accounts, browser sessions/CSRF, owner-scoped device pairing/revocation, DPAPI credentials and desktop consent controls. Relay, remote commands, public result projection and full execution acceptance remain open. See [scope and evidence](coordinator.md). |
| S5 — Website integration | Pending | Paired-device dashboard transport and a real HTTPS deployment. |
| S6 — Reliable remote execution | Pending | Durable remote command receipts, reconnect/idempotency tests, cancellation and cross-profile CARLA endpoint ownership. Existing local queue durability is only a foundation. |
| S7 — Managed AI installations | Pending | Supported environment/weight installation and lifecycle through the app. |
| S8 — Evidence delivery | Pending | Authorized bounded telemetry/recording transfer and retention. |
| S9 — Multi-user capacity | Pending | Monitoring, quotas, device routing and load tests. |
| S10 — Distribution and release | Pending | Signing, updates/rollback, supported-machine validation and external beta. |

## Latest work

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

The earlier Windows 3.14 functional failures were cold-start deadlines in the
test harness. Docker configuration now has a 90-second test deadline; API worker
polling has 30 seconds and includes the last observed state on failure. Production
deadlines were unchanged. All five jobs passed for `2318b3d` in
[run 39883641192](https://github.com/bshwao/Poltergeist/actions/runs/39883641192).
The complete S3 commit `7cda315179f9829a133ae8d80655d988acb5a816` also passed
all five jobs in [run 39923801579](https://github.com/bshwao/Poltergeist/actions/runs/39923801579).
Each run validates its recorded commit; the S4 branch requires its own checks.

## Remaining practical gates

The private installers are unsigned. Clean-machine installation, native platform
support, licensing/redistribution decisions, visual first-run inspection and an
external tester remain open. Windows sign-in obscured the latest desktop preview,
so automated callback tests do not constitute a completed visual inspection.
No public hosted service or authenticated remote execution is available yet.

Visual design, guided workflows, experiment agents and candidate API connections
are recorded separately as [product impact proposals](product-impact-proposals.md).
They are proposals; the active S0–S10 scope and gates above remain intact.
