# S5 hosted experiment dashboard

The source website now supports account creation/sign-in, device pairing with
desktop approval, owned-computer selection, truthful connection/consent states,
baseline experiment configuration, launch, cancellation and measured results.
Thirteen starting recipes include the installation check and twelve unchanged
S0 stress presets. Synthetic and CARLA mode choices respect approved scopes;
managed agent installation/selection remains S7 work. The result view keeps
acceptance separate from completion and preserves zero versus unmeasured values.

The source also includes **Guided lab** and **Discovery**. An updated companion
negotiates the private experimentation channel and retains all suites, approvals
and trial evidence locally. Execution remains baseline-only, scoped to current
local consent and capped at twelve executions per plan. See
[guided experimentation and discovery](guided-experimentation.md).

The [public dashboard](https://poltergeistapp.duckdns.org/app/) is live on the
Oracle VPS. Public S5 acceptance passed with the packaged Companion and a real
CARLA baseline whose result persisted after reload. See the
[public deployment record](validation/s5-vps-deployment-20261103.md) and the
[local S4–S5 validation](validation/s4-s5-local-20261102.md). S4 build G
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
process. The deployed Caddy mode trusts forwarding headers only from the
coordinator's loopback interface. The coordinator uses a single worker.

The optional server setting redirects `/` to `/app/` and snapshots only
`hosted.html` and bounded flat build assets. It rejects links and serves no
source maps, user files or SPA fallback into API paths. CSP permits same-origin
scripts, styles and connections without inline script or style permission. All
served responses disable caching and referrers. Static paths cannot proxy the
local companion's API.

## Deploy the same-origin service

For Caddy-managed HTTPS, use `compose.hosted.caddy.yml`. Set
`POLTERGEIST_HOSTED_HOSTNAME` to the registered public DNS name, with its A
record pointing at the VPS. Caddy owns TCP 80 and 443 and retains its
certificate data in `caddy-data`. The coordinator shares Caddy's network
namespace and binds HTTP to `127.0.0.1:8000`. In this mode Uvicorn accepts
forwarded scheme and client address only from loopback. Caddy preserves the
request Host and replaces client-supplied forwarding headers. The application
continues to enforce the configured HTTPS origin. Caddy's admin API is disabled.

```sh
sudo docker compose --env-file /srv/poltergeist/hosted.env -f compose.hosted.caddy.yml config --quiet
sudo docker compose --env-file /srv/poltergeist/hosted.env -f compose.hosted.caddy.yml up -d --build
```

Keep the project name and `coordinator-data`, `caddy-data` and `caddy-config`
volumes when replacing containers. On a host that uses Caddy for a placeholder
site, use the same project and Caddy volume names when switching to the app.
Compose should recreate the services with the new configuration. Removing
volumes would discard the stored account data and certificate state.

### Direct TLS alternative

`compose.hosted.yml` builds the separate coordinator and hosted frontend in one
image. One process serves the site, HTTPS API and WSS relay on one origin. The
container uses UID/GID 10001 and a named volume for its SQLite database. Keep
that volume local to one host and run one replica. The ordinary
`docker-compose.yml` stack is for the trusted local simulation API.

Use a Linux Docker host with a DNS name and a trusted TLS certificate for that
name. Prepare a real directory outside the checkout containing `fullchain.pem`
and `privkey.pem`. Give the container's GID 10001 directory traversal and file
read access while keeping the key private. Keep the certificate and key out of
the Docker build context. Restart the coordinator after certificate renewal.

Create `/srv/poltergeist/hosted.env` with the settings for your host.

```dotenv
POLTERGEIST_HOSTED_ORIGIN=https://experiments.example.org
POLTERGEIST_HOSTED_TLS_DIR=/srv/poltergeist/tls
POLTERGEIST_HOSTED_BIND=0.0.0.0
POLTERGEIST_HOSTED_PORT=443
```

The defaults bind the published port to loopback at 8443. The origin must match
the browser-visible scheme, hostname and port. Publish only the coordinator
port. With a load balancer, use TCP passthrough so the coordinator receives
TLS and the original Host header. It ignores forwarded proxy headers.

To show an installer download link, set `POLTERGEIST_COMPANION_DOWNLOAD_URL`
for Compose or `VITE_COMPANION_DOWNLOAD_URL` before running the native
`npm --prefix frontend run build:hosted` command. Use a public HTTPS URL for the
approved installer without credentials or a fragment. The URL becomes part of
the frontend bundle. An unset value leaves setup guidance in place without a
download link.

Run these commands from the source checkout on that host.

```sh
docker compose --env-file /srv/poltergeist/hosted.env -f compose.hosted.yml up -d --build
curl --fail --show-error https://experiments.example.org/healthz
curl --fail --show-error --location https://experiments.example.org/
```

Before onboarding users, stop the service and snapshot its private data volume
using the host backup tools. Test restoration on an isolated host. Retain the
volume when replacing the container and repeat both HTTPS checks on the deployed
hostname. Complete the browser workflow below with the current packaged
Companion, including a second account denied access to the owner’s computer,
a real CARLA result and revocation.

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
recipe. Results are versioned summaries. The [S8 checkpoint](evidence-capacity-release.md)
adds requested telemetry pages, recording discovery, paged sample selection and
verified camera/LiDAR previews with separate local evidence consent. Preview
storage is limited to one sample up to 4 MiB. Complete recording exports remain
local. CARLA camera/LiDAR capture needs its own default-off local approval;
synthetic requests cannot enable capture. Current remote execution supports the baseline controller only.

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

To exercise the deployment image, start a disposable Compose project bound to
`127.0.0.1:8058` with origin `https://localhost:8058` and a localhost TLS
certificate. Set `POLTERGEIST_HOSTED_EXTERNAL_CA` to the CA file and run the
same Playwright command. The fixture verifies the loopback service over TLS
and starts only its isolated Companion controls. Use an empty coordinator
volume because the browser registers test accounts. The browser uses its
fixture certificate exception. The Companion verifies the CA and hostname.

The separate live proof is opt-in: set `POLTERGEIST_HOSTED_LIVE_CARLA=1` before
running the same browser command, with matching CARLA already running on the
companion's configured endpoint/map. Point `POLTERGEIST_TEST_PYTHON` to the
Python 3.12 environment with the CARLA 0.9.16 client. The fixture checks for
the client before starting. The test selects `carla.spec.ts` and the normal
companion runtime, with no synthetic adapter override. The test never starts or
reloads the simulator. The original 0.5-second Town10HD_Opt run returned 11 frames, a real
CARLA runtime/provenance summary, and durable results after reload. Native RPC
also verified actor IDs and world settings before/after. See the
[live receipt](validation/s5-carla-browser.json) and
[public summary](validation/s5-carla-summary.json), including partial coverage
and null TTC. The original absolute-placement and numeric-normalization failures
remain recorded; the successful repeat does not relabel them as passes.

The quick check uses the existing template's relative actor placement. Command
validation canonicalizes template parameters through the same scenario validator
as the worker before hashing: JavaScript's `2` and a template's `2.0` must not
change the frozen recipe during admission. Receipt IDs still bind to canonical
content; a changed request cannot replace an accepted recipe.
