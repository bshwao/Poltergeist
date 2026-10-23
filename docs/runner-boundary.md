# Coordinator and local runner boundary v1

Status: S1 typed prototype boundary. There is no hosted transport, pairing,
authentication, companion relay daemon or new remote execution endpoint. The existing
local API still combines coordination/execution/storage. Do not expose it publicly.

The [private S2 desktop](companion.md) now calls those local services directly
without opening HTTP. It uses a separate per-user profile and implements no
hosted transport or pairing. The typed requests below remain proposed boundary
contracts; the desktop does not turn them into an unauthenticated dispatcher.

`shared/models/runner.py` exports `poltergeist-runner-v1` through the shared JSON
Schema and generated TypeScript types. `Poltergeist.cmd capabilities` emits the
prototype's `RunnerCapabilities`. Execution modes and feature lists describe code
capabilities, while `synthetic_status` and `carla_status` preserve the readiness
report's prerequisite semantics. A listed mode is not proof that its server or
model ran. The report fixes local concurrency at one and remote execution at false.

| Boundary | v1 shape / responsibility |
| --- | --- |
| Capability negotiation | Protocol and implementation versions, initial Windows x64 target, SDK versions, local features, prerequisite statuses, one execution slot |
| Execute request | Client-generated request UUID, validated `Scenario`, explicit `test`/`carla` mode, optional locally approved registration UUID, typed execution/evaluation options |
| Cancel request | Separate request UUID and owned simulation UUID; cancellation remains cooperative, not immediate termination |
| Progress and result | Retain complete `SimulationProgress`/`SimulationResult` locally; define an explicit authorized hosted projection with approved identity, mode and measurement coverage |
| Evidence access | Existing owned result/report/artifact identities and indexed downloads; future transport must authorize each access and bound bytes |

Requests deliberately contain no package, interpreter, model, storage or shell
paths. Registering/trusting arbitrary code, installing dependencies, selecting
models and changing machine configuration remain device-local actions. The
readiness API's detailed local file paths are not part of capability negotiation.
No model weights or inference environments are implied by `approved_sdk_agents`.

Raw results contain agent snapshots with local package/interpreter/model paths
and arbitrary configuration. Readiness and errors can also contain local paths.
These local contracts must not be serialized wholesale to a hosted connection.
The [S4 design](hosted-pairing-design.md) specifies a separate public projection;
that projection and authenticated dispatch are not implemented yet.

The future hosted coordinator owns accounts, authorization, device pairing,
request ownership/correlation and relay flow control. The local runner retains
queue/leases, CARLA startup/map/world ownership, tick/sensor/agent control loops,
local worker deadlines, evaluation, SQLite and original artifacts. Raw recordings
stay local unless the owner requests an authorized transfer. Scenario generation
can be coordinated remotely; map validation and execution remain local.

Request UUIDs are correlation/deduplication inputs, not an implemented idempotency
guarantee. A future authenticated dispatcher must persist request ownership and
acceptance before execution, resolve uncertain acknowledgements without blind
retries, enforce per-device policies and reject unsupported protocol versions.
The S1 DTOs are not that dispatcher. Local queue/result models remain authoritative
for accepted operations. S4 security and S5 transport integration are still required
before any public remote control.

The private CLI only executes bundled smoke examples through existing isolated
validation APIs on ephemeral loopback ports. It provides no always-on server and
does not implement the remote execute/cancel request models. This defines a
reviewable split while preserving the current local development workflow.
