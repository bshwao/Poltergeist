# S8 public recording review on November 4, 2026

The fixture uses the public Poltergeist dashboard, a separate account and an
isolated packaged Companion profile on this Windows PC. CARLA 0.9.16 runs
`Town10HD_Opt` with Low graphics. This complements the [loopback capture,
quota and retention validation](s8-120-local-20261103.md).

## Earlier attempts and restart recovery

- The first public run, `e31c1983-b6d1-4888-956b-738fcf56c307`, completed
  120 simulated seconds with 2,401 frames. No recording evidence request was
  sent before completion. The viewer opened its recording and sample list.
  A case-sensitive test locator then failed to find the camera preview. The
  world restored, the fixture device was revoked, and the browser signed out.
- The corrected retry, `dc276afc-8d5f-4c9c-8cb7-499feb5b9d7f`, committed
  its completed result at 1:17:08 a.m. Toronto time. Windows logged shutdown
  at 1:17:29 a.m. and started again at 8:43:57 a.m. The browser did not write a
  transfer receipt. After restart, SQLite `quick_check` returned `ok`, the
  result still reported 2,401 frames, and 4,804 recording files including
  indexes totaled 608,403,160 bytes. The Companion's normal revocation flow
  revoked the fixture device and preserved those files.
- The morning run, `952e4039-4990-4ed5-85f3-d9a1ede50bf5`, completed
  120 simulated seconds with 2,401 frames. The browser verified early camera
  and LiDAR samples and a final-page preview. The harness then checked a
  nonexistent response field for chunk size and failed before saving its
  transfer metrics. The world restored, the fixture device was revoked, and
  the browser signed out. The corrected harness measures the length of the
  actual base64 field.

The shutdown retry establishes local persistence of completed results through
an orderly shutdown.

## Completed public acceptance

Run `84cf8b67-cf9c-469b-9174-ce4bc273b6ce` passed through the public HTTPS
dashboard and the packaged Companion's WSS connection on this PC. The dashboard
made zero recording evidence requests during execution. It showed the completed
result 226.036 seconds after submission and still showed it after a reload.

| Measurement | Result |
| --- | ---: |
| Simulated duration and native frames | 120 seconds, 2,401 frames |
| Native frame range | 119,605–122,005 |
| Camera and LiDAR metadata | 4,802 samples, 193 pages |
| Local raw recording payload | 606,172,576 bytes |
| Successful decoded evidence response bodies | 3,608,375 bytes |
| Browser metadata response p50 / p95 / max | 54.5 / 60.7 / 67.4 ms |
| Rate limiting | One 36-second `Retry-After` wait |
| Sample response chunks | 9, at most 87,384 base64 characters each |

The browser verified SHA-256 on the early camera and LiDAR previews and the
exact-frame preview at the end of the recording. The Companion restored CARLA
to asynchronous mode with no fixed time step and no remaining vehicle, walker
or sensor actors. The fixture revoked its device, verified that the revoked
device history returned HTTP 404, signed out and closed the Companion cleanly.

The response measurement covers the browser-decoded application bodies and
excludes HTTP headers and TLS overhead. The successful request latency excludes
the separately recorded rate-limit wait. These are single-client measurements
for the current public deployment and this PC.
