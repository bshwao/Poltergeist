# S6 local reliability validation, November 3, 2026

Validation used the `publicity` worktree at base `84007f9`, isolated Companion
profiles and loopback HTTPS/WSS coordinators. The later public deployment is
recorded separately in the [S6 rollout](s6-vps-rollout-20261103.md).

## Delivery and account isolation

The coordinator now revalidates the full connection grant and heartbeat deadline
inside the socket send lock, immediately before passing a durable command to the
socket. A revocation, replacement generation or deadline expiry while delivery is
waiting can therefore prevent the old connection from receiving the command.
The replacement generation can still pick up authorized pending work. Two
regressions failed before the fix. All three fault variants passed in the final
40-check relay and transaction batch. The separate 294-check source batch
started before the send-lock change and passed.

All three regular hosted Chromium checks passed. In the two-account workflow,
retrying a lost acceptance created one original job. During the next job's
accepted cancellation, the fixture cut Alice's established TCP connection and
refused its reconnect attempts. The dashboard showed Alice offline and Bob
connected. Alice's run finalized locally as cancelled while disconnected. The
browser recovered the cancellation after reconnect, with two total Alice runs
and zero Bob runs. Windows logged TCP reset exceptions during fixture teardown.

The opt-in CARLA browser test cut the relay after a measured tick. The same
simulation ID completed locally while the dashboard still showed the device
offline and made no completion claim. Automatic reconnection surfaced the
original completed result, with one local job. The run recorded 601 frames
over 30 simulated seconds on CARLA 0.9.16. The browser verified camera and LiDAR
samples and recovered the completed result after reload. Native RPC confirmed
the owned test episode returned to asynchronous mode without dynamic actors
before its processes were stopped.

## Native CARLA recovery

The packaged CARLA 0.9.16 process-loss check exercised a Low `Town10HD_Opt`
instance. The owned Companion child exited with code 79 after durable running
state. An independent RPC client observed two orphaned vehicles and synchronous
settings. A new Companion process recovered the original job as failed. A second
profile refused to use the same episode without changing its actors or settings.
On an explicitly restarted test episode, an independent baseline finished with
11 frames and the old request remained failed exactly once. A live competing
profile also failed to acquire the active endpoint. Fresh RPC observations
confirmed restoration of the asynchronous world and original settings.

That crash and ownership run used unsigned bundle manifest SHA-256
`1172c345b22091f94f02ffb129918ecef739302286bc2e1b4e720317cac25794`.
The refreshed portable and installed Companion use manifest SHA-256
`1e0ad36238714f81b4108a63557229e1d73f4c4f8d5cad0a48875e65e69f2625`.
The coordinator source fix is outside the Windows Companion bundle.

The refreshed package passed both native cancellation cases. A cancellation
accepted after the first measured tick produced a cancelled result with two
frames. A cancellation accepted after native cleanup and before the final write
retained the completed 11-frame result. Both acknowledgements were nonterminal
at acceptance, and both results recorded the cancellation request. In each case,
a separately queued run completed, artifact references verified, and reopening
preserved the original acknowledgements and results with exactly two jobs. An
independent RPC client verified world restoration after each case.

An initial cancellation attempt hit an IMU initialization timeout. A retry
setup omitted the fixture plan and failed before execution. An initial browser
run hit a camera timeout after measured progress. The failed attempts are
retained separately in local evidence.

## Installed Companion recovery

The disposable validation installer passed its native payload checks. The
installed Companion completed the normal consent callbacks against a separate
source coordinator, including native DPAPI, two-account isolation, lost
approval and acceptance replies, cancellation, durable reopen and revocation.
The package's first-party Python files matched the current worktree.

Suspending the owned Companion process for 102.281 seconds exceeded the normal
90-second heartbeat deadline. The device went offline, its last-seen timestamp
stayed fixed and the result request remained queued. After resume, the
connection generation advanced from 3 to 4. The original ACK and pending
result reconciled, with one original job and one receipt.

The installed Start-menu shortcut opened the expected GUI interpreter and
compiled validation profile. Native visual inspection confirmed three saved
runs with their original completed/cancelled states, an empty waiting queue,
and a disconnected relay with remote execution unchecked. The disposable
installation was then uninstalled. All 13 validation profile files retained
their hashes and the validation registration and shortcut were removed.

## Remaining acceptance

Physical Windows sleep/resume, a physical network-adapter outage, power-loss
durability and independent supported-machine acceptance remain open. The
socket fault is scoped to the isolated test connection.
