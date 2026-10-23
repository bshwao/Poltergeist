# Hosted accounts and companion pairing: S4 implementation design

Status: **design only**. This document defines the next private implementation
after the S3 work. It does not establish that accounts, pairing, authenticated
remote execution, a hosted service or a public security review exist. Follow the
[S0–S10 roadmap](scalability-plan.md) and retain its outstanding machine,
distribution and deployment gates.

The first implementation must run entirely on local test infrastructure, with
two accounts and a real companion using a disposable profile. It must require no
external identity account, email service, hosting provider or production secret.
Public deployment remains a separate gate.

## Application boundary and code ownership

Create a separate `coordinator/` package with its own ASGI factory, settings,
database metadata, migrations, authentication, pairing and relay services.
Settings must not inherit the local backend's `.env`, storage root or SQLite URL.
An explicitly configured SQLite database is sufficient for the first single
process integration prototype; this makes no multi-instance capacity claim.
Keep its persistence interface independent of `backend.database.Database`, which
supports only the local SQLite schema. Later hosting and S9 capacity work can
select and validate a different database without moving a PC's data there.

Do not mount `backend.main.app` inside the coordinator. Its `/agents`, readiness,
simulation, optimization, report and artifact routes trust local callers. They
can inspect or execute local code and expose machine paths or stored evidence.
The companion must continue to call local services directly, without opening an
inbound HTTP listener. The hosted service must not import or instantiate
`ApiRuntime`, `SimulationRunner` or the CARLA launcher.

Reuse the strict execute/cancel DTOs from
[runner.py](../shared/models/runner.py), scenario validation, the existing local
queue, agent fingerprint checks and artifact containment checks. Add versioned
coordinator/relay DTOs to shared contracts, schema export and generated TypeScript
without changing current local API response contracts. Retain the existing
`remote_execution_enabled: false` capability shape for the S1 prototype; define
an authenticated relay capability document separately rather than silently
changing that contract's meaning.

## Account and browser session mechanism

Choose provider-independent username/password accounts for the first private
vertical slice. Account IDs are server-generated UUIDs. Usernames use an explicit
ASCII format, for example `[a-z0-9][a-z0-9_-]{2,31}`, with a unique canonical
lowercase value. A username is not a verified email address or legal identity.
Git credentials and the user's GitHub sign-in have no role in application auth.

Use `argon2-cffi`'s password hasher, pinned in separate coordinator requirements.
Start with Argon2id, 64 MiB memory, three iterations and parallelism one; benchmark
the actual coordinator runtime before accepting those defaults. Store the PHC
encoded hash and rehash after a successful login when parameters change. Do not
implement a new password algorithm. This adds a dependency to the coordinator,
not to the desktop runtime. Argon2id is the preferred password-storage algorithm
in [OWASP's password-storage guidance](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html).

Accept passphrases of 15–128 Unicode characters, cap encoded input at 1 KiB, and
do not trim or truncate passwords. Reject invalid encodings before hashing. Run
hash work outside the ASGI event loop with a concurrency limit of two and no
unbounded waiting queue. Unknown accounts still perform a dummy verification;
login failures use one public error. Registration, login and pairing routes need
bounded request sizes and rate limits before expensive work. Initial testable
limits: five failed logins per account and twenty per source address per fifteen
minutes; return a retry time, do not permanently lock out the account. Never
trust forwarded source-address headers without an explicitly trusted proxy.

Use 32 random bytes for each opaque browser session, store its SHA-256 digest,
and resolve the account from that session on every request. Initial limits are
thirty minutes idle and twenty-four hours absolute, enforced server-side.
Rotate on login; logout and password change revoke sessions in the database.
Use a host-only `__Host-` cookie with `Secure`, `HttpOnly`, `SameSite=Lax` and
`Path=/`. No browser bearer token belongs in local/session storage. These cookie
properties and server-side expiration follow
[OWASP session guidance](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html).

Bind a separate random CSRF token to each session, deliver it in the login
response, and keep it in browser memory. Store this CSRF value server-side so an
authenticated read-only session-bootstrap response can return the same value
after page reload; it does not authenticate without the separate session cookie.
This endpoint must not mutate session state or return permissive CORS headers.
Require a custom request header and reject cross-origin requests before serving
the bootstrap response; deny preflight so a cross-origin script cannot send the
header. Do not rotate the token on an unauthenticated GET.
All state-changing browser endpoints require the session, exact allowed Origin,
JSON content type and matching CSRF header, including logout and device revoke.
Login and registration also enforce the configured Origin and JSON content type
to prevent login CSRF. Reject cross-origin browser use in the first private
prototype. S5 must resolve the actual frontend/API origin relationship: use a
same-origin gateway or another reviewed design, then verify it in a real browser;
do not weaken cookies simply to accommodate an unverified hosting arrangement.

Provide account creation/login/logout/password change in this slice. Password
recovery, email verification, MFA, public registration abuse controls and operator
recovery procedures remain explicit public-release work. Do not advertise these
private accounts as production-ready until those decisions and checks exist.

## Pairing transaction and credential custody

Pairing binds one companion identity and one ownership generation to exactly one
account. An account may own several devices; account sharing, device transfers
and delegated users are outside v1. A revoked identity must not be reassigned to
a different owner; explicit re-pairing creates a new identity. This prevents old
local run receipts from becoming readable by a new account.

The companion is configured locally with one coordinator HTTPS origin. Show that
origin in the pairing window; reject user-info, query strings, fragments and
redirects for credential-bearing requests. A test-only constructor flag may
permit loopback HTTP in isolated tests; normal configuration must require HTTPS.
Neither a browser nor a remote command may change this origin.

1. On a local **Pair** action, the companion creates a 256-bit private attempt
   secret and sends a create request. The coordinator stores its digest and
   returns an attempt UUID, reserved device UUID, a random ten-character
   unambiguous base-32 code, five-minute expiry and a three-second poll interval.
   Store only the code digest. Limit creation per source and total live attempts;
   a code collision is retried before publishing the attempt.
2. The signed-in browser submits the code. In one conditional transaction,
   `pending` becomes `claimed` with the session's account UUID. A second claimant
   cannot replace it. Return the same general invalid/expired response for a
   nonexistent or unusable code. Limit guesses per account and source, and store
   counters with expirations so a process restart cannot bypass the limit.
3. Polling requires the private attempt secret, not the human code. The companion
   displays the configured service origin, claimed username/account UUID and
   exact requested scopes. It asks the local user to approve or deny. Browser
   claim alone never establishes ownership or authorizes execution. Expiry still
   applies while waiting for local approval.
4. Before approval, generate a 256-bit device credential locally and atomically
   persist a pending enrollment record protected for the current Windows user.
   The record includes origin, attempt/device/owner IDs, approval request UUID,
   intended policy and the private attempt/device secrets. Then send approval,
   authenticated by the attempt secret, including the expected claimed account,
   attempt revision, approval UUID and credential. The server hashes the
   credential immediately; it never persists or logs the raw value.
5. One transaction checks expiry, claim, revision and secret; creates the owned
   device, exact grants and credential digest; and changes `claimed` to
   `approved`. Repeating the same approval UUID and digest returns the same
   nonsecret outcome. A changed payload conflicts. The companion confirms the
   device connection using its saved credential before finalizing local paired
   state. A lost approval response therefore requires reconciliation, not a new
   credential or another ownership claim.

The state machine is `pending -> claimed -> approved`, with `pending/claimed ->
denied | expired | cancelled`. Terminal states cannot transition again; the one
exception is an identical approval retry returning its original outcome without
another transition. Use conditional updates plus unique constraints, not a
process-only mutex. Compute expiry from server UTC time. Prune expired secrets
and human-code digests after a bounded reconciliation window; retain only
nonsecret audit information according to a configured retention policy.

The public human code is not a device credential. Code entropy, expiry and
guessing limits are distinct controls, as described by
[RFC 8628's device-flow security analysis](https://www.rfc-editor.org/rfc/rfc8628.html#section-5).
This custom local-confirmation flow does not claim OAuth conformance.

Use Windows DPAPI through a small tested credential-store adapter, in current-user
scope, without `CRYPTPROTECT_LOCAL_MACHINE`; free native buffers on all paths.
Write only the protected blob under the companion profile. DPAPI ordinarily ties
decryption to the user's logon and computer, with exceptions such as roaming
profiles; it does not protect against malicious code running as that user.
See [Microsoft's CryptProtectData contract](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata).
DPAPI failure must stop pairing rather than fall back to plaintext. Test suites
can inject an in-memory store, but packaged Windows verification must use DPAPI.

## Minimum metadata and constraints

All coordinator tables belong to the coordinator database. UUIDs below are
validated identifiers, not authorization by themselves.

| Table | Essential fields and constraints |
| --- | --- |
| `accounts` | UUID PK, unique canonical username, encoded password hash, created/disabled timestamps, auth revision |
| `sessions` | UUID PK, unique session-token digest, account FK, separate CSRF token, created/last-used/absolute-expiry/revoked timestamps, auth revision |
| `pairing_attempts` | UUID PK, unique reserved device UUID, private-secret/code digests, state, expiry, claimed account FK, revision, approval UUID/digest, terminal timestamp |
| `devices` | UUID PK, immutable owner FK, bounded display name, ownership generation, policy revision, created/revoked timestamps, last authenticated contact |
| `device_credentials` | UUID PK, device FK, unique token digest, ownership generation, created/expiry/revoked timestamps; one active credential in v1 |
| `device_grants` | Device FK, ownership generation, scope; unique tuple; scopes are a closed enum |
| `commands` | Device/owner/generation FKs, request UUID, operation, canonical payload digest, bounded validated payload, acceptance/delivery/status timestamps, local simulation UUID; unique `(device, generation, request UUID)` |
| `security_counters` | Bounded bucket key, count, window/expiry; atomic updates for auth and code-guess limits |
| `security_events` | Event UUID/time, account/device/attempt/command identifiers when known, closed event/outcome codes; no credentials, request bodies or local diagnostics |

Foreign keys and conditional revisions enforce ownership/terminal transitions.
Every service query starts from the authenticated account or device identity and
includes that ownership scope. Cross-owner resource lookups return a uniform 404;
logs may record the denial without exposing existence to the caller. Device
credentials cannot act as browser sessions, and sessions cannot open a device
connection. Do not accept owner IDs from request bodies as proof of identity.

Keep a separate local remote-command receipt table in the PC database, migrated
with the local schema. It stores approved device/owner/generation, request UUID,
canonical digest, operation and local simulation UUID. Use
`SimulationService.submit(..., on_created=...)` to persist the receipt in the same
transaction as the queue job. Receiving a command without this durable boundary
must never start execution. Full reconnection/fault reconciliation belongs to S6,
but duplicate command protection is required before the first remote execute.

## Outbound connection and command checks

Use a dedicated `/v1/device/connect` WebSocket initiated by the companion, with
the device credential in an Authorization header during the TLS handshake.
Never put secrets in URLs or WebSocket subprotocols. Reject browser Origin/cookie
authentication on this device-only endpoint. Server and client logs must redact
authentication headers and enrollment secrets. Pin `websockets` as an explicit
companion runtime dependency if used; it is currently present transitively via
Uvicorn. `httpx` is a development dependency today and cannot be assumed bundled
for runtime pairing requests.

Use a discriminated `poltergeist-relay-v1` envelope with a closed message-type
union, device/ownership generation, request UUID and validated body. Nested
execute/cancel bodies may reuse `poltergeist-runner-v1`. Reject unknown types,
fields, protocol versions and nonfinite values before dispatch. Initial bounds:
256 KiB encoded messages, thirty-two queued command messages per device, a
thirty-second heartbeat and ninety-second dead-connection timeout. Do not relay
recording bytes through this command channel. Coalesce replaceable progress
updates; retain acceptance/final outcomes durably. These are prototype limits to
measure, not validated S9 capacity targets.

Allow one authenticated live connection per device. Reconnecting creates a new
connection generation and fences/closes the old sender. Connection generation is
not ownership generation. Recheck database credential validity, ownership and
grants before accepting a command, before delivery and before accepting replies;
the companion also checks its approved local binding and current policy. A
permission decision cached at handshake is insufficient. In the first single
process service, revocation commits before the owned socket is closed and pending
undelivered commands are rejected. A device already disconnected remains revoked
on reconnect. Multi-instance invalidation is an S9 prerequisite, not an implied
property of an in-memory connection registry.

Initial scopes are `status:read`, `run:execute:test`, `run:execute:carla`,
`run:cancel` and `result:read`. Pairing requests these explicitly and local
approval may grant a subset. Every non-baseline agent requires a separate local
UUID-and-fingerprint approval; new registrations inherit no remote trust. Begin
the integration proof with baseline only. Remote requests must not register
agents, install packages, select interpreters/models/paths, edit configuration,
start shells, or access arbitrary local runs/files.

Device-local policy caps duration, ticks, queue capacity and sensor recording,
and controls map reload and graphics changes. Start with a visible local policy
of at most 120 simulated seconds, 10,000 ticks, ten waiting remote runs, no map
reload and no raw capture. Local approval can lower those bounds; expansion needs
another explicit local action. Reject options exceeding policy rather than silently
changing the requested experiment. Resolve approved agents through the existing
registry fingerprint verification immediately before queue acceptance and again
through the existing execution path. Cancellation requires the same authorized
device/owner/generation and an associated remote execution receipt; possession of
an unrelated local simulation UUID grants no access.

Revocation denies new remote work and result reads immediately at the coordinator.
Already accepted local work may finish and remain local; do not claim remote
revocation forcibly stopped CARLA. The local Stop/Cancel controls always remain
available. Local unpairing disables the relay and attempts authenticated server
revocation before erasing credentials; if offline, display that server revocation
still needs account-side confirmation. Re-pairing requires a new explicit local
approval and does not expose an old identity's stored runs.

## Public progress and result projection

Do not serialize `SimulationResult`, `SimulationRecord`, `JobInfo`, diagnostics or
agent registrations directly onto the public connection. Their nested
`AgentSnapshot` contains absolute paths, interpreter/model/runtime file lists and
arbitrary agent configuration. Exception strings and readiness details can also
reveal local files. Original local provenance must remain unchanged.

Define `HostedRunSummaryV1` with an explicit constructor/allowlist:

| Public field | Source and rule |
| --- | --- |
| Protocol, device/owner generation, request/run UUIDs, revision | Validated remote receipt; coordinator derives owner, never trusts a reply's owner field |
| Mode, lifecycle status, progress, queue position, cancellation-requested flag, timestamps | Typed local lifecycle projection; cancellation requested is not cancellation completed |
| Scenario digest and name | Canonical accepted scenario; bounded user-authored name, no machine configuration |
| Agent identity | `baseline`, or locally approved registration UUID, bounded manifest name/version/protocol, package digest; no configuration, manifest extensions or paths |
| Metrics and evaluation coverage/settings | Explicit `SafetyMetrics` projection, preserving nulls and observed coverage; no invented zero for unavailable evidence |
| Runtime | Adapter, CARLA client/server versions, map name, seed and fixed delta; no process IDs or filesystem/interpreter details |
| Provenance summary | Closed observation/source/control/navigation enums and approved sensor identities/types; no arbitrary diagnostic strings or raw observations |
| Error | Closed public error code and fixed readable message; original exception stays local, with a correlation UUID for support |
| Evidence availability | Explicit state such as `local_only`, `available`, `device_offline` or `missing`; opaque evidence UUID/kind/media type and known size/digest, no storage path or download URL |

Progress uses the same safe lifecycle subset. Unknown or missing data remains
unknown. Public evidence UUIDs resolve only through owner/device/run-scoped
lookups; they are not bearer download capabilities. S4 may advertise local-only
evidence, but must not present downloads as implemented. Authorized bounded
transfers, raw telemetry paging and recording delivery are S8 work. A public
contract addition requires another explicit disclosure review; new local fields
must not appear automatically.

## Implementation and validation gates

Each step below produces executable evidence. Run tests against disposable
databases/profiles and fake or synthetic runners unless CARLA behavior is the
specific subject. Preserve existing developer workflows and private desktop data.

1. **Contracts and isolated account service.** Add coordinator factory/settings,
   own migrations, hashing/session/CSRF services and schema-generated DTOs. Tests
   cover password verification/rehash, username races, bounded malformed inputs,
   dummy unknown-user checks, login throttling, expiry/logout/password-change
   revocation, cookie attributes, Origin/CSRF denial and absence of local backend
   routes. Prove no CARLA import, local DB creation or local `.env` inheritance.
2. **Pairing service with a test credential store.** Exercise every transition,
   TTL boundary, rate limit, competing account claim, stale approval revision,
   approval replay and changed-payload conflict. Kill/restart the service between
   claim and approval. Lose the approval response deliberately and prove the
   saved credential reconciles one owner/device, with no plaintext stored or
   logged. Inject failed local credential persistence and prove approval was not
   sent. No execute command is enabled at this step.
3. **Real companion pairing and authenticated relay.** Integrate local
   approve/deny/unpair UI, DPAPI and bounded outbound I/O without blocking the GUI.
   Test real TLS with a fixture CA, hostname failure, untrusted certificate,
   credential-bearing redirect refusal, malformed/oversized messages, blocked
   queues, heartbeat expiry and fenced duplicate sockets. Verify packaged Python
   contains its actual transport/TLS dependencies and same-user DPAPI round-trip;
   a substitute in-memory store does not prove packaged credential protection.
4. **Owner-scoped synthetic execution.** Pair two companion profiles to two
   accounts and use the existing queue with the durable receipt hook. Prove one
   accepted execution for repeated request IDs, conflict for changed payloads,
   and correct cooperative cancellation. Attempt each operation with swapped
   device/run/agent/evidence IDs. Another account must neither read data nor cause
   queue writes. Test denied map reload, raw capture, unapproved agent, changed
   agent bytes and unknown operations. Drop acknowledgements before/after queue
   acceptance; no blind resubmission may start a second job.
5. **Revocation and disclosure audit.** Revoke while connected, while a command
   waits for delivery, after local acceptance and while disconnected. Old
   credentials, cached sockets, stale generations and a service restart must not
   restore control. Record which already accepted work finishes locally. Fill
   agent snapshots/configuration/errors with sentinel paths and secrets and
   prove public contracts, logs and security events never contain them. Preserve
   null metrics, incomplete coverage and explicit evidence availability.
6. **S4 private acceptance, then external deployment gate.** Record a repeatable
   two-account install-profile demonstration: local approval, owned synthetic
   control, readback, second-account denials and revoke/reconnect denial. This
   proves the private S4 boundary only. S5 still requires actual HTTPS website
   integration and baseline CARLA execution; S6 requires the wider crash/sleep/
   reconnect/cancel fault matrix. Public hosting additionally requires chosen
   infrastructure, real TLS/proxy/cookie validation, backups and secret custody,
   abuse/recovery policy and security review. S1/S3 independent-machine and S10
   signed-release/external-beta gates remain open until separately evidenced.

Do not mark S4 complete from DTOs, in-process authentication mocks or a green
happy-path test alone. The decisive evidence is that an owner can approve and
control a paired companion, revoke it, and demonstrate that another authenticated
account cannot read its data or submit work, across real transport and restart.
