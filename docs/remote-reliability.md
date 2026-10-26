# S6 reliable execution: current checkpoint

S6 is in progress. Public S5 deployment is deferred at the user's request.
The current source adds CARLA endpoint ownership across companion profiles,
native process-loss checks, real coordinator crash/restart acceptance and
deadline checks across receive/scheduling gaps.
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
The command acquires exclusive ownership, refuses synchronous mode, waits for a
natural asynchronous snapshot and checks its frame/episode before trusting the
actor inventory. Dynamic actors prevent recovery. Only valid quarantine metadata
is cleared. It does not destroy actors,
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
cleanup, during either opening or running. Recovery marks claimed work failed with an interrupted-execution error.
It never retries that execution under a new simulation identity. A surviving
uncancelled waiter runs normally; a cancelled waiter stays cancelled.

Original execute/cancel receipts survive the crash and are replayed under the
same binding. A cancellation acknowledgement for interrupted active work does
not imply successful cancellation: the recovered result is failed with the
cancel-request flag preserved. Public errors remain fixed, with zero observed
frames and unknown measurements for the interrupted run.

## Evidence and remaining work

The earlier checkpoint passes 139 targeted ownership, cleanup, process-loss and
remote-receipt checks, with one unavailable Windows symlink fixture skipped.
The real website/normal-companion CARLA baseline also passes with the new lease:
0.5 simulated seconds, 11 frames, restored episode/actor IDs/world settings, then
cleanup of only the test-owned simulator process tree. This is a short workflow
proof with partial coverage, not a new safety benchmark. The unchanged baseline
retains SHA256 `bc62cc570dfbd8f3f8372538eb90305b2fd2be3d67970a67f8f5ad4dba0f1a4d`.
See the [checkpoint receipt](validation/s6-ownership-recovery.json).

The follow-up [relay/native recovery receipt](validation/s6-relay-native-recovery.json)
records these additional checks:

- Actual HTTPS/WSS coordinator processes crash immediately before or after an
  execute/cancel reply commits. Two accounts and two companions reconnect to the
  same database. Original ACKs reconcile, Alice keeps one run and Bob's queue
  stays empty. A second outage during cancellation lets cleanup finish locally;
  the later result is cancelled while the original cancellation ACK is unchanged.
- Companion and coordinator recheck deadlines after receives. A buffered
  ready/pong/reply/command cannot revive an expired session. Delivery rechecks
  after pending work is loaded, and Stop during a receive prevents dispatch.
  Server-side heartbeat polling no longer updates device last-seen activity.
- A dedicated real CARLA server retains two vehicles and synchronous/fixed-step
  settings after actual companion process loss in running state. Direct actor-ID
  RPCs establish vehicle presence. The same-episode quarantine blocks another
  profile without changing those vehicles or settings. After only the owned
  server is restarted, a new baseline completes; the original interrupted request
  still has its one failed job. Live contention also blocks a second profile
  while a healthy native owner is running, then verified cleanup succeeds.

### Initial actor inventories are not proof of an empty paused world

The first observer reported frame zero and no actors even while the owner was
running. That observation cannot establish actor absence. CARLA 0.9.16's client
[skips its initial state wait in synchronous mode](https://github.com/carla-simulator/carla/blob/0.9.16/LibCarla/source/carla/client/detail/Simulator.cpp#L122-L131),
and [enumerates actors from its cached episode state](https://github.com/carla-simulator/carla/blob/0.9.16/LibCarla/source/carla/client/detail/Episode.cpp#L115-L120).
The corrected witness queries known spawned IDs through fresh clients. Recovery
requires a naturally received nonzero snapshot whose
[episode identity matches the world](https://github.com/carla-simulator/carla/blob/0.9.16/LibCarla/source/carla/client/WorldSnapshot.h#L22-L28)
before considering the actor list. It never forces a tick to inspect a paused
world. The initial weak witness and the corrected direct-RPC evidence are retained
separately; frame-zero observations are not used to claim cleanup.

| Fault area | Current evidence | Remaining acceptance |
| --- | --- | --- |
| Lost acceptance / duplicate submit | Real TLS coordinator crashes before/after ACK commit; original identity/ACK and one run survive | Wider operational fault combinations and independent machines |
| Cross-profile endpoint ownership | Real active CARLA contention plus OS process/alias tests and cleanup retention | Unsupported-client behavior and other OS accounts/machines |
| Claimed worker process loss | Actual opening/running process exits, real orphaned CARLA vehicles/settings, honest failed recovery and reset quarantine | Wider native fault timing and independent machines |
| Cancellation ordering | Lost ACK before/after commit, a repeated coordinator outage, offline cleanup and durable final result; waiter/rollback tests | Additional finalization race stress and external acceptance |
| Reconnection and heartbeat | Native TLS deadlines and reconnects; late-frame/overdue-ping/scheduling-gap rejection; truthful activity timestamps | Physical PC sleep/resume and longer real outages |
| Distribution | Source runtime verified | A new packaged runtime and independent-machine fault validation |

Earlier fixture failures remain recorded: the first OS crash test killed a
Windows venv launcher, allowing its actual child to read EOF and clean up. It
now exits the actual child abruptly. The first worker crash fixture swapped
owner/device arguments and incorrectly required a queued cancellation to remain
nonterminal; both fixture assumptions were corrected without weakening assertions
about production identity or final outcomes. The original failed logs are retained.
The follow-up's seven pre-fix late-frame tests also remain recorded as failed;
their passing repeats do not replace that evidence.
