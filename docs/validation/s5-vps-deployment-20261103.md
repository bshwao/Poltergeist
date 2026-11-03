# Poltergeist VPS deployment checkpoint on November 3, 2026

The [public dashboard](https://poltergeistapp.duckdns.org/app/) and coordinator
are running on the Oracle Ubuntu 24.04 x86-64 VPS. The temporary public page,
trusted certificate, dashboard render, health endpoint and public Companion
workflow passed.

## Release and access

- Source base `fe226ef` plus the S4–S5 and Caddy changes from the worktree.
- Staged archive SHA-256 `c484017289a983f98b48c56410e0da92e3bd0e3f1006a68fc655854a083eeabe`.
- Source archive including the final proxy configuration has SHA-256
  `9f88305e1429a2043b47416b90f33e2115a33f1611a390484d8f0f4f993dd6ce`.
- Remote release `/opt/poltergeist/s5-c4840172`, owned by `deploy:deploy` with mode `0750`.
- Coordinator image `sha256:978a1667f93ab8112d19ae808b8e59184c929c88586ef5d853eb382920d21832`.
- The existing `deploy` SSH access and Docker group membership worked. The setup
  used existing administrator access to assign ownership of the new release folder.
- The Oracle Default Security List for `poltergeist-vcn` now permits stateful
  IPv4 ingress for TCP 80 and 443. The original SSH rule remains in place.

## VPS checks

The release built on the VPS. Caddy served a temporary page with its local test
CA. The coordinator then replaced that page behind the same proxy. A client
verified the CA and hostname and received HTTP 200 for `/healthz` and `/app/`.
The coordinator returned `poltergeist-coordinator-v1`. SQLite passed `quick_check`.

For the public rollout, Caddy obtained a Let's Encrypt certificate for
`poltergeistapp.duckdns.org`. Windows verified the certificate and hostname
without a certificate exception. The current certificate expires February 1,
2027, and Caddy manages renewal. Public HTTP redirects to HTTPS with status 308.
The public health and dashboard endpoints returned HTTP 200. Edge rendered the
sign-in page. The dashboard sends a same-origin content security policy and
`Cache-Control: no-store`. The trusted local API path `/api/simulations`
returned 404.

The initial ECDSA certificate was trusted by Edge, but the packaged Python
runtime failed verification because its Windows certificate store included an
expired cross-signed ISRG Root X2. Caddy now uses Let's Encrypt's RSA YR2 chain
and a separate storage directory, `/data/caddy-rsa`, within its existing volume.
The earlier certificate files were retained. The unchanged bundled Python
verified TLS 1.3 with its normal trust store, and its WSS client received HTTP
403 when connecting without device credentials. The Compose and static suite
passed 12 tests after the change. See [Let's Encrypt certificate chains](https://letsencrypt.org/certificates/).

Caddy publishes TCP 80 and 443. The coordinator binds `127.0.0.1:8000` in the
shared container namespace and runs as UID/GID 10001. External checks connected
to 80 and 443 and could not connect to 8000 or 2019. Compose uses
`poltergeist-hosted_coordinator-data`, `poltergeist-hosted_caddy-data`, and
`poltergeist-hosted_caddy-config`. SQLite passed `quick_check` after the public
switch. The coordinator had zero restarts when checked. Docker is enabled at boot.

## Backup and rollback

From the release directory, run `bash backup-production.sh` for the public
stack. It uses the SQLite online backup API, checks the snapshot, and writes a
mode-`0600` file under `backups/`. The verified public snapshot is
`backups/coordinator-20261103T153413Z.db`, SHA-256
`9c4bcaa8a8c4667e94e1ee84dd38db9bec841b625f7f949443616a601b571c1c`.
The post-acceptance snapshot is `backups/coordinator-20261103T153857Z.db`.
Keep a private copy away from the VPS. Protect backups of the Caddy volumes
because they contain private certificate material.

The public environment file is `hosted.env`, mode `0600`. If a later app image
fails, the verified image is pinned locally as
`poltergeist-hosted-coordinator:s5-c4840172`. From the same release directory:

```sh
docker image tag poltergeist-hosted-coordinator:s5-c4840172 poltergeist-hosted-coordinator:latest
docker compose --env-file hosted.env -f compose.hosted.caddy.yml up -d --no-build --no-deps coordinator
```

The private configuration is `rehearsal.env`. If a later public rollout fails,
return to the verified private binding from the same release directory:

```sh
docker compose --env-file rehearsal.env -f compose.hosted.caddy.yml up -d --no-build
```

This command preserves all three named volumes. A database restore requires a
separate decision and verification against the selected snapshot.

DuckDNS confirmed registration of `poltergeistapp.duckdns.org` and updated the
IPv4 address to `147.5.109.245`. Local DNS and an independent public resolver
returned the expected A record. The VPS address is ephemeral, so update the
DuckDNS record if Oracle assigns a different address. The deployed app uses
the same HTTPS origin for the dashboard, account API and Companion relay.

## Public Companion acceptance

Chromium used its normal certificate verification and the unchanged Companion
used its bundled Python 3.12.15 with all 6,329 manifest files verified. The
isolated account registered and signed in on the public website, paired its
Companion with local approval, and used WSS to submit one baseline job. The
run completed on CARLA 0.9.16, map `Town10HD_Opt`, after 2.00000003 simulated
seconds with 41 frames. Run ID `956de261-64ab-46a5-879a-6493c29a1dfb` was
visible after reload. CARLA's world state restored. Revocation blocked web
history access and kept the local result. The test session signed out and the
Companion closed cleanly. The test-owned CARLA instance stopped after world
restoration, and port 2000 was released.

The sanitized receipt and result screenshot are under
`validation-evidence/s5-public-acceptance-20261103-retry/`. The S5 public
acceptance criterion passed on this PC. Independent-machine testing and the
S6 fault matrix remain separate gates.
