# S4 account and pairing foundation

The current source and S4 private companion build provide accounts, revocable
browser sessions, device ownership and explicit approval on the Windows device.
**S4 remains in progress.** There is no public coordinator address, hosted account
UI, WebSocket relay or authenticated remote execution. The companion reports
`remote_execution_enabled: false`. S5 dashboard integration and the S6 fault
acceptance matrix remain open. The previously delivered S3 installer is unchanged.

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
dependencies. Its outbound HTTPS and Windows credential protection use the
bundled interpreter and standard Windows APIs.

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

S4 still needs the authenticated outbound WebSocket relay, generation fencing,
owner-scoped command admission/cancellation/result reads, atomic local command
receipts, an explicit public result projection, and real two-account execution
acceptance across restarts/revocation. S6 must also exercise bounded queues,
disconnections and total elapsed network deadlines: the current enrollment
transport bounds bytes and socket inactivity, rather than the whole DNS/TLS/body
operation. Definitive abandonment of a never-committed/expired pending approval
also remains in that fault matrix; the current UI retains uncertain credentials.
The [pairing design](hosted-pairing-design.md) remains the acceptance
contract; these foundations do not close that plan step.
