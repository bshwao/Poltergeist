# S8–S10 implementation and acceptance

Updated October 31, 2026. This is a private implementation checkpoint. Public
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

The [November 3 acceptance run](validation/s8-120-local-20261103.md) passed
at the 120-second hosted limit on this PC. Chromium reviewed 4,802 samples
across 193 pages using 3.61 MB of evidence responses from a 606 MB capture.
The local quota check, verified ZIP export and dashboard removal state passed.
The [November 4 public acceptance run](validation/s8-public-wan-20261104.md)
reviewed 4,802 samples across 193 pages on the same PC through the deployed
dashboard. The browser decoded 3.61 MB of successful evidence responses and
verified early camera and LiDAR previews plus the last recorded frame. Successful
metadata requests had a p95 of 60.7 ms, with one separate 36-second rate-limit wait.

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
an idle body receive after ten seconds. The CLI sets Uvicorn's concurrency
threshold to the device ceiling plus 128 HTTP slots (228 by default), WebSocket
receive queue one and keepalive five seconds. The proxy boundary is documented
in the [public deployment record](validation/s5-vps-deployment-20261103.md).

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
releases the concurrent command burst. The fixture uses the same transport budget
as the CLI and its five-second keepalive. Caught setup/workload
failures produce JSON evidence; shutdown failure retains the owned fixture for
inspection. Existing output files are refused before load starts. The provisional
two-second latency and 128 MiB traced-memory targets are unchanged. Protocol 2
measurements must be identified separately from the older table above.

With the original 128 transport threshold, protocol 2 connected all 100 simulated
devices but completed only 58 owner probes before its retry budget expired. It
recorded 659 HTTP 503 responses, no commands and unknown command latency, while
traced allocations peaked at 93,943,116 bytes. The
[failed measurement](validation/s9-protocol2-100-cap128-20261029.json) is retained.
Uvicorn includes open device sockets in its concurrency accounting; this is
confirmed in the pinned installed implementation and its
[upstream server behavior](https://github.com/Kludex/uvicorn/blob/main/docs/server-behavior.md).
The old shared threshold left too little HTTP headroom once device slots filled.
The CLI and load fixture now derive their threshold from the device ceiling plus
the fixed HTTP allowance. The 64-active-request guard and all timing, memory,
body, ownership and durability checks are unchanged.

Protocol 2 measurements with the 228 transport threshold, Python 3.12.15 and
SQLite 3.53.1 on the development Windows laptop:

| Devices / commands | Routing and ownership | p95 latency | Traced peak | Target |
| --- | --- | --- | --- | --- |
| [25 / 500](validation/s9-protocol2-25-headroom-20261029.json) | 500 routed/completed; 25 denials | 1.34 s | 16.4 MiB | Passed |
| [100 / 500](validation/s9-protocol2-100-headroom-20261029.json) | 500 routed/completed; 100 denials | 6.92 s | 48.8 MiB | **Latency failed** |

The 100-device run recorded 309 HTTP 503 responses, all recovered within the
existing retry budgets. Retrying makes the failure evidence complete; it does
not remove overload time from command latency. Both fixtures shut down cleanly.
The validation companion was open and idle, with no concurrent local benchmark
or regression suite. This establishes simulated routing at the configured
ceiling, while the two-second latency target still fails there.

A separate [aggregate diagnostic profile](validation/s9-transaction-profile-20261029.json)
of 100 devices and 200 commands recorded 2,700 transactions and 7.09 seconds in
path validation during an 18.53-second workload. Instrumentation adds overhead;
this diagnostic is not an acceptance measurement. Browser command admission now
shares a short transaction with session authentication, full live-connection
validation and the receipt insert. Command readback similarly shares its
transaction with session authentication. Each request still reads current
credentials, account/session revocation, policy, grants and ownership as
applicable; no authorization or filesystem decisions are cached. A failed
command request rolls back its session activity timestamp. Admission notifies
the relay only after commit, and no transaction spans a body/network await.
SQLite FULL durability and fresh per-transaction path checks remain enabled.

Follow-up measurements on the combined S12/S13 and transaction source at
`50155e7`, after the owned local regression/browser processes completed:

| Devices / commands | Routing and ownership | p95 latency | Traced peak | Target |
| --- | --- | --- | --- | --- |
| [25 / 500](validation/s9-atomic-browser-25-20261031.json) | 500 routed/completed; 25 denials | 1.41 s | 17.1 MiB | Passed |
| [100 / 500](validation/s9-atomic-browser-100-20261031.json) | 500 routed/completed; 100 denials | 9.08 s | 49.1 MiB | **Latency failed** |

The larger run recorded 283 HTTP 503 responses and completed within its existing
retry budgets. Both fixtures cleaned up. These measurements demonstrate no
latency improvement at 100 devices; that target remains open. They include the
new S12/S13 models and ordinary host variance, so they do not isolate the effect
of transaction batching. Earlier failures and successful 25-device receipts
remain available. No timing, memory or ownership target was relaxed.

Separate existing HTTPS/WSS tests cover transport security. The configured
100-device latency target, real concurrent CARLA runs on representative PCs and
hosted costs remain unvalidated. A small three-user smoke benchmark runs in core CI.

The next S9 checkpoint starts from merged S14/S15 source `11bd256`. An unchanged
protocol-2 baseline again completed all 500 commands and 100 ownership denials,
but p95 7.84 s failed the two-second target. The following changes retain that
target, the 64-active-request guard, the 228 transport threshold and FULL durability:

- The store reuses immutable path component names. Every transaction still
  performs fresh `lstat` checks on the database and each ancestor; filesystem
  results are never cached. Changing the configured path refreshes the names.
- Command readback runs its short transaction on the relay loop, like admission,
  avoiding a worker-thread round trip for the same serialized SQLite connection.
- Pending delivery combines full connection validation and its query/update in
  one transaction. Every subsequent send checks the entire current policy,
  grants, credential and connection generation again. No transaction spans an
  await, and failed commits roll back delivery timestamps.
- HTTPcore's async backend detector repeatedly attempts to import `sniffio`.
  The current environment lacked it. The core dependencies now explicitly include
  [sniffio 1.3.1](https://pypi.org/project/sniffio/1.3.1/), with its wheel hash pinned
  in the portable inputs. Regenerating the core lock also restored the declared
  HTTPX dependency and its existing pinned transitive packages. No existing
  package version was upgraded. A cross-file contract checks that declared
  runtime dependencies appear in both the core lock and portable manifest.

Sequential loopback measurements on this Windows laptop:

| Variant / devices / commands | p95 latency | HTTP overload retries | Result |
| --- | --- | --- | --- |
| [Unchanged `11bd256` / 100 / 500](validation/s9-11bd256-baseline-100-20261031.json) | 7.84 s | 289 | Latency failed |
| [Path names reused / 100 / 500](validation/s9-path-components-100-20261031.json) | 6.80 s | 294 | Latency failed |
| [Readback on relay loop / 100 / 500](validation/s9-relay-loop-100-20261031.json) | 7.27 s | 71 | Latency failed |
| [Explicit async detector dependency / 100 / 500](validation/s9-runtime-dependency-100-20261031.json) | 4.36 s | 71 | Latency failed |
| [Combined delivery transactions / 100 / 500](validation/s9-delivery-transactions-100-20261031.json) | 4.31 s | 70 | **Latency still failed** |
| [Final source / 25 / 500](validation/s9-delivery-transactions-25-20261031.json) | **0.641 s** | **0** | **Passed** |

Every row completed all 500 commands, returned all cross-owner denials and
cleaned up. The final traced peaks were 49,637,963 bytes at 100 devices and
17,030,936 bytes at 25 devices, below the unchanged 128 MiB limit. The final
100-device workload took 19.03 s. Host activity outside the owned fixtures was
not measured; these sequential points do not isolate every change or establish a
provider sizing guarantee. Earlier slower results remain recorded.

A separate diagnostic measured 1,000 backend-detection calls under tracemalloc:
0.986 s without `sniffio`, 0.0093 s with it. That microbenchmark is not acceptance
evidence. Two instrumented call-profile attempts produced incomplete workloads;
neither is counted as a latency pass. See the
[scoped overhead checkpoint](validation/s9-relay-overhead-20261031.json).

### November 3 capacity acceptance on this PC

The first current run stalled with its fixture database under OneDrive. A
repeat on local storage timed out during the ownership probes. HTTPX had been
loading identical certificate settings once per simulated browser on the same
event loop. A 30-client diagnostic took 9.77 seconds with separate trust contexts
and 0.04 seconds with one shared context. Each browser still has its own client,
session and command sequence.

The benchmark now offers a separate simulated-client process so allocation
tracing measures the coordinator. The same 100-client burst, deadlines, retry
rules, server limits and two-second p95 target apply. With this mode and the
same workload, the unchanged source measured 2.047 seconds p95. The optimized
source removes duplicate validation and serialization of already validated
commands, skips construction of reply views that the relay discards, and reuses
device data checked in the same transaction. Authorization is checked against
current credentials, policy and grants. Send-lock fencing and SQLite FULL
durability remain in force.

| Devices | Completed and routed | Cross-owner denials | p95 | Traced coordinator peak |
| --- | --- | --- | --- | --- |
| 100 | 500 | 100 | 1.672 s | 23.23 MiB |
| 100, repeat | 500 | 100 | 1.687 s | 22.96 MiB |
| 25 | 500 | 25 | 0.422 s | 8.29 MiB |

The 100-device runs each recorded 36 overload responses, all recovered within
the existing budgets. All three fixtures shut down cleanly. The comparison and
earlier failed variants are in the [machine-readable record](validation/s9-local-capacity-20261103.json).

All 177 regression cases passed across two batches. The initial run passed
172 cases. The five remaining crash-recovery cases passed after the fixture
was updated to inject faults at the relay's reply persistence entry point.

```powershell
python -m scripts.load_coordinator --isolate-clients --users 100 --rounds 5 --output capacity-100.json
python -m scripts.load_coordinator --isolate-clients --users 25 --rounds 20 --output capacity-25.json
```

Representative concurrent CARLA/GPU execution, production WAN performance and
deployed hosting costs remain separate acceptance gates.

### November 4 loopback HTTPS capacity check

The same workload can run through Caddy using `--caddy`. The fixture binds both
services to loopback and uses Caddy's temporary CA to verify HTTPS and WSS.
The Windows trust store stays unchanged. Browser simulation uses HTTP/1.1 and
the coordinator keeps the same admission limits, retry budget and deadlines.
The allocation measurement covers Python in the coordinator process.

| Transport and devices | Completed and routed | Cross-owner denials | p95 | Result |
| --- | --- | --- | --- | --- |
| HTTPS/WSS, 25 | 500 / 500 | 25 | 1.797 s | Passed, with no overload retries |
| HTTPS/WSS, 100 | 193 / 231 | 100 | Unavailable | A request hit its deadline |
| HTTPS/WSS, 100 repeat | 220 / 231 | 100 | Unavailable | A request hit its deadline |
| Direct HTTP/WS, 100 | 500 / 500 | 100 | 9.875 s | Completed above the 2 s target |

All runs stopped their fixture processes and removed their temporary storage.
Both 100-device runs completed all device handshakes and owner probes. The
workloads stopped with incomplete readback and reported no latency percentile.
The Windows loop also logged an accept error during shutdown of the first
100-device run. The direct control also exceeded the latency target on November
4, so the slowdown cannot be attributed to Caddy from these runs. The earlier
November 3 passes are retained above. The current coordinator app, command and
relay files matched the hashes from those passes. This PC used its Silent power
plan during the November 4 checks. Public VPS capacity and cost remain open.

A diagnostic without allocation tracing completed 406 of 500 HTTPS commands,
routed 426 and denied all 100 cross-owner requests before a request hit its
deadline. A separate direct diagnostic with transaction timing completed and
routed all 500 commands, denied all 100 cross-owner requests and measured
4.922 seconds p95. Its 3,102 filesystem path validations took 10.419 seconds
in total during a 31.576-second profiled interval that includes client startup
and teardown. These diagnostics leave the two-second target unchanged.

The direct comparison in Ubuntu WSL2 used Python 3.14.4, SQLite 3.46.1, the
pinned coordinator dependencies and an ext4 database on this PC. At 100 devices,
it denied all 100 cross-owner requests and hit a deadline after 231 commands
completed and 284 routed. Of 651 successful status reads, 420 still reported
queued. The stress fixture repeats queued reads after 5 ms. The hosted dashboard
waits three seconds between history polls and reads its pending command in that
cycle. This difference limits comparisons between stress-fixture latency and
what a dashboard user sees.

The WSL2 run through checksum-verified Caddy 2.11.7 also hit a deadline, after
260 commands completed and 270 routed. It denied all 100 cross-owner requests.
Both Linux fixtures cleaned up. [Caddy release archive and checksum](https://github.com/caddyserver/caddy/releases/expanded_assets/v2.11.7)

The dashboard history endpoint now reuses the ownership and permission checks
from its current read transaction for the page. Each stored command is still
parsed and checked against those permissions before its reply is returned.
The browser and command transaction suites passed. A separate regression
confirmed that an allowed operation column cannot bypass the payload's scope.

An experimental relay combined first-frame selection and authorization under
the send lock. All 16 focused delivery and reconnect tests passed, including
credential expiry during commit. The matched WSL2 HTTPS/WSS run still hit a
deadline after 250 commands completed and 260 routed, with all 100 cross-owner
denials correct. The prior run completed 260 and routed 270. The deadline
recurred, so the existing delivery path was restored.

An instrumented WSL2 HTTPS/WSS run on the restored relay completed 244 commands,
routed 255 and returned 100 cross-owner denials. The request that reached its
command deadline was a status readback. The coordinator forwarded its HTTP 200
headers in 9.6 ms and sent its body. The client trace reached response closure
as the cumulative command deadline expired, about 0.243 seconds into that
readback. This sample shows that a deadline can expire even when the coordinator
answers the final readback promptly.

A later Windows comparison used the restored relay and the same 100-device
workload. Both transports completed and routed all 500 commands, denied all 100
cross-owner requests and cleaned up. HTTPS/WSS measured 5.704 seconds p95 through
Caddy. Direct HTTP/WS measured 2.125 seconds p95. Both exceeded the two-second
target. These runs establish full command completion on this PC over both
transports before tuning Caddy's upstream pool.

#### Caddy upstream connection reuse

Three sequential Windows runs compared Caddy's default pool with a four-second
idle timeout and up to 100 idle connections to the coordinator. The shorter
idle timeout closes pooled sockets before the coordinator's five-second HTTP
keepalive. Each run connected 100 devices, completed and routed 500 commands,
denied 100 cross-owner requests and cleaned up.

| Caddy upstream pool | p50 | p95 | Commands/s |
| --- | --- | --- | --- |
| Adjusted | 1.719 s | 2.235 s | 43.72 |
| Default control | 0.750 s | 5.547 s | 44.44 |
| Adjusted repeat | 1.578 s | 1.703 s | 49.77 |
| Repository configuration | 1.656 s | 1.781 s | 46.72 |

The adjusted pool reduced the tail and raised the median relative to the default
control. Both repeat runs met the two-second p95 target. The deployment Caddyfile
and benchmark fixture now use the tested values. The final run used 24.84 MiB
of traced coordinator allocations against the 128 MiB limit. Deployed load and
provider costs remain open. [Caddy upstream transport settings](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy#the-http-transport)

The [rollout rehearsal](validation/s5-vps-deployment-20261103.md#s9-caddy-pool-rollout-and-rollback)
verified HTTPS, session continuity, reconnect and durable commands across the
tuned restart and rollback. Live rollout awaits VPS SSH access.

```powershell
python -m scripts.load_coordinator --isolate-clients --users 25 --rounds 20 --caddy C:\tools\caddy.exe --output capacity-25-https.json
python -m scripts.load_coordinator --isolate-clients --users 100 --rounds 5 --caddy C:\tools\caddy.exe --output capacity-100-https.json
```

### Concurrent local CARLA recording

The November 4 run overlapped all 500 HTTPS status commands with measured
progress from a real 120-second CARLA recording on this PC. The 100 simulated
devices connected, all 500 commands were routed and completed, and all 100
cross-owner requests were denied. The measured p95 was **2.656 seconds**,
exceeding the two-second target. The coordinator's traced Python peak was
25,767,499 bytes. All 28 overload replies recovered within the existing budget.

The same-PC CARLA 0.9.16 episode used Low graphics on `Town10HD_Opt`, a
320 × 180 camera and run `73e77ee6-41b3-460b-8610-b5adeb3e3837`. It completed
with 2,401 telemetry frames. The capacity workload overlapped measured native
progress for its full 19.969-second window. Original world settings and actor
counts matched after the run, both fixture processes released their resources,
and the owned CARLA listener closed. This was a latency failure under local
simulator load. Production WAN performance and provider costs remain open.
The [recorded result](validation/s9-carla-overlap-20261104.json) retains the
workload timing, target failure and cleanup checks.

### OCI transfer and storage estimates

The [public S8 run](validation/s8-public-wan-20261104.md) used 3,608,375 decoded
response bytes for one 120-second recording review. At that review pattern,
100 devices doing one review each day would generate about 10.83 GB of
decoded evidence bodies over 30 days. At ten reviews per device per day the
figure would be 108.25 GB. These are workload examples. The provider measures
network traffic, which includes traffic outside the evidence workflow and
depends on compression, retries and protocol overhead.

Oracle lists the first 10 TB per month of outbound data transfer as free.
The North America, Europe and UK price above the allowance starts at
US$0.0085 per GB. Actual charges depend on the region, remaining allowance and
the account's usage. [OCI outbound pricing](https://www.oracle.com/apac/a/ocom/docs/corporate/pricing/oracle-paas-and-iaas-global-price-list.pdf)

The two passing 100-device fixtures used 1,163,264 and 1,175,552 bytes in the
coordinator database after 500 status commands each. The November 3 deployment
backup after public acceptance was 143,360 bytes. These are database file
sizes. OCI storage charges also depend on the allocated boot and block volumes,
their performance settings and any backups. Raw recordings in the current
workflow stay on the Companion. Compare the VNIC's `VnicToNetworkBytes` over
the review window with the application counters, and use OCI cost data for
actual billed transfer and storage. The VNIC metric includes other traffic on
that interface. [OCI VNIC metrics](https://docs.oracle.com/en-us/iaas/Content/Network/Reference/vnicmetrics.htm) and
[OCI block volume performance](https://docs.oracle.com/en-us/iaas/Content/Block/Concepts/blockvolumeperformance.htm)

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
Add `--preflight` to check the local signing inputs without building. The
preflight accepts the same compiler, version, SignTool, certificate and timestamp
arguments. `--output` and `--cache` are only required for an actual build.
The regular build runs the same checks before creating its output directory.
Windows checks the exact store certificate, public fingerprint, validity,
signing usage and private-key association. The preflight also checks the pinned
Inno Setup version and the SignTool path. Actual signing still establishes
whether the provider can use its key and the timestamp services respond.

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
with the release builder to sign those delivery fields.

The hosted Caddy configuration now reserves `/companion-updates/release.ps1`
and `/companion-updates/<version>/Setup.exe` on the dashboard's HTTPS origin.
It reads a dedicated, read-only mount and sends those files without compression
or caching. The directory, other file names, and writes return 404. The
default staging directory is `deploy/releases`. On the VPS, set
`POLTERGEIST_RELEASE_FILES_DIR` to a persistent directory outside the source
checkout before rolling out the proxy. Keep only approved release files in
that directory and use regular files, without symbolic links.

For the signed baseline, set `feed_url` in the bundled policy to the dashboard
origin followed by `/companion-updates/release.ps1`. Build the candidate with
`--installer-url` set to the same origin and its versioned `Setup.exe` path.
Verify the finished installer and metadata using the bundled publisher pin.
Stage each version's installer first and confirm its HTTPS response size and
hash. Then replace the feed metadata atomically within the mounted directory.
Keep earlier installers and matching signed metadata for compatible rollback.

On November 4, Caddy 2.11.7 served the new route over verified loopback HTTPS.
The Companion checked metadata separately and verified the downloaded 330,000-byte
fixture against its expected hash. Empty and unapproved paths returned 404 and
the upstream health route stayed reachable. The test used a process-local TLS
trust context and a fixture for publisher signatures. Public deployment and
the signed install, update and rollback are awaiting a Windows publisher identity.

The November 4 signing check used the x64 SignTool from Microsoft's Windows SDK
BuildTools `10.0.28000.2705`. Windows verified the tool's Microsoft Authenticode
signature before it ran. SignTool verified the installed Edge executable and its
RFC 3161 timestamp. The Companion's verifier accepted the same trusted fixture
with its exact public pin and rejected a different pin and a modified copy.
The release preflight rejected an absent certificate through the actual Windows
store before creating the requested output or cache directory. The focused
release, installer, update and proxy suite passed all 71 tests.
[Microsoft SDK BuildTools](https://www.nuget.org/packages/Microsoft.Windows.SDK.BuildTools/10.0.28000.2705)

The installed runtime also supports explicit check/download commands:

```powershell
python -m scripts.download_update --data-dir C:\path\CompanionPrototype
python -m scripts.download_update --data-dir C:\path\CompanionPrototype --download
```

The downloaded-file update CLI, run from the installed current version, remains:

```powershell
python -m scripts.update_companion --data-dir C:\path\CompanionPrototype --installer C:\downloads\Setup.exe --manifest C:\downloads\release.ps1 --install-root C:\path\Companion
```

The native **Close and install candidate** action verifies the reviewed release
again, checks queued work and closes the Companion. The review binds the
verified metadata, including the exact installer and bundle digests, to the
download and helper handoff. The helper waits for the old process and installer
mutex to exit, then checks the release identity, durable queue and both leases.
It verifies the staged copy against that same release before running Setup.
On success it reopens the verified installed release. If the reviewed files
change, select the candidate again. A change detected during staging retains
the backup and a failed receipt before any installer starts.

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
supports the current profile schema. The builder writes schema 6 through the
bundled runtime's current revision, now 7. Legacy manifests that omit the
window default to schema 6 only.
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

On November 4 the reviewed-release handoff shipped in a fresh offline validation
bundle. Its private interpreter rejected a substituted release with the same
version and a different bundle digest using fixture signature checks. The helper
created no backup and launched no installer. The unsigned validation installer
SHA-256 was `1d689679775bf127435dd25aeb946c988495b8b122f8a1e22b7683559673a78a`.
The installed build `private-7446dbcb83c105ad` passed the native dependency probe,
then executed and reopened both synthetic baseline and SDK runs through desktop
callbacks in a separate automated profile. The Windows registration used an
isolated validation identity. Publisher-signed installation and updates remain
pending.
