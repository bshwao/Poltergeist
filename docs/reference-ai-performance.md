# Reference AI performance acceptance — October 19, 2026

**Phase 2.5's 20 Hz throughput target passes on the tested RTX 2060 laptop
with the documented no-rendering performance profile.** All ten live template
trials passed, with zero 50 ms overruns across 1,600 complete decisions.
This supersedes the unfinished performance result in the
[October 18 validation record](reference-ai-validation.md).

## Acceptance measurements

CARLA 0.9.16, Town03, Windows 11, Python 3.12.10 parent/worker, RTX 2060 with
6 GiB VRAM, PyTorch 2.6.0+cu124. The published PlanT 2.0 checkpoint and float32
weights remain unchanged. Inputs still use measured privileged scene state and
a supplied route. Camera/LiDAR capture was disabled, rendering was disabled,
and the positive collision-callback settle window was 0.001 s.

Each experiment ran for eight simulated seconds with 160 neural decisions.
Cycle times include scene preparation, SDK validation and transport, inference,
CARLA physics/telemetry, progress callbacks, decision validation, writing and
flushing JSONL evidence, and OS memory measurements. The gate requires a complete
live trace, real-time factor at least 1, and at least 95% of complete cycles
within 50 ms. The older compute-only `full_step` field is retained separately;
it cannot establish the new cycle gate by itself.

| Trial | Seed | Cycle p95 | Cycles over 50 ms | Execution wall time | Real-time factor |
| --- | --- | --- | --- | --- | --- |
| Road following | 0 | 35.86 ms | 0 / 160 | 4.289 s | 1.865 |
| Road following | 43 | 34.75 ms | 0 / 160 | 4.189 s | 1.910 |
| Sudden braking | 0 | 35.01 ms | 0 / 160 | 4.428 s | 1.807 |
| Sudden braking | 43 | 37.15 ms | 0 / 160 | 4.759 s | 1.681 |
| Pedestrian crossing | 0 | 36.48 ms | 0 / 160 | 4.680 s | 1.709 |
| Pedestrian crossing | 43 | 37.05 ms | 0 / 160 | 4.699 s | 1.703 |
| Intersection | 0 | 35.97 ms | 0 / 160 | 4.495 s | 1.780 |
| Intersection | 43 | 36.37 ms | 0 / 160 | 4.559 s | 1.755 |
| Occluded pedestrian | 0 | 37.15 ms | 0 / 160 | 4.641 s | 1.724 |
| Occluded pedestrian | 43 | 37.77 ms | 0 / 160 | 4.869 s | 1.643 |

Real-time factor divides simulated execution seconds by execution wall seconds.
Model loading, graph capture, and bounded reset warmup are recorded as setup;
all actual executed decisions, including the first, are measured. No model
decision or simulation tick is skipped. The performance profile is selected
explicitly; these results do not promise the same timings with rendering,
camera inputs, CPU inference, other hardware, or competing workloads.

Every proposed learned control reached the adapter unchanged, with no ego
intervention. The seed-43 intersection run recorded a collision, and traffic
violations remain in the reports. Passing the timing gate does not establish
safe driving, perception competence, or independent global navigation.

## Implemented optimizations

- Cached local map arrays, rejected offscreen polygons, and sampled routes
  numerically before constructing output poses. Raster pixels and route values
  retain exact parity, and public snapshots remain independently owned.
- Removed redundant SDK copies and transport traversals while retaining full
  validation, finite-value checks, byte limits, and frame association.
- Used float32 SDPA and setup-only CUDA graphs for exact object counts 0–8.
  Every execution copies current inputs and replays inference; larger scenes
  use ordinary SDPA. Real-checkpoint outputs match eager float32 within 1e-5.
- Decoded compact rasters into arrays and packed feature uploads into two
  transfers. Input values match the original tensors bit for bit.
- Cached fixed signal geometry while reading signal states and actor membership
  every tick. Failed or discontinuous reads invalidate the appropriate cache.
- Scoped Windows timer precision in both parent and worker, with high-resolution
  deadline clocks and balanced release on exit.
- Reused Windows memory-reading bindings and the already validated JSON evidence
  serialization. GC stays enabled; `--performance` temporarily raises the parent
  generation-zero threshold to at least 50,000 and restores it afterward.

Before optimization, the October 18 hazard trials achieved approximately
0.43 real-time factor. Intermediate attempts are retained, including failures
of the stricter cycle gate. Offline replay confirmed repeated generation-two
collections spending 20–35 ms scanning retained map/evidence graphs while
collecting zero objects; the opt-in threshold change removed those regular
pauses without disabling collection or increasing peak memory in that replay.

Process high-water memory and device-wide GPU snapshots remain in the reports.
For example, the first accepted road trial recorded approximately 163 MB parent
and 1.73 GB worker peak working sets. The CUDA graph cache adds about 18.8 MB
of allocated tensor storage; that is not a total GPU peak measurement.

## Reproduction and software checks

Use the command in [the launch guide](reference-ai-pilot.md), with `--performance`,
two seeds, and `--require-real-time`. A failed timing gate returns a nonzero exit
status while preserving the complete evidence. Start one CARLA instance on
Town03 and use a new output directory for every run.

The main acceptance report is stored locally under the ignored directory
`storage/reports/reference-ai-performance-acceptance-20261019/`. Its verification
fields for live completion, checkpoint identity, all requested runs, and repeated
throughput are all true. All recorded integration-source hashes match the
tested implementation. Raw cycle samples, observations, outputs, applied
controls, telemetry, recipes, settings, and fingerprints are retained.
The additional `reference-ai-performance-occluded-20261019` report verifies both
occluded-pedestrian trials against the same gate. Hidden actors remain exposed
through ground truth; this does not test visual occlusion.

`reference-ai-performance-controls-20261019` contains matched three-second
reference/baseline/full-braking-ablation trials for seeds 0 and 43. All six
completed. Each ablation changed all 60 applied controls and measured motion:
the learned policy travelled about 26.7–26.8 m, versus 3.71 m under the labelled
full-braking replacement. The original neural action remains in each ablation
record. Both reference trials also passed the cycle gate. These checks verify
control-path influence and do not rank driving safety.

The broad regression suite passed 1,994 tests with warnings treated as errors;
seven cases requiring optional inference/raster dependencies were skipped in
the core environment. The prepared model environment separately passed all 52
reference-model cases, including actual CPU/CUDA checkpoint parity, exact input
packing, setup-only graph capture, changed observations, reset reproducibility,
and immutable prior outputs. The CARLA environment passed all 26 scene tests,
and timer/GC behavior passed 16 tests on Python 3.12 and 3.14. Dependencies and
diff whitespace checks pass. A built wheel includes the new timing and graph
runtime modules and the model's MIT source notice. The dedicated test world
was restored to asynchronous mode with no remaining vehicles, walkers, or
sensors before closing the owned server.
