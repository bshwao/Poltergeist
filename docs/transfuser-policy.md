# Trained RGB and LiDAR fusion (S16)

The Companion's **AI models** panel offers **TransFuser RGB + LiDAR (CPU)**.
It predicts four waypoints from front RGB pixels, a LiDAR height histogram,
ideal simulated ego kinematics and a supplied framework route. Its released
PID controller produces steering, throttle and braking. Other actors' ground
truth, simulator traffic-light flags and structured scene inputs are excluded.

This adapts the official [CVPR 2021 TransFuser network](https://github.com/autonomousvision/transfuser/blob/6d1d0421598e3767ced97a113739a40a621cdc25/transfuser/model.py).
The complete trained network has 66,293,634 parameters. It uses ResNet-34 RGB
and ResNet-18 LiDAR encoders, four fusion transformers and a waypoint decoder.
Constructors do not download ImageNet weights. The exact checkpoint supplies
every tensor through restricted `weights_only=True` loading and strict matching.
It is distinct from the sensor teaching example and PlanT's privileged planner.

## Prepare and run

Stop local execution, open **Manage AI…**, select the fusion model and choose
**Install / resume AI**. Selection alone starts no download. Preparation creates
a separate owned environment and verifies its dependencies, files and actual
pretrained inference. It preserves the AIM and PlanT packs and existing results.
Allow at least 3 GiB free space. Enable the selected AI through the local-code
trust review, then choose the registered fusion model for a CARLA scenario with
a framework route and 0.05-second steps.

Readiness requires inference after the declared warmup; a neutral initialization
tick alone cannot qualify the model as ready. Synthetic runs use manufactured
camera/LiDAR fixtures and are labelled accordingly. They do not establish native
perception or driving quality. Missing samples, incompatible calibration, gaps,
invalid predictions and budget overruns fail without substituting a controller.

## Calibration and recorded decisions

The rig follows the pinned [agent](https://github.com/autonomousvision/transfuser/blob/6d1d0421598e3767ced97a113739a40a621cdc25/leaderboard/team_code/transfuser_agent.py)
and its bundled [Leaderboard wrapper](https://github.com/autonomousvision/transfuser/blob/6d1d0421598e3767ced97a113739a40a621cdc25/leaderboard/leaderboard/autoagents/agent_wrapper.py):

| Sensor | Mount and settings |
| --- | --- |
| Front RGB | 400 × 300, FOV 100°, x 1.3 m, z 2.3 m, zero rotation; lens-circle multiplier/falloff 3, chromatic aberration intensity 0.5 and offset 0 |
| Ray-cast XYZI LiDAR | x 1.3 m, z 2.5 m, yaw −90°; 64 channels, range 85 m, 600,000 points/s, rotation 10 Hz, upper/lower FOV 10°/−30°; atmospheric attenuation 0.004, dropoff general/intensity-limit/zero-intensity 0.45/0.8/0.4 |

Both sensors deliver the current native frame and timestamp on every physics
tick. A 10 Hz LiDAR rotation covers half a revolution per 20 Hz simulation step;
the adapter does not invent a full sweep. The existing aggregate 2 MiB raw
payload bound remains enforced. Optional optical/dropoff settings are bounded,
persisted and validated in the dashboard; legacy sensor defaults are preserved.

RGB uses the released 256 × 256 center crop, channel order and 0–255 float
scale. LiDAR flips local Y and preserves the release's exact histogram bins,
height split at −2 m and five-return saturation. With its one-sample sequence,
the upstream past-to-current pose transformation is identity. No neighboring
actors' ground-truth positions are used to manufacture the histogram.

One neutral warmup tick precedes inference. The first four subsequent ticks
infer; later predictions refresh every other tick, matching the released
scheduler. PID control runs each tick. Held predictions retain their originating
frame/time, both sensor hashes and original world trajectory. Current input
hashes are recorded separately. The PID receives a clone so its coordinate flip
cannot mutate the cached prediction. Diagnostics identify assistance, checkpoint,
source, device, parameter count, inference time and complete policy-step time.

The CPU profile uses two Torch threads and a two-second policy-step budget,
separate from the worker deadline. Passing that bound does not imply 20 Hz
wall-time performance. This compatibility integration uses CARLA 0.9.16;
the upstream training documentation refers to CARLA 0.9.10.1. Native validation
and broader driving-quality evaluation remain separate requirements.

## Checkpoint provenance and terms

The official [model archive](https://s3.eu-central-1.amazonaws.com/avg-projects/transfuser/models.zip)
is 697,106,220 bytes. Only the stored `transfuser/best_model.pth` member is
downloaded: offset 431,487,567, length 265,617,761, SHA-256
`16256a96134d9f83fdea30bfaf04b26a3710896e64be11be03d8f282946f5994`.
The manager verifies the precise HTTP content range, total size and complete
checkpoint hash before publishing or loading it. No archive code is executed.

The adapted code retains the pinned MIT notice. Reviewed upstream release
materials do not separately declare checkpoint redistribution terms. No such
permission is inferred from the code license or public download. Weights are
not committed or bundled. Public checkpoint distribution review remains open.

## Validation scope

On November 5, four independent 30,000-point offline inputs matched the pinned
upstream LiDAR preprocessing exactly. The actual pretrained network's waypoints
and PID outputs matched the upstream implementation, with maximum waypoint error
zero at a 0.000001 absolute tolerance. Measured CPU forward/control calls took
0.249–0.262 seconds under Torch 2.6.0+cpu / NumPy 2.2.4. These are offline fixture
observations, not native driving, total application latency or a real-time claim.

The [native validation receipt](validation/s16-transfuser-20261105.json) records
a four-second Epic CARLA 0.9.16 / Town03 execution through the separately owned
managed interpreter: 81 RGB and 81 LiDAR frames, 41 learned predictions and
80 applied controls. Every control matched CARLA's native read within 0.00002.
Policy-step p95 was 0.2903 s, maximum 0.3482 s, with no two-second budget misses.
The ego moved 13.85 m from an initial measured speed of 2.06 m/s. The window
expired 16.08 m before the goal; this is not a successful route-completion claim.
Overall severity remains null with traffic-rule coverage 80/81. World settings,
weather and zero dynamic actors were restored. An earlier recording-callback
failure is retained separately; fixing that observer did not change the policy.

The local model/sensor lifecycle selection passed 221 checks; an additional
policy-budget rejection check passed afterward. Contract checks, dashboard build,
401 dashboard checks and wheel source/notice inclusion were verified separately.
The wheel contains no checkpoint. Full CI and human visual acceptance are
tracked independently; local engineering checks do not close those gates.

See [managed AI](managed-ai.md), the [camera policy](sensor-driving-policy.md)
and the [roadmap](scalability-plan.md) for lifecycle and remaining release gates.
