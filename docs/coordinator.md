# S4 account, pairing and private relay

The current source and S4 private companion build provide accounts, revocable
browser sessions, device ownership and explicit approval on the Windows device.
**S4 remains in progress.** The source now includes an authenticated outbound
WebSocket relay, durable command receipts and a safe baseline result projection.
Normal companion execution still reports `remote_execution_enabled: false`;
the relay execution proof uses an explicit constructor-only private test gate.
There is no public coordinator address or hosted account UI. S5 dashboard
integration and the S6 fault acceptance matrix remain open. The previously
delivered S3 installer is unchanged.

## Separate service

The `coordinator` package is an independent ASGI application. Importing or
constructing it does not initialize a database, read a developer `.env`, import
`backend.main`, start a local worker or connect to CARLA. Its lifespan explicitly
initializes a separately owned SQLite database. An unrelated database is refused.

For a private development service, install `requirements-coordinator.lock` and
the project with its `coordinator` extra. Run:

```powershell
python -m coordinator --database C:\PrivateCoordinator\coordinator.db `
  --origin https://localhost:8443 --host 127.0.0.1 --port 8443 `
  --certfile C:\PrivateCoordinator\localhost.pem `
  --keyfile C:\PrivateCoordinator\localhost-key.pem
```

Use a certificate trusted by the intended client for that hostname. Production
configuration requires HTTPS; the CLI has no insecure fallback. The test-only
loopback HTTP setting is unavailable through normal companion configuration.
The service checks its configured host/scheme, rejects redirects in the device
client, and does not trust ambient proxy headers. Public deployment, operational
backup/retention, account recovery and multi-user capacity are still pending.

The companion bundle excludes the hosted `coordinator` package and its Argon2
dependencies. Its outbound HTTPS, pinned `websockets` transport and Windows
credential protection use the bundled interpreter and standard Windows APIs.

## Accounts and request boundaries

Accounts use lowercase ASCII usernames and Argon2id password hashes. Passwords
retain intentional whitespace. Browser credentials are opaque, hashed in the
database, revocable, and purpose-separated from pairing/device credentials.
Sessions have a 30-minute idle limit and a 24-hour absolute limit. Password
changes invalidate all sessions for that account; login replaces the previous
session in the same browser cookie.

The secure, HttpOnly, same-site cookie is accompanied by a separate CSRF token.
Browser mutations require the exact configured Origin, JSON and that token.
Read/bootstrap endpoints require `X-Poltergeist-Client: browser`. Device endpoints
reject browser cookies/Origin. Requests have bounded headers/bodies, duplicate
security headers are rejected, and errors omit submitted secrets. CORS and the
local simulation API are not exposed by this service. Rate-limit counters
survive service restarts; Argon2 work has a bounded concurrency budget.

The implemented browser endpoints are `/v1/auth/register`, `/v1/auth/login`,
`/v1/auth/session`, `/v1/auth/logout`, `/v1/auth/password`, `/v1/pairing/claim`,
and owner-scoped `/v1/devices` reads/revocation. They are foundations for S5;
there is no account website in this slice.

## Private relay and command boundary

The companion initiates `/v1/device/connect` over WSS with a device credential in
the Authorization header. Redirects, browser Origin/cookies, query parameters,
subprotocol credentials and ambiguous security headers are refused. One live
connection owns each device; reconnect increments a persisted connection
generation and fences the old socket. Ownership generation is a separate value.
The prototype has a 256 KiB message limit, thirty-two pending commands per device,
thirty-second heartbeats and a ninety-second dead-connection timeout. The
in-memory connection registry requires one service worker.

Owner requests use `POST /v1/devices/{device_id}/commands`; readback uses
`GET /v1/devices/{device_id}/commands/{request_id}`. Closed typed operations are
`execute`, `cancel`, `status` and `result`. Scope, ownership, credential and
generation checks are repeated at admission, delivery, reply and readback.
An execute acknowledgement identifies acceptance; it does not report completion
or include result metrics. Result reads separately require `result:read`.

Execution uses the existing local queue and baseline controller. The remote
contract omits package, interpreter, model, configuration and filesystem choices.
It admits at most 120 simulated seconds and 10,000 ticks, subject to the lower
locally approved policy, and permits no map reload, graphics changes or raw
capture. A zero waiting allowance means an idle worker slot; it cannot add a
waiter behind active or queued local work. Cancellation and result requests must
resolve an execution receipt under the same origin/device/owner/generation.
An unrelated local simulation UUID grants no access.

Migration 5 inserts local acceptance receipts in the same transaction as the
simulation and worker job. Identical retries return the original run; changed
content conflicts, and a losing competing submission rolls back its tentative
run/job. Cancel receipts commit before the runner is signalled. Separate
persisted projection revisions cover queue and cancellation changes that need
not advance the simulation revision. Coordinator schema 2 retains command
outcomes across restarts while preserving the original schema migration.

`HostedRunSummaryV1` is built field by field. It retains unavailable metrics as
null, measured coverage, accepted scenario identity and a bounded runtime and
provenance subset. It omits local snapshots, paths, diagnostics and exception
strings. Evidence is explicitly `local_only` or `missing`; downloads and raw
telemetry transfer are still S8 work. Local Stop/Unpair disables admission before
joining the relay, and retains runtime/profile ownership if shutdown is blocked.
Revocation denies new commands/results; accepted local work may finish locally.

## Local pairing consent

1. In the companion's **Connection** tab, save one HTTPS coordinator origin.
   Select a computer name and requested permissions, then create a pairing code.
2. The signed-in account claims the ten-character, five-minute code through
   the coordinator API. Claiming it alone creates no owned device or execution
   permission. Another account cannot replace the claimant.
3. Choose **Refresh / resume** on the companion. Review the read-only saved
   server, username/account UUID and selected permissions, then explicitly
   approve or deny the claim. Editing the address field does not change the
   displayed saved server or silently redirect approval.
4. Before approval is sent, the companion atomically saves its credential and
   approval identity using current-user Windows DPAPI, bound to this profile.
   There is no plaintext fallback. Failed protection leaves approval unsent.
5. The coordinator stores only secret hashes and creates ownership, policy and
   grants in one transaction. Exact approval retries are idempotent. After a
   lost response or restart, **Refresh / resume** confirms the saved credential;
   it can also recover after the original terminal attempt was cleaned up.
6. **Revoke pairing** invalidates device access and clears the local protected
   record after confirmation. A lost revoke response can be retried. An
   unapproved code may be discarded. A saved pending/paired credential cannot
   be silently discarded or rebound to another server.

Snapshots/diagnostics project explicit public fields and omit credentials and
approval payloads. A damaged enrollment is reported without hiding local runs.
Rejected coordinator addresses are omitted from rendered validation errors and
normal companion logs, including malformed port/host errors from URL parsing.
Saving CARLA/setup choices preserves the configured coordinator. Local runtime
status polling does not initiate a network connection.

The current approval policy records scopes for status, synthetic/CARLA execution,
cancellation and result reads. Bounds are at most 120 seconds, 10,000 ticks and
10 waiting runs, with no map reload, graphics change or raw capture. The preview
UI approves only the baseline controller. These grants are persisted, but no
remote command dispatcher exists yet, so they cannot currently start work.

## Evidence and remaining acceptance

See the [sanitized foundation receipt](validation/s4-pairing-foundation.json) for
the exact checked scope and private bundle identity. Tests cover two-account
isolation, claim/approval races, TTL, revocation, CSRF/cookies, protected storage,
lost responses, restarts, secret-free projections and real desktop callbacks.
Real loopback TLS checks use a short-lived fixture certificate trusted only by
the validation clients. They do not install a root CA or prove public hosting.

The source acceptance test now uses two account sessions and two started
companions over real loopback TLS. Alice executes and cancels synthetic work;
Bob's worker remains empty while swapped device/run requests are denied. It
drops the first acceptance acknowledgement, reconnects to one original local
run, reads a safe result, revokes while connected and confirms denial after a
coordinator restart. Windows runs use actual current-user DPAPI; other platforms
use an explicit memory fixture and make no Windows protection claim.

Private build F also passed one packaged profile/two-account execution proof
across two independent bundled processes: DPAPI enrollment, outgoing TLS/WSS,
lost-acknowledgement retry, completed results, queued and active cancellation,
durable reopening and revocation denial. It checked all 167 first-party sources
and 6,078 manifest files against the frozen bundle, with every imported desktop
module inside that bundle. The coordinator stayed in the separate source runtime.
This proof uses the explicit private constructor flag and exercises no GUI relay
controls.

The normal desktop still needs a reviewed local relay connection/control flow
and combined native desktop/install-profile acceptance before its execution gate
can be enabled.
This API-driven private proof does not establish a hosted website, visual UI
acceptance, CARLA execution or a public deployment. See the
[relay receipt](validation/s4-relay-prototype.json) for the current evidence.
S6 must also exercise the wider disconnection/crash/sleep matrix and total
elapsed network deadlines: the current enrollment
transport bounds bytes and socket inactivity, rather than the whole DNS/TLS/body
operation. Definitive abandonment of a never-committed/expired pending approval
also remains in that fault matrix; the current UI retains uncertain credentials.
The [pairing design](hosted-pairing-design.md) remains the acceptance
contract; these private boundaries do not close that plan step.
