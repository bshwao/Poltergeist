# S6 VPS rollout, November 3, 2026

The [public dashboard](https://poltergeistapp.duckdns.org/app/) serves the S6
relay fix from `cb62e95`. The candidate image extends the exact running S5
image with the committed `coordinator/relay.py`. This kept the S5 dashboard
assets, runtime dependencies, proxy configuration and persistent volumes in use.

- Candidate image `sha256:c8976df749a2336591127f5aed83ff6f252e4f655be934780ca7c54cef4ccb54`.
- Live relay SHA-256 `f5b6f6353b4fcbc4578f055cd0fa384fcf5e4c9a090fa366c47c76cd8e013491`.
- Deployment files `/opt/poltergeist/s5-c4840172/s6-cb62e95`.
- Verified online SQLite backup `backups/coordinator-20261103T234839Z.db`,
  mode `0600`, SHA-256
  `b8a05cdf6297b04a957a059f37c3f212042539e2d5121fbe47f96b81b8e297e5`.

Only the coordinator container was recreated. Caddy kept the same container ID.
Public HTTPS health passed, Chromium rendered the sign-in page with normal TLS
verification, and SQLite `quick_check` returned `ok`. A disposable paired
Companion completed a five-frame synthetic
run before the restart. Its connection generation advanced from 1 to 2 after
the restart. Retrying the original command returned the same acknowledgement.
A fresh result request returned the same completed simulation ID, and the local
profile still had one job.

The previous image remains available under `poltergeist-hosted-coordinator:s5-c4840172`.
The [S5 rollback procedure](s5-vps-deployment-20261103.md#backup-and-rollback)
still applies. Rebuilding the old S5 release directory would replace the S6
image, so use the pinned S6 image or build from the current source for upgrades.

## Windows sleep and resume

Windows logged 404.9 seconds of Modern Standby on November 3. The packaged
Companion reconnected to the production VPS 3.4 seconds after Windows logged
resume. The fixture captured the server-issued connection generation advancing
from 1 to 2 on the PC. Retrying the original command preserved its
acknowledgement. A fresh result request returned the same completed five-frame
run, and the local profile still had one job. The test device was revoked and
the disposable Companion closed after verification.

## Windows Wi-Fi outage and recovery

Windows recorded a Wi-Fi disconnect at 8:55:37 p.m. and a reconnect at
8:58:59 p.m. on November 3, Toronto time. The fixture measured 202.3 seconds
between the first failed HTTPS check and the first successful check. The
Companion reconnected automatically 28.4 seconds after HTTPS became reachable,
advancing the server-issued connection generation from 1 to 2. Retrying the
original command preserved its acknowledgement. A fresh request retrieved the
same completed five-frame run, and the local profile still had one job. The
fixture revoked its test device and closed the disposable Companion.
