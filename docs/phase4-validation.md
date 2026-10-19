# Phase 4 validation — October 19, 2026

SDK 1.3 sensor delivery is implemented and exercised against CARLA 0.9.16.
The supplied sensor pilot is a rule-based integration example, with LiDAR
corridor braking and an RGB visibility guard. It is not a trained perception
policy. PlanT remains the separate trained simulator-state reference.

## Automated validation

- Full core regression: **2,348 passed, 10 optional dependency tests skipped**.
- Final sensor, recording-provenance, persistence and CLI acceptance:
  **334 passed**, including the changes made after the broad run began.
- CARLA Python/OpenCV capture, reference-scene and sensor-pilot checks:
  **77 passed**.
- Existing trained reference checkpoint on CPU: **66 passed**, one CUDA graph
  test skipped. The live sensor pilot does not use or replace that neural model.
- Frontend: **211 tests passed**, production build and generated-contract checks
  passed. This updates compatibility plumbing, not dashboard agent selection.
- Wheel built successfully; sensor SDK, collection, calibration, fixture and
  persistence sources are included. Whitespace checks passed.

Tests cover exact sensor IDs, native frames, timestamps, mounts, FOV/channel
calibration, payload checksums, finite XYZI data, bounded queues and messages,
missing/stale/duplicate/future data, cancellation, partial setup cleanup,
input/evaluator separation and old SDK compatibility. Camera FOV normalization
also handles native float rounding for non-round configured values.

Recording tests cover several cameras or LiDARs of the same type, optional
legacy recording streams, raw-byte roundtrips and independent per-stream clocks.
Persisted recordings must match their declared input source, rig and finalized
telemetry clock. Forged callback metadata and changed disk provenance are
rejected. Cancelled runs can preserve a partial terminal packet; completed runs
require every declared stream in every measured frame. Old nullable provenance
remains readable.

## Live sensor operation

Environment: CARLA 0.9.16 on Town03, Python 3.12.10 parent, Python 3.14.0 worker,
RTX 2060, offscreen Epic rendering at 640×360, 0.05 s physics steps. The declared
rig uses a 320×180 BGRA camera with 90° FOV and a 32-channel ray-cast LiDAR with
60 m range, 56,000 configured points/second and 20 Hz rotation. Both mounts are
1.5 m forward and 2 m above the ego origin. Sensor waits were bounded at 5 s.

| Trial | Execution | Driving outcome | Decisions | Measured sensor samples | Collisions |
| --- | --- | --- | --- | --- | --- |
| Clear finite route | Completed | Goal reached at 7.05 simulated seconds | 141 | 284 | 0 |
| Stationary lead hazard, 5 s | Completed | Goal not reached within window | 100 | 202 | 0 |
| Raw recording/persistence, 1 s | Completed | Full window measured | 20 | 42 | 0 |

The clear trial reached within 1.005 m of its destination, slowed below the
0.5 m/s goal threshold for the required 0.2 s, and stopped early. The last speed
was approximately 0.000087 m/s. All 141 camera payload hashes and all 141 LiDAR
payload hashes were distinct; point clouds contained 1,320–1,451 actual returns.

The hazard trial placed a stationary lead vehicle 12 m ahead and started the ego
at 4 m/s. Its diagnostics reported LiDAR corridor braking on 38 decisions;
the nearest measured bumper-relative return reached 2.123 m. It ended at about
0.177 m/s without collision and did not reach the destination. These observations
demonstrate the sensor/control connection, not general driving competence or
perfect stopping behavior.

All 241 clear/hazard observations were revalidated: exact native frame association,
matching timestamps, lossless checksummed samples, no other-actor state, no
privileged scene/raster/signals, and no ego box geometry or map speed limit.
Applied controls matched the agent's frame-associated proposal without framework
overrides. Ideal ego localization and supplied-route assistance remain explicit.

The recording check wrote actual samples through `CaptureWriter`, persisted
21 evaluator telemetry frames plus 42 raw samples through SQLite/artifact storage,
and reopened them successfully. The saved live data also passed the final stricter
recording-provenance validation added afterward. It took about 2.10 execution wall
seconds for 1 simulated second with recording I/O and other tests running. This
is functional synchronization evidence, **not rendered-sensor 20 Hz acceptance**.
The state-only Phase 2.5 throughput measurements do not apply to this workload.

Raw evidence is retained locally under `storage/reports/phase4-live-20261019/`:
clear/hazard result JSON and full decision JSONL, the hazard recipe, a camera
preview, package/source fingerprints, and the recording check's database,
telemetry and raw artifacts. Generated evidence is ignored by Git. Final changes
to native FOV normalization and stored metadata validation were checked offline;
the tested 90° rig and controller are unchanged.

The owned CARLA instance was confirmed empty and asynchronous, then closed.
See [sensor setup and commands](sensor-agents.md) to run the example. General
agent registration and dashboard selection remain Phases 5–6.
