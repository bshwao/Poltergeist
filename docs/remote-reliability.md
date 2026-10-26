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

### Enrollment requests and uncertain approvals

Enrollment now gives each HTTP exchange one ten-second monotonic budget,
including waiting for a prior request, startup, pipe writes and DNS/TLS/HTTP
work. Slow headers or a trickling body cannot renew it. The absolute deadline
survives pauses between constructing and starting the request. Expired startup
does not release credentials to the worker.

A disposable isolated Python process performs the exchange. It uses the same
private runtime as the companion, launched by absolute worker path with `-I -B`.
Credentials travel through anonymous pipes and Authorization headers; they do
not appear in arguments, environment variables or temporary credential files.
Parent and child bound their pipe messages independently; the server body is
limited to 64 KiB. TLS certificate/hostname verification stays enabled, proxies
and redirects are disabled, and errors omit response bodies and private values.
The private `_ssl_context` fixture override transfers public CA anchors only;
it does not reproduce arbitrary custom TLS policy or client certificates.

The request owner uses a suspended Windows launch assigned to its own job before
resuming, so termination covers the actual interpreter behind a venv launcher.
Owned reader/writer threads prevent a blocked stdin write from trapping the
calling thread. After the request budget expires, a separate three-second
cleanup attempt terminates and inspects only the owned process tree, reaps the
launcher and joins its pipe threads. Unconfirmed cleanup retains its owner and
the companion profile lease; another request cannot bypass it through a newly
constructed client. Explicit close retries incomplete waits or handle closure.
An unverifiable membership inspection remains quarantined; retry does not turn
missing process-exit evidence into successful cleanup.

These are operation deadlines, not an unconditional kernel timing guarantee:
Python documents that [native process creation may be uninterruptible](https://docs.python.org/3.12/library/subprocess.html#subprocess.run),
and Windows documents that [termination is asynchronous while I/O finishes or is cancelled](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-terminateprocess).
An elapsed budget is checked again after startup and before success is returned.
No successful cleanup is claimed merely because termination was requested.

An HTTP timeout does not prove that the coordinator did not commit approval.
The protected pending credential stays available for Refresh / resume. The
explicit **Cancel saved approval** action offers a second recovery path. Its
atomic coordinator operation either fences an uncommitted attempt or revokes
only the exact already-approved device/owner/credential. A pruned attempt cannot
be recreated through the API. Changed bindings, newer generations/policies and
another active credential fail closed. Lost cancellation replies and failed
local credential removal remain retryable. Credentials are removed only after
the coordinator confirms the bounded outcome; session consent stays off.

### Immutable cancellation acknowledgements

Schema revision 6 adds the cancellation `terminal` acceptance snapshot to each
new local receipt. Revisions 1–5 and their checksums remain unchanged. The bit is
stored in the same writer transaction as acceptance and is reused for the first
ACK and every replay, including after cleanup, restart or offline completion.
Queued and active cancellation return `terminal=False` at acceptance; a request
against an already-finalized run records `True`. Read the separate result for
the eventual cancelled/completed/failed outcome.

Older cancellation receipts did not record this bit. Their new nullable column
stays unknown; v1 replay returns `device_unavailable` rather than inventing the
old ACK or signalling cancellation again. The original receipt/run is retained,
and an authorized result request can still read its final outcome. This
compatibility limitation is covered by migration, replay and crash tests.

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
| Reconnection and heartbeat | Native TLS deadlines, a 102-second packaged-process suspension, new-generation reconnect and original-result reconciliation; truthful activity timestamps | Physical PC sleep/resume, NIC faults and longer real outages |
| Distribution | Runtime H passes native CARLA crash/quarantine/reset; Runtime I matches 173 sources and passes installed compiled-profile TLS/DPAPI/recovery/suspension with data-preserving uninstall | Actual shortcut/visual and independent-machine fault validation |

### Refreshed private Windows runtime

Runtime H carries the S6 production changes from `cf850bc`, with private Python
3.12.15, the pinned CARLA client and 6,082 integrity-checked files. The normal
bundle still requires explicit desktop consent for remote execution. Its full
pairing, lost-ACK, cancellation, restart and revocation regression passes using
the normal Tk callbacks and native DPAPI. This is an unsigned portable bundle;
it has not replaced the user's installation.

The native crash driver now accepts `--bundle` and requires the bundle's isolated
interpreter, an entirely bundled import path and matching file integrity. It
records provenance again after lazy CARLA imports. A real packaged worker exits
with code 79 after durable running state, leaving two vehicles and synchronous
settings. Reopening retains the original failed job and ACK; a separate packaged
profile refuses the unclean episode without changing its known actors/settings.
Restarting only the identity-checked test server allows a fresh 11-frame baseline.
The original request remains failed once. A fresh asynchronous snapshot verifies
cleanup before the owned replacement simulator is stopped.

The public [packaged recovery record](validation/s6-packaged-recovery.json)
retains identities, hashes, scoped results and limitations. Its native CARLA
driver uses a programmatic approved local binding; the separate TLS/consent
driver uses synthetic execution. These are complementary proofs, not a combined
website-driven CARLA crash acceptance test.

The consent driver also has an opt-in native suspension case:

```powershell
.\.venv\Scripts\python.exe tests/packaged_companion_consent.py --bundle validation-evidence/portable-dist/s6-recovery-h-20261025/Poltergeist-S1-windows-x64 --exercise-suspension
```

It suspends only its own live `Popen` child through the retained Windows process
handle and always attempts to resume it in `finally`. The observed suspension
lasted 102 seconds, exceeding the unchanged 90-second heartbeat deadline. A
result request remains queued while the owner view becomes offline and last-seen
stays unchanged. After resume, the connection generation advances from 3 to 4,
the result reconciles, and retrying the original execute returns its exact ACK
with one local job/receipt. The rest of the consent/cancellation/revocation
regression also passes. The test does not put the physical PC to sleep.

Earlier fixture failures remain recorded: the first OS crash test killed a
Windows venv launcher, allowing its actual child to read EOF and clean up. It
now exits the actual child abruptly. The first worker crash fixture swapped
owner/device arguments and incorrectly required a queued cancellation to remain
nonterminal; both fixture assumptions were corrected without weakening assertions
about production identity or final outcomes. The original failed logs are retained.
The follow-up's seven pre-fix late-frame tests also remain recorded as failed;
their passing repeats do not replace that evidence.

The first follow-up CI run also exposed a stale normal-reply fixture: it invoked
Stop during dispatch and then expected a reply on that stopped socket. The
corrected normal-path fixture stops after sending its reply; a separate case
proves that Stop during dispatch suppresses the reply. All 82 contract/relay
checks pass on Python 3.12 and 3.14. The original failed CI logs are retained.

The [enrollment and ACK recovery record](validation/s6-enrollment-reconciliation.json)
tracks these source changes and their own evidence; the older runtime H proof
above does not validate a later payload. Private Runtime I matches all 173 source
Python files and passes the refreshed real TLS/DPAPI/native consent workflow,
including lost pending-approval/cancellation replies, a distinct fresh pairing,
and a 102-second process suspension. An independent outside-checkout probe verifies
that its GUI interpreter starts the exact bundled enrollment worker through the
private `python.exe`, with isolated imports and joined process/pipe ownership.
All five coordinator crash cases also pass with this source transport.

The [installed Runtime I record](validation/s6-installed-recovery.json) closes the
same-laptop compiled-profile callback checkpoint. The validation installer passes
native import/GUI/integrity checks, then a fresh installer-owned profile completes
first-run synthetic setup, uncertain approval recovery, normal consent, lost ACK,
cancellation, 102-second suspension and durable reopen/revocation. Uninstall keeps
every profile file unchanged. Shortcut target/arguments are checked separately;
the shortcut itself is not invoked. A first attempt exposed a harness scan gap
for logs outside the fixture folder; the expanded scan passes on a second fresh
installation/profile, and both attempts remain recorded.

S6 remains open for actual installed shortcut/visual acceptance, wider native
cancellation/finalization races, physical sleep/network faults and external
supported-machine acceptance. Passing a private checkpoint does not close those
requirements or the earlier independent-machine/release gates.
