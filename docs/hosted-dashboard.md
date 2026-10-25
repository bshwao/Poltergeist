# S5 hosted experiment dashboard

The source website now supports account creation/sign-in, device pairing with
desktop approval, owned-computer selection, truthful connection/consent states,
baseline experiment configuration, launch, cancellation and measured results.
Thirteen starting recipes include the installation check and twelve unchanged
S0 stress presets. Synthetic and CARLA mode choices respect approved scopes;
managed agent installation/selection remains S7 work. The result view keeps
acceptance separate from completion and preserves zero versus unmeasured values.

S5 is **in progress**. Local synthetic browser acceptance passes. Public HTTPS
hosting, a real CARLA run launched through this website, the current packaged
companion, and independent-machine acceptance still need verification. S4 build G
is historical evidence for its recorded source; the new shared contracts are
not included in that archived build.

## Build and serve

The hosted site has a separate entry point and no dependency on the local
dashboard's localhost API, workspace bootstrap or environment URL override.

```powershell
npm --prefix frontend ci
npm --prefix frontend run build:hosted
python -m coordinator --database C:\PrivateCoordinator\coordinator.db `
  --origin https://localhost:8443 --host 127.0.0.1 --port 8443 `
  --certfile C:\PrivateCoordinator\localhost.pem `
  --keyfile C:\PrivateCoordinator\localhost-key.pem `
  --hosted-assets C:\Path\To\Poltergeist\frontend\dist-hosted
```

Use a certificate trusted for the service hostname. Open its `/app/` path and
configure that exact HTTPS origin in the companion. Deployment needs an actual
Python coordinator host with HTTPS/WSS, persistent private storage and one
process; a static-only website host cannot run this service. Proxy trust and
multi-process routing have not been added. No public domain or hosting provider
is configured by this change.

The optional server setting snapshots only `hosted.html` and bounded flat build
assets. It rejects links and serves no source maps, user files or SPA fallback
into API paths. CSP permits same-origin scripts/styles/connections with no
inline script or style permission. All served responses disable caching and
referrers. Static paths cannot proxy the local companion's API.

## Browser workflow

Create a code in the companion's Connection tab, enter it in the signed-in
website, then review and approve the displayed account and limits locally.
Start the local runtime, explicitly allow requests and Connect. Pairing alone
does not grant execution. Paused sessions reject every new remote operation;
previous coordinator receipts remain readable by their current authorized owner.
Revocation removes control and history access; accepted local work may finish.

The browser uses the HttpOnly session cookie and memory-only CSRF token. It
stores no password, pairing code, session credential or device credential in
URLs, local storage or session storage. The optional `#claim=<attempt UUID>`
fragment recovers only the current account's public claim status.

Request identities survive uncertain delivery in the open page. Retry uses the
same body and UUID, while durable history recovers admitted runs after reload.
Reloading before a request reaches the coordinator cannot recover an unadmitted
recipe. Results are versioned summaries; telemetry/recording downloads stay
local until S8. Current remote execution supports the baseline controller only.

## Validation

Run `npm --prefix frontend test`, both build commands, and the coordinator tests.
With Python test dependencies, OpenSSL and Playwright Chromium available:

```powershell
$env:POLTERGEIST_TEST_PYTHON = (Resolve-Path .venv\Scripts\python.exe)
npm --prefix frontend run test:e2e:hosted
```

The isolated fixture runs the production website build over real local HTTPS
and companion WSS, with separate browser contexts and source controllers. Its
control listener is separate, loopback-only, token protected and absent from the
production application. Companion transports verify the fixture CA and hostname.
The browser alone explicitly ignores that private fixture certificate; this is
functional browser evidence, not public TLS trust validation. Fixture credential
stores are in memory and no installer/native consent interaction is exercised.

The proof covers local approval, cross-owner history denial, lost browser HTTP
response after admission, exact-identity retry and one worker job, null/zero
metrics, reload, cooperative active cancellation, paused/offline views, stored
results, revocation, sign-out, strict CSP and absence of localhost API traffic.
Desktop and phone screenshots verify layout and horizontal bounds. The initial
Windows Python 3.12 browser run logged Proactor connection-reset callbacks
during browser transport teardown; those diagnostics are retained, not hidden.
This does not claim the S6 crash/sleep/failure matrix is complete.
