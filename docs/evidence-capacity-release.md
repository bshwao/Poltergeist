# S8–S10 implementation and acceptance

Updated October 29, 2026. This is a private implementation checkpoint. Public
hosting remains deferred. Independent PCs, a real publisher identity and external
beta acceptance are release gates; this document does not claim their completion.

## S8: evidence delivery

Summaries continue to use the durable command queue. Evidence uses a separate
owner-authorized `POST /v1/devices/{device_id}/evidence` request. It never enters
the command table, result history or cloud object storage. A browser must have
the existing result scope **and** `allow_evidence_read`, approved explicitly in
the Companion's pairing panel. Existing saved approvals default to false; pair
again to grant evidence access. The connection consent checkbox still applies.

New companions advertise `X-Poltergeist-Evidence: v1`. The coordinator negotiates
support only with those clients; unextended v1 clients keep their original hello
and ready messages. The dashboard disables evidence review for an old, offline,
paused or unapproved companion. Each read rechecks account, device generation,
execution receipt and permissions. A result scope cannot read arbitrary local
runs or filesystem paths.

Recording discovery and rendering additionally negotiate
`X-Poltergeist-Recordings: v1`. Camera/LiDAR capture is available only for CARLA
requests with the separate, default-off **Allow raw capture** pairing approval.
The website's camera capture is 320 × 180 BGRA pixels. Existing approvals and
synthetic runs cannot enable it. Capture stays local while the experiment runs.

| Resource | Limit |
| --- | --- |
| Telemetry page | 1–100 projected frames; 60,000 bytes before encoding |
| Raw recording chunk | 65,536 bytes, addressed by saved artifact/sample index |
| Recording catalogue | At most 64 references; no local paths |
| Sample metadata page | 1–100 entries; browser keeps 25 at a time |
| Browser sample preview | One sample, at most 4 MiB; larger samples need local export |
| Concurrent evidence reads | One per device, eight service-wide by default |
| Per-account evidence rate | 120 reads/minute, shared across its devices |
| Relay message | 262,144 bytes; WebSocket compression disabled in the CLI |
| Evidence reply wait | Ten seconds after the bounded send |
| Local evidence budget | 20 GiB, including pending artifacts and sample indexes |
| Free disk reserve | 512 MiB before each artifact write |

Telemetry pages preserve recorded frame/time and controls. Unobserved speed,
collisions and violations remain null. Byte cursors resume only with the original
file version; changed files fail explicitly. Samples retain the recorded native
frame/time, expected whole-sample SHA-256, and per-chunk SHA-256. The browser
verifies the exact serialized page bytes before display, keeping one page.

Recordings produced by this checkpoint keep a bounded-line `samples.ndjson`
index beside their existing manifest. Metadata pages and selected sample cursors
seek directly into that index, validating the byte boundary and sample identity.
Lookup without a cursor reads at most 64 MiB of index lines and one 64 KiB sample
chunk. The generated manifest header is read with a 4 KiB bound. Older manifests have a 2 MiB fallback
limit. Oversized or damaged legacy evidence requires local export rather than
unbounded parsing. Evidence absence, explicit local deletion, a changed file and
transport unavailability are separate outcomes; none becomes a measured zero.

Cancellation stops browser consumption and subsequent requests. At most the
already requested page/chunk remains in flight, bounded by the server timeout.
The server has no producer queue and does not stream the next chunk until asked.
Late replies after a timed-out request can close the relay and reconnect normally.

The Companion offers **Export evidence…** and **Remove evidence…** for a finalized
selected run while its queue is idle. ZIP export streams files from disk. Removal
retains the saved result and an explicit tombstone. There is no automatic age
deletion. Stop accepting work before maintenance; the worker condition and relay
admission lock prevent execution racing with removal. The closed-app alternative:

```powershell
python -m scripts.manage_evidence --data-dir C:\path\CompanionPrototype --run RUN_UUID --export C:\exports\evidence.zip
python -m scripts.manage_evidence --data-dir C:\path\CompanionPrototype --run RUN_UUID --delete
```

Developer deployments can configure `ARTIFACT_QUOTA_BYTES` and
`ARTIFACT_MIN_FREE_BYTES`. The isolated Companion uses the defaults above. Limits
cover generated artifacts, not CARLA, AI packs, databases or user-selected exports.
One owning worker maintains each profile's accounting; externally modifying its
files requires reopening the runtime to inventory them again. Quota errors abort
publication. Conservative reservations after an I/O failure can require a restart.

The website reviews telemetry, recording catalogues and paged sample metadata.
Explicit inspection verifies every chunk and the entire saved sample before
rendering camera pixels or inspecting finite LiDAR points in sensor coordinates.
It preserves native CARLA frames/timestamps, which need not start at zero for a
run. Cancellation stops further chunks; changing pages discards the old preview.

A sixty-second live CARLA 0.9.16 run saved 2,402 samples and 302,839,504 raw bytes.
Chromium reviewed all 97 metadata pages and verified first-page camera/LiDAR and
late-frame previews using 2,132,058 response bytes. No evidence request ran during
execution. JavaScript heap after explicit collection was 3,969,100 bytes before
review and 5,009,352 afterward; this is retained heap, not a native-process peak.
Native frames 235053–236253 were preserved. This laptop proof does not establish
other hardware or long-running production transfer capacity. Cloud sharing is disabled, so no
cloud retention/deletion policy or storage spend is activated.

## S9: capacity and operations

This implementation deliberately uses one coordinator process and its private
SQLite metadata store. Device IDs and database connection generations select and
fence the correct socket; account ownership is checked at every admission/read.
Each Companion retains its independent queue, database and simulator ownership.
Multiple Uvicorn workers or multiple relay instances are not supported: shared
routing must be implemented before scaling out. Do not share a local runner's
writable SQLite file between users or cloud workers.

Defaults are 100 connected devices, five live devices per account, eight evidence
transfers, 32 queued control commands per device, 10,000 retained commands per
account and 100,000 retained commands service-wide. Retry of an existing request
still returns its original receipt at the storage limit. Revocation frees a
device slot. Receipt retention is conservative: hitting its limit requires an
operator to preserve/archive the private service data before enabling more work;
the service does not delete security/execution receipts silently.

Authenticated browser traffic is bounded to 1,200 requests/minute/account.
Request/evidence windows are process-local and bounded to 10,000 buckets;
authentication and pairing abuse limits remain persisted in SQLite. The HTTP
boundary limits concurrent requests to 64, bounds bodies to 32 KiB and times out
an idle body receive after ten seconds. The CLI also sets Uvicorn concurrency
128, WebSocket receive queue one and keepalive five seconds. Enforce deployment
proxy connection/header deadlines too; hosting has not yet been deployed.

`GET /v1/operations` requires a separate high-entropy bearer token from
`POLTERGEIST_MONITOR_TOKEN` (43–128 URL-safe characters). Ordinary account sessions
cannot read it. It exposes connection/transfer occupancy, configured caps, queued
and retained command counts, database/WAL bytes, transfer bytes and capacity
refusals since process start. It contains no owner/device labels, credentials or
local paths. Disable the endpoint by leaving the token unset. Alert on prolonged
queue growth, occupancy near a cap, increasing refusals or storage growth; retain
operator measurements independently of process-local counters.

The disposable benchmark creates distinct accounts and simulated companions,
routes status commands over actual loopback HTTP/WS and tests cross-account
denials. It stores no credentials in its JSON report:

```powershell
python -m scripts.load_coordinator --users 25 --rounds 20 --output capacity.json
```

Provisional target: 25 connected devices, 500 completed commands, all ownership
checks correct, p95 command latency ≤2 s and peak traced Python allocations
≤128 MiB. These are initial engineering targets awaiting product agreement.

| Measurement | Result |
| --- | --- |
| First run | Routing/ownership passed; p95 5.38 s, latency target failed |
| Retained WAL and bounded request windows | p95 4.26 s, latency target failed |
| Serialized reusable SQLite connection and single-stat path checks | **p95 1.75 s; 500/500 commands; 25/25 denials; 23.3 MiB traced Python peak** |
| Follow-up Python 3.12.10 / SQLite 3.49.1 | p95 2.36 s; latency target failed; ownership/routing passed |
| Follow-up Python 3.14.0 / SQLite 3.50.4 | p95 1.57 s; target passed |
| Atomic activity/receipt commit, Python 3.12, overlapping regression I/O | p95 2.38 s; latency target failed; ownership/routing passed |
| Atomic activity/receipt commit, isolated Python 3.12 | **p95 1.56 s; 500/500 commands; 25/25 denials; 16.6 MiB traced Python peak** |
| Atomic activity/receipt commit, isolated Python 3.14 | **p95 1.39 s; 500/500 commands; 25/25 denials; 23.0 MiB traced Python peak** |

All failed and successful receipts are retained in
`docs/validation/s9-capacity*-20261029.json`. Durable reply handling now rechecks
the credential and every grant inside the same transaction as device activity
and receipt persistence. Failed persistence rolls both updates back. This avoids
a redundant durable activity commit; it does not cache authorization decisions.
SQLite FULL durability is explicitly set on each connection, and per-operation
link checks remain enabled. Final measurements run without concurrent test
traffic; the retained contended failure demonstrates shared-host sensitivity. Local
measurements have host-load variance; they are not a provider sizing guarantee.
The benchmark excludes TLS cost, native allocations/RSS, recordings, actual
simulations, physical sleep, WAN failures and cloud traffic/storage prices.

The first 100-device burst stopped during the cross-owner probes: 35 responses
were HTTP 503 and 65 were the expected 404 denials. It measured no command latency
and does not establish the 100-device target. The coordinator's 64-request window
returns 503 under overload; that response is distinct from an ownership denial.
The [failure observation](validation/s9-configured-100-overload-20261029.json)
preserves the outcome.

Benchmark protocol 2 retries 429/503 within a fifteen-second absolute request
budget and at most sixteen attempts, honors numeric Retry-After values, and keeps
the original command body/request ID. It records retry/status counts and never
replaces incomplete latency with zero. All owner probes finish before a barrier
releases the concurrent command burst. The fixture now matches the CLI's
128-connection concurrency limit and five-second keepalive. Caught setup/workload
failures produce JSON evidence; shutdown failure retains the owned fixture for
inspection. Existing output files are refused before load starts. The provisional
two-second latency and 128 MiB traced-memory targets are unchanged. Protocol 2
measurements must be identified separately from the older table above.

Separate existing HTTPS/WSS tests cover transport security. The configured
100-device ceiling, real concurrent CARLA runs on representative PCs and hosted
costs remain unvalidated. A small three-user smoke benchmark runs in core CI.

## S10: signing and safe update tooling

`scripts.build_release` composes the existing pinned, hash-verified portable
builder, the pinned Inno Setup compiler, signed installer/uninstaller production
and signed release metadata. It creates local release candidates; it never
publishes or sends them. Reproducibility concerns the unsigned payload; trusted
timestamps naturally change signed bytes.

The initial adapter uses a certificate in the Windows current-user `My` store
and SignTool, compatible with a store-backed hardware key/provider. A remote
signing service may need its own adapter once its details are supplied. Private
keys and certificate passwords are never arguments or chat content. Public
configuration comprises the SHA-1 certificate selector, SHA-256 certificate DER
fingerprint and timestamp services. Before building, commit the **public**
publisher pin into `companion/release-policy.json` and build a fresh wheel/bundle.
The default policy has no pins and disables signed updates.

The signed build checks that the bundled update policy trusts the intended
publisher and its project-wheel version matches the installer version. Inno
signs both Setup and Uninstall. Native verification requires a valid trusted
Authenticode signature, the pinned publisher and a timestamp. References:
[Microsoft SignTool](https://learn.microsoft.com/en-us/windows/win32/seccrypto/signtool),
[Inno signing](https://jrsoftware.org/ishelp/topic_setup_signtool.htm),
[signed uninstallers](https://jrsoftware.org/ishelp/topic_setup_signeduninstaller.htm).
The Windows PowerShell 5.1 metadata adapter requires an `http://` Authenticode
timestamp endpoint, as specified by [its native cmdlet](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.security/set-authenticodesignature?view=powershell-5.1).
The timestamp's cryptographic signature and trust chain are verified. The
installer's RFC 3161 endpoint uses HTTPS; these are separate configuration values.
Only hashes/certificates reach timestamp services. Release payloads stay local.

Release metadata is a signed `.ps1` container holding one JSON comment. It is
parsed as data and is **never executed**. It records channel, version, 14-day
validity, installer bytes/hash, exact bundle-manifest hash, supported profile
schema window and relay protocol. The updater does not accept publisher pins
from downloaded metadata. Metadata and installer signatures are verified before
staging and again on staged copies.

```powershell
python -m scripts.build_release --output C:\release\candidate --cache C:\release\inputs --compiler C:\tools\ISCC.exe --version 0.2.0 --signtool C:\tools\signtool.exe --certificate-thumbprint PUBLIC_CERT_SELECTOR --publisher-sha256 PUBLIC_CERT_SHA256 --timestamp-url https://YOUR_RFC3161_SERVICE --metadata-timestamp-url http://YOUR_AUTHENTICODE_TIMESTAMP_SERVICE
```

The example version requires the same version in the packaged project wheel.
The native **Updates** tab checks only on request. It reviews the verified signed
metadata's version, size and expiry before a separate download action. A bundled
`feed_url` supplies metadata over HTTPS; installer downloads must use that same
origin. Credentials, URL queries/fragments, redirects and compressed responses
are refused. There is no background poll or download. The current policy keeps
both publisher pins empty and `feed_url` null, so this delivery path is disabled.

Downloads keep one 64 KiB transport chunk, verify the signed byte count, whole
SHA-256 and native publisher, and enforce a 512 MiB free-space reserve. Each
transfer has a ten-minute deadline with ten-second read timeouts. At most twenty
candidate directories are retained. **Select downloaded update…** reopens a
cached candidate after signature/integrity verification, reusing its installer. **Clear candidate** removes
the selected feed/cache download; **Remove cached download…** can explicitly
remove an expired candidate without accepting it as an update. Installation backups and selected local rollback files are kept.
Signed `rollout_percent`/`rollout_salt` values select a stable local cohort; its
random ID and account/device identifiers are never sent to the feed. A zero
percent rollout offers nothing. Cached metadata remains valid until its signed
expiry; changing a remote rollout does not revoke a previously downloaded copy.

Configure the real public feed in the bundled policy before building the first
signed baseline. Use `--installer-url`, `--rollout-percent` and `--rollout-salt`
with the release builder to sign those delivery fields. No feed is deployed here.
The installed runtime also supports explicit check/download commands:

```powershell
python -m scripts.download_update --data-dir C:\path\CompanionPrototype
python -m scripts.download_update --data-dir C:\path\CompanionPrototype --download
```

The downloaded-file update CLI, run from the installed current version, remains:

```powershell
python -m scripts.update_companion --data-dir C:\path\CompanionPrototype --installer C:\downloads\Setup.exe --manifest C:\downloads\release.ps1 --install-root C:\path\Companion
```

The native **Close and install candidate** action verifies the release again,
checks queued work and closes the Companion. A separate helper waits for the
entire old process and its installer mutex to exit, then rechecks the durable
queue and both leases. On success it reopens the verified installed release.
Failures retain their receipts/backups and show a recovery message.

The Companion must be closed. Its profile lease and the database worker lease
exclude live execution; active/queued simulations or optimization work prevent
the update. The installer identity/profile/version must match. The updater
backs up committed SQLite WAL data and local setup before any native install,
checks disk capacity, retains the prior runtime and installation marker, verifies the new bundle identity
and exact active installation version/build/profile,
and runs its private-runtime migration probe. No agent registry identities or
recordings are rewritten by the updater. A receipt records prepared, installed
or failed/preserved. Failed installation/migration keeps its backup and staged
payload for recovery; it does not report success.

The native **Select signed rollback…** action, or an explicit `--rollback`, accepts a lower signed version only when that release
supports the current profile schema; the present default window is schema 6.
It takes another backup and preserves newer results. Expired rollback metadata
needs a newly signed validity window around the same retained installer. An
incompatible schema rollback is refused. Recovery from a failed migration must
use the retained database snapshot and runtime under a closed profile, preserving
the newer database separately first; restoring an old database after new work
requires an explicit data-recovery decision. Automatic destructive database
rewinds are not implemented.

Legacy private installers without the new installation version marker are
ineligible for automatic updates. Install a first signed baseline through the
ordinary installer and verify it on an independent PC before testing upgrades.

Native unsigned-file rejection and injected signature-check fixtures exercise
the safeguards. **No production signature, timestamp-service success, actual
signed upgrade/rollback, public distribution or external beta has been claimed.**
The owner deferred providing signing/tester details on October 29, 2026. Public
configuration, real signed upgrade/rollback and the physical-PC matrix remain
release gates. See the
[beta acceptance record](private-beta.md) for the remaining release gates.

## Initial validation receipt

The [machine-readable checkpoint](validation/s8-s10-checkpoint-20261029.json)
retains source hashes and scope. The full Python 3.14 run completed with 3,300
passed, ten optional-dependency skips and one existing expected-response assertion
that lacked the new capability field. That assertion was corrected; all eight
browser projection checks and the final 22-test focused batch pass. Thirty-one
new workflow checks pass with the repository's pinned Python 3.12 dependencies.
The final absolute-body-deadline/API batch passes 33 tests on each interpreter.
All 334 frontend tests, shared-schema checks and the hosted production build pass.

The HTTPS/WSS Chromium workflow passes with real companions, telemetry review,
two-account isolation, cancellation, responsive layouts, pause and revocation.
The fresh private Python 3.12.15 bundle passes 6,192 integrity-file checks, SDK
subprocess startup, a captured synthetic run and native desktop callback/reopen
checks. Its isolated profile is released after closure. Pinned Inno Setup 7.1.0
compiles a validation-only installer; Windows reports **NotSigned**. No native
installation, external machine, real release signature or public deployment is
claimed by this checkpoint. All 189 packaged first-party Python sources match
the workspace. The coordinator server is intentionally excluded from the desktop
bundle; its final request-body deadline has separate source/API checks.

## Recording/update follow-up validation

The [follow-up receipt](validation/s8-s10-followup-20261029.json) preserves the
current source identities, exact artifacts, passing checks and failed attempts.
The broad Python 3.14 run passed 3,338 tests with ten optional-dependency skips.
It began before the final update-helper, layout/cache and atomic-reply changes;
subsequent focused runs cover those changes, including rollback, native process
handoff, queued-work refusal, disk reserve, slow-body deadlines and bounded
download allocations. These counts overlap and are not added together.

All 340 frontend tests, shared-schema/type checks and the hosted production build
pass. The live CARLA recording review and the final two-account HTTPS/WSS browser
workflow pass. The latter covers a lost response, idempotent execution,
cancellation, reload, responsive layouts, pause and revocation.

The current Python 3.12.15 private bundle verifies 6,280 files; all 194 packaged
first-party Python modules match the worktree. Its native callbacks, synthetic
run/telemetry read, saved-result reopen, lease release and native CARLA 0.9.16
connection pass. Native update delivery stays disabled without a publisher.
Inno Setup 7.1.0 compiles a validation-only installer; Windows reports
**NotSigned**. At that checkpoint it had not been installed or published. The coordinator is excluded
from the desktop bundle and has separate final recovery/security/load checks.
Real signed installation, upgrade/rollback and external beta remain unverified.

## Native installer lifecycle follow-up

The [native lifecycle receipt](validation/s10-native-lifecycle-20261029.json)
records a later unsigned install in separate workspace-owned app/profile folders.
First-run setup, baseline and SDK subprocess execution, export, telemetry review
and reopening saved results pass using the installed private interpreter.
Repair while the test app was running was refused without changing any installed
file. Closed-app repair preserved all ten profile files and retained its old runtime.

The first uninstall kept the profile and recovery copy but removed the ownership
marker, causing same-folder reinstall to fail. The installer now retains
`installation.ini` whenever `recovery/` remains. The fixed native uninstall/reinstall
passes with no manual changes between those steps, all profile file bytes
unchanged, and both results reopened. The pre-fix fixture was recovered once using
its hash-matched original marker before applying the fixed build; that separate
fixture recovery is recorded explicitly.

Final cleanup removed the validation registration and active runtime, keeping the
profile, one recovery copy and its marker as evidence. The fixed validation
installer SHA-256 is
`566014a89d321ed277260ba0692a3645ee3b83d781160ffd1680d50e27ee994a`;
Windows still reports **NotSigned**. These checks do not establish real signed
upgrade/rollback, hardware mouse input, a machine without developer tools, or an
external beta. Signing identity/tester details remain deferred by the owner.
