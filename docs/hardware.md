# Execution profiles and hardware measurement

Choose a profile based on the input an agent actually consumes. Lowering graphics
does not remove inference, actor physics, scene preparation, transport or evidence
I/O. Synchronous CARLA waits for each controller decision, so a correct run can
take longer than its simulated duration.

| Profile | Required components | What to measure |
| --- | --- | --- |
| Core synthetic integration | Core Python packages; Node for the dashboard | Registration, protocol, decisions, analysis, exports and reopen checks. No CARLA/physical driving or learned-perception claim. |
| CARLA with baseline/starter | Matching 0.9.16 server/client, Python 3.12 backend | Actor/setup cost, full execution time, recorded runtime, cleanup/restoration. State-only runs can explicitly use no-rendering. |
| PlanT reference | Prepared isolated worker, pinned weights, declared model file | Model load/reset time, inference and full-cycle p95, deadline misses, memory, real-time factor. It uses privileged structured state. |
| Camera/LiDAR controller | Rendering for RGB, compatible declared rig, sufficient transport/storage budget | Sensor frame synchronization, missing samples, capture volume and full-cycle latency. Off-screen rendering still renders sensors. |

The existing [reference performance record](reference-ai-performance.md) measured
20 Hz on one RTX 2060 6 GiB laptop using a no-rendering state-input profile. That
is a dated measurement with explicit conditions, not a minimum specification or
a guarantee for rendering, sensor perception, CPU inference or another machine.
The [Phase 10 validation record](phase10-validation.md) lists current installation
and live workflow checks separately from that throughput benchmark.

## Reduce execution load

- Run one CARLA instance and one tick-owning backend. Close competing inference
  benchmarks while measuring. Check Task Manager and the doctor's optional GPU
  point snapshot; it includes CARLA and other applications, not just the model.
- Use a small Low-quality window when that map/driver combination supports it.
  On the tested Windows machine, Town10HD_Opt Low works while Town03 Low has had
  native shader failures. Avoid changing a working installation solely to match
  a different machine's graphics profile.
- For structured-state agents, disable unnecessary raw captures and explicitly
  select `no_rendering_mode: true`. The adapter restores the prior setting.
  RGB agents must retain rendering. No-rendering and off-screen rendering are
  different modes.
- Start with a short route, few actors, one seed and one repetition. Increase
  experiment size after a complete run. Short runs validate integration; they
  are too small for a meaningful timing distribution or behavior comparison.
- Prepare model dependencies and weights once in a separate worker environment.
  Keep them stable while registered. Choose CPU/GPU explicitly when investigating
  memory contention. CPU execution can be slower than simulated time.
- Keep active SQLite databases and recordings on local disk. Large raw sensors
  and repeated export requests increase disk and validation work. Inspect one
  saved sample before scaling the rig or capture duration.

Do not mask a failed sensor/agent deadline by substituting baseline controls or
synthetic data. The worker reports a failed run. Diagnose whether the model is
loading, inference is slow, sensor frames are missing, or the process has exited;
only choose a longer explicit deadline when that behavior is acceptable for the
experiment. A longer deadline does not make a controller real-time.

## Measure throughput

The installation validator records actual worker-step latency and an overall
operation wall time, including setup, serial runs and artifact reads. Neither is
the complete per-tick cycle budget. The baseline has no external worker-step
sample, so its worker metric remains unavailable.

For the reference policy, use the existing dedicated harness with repeated seeds,
`--performance --require-real-time`, and a new evidence directory as documented
in [the pilot guide](reference-ai-pilot.md). Its gate includes all executed model
decisions, simulation work and evidence callbacks. Retain setup timing, cycle
p95/p99, budget misses, real-time factor, code/model hashes and hardware context.
Passing this gate establishes throughput under those conditions, not safe driving.

GPU/OS snapshots and process high-water values have different scopes. Do not
subtract a point GPU snapshot from a total and call the result a measured model
peak. No universal minimum GPU/VRAM, Linux GPU throughput, macOS CARLA stack or
container GPU configuration has been certified by this phase.
