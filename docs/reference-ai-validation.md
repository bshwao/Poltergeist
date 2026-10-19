# Reference AI validation — October 18, 2026

**Live learned control is verified. The full loop does not meet the 20 Hz
real-time target on this machine.** The integration and measurement workflow are
implemented; performance remains an open result rather than a passed checkpoint.

## Tested stack

- CARLA client/server 0.9.16, Town03, 0.05 s physics step, Windows 11.
- NVIDIA RTX 2060, 6 GiB VRAM, driver 617.42; 12 logical CPU threads.
- Python 3.12.10 parent/worker, PyTorch 2.6.0+cu124, torchvision 0.21.0+cu124,
  transformers 4.49.0, timm 1.0.19, NumPy 2.2.4, SciPy 1.15.2, OmegaConf 2.3.0.
- Official PlanT 2.0 checkpoint `epoch=029_final_1.ckpt`, 37,183,940 parameters,
  float32 inference. SHA-256:
  `2f896d8d7640d4b7b68bcef8615a73e9761e850870a24f914c4c908f0f2199f4`.
- Neural source revision `6269e5a43c6f6b984d2bd97e3ec4a025ebefbeef`;
  [MIT source](https://github.com/autonomousvision/plant2) and
  [Apache-2.0 weight model card](https://huggingface.co/SimonGer/PlanT2/blob/main/README.md).

The inference-only port loads every checkpoint tensor strictly. It constructs
BERT/ResNet architecture offline, uses the published weights, and removes the
upstream training/Leaderboard/client dependencies. Ground-truth scene data and
a supplied route enter through SDK 1.1. A deterministic tracker converts the
learned path/waypoints to actual vehicle controls.

## Control and reaction evidence

Direct CPU/CUDA fixture tests observed clear-road acceleration, braking for a
stopped lead vehicle, and braking for a moving pedestrian. Reset reproduced the
same CPU result. The CUDA gather optimization was checked against the original
class-specific projections with the actual learned weights; lossless raster
encoding preserves the exact neural input. A synthetic red-light fixture did
**not** produce braking. That is a recorded limitation of the tested stack, not
evidence of traffic-light competence.

Repeated live road-following trials completed with unchanged model-to-adapter
controls and no scripted ego interventions. In the two-second matched trials,
the learned policy travelled approximately 16.61 m; the deliberate full-braking
ablation travelled approximately 3.71 m. All 40 applied control frames changed
and measured motion differed. The original learned action is retained in every
ablation decision. These checks establish that model predictions affect the
vehicle; they do not establish a safety ranking.

## Live hazard trials

The following trials used 8 s duration, no rendering, no camera/LiDAR capture,
and an explicitly shortened 0.001 s sensor-settle window. Other local workloads
were not fully controlled, so these are machine observations, not benchmark
equivalence or a hardware guarantee. Traffic-rule counts follow Poltergeist's
detector definitions and recorded coverage.

| Trial | Seed | Execution | Collisions | Traffic violations | Real-time factor |
| --- | --- | --- | --- | --- | --- |
| Road following | 0 | completed | 0 | 2 | 0.432 |
| Road following | 43 | completed | 0 | 2 | 0.447 |
| Sudden braking | 0 | completed | 0 | 2 | 0.438 |
| Sudden braking | 43 | completed | 0 | 2 | 0.435 |
| Pedestrian crossing | 0 | completed | 0 | 3 | 0.434 |
| Pedestrian crossing | 43 | completed | 0 | 8 | 0.448 |
| Intersection | 0 | completed | 1 | 8 | 0.435 |
| Intersection | 43 | completed | 1 | 7 | 0.435 |
| Occluded pedestrian | 0 | completed | 0 | 2 | 0.312 |
| Occluded pedestrian | 43 | completed | 0 | 4 | 0.394 |
| Changed braking conditions | 17 | completed | 0 | 0 | 0.412 |
| Changed pedestrian conditions | 29 | completed | 0 | 0 | 0.391 |

The changed braking recipe used ego/lead speed 8 m/s, a 15 m gap, 0.8 s braking
delay, and 7 m/s² requested lead deceleration. The changed pedestrian recipe used
ego speed 8 m/s, pedestrian speed 1.8 m/s, distance 16 m, and crossing delay 0.4 s.
These recipes were held out from the initial integration check; their absence
from the model authors' training data is not established.

Both intersection runs contain real collision evidence. Completed execution does
not convert those runs into successful driving. The occluded-pedestrian trials
still expose privileged hidden-actor state and do not test visual occlusion.
Lane-invasion counts and peak GPU memory are unavailable. Existing rule checks,
motion, route progress, OS process-memory peaks, and device-wide GPU snapshots
are retained without filling unknown measurements with zero.

Two first attempts at the changed recipes failed during initialization because
CARLA stopped responding. Those failed reports remain separate. Restarting the
empty test server allowed both recipes to finish; the server interruption was
not classified as a model decision failure. The initial custom-recipe seed
override issue found during testing was corrected and regression-tested.

## Performance result

The first rendered one-second pilot had about 320 ms median complete steps and
RTF 0.138. Lossless raster transport, cached map/signal geometry, vectorized
raster transforms, and an equivalent CUDA-friendly token projection reduced
overhead without substituting a controller or skipping decisions.

In the initial repeated optimized no-rendering comparison, the learned agent's
median neural forward pass was about 20.6–20.8 ms, but median complete steps were
about 115 ms and RTF about 0.387. The matched baseline achieved RTF 1.31–1.41.
In the subsequent default hazard matrix, complete-step p95 was about 135–154 ms;
the changed pedestrian trial reached about 167 ms p95. Every repeated live
20 Hz throughput gate remained **false**.

Profiling identified map-raster/route preparation, defensive data copies and
protocol overhead, CARLA advancement, and rule/sensor processing in addition to
neural inference. Cold loading/first inference are reported separately or kept
in the measured samples as described by each report. A per-step timeout is a
failure watchdog, not the real-time budget. No hard real-time claim is made.

## Evidence locations

Raw local evidence lives under `storage/reports/` and is intentionally ignored by
Git. Each directory contains a report, fingerprints, settings, recipes, model
outputs, applied controls, and measured telemetry:

- `reference-ai-live-smoke-20261018`: first live learned control.
- `reference-ai-live-compact-20261018`: two seeds with baseline and control ablation.
- `reference-ai-live-fast-20261018`: repeated no-rendering baseline/ablation timing.
- `reference-ai-profile-20261018`: diagnostic profiling run.
- `reference-ai-live-hazards-20261018`: default road/braking/pedestrian/intersection matrix.
- `reference-ai-live-occluded-20261018`: two occluded-pedestrian trials.
- `reference-ai-live-heldout-braking-retry-20261018` and
  `reference-ai-live-heldout-pedestrian-retry-20261018`: changed conditions.
- The corresponding directories without `-retry-` retain the server-unavailable
  initialization failures.

## Software checks

The full Python regression run passed 1,926 tests with warnings treated as
errors; three optional-dependency cases were skipped in the core environment.
The prepared inference environment separately passed the real-checkpoint and
SciPy/controller tests, and the CARLA environment passed all 20 scene/map tests
including OpenCV rasterization. Focused tests after final changes additionally
cover compact raster validation, unknown-light handling, saved recipe seed
preservation, and stopping remaining suite jobs on cancellation.

All 209 frontend tests, production TypeScript/Vite build, contract generation/
drift checks, and model-environment dependency consistency checks pass. The
frontend continues to preserve the existing server rendering setting by default;
the learned pilot is launched through the CLI/Python path.

Follow [the launch guide](reference-ai-pilot.md) to reproduce the supported
workflow. Functional integration is ready for subsequent phases; reaching the
20 Hz end-to-end target remains unfinished performance work.
