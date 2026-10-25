# S6 reliable execution: current checkpoint

S6 is in progress. Public S5 deployment is deferred at the user's request.
The current source adds CARLA endpoint ownership across companion profiles and
native process-loss checks for the existing durable command/worker boundary.
It does not close the complete crash, network and physical sleep acceptance matrix.

## CARLA execution ownership

Normal companion and local API CARLA runs acquire a nonblocking OS lease before
connecting or starting the simulator. Ownership is independent of a profile or
database. Windows uses the OS user's Local AppData `Poltergeist/CarlaEndpointLeases`;
POSIX uses the OS account's home cache. TEMP and profile environment overrides
do not select a different lease directory. Lock filenames hash endpoint identities.
No credentials, local simulation data or PID-based termination authority are stored.

Loopback addresses share a key, and hostname connections reserve the configured
name and every currently resolved address. Independent ports can run concurrently.
Contention fails without waiting for the current run to finish. Native locks
remain held while an owner is suspended or a native call is blocked; there is no
timestamp expiry that permits a second owner to steal a live endpoint.

Before the first world mutation, a small versioned active marker is flushed to
disk. Cleanup clears it only after sensors, actors and world restoration report
success. A cleanup error retains ownership for retry; releasing an object or
losing a process never labels its episode clean. Files are never deleted during
lease release because unlinking a locked file could create a second lock owner.

After process loss, the same episode stays quarantined. A different empty,
asynchronous episode allows recovery when the interrupted run could not reload
the map. A possible interrupted map reload has ambiguous episode identity and
requires explicit inspected recovery after restarting CARLA:

```powershell
python -m scripts.recover_carla_endpoint --host 127.0.0.1 --port 2000 `
  --confirm-server-restarted
```

Use the original configured hostname so all of its lease records are inspected.
The command acquires exclusive ownership, refuses dynamic actors or synchronous
mode, and clears only valid quarantine metadata. It does not destroy actors,
reload maps or change world settings. Corrupt metadata fails closed for inspection.

This is coordination among cooperating Poltergeist processes for one OS account
on one computer. Other OS users, remote computers, older builds and arbitrary
CARLA clients do not participate. The existing unexpected-frame check remains.
Injected external CARLA clients must supply their connection settings to use
the lease; endpoint-free injected mocks remain available for isolated tests.
OS DNS resolution can block; Stop must retain worker/profile ownership while a
native call is alive rather than claiming that the process has stopped.

## Process loss and durable commands

The native crash fixture uses actual workers, SQLite transactions and remote
receipts, then exits the real worker process with `os._exit` before any Python
cleanup. Recovery marks claimed work failed with an interrupted-execution error.
It never retries that execution under a new simulation identity. A surviving
uncancelled waiter runs normally; a cancelled waiter stays cancelled.

Original execute/cancel receipts survive the crash and are replayed under the
same binding. A cancellation acknowledgement for interrupted active work does
not imply successful cancellation: the recovered result is failed with the
cancel-request flag preserved. Public errors remain fixed, with zero observed
frames and unknown measurements for the interrupted run.

## Evidence and remaining work

Both Python 3.12 and 3.14 pass 139 targeted ownership, cleanup, process-loss and
remote-receipt checks, with one unavailable Windows symlink fixture skipped.
The real website/normal-companion CARLA baseline also passes with the new lease:
0.5 simulated seconds, 11 frames, restored episode/actor IDs/world settings, then
cleanup of only the test-owned simulator process tree. This is a short workflow
proof with partial coverage, not a new safety benchmark. The unchanged baseline
retains SHA256 `bc62cc570dfbd8f3f8372538eb90305b2fd2be3d67970a67f8f5ad4dba0f1a4d`.
See the [checkpoint receipt](validation/s6-ownership-recovery.json).

| Fault area | Current evidence | Remaining acceptance |
| --- | --- | --- |
| Lost acceptance / duplicate submit | S4 real WSS proof, S5 browser response loss and transactional receipt races | Wider coordinator/process crash timing combinations |
| Cross-profile endpoint ownership | Native OS process contention, loopback/DNS alias overlap, cleanup retention and abrupt-loss quarantine | Broader live multi-profile native contention and unsupported-client behavior |
| Claimed worker process loss | Actual process exit before open completes, honest failed recovery and stable receipt replay | Native process loss during running CARLA with orphaned simulator resources |
| Cancellation ordering | Active request flag survives interruption; cancelled waiter remains cancelled; existing atomic rollback tests | Additional disconnect/crash boundaries around finalization |
| Reconnection and heartbeat | Existing native TLS deadlines, stale-generation rejection and monotonic heartbeat tests | Physical PC sleep/resume, long outages and repeated coordinator failure |
| Distribution | Source runtime verified | A new packaged runtime and independent-machine fault validation |

Earlier fixture failures remain recorded: the first OS crash test killed a
Windows venv launcher, allowing its actual child to read EOF and clean up. It
now exits the actual child abruptly. The first worker crash fixture swapped
owner/device arguments and incorrectly required a queued cancellation to remain
nonterminal; both fixture assumptions were corrected without weakening assertions
about production identity or final outcomes. The original failed logs are retained.
