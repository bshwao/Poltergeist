# Phase 3 validation — October 19, 2026

Phase 3's explicit tasks, route ownership, authoritative controls, interventions
and measured completion are implemented. The reference AI still uses privileged
simulator state and framework-provided navigation. Its deterministic tracking,
startup limits, task speed cap and destination approach cap are declared in
diagnostics; goal stopping is not claimed to be learned by the network.

## Automated checks

- Full core regression: **2,147 passed, 10 optional dependency tests skipped**.
  Final localized runner/contract/navigation/persistence acceptance: **347 passed**,
  including malformed routes, ownership rejection before setup, scripted audit
  timing, loop crossings, invalid elevation, first-reach termination, callback
  failures and forged goal evidence.
- Final checkpoint tests in the prepared Python 3.12 environment on CPU:
  **66 passed, one CUDA-only test skipped**. Tests execute actual pinned weights
  with finite routes and distinguish neural speed from controller assistance.
- CARLA Python/OpenCV navigation, scene and persistence checks passed. Actual
  goal telemetry also saved and reloaded through SQLite and artifact storage.
- Frontend: **210 tests passed**, production build passed, schema/type drift
  checks passed. Compatibility plumbing is updated; no agent-selection UI is
  claimed.
- A wheel built successfully and contains navigation, task contracts, the
  reference implementation and its attribution. Diff whitespace checks passed.

## Live CARLA checks

Machine: CARLA 0.9.16, Python 3.12.10 parent/worker, RTX 2060 6 GiB. Town03,
0.05 s synchronous steps, the existing `--performance` profile (no rendering,
0.001 s sensor settle, scoped GC tuning). The neural inference backend was
float32 SDPA through CUDA graphs. Checkpoint SHA-256:
`2f896d8d7640d4b7b68bcef8615a73e9761e850870a24f914c4c908f0f2199f4`.

The finite task starts the ego at 2 m/s, requests 4 m/s, and resolves about 24.10 m
of actual forward-lane route. Completion requires distance at most 1.5 m, height
error at most 2 m, and measured speed at most 0.5 m/s continuously for 0.2 s.
The time limit is 12 s with opt-in early termination.

| Trial | Driving outcome | Simulated end | Final distance | Final speed | Complete-cycle p95 | Misses above 50 ms | Execution RTF |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Initial task, seed 0 | Reached | 6.75 s | 1.407 m | 0.411 m/s | 36.04 ms | 0 / 135 | 1.80 |
| Task repeat after route fixes, seed 43 | Reached | 10.85 s | 0.562 m | 0.377 m/s | 26.48 ms | 0 / 217 | 2.17 |
| Legacy road following, seed 0 | Full window completed | 3.00 s | No task | No goal criterion | 31.77 ms | 0 / 60 | 1.81 |
| Legacy road following, seed 43 | Full window completed | 3.00 s | No task | No goal criterion | 31.02 ms | 0 / 60 | 1.88 |

Both goal trials had zero measured collisions and zero framework control
interventions. Every decision executed the learned model. Assistance labels
remain separate from framework interventions. The task repeat includes the
loop-tracking and initial-approach fixes. Subsequent zero-length-route and height
guards were verified with focused tests. Current navigation reproduced all 217
recorded route inputs, and all 218 telemetry frames passed current persistence
replay and save/reload.

A matched legacy baseline finished the full 12 s but **did not satisfy the
goal** and recorded **one collision**. Its result remains `completed` for execution
and `timed_out` for driving. This is evidence that goal and execution success
are distinct, not a general comparison of driving competence.

The two legacy regression trials passed the harness's repeated throughput gate.
Their later source drift is limited to stricter result-contract validators;
the driving-loop sources are unchanged. The initial goal trial preceded final
navigation edge fixes, so the separate goal repeat and final offline replay
provide the functional acceptance. These short checks supplement the broader Phase 2.5
[performance validation](reference-ai-performance.md); they do not replace that
template matrix or establish hard real-time guarantees.

Raw local evidence is retained under
`storage/reports/phase3-live-20261019/{goal-trial,goal-final,legacy-regression}/`.
Each directory includes model/source fingerprints, recipes, full telemetry,
decision diagnostics, timing and results. These generated artifacts are ignored
by Git. The owned CARLA instance was confirmed empty/asynchronous and closed
after testing.

Run the mapped example using [the driving task guide](driving-tasks.md).
Sensor perception, general agent registration, dashboard selection and full
action-timeline review remain the later phases in the integration plan.
