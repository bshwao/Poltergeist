# Managed AIM camera policy (S16)

The Windows Companion's **AI models** panel offers **AIM camera (CPU)** alongside
PlanT. AIM predicts four waypoints from its front RGB image and a supplied route;
the released PID controller converts those predictions into steering, throttle
and braking. This is a trained image policy, separate from the rule-based sensor
teaching example and PlanT's privileged structured-scene planner.

The integration adapts the official
[TransFuser CVPR 2021 AIM baseline](https://github.com/autonomousvision/transfuser/tree/6d1d0421598e3767ced97a113739a40a621cdc25/aim)
at source revision `6d1d0421598e3767ced97a113739a40a621cdc25`.
The network has 21,470,722 parameters. The complete checkpoint supplies the
ResNet-34 image encoder and waypoint decoder; constructing the network never
downloads separate ImageNet weights. Checkpoint loading uses restricted
`weights_only=True` deserialization of the same bounded bytes whose size and
SHA-256 were verified, followed by strict state-dictionary matching.

## Prepare and use

1. Stop the local runtime. Open **Manage AI…**, choose **AIM camera (CPU)** and
   select **Install / resume AI**. Allow 3 GiB free space. Model choice survives
   reopening the Companion; choosing a model does not start a download or run.
2. Wait for **AI ready**, which requires isolated dependency imports and actual
   pretrained inference on an explicitly labelled deterministic sensor fixture.
   This check establishes inference compatibility, not successful driving.
3. Choose **Enable selected AI for local runs…**, accept the local code trust
   notice, then select **Managed AIM camera (CPU)** in the Agent selector.
4. Choose CARLA and a scenario with a framework-provided driving destination and
   0.05-second simulation steps. Inspect the saved input image, proposed/applied
   controls, model diagnostics and resulting measured motion in the usual
   decision timeline. Camera runs scope rendering on and restore the previous
   world settings afterward. CARLA's selected graphics quality is preserved.

**Synthetic test** exercises the SDK using deterministic fixture pixels. Its
results are labelled accordingly and do not establish camera perception or live
CARLA driving. Missing images, incompatible calibration, missing route, changed
timestep, invalid predictions or exceeded policy budget fail explicitly; the
application does not substitute a baseline controller.

## Inputs, timing and provenance

The supported rig is one 400 × 300 BGRA front RGB camera, 100° FOV, mounted
1.3 m forward and 2.3 m above the ego origin with zero rotation. Every decision
requires its current native sensor frame and timestamp. The released preprocessing
center-crops 256 × 256, converts BGR to RGB and preserves the upstream 0–255
floating-point pixel scale before the network's normalization.

The policy also receives **ideal simulated ego pose and speed** and a resolved
**framework route**. These assistance sources are declared in diagnostics.
Other actors' ground-truth state, structured scene data and simulator traffic-light
flags are excluded. It is not an unassisted camera-only driving claim.

The managed profile uses CPU inference with two Torch threads and a one-second
policy-step budget, distinct from the SDK's process deadline. Diagnostics retain
the checkpoint/source identity, device, actual parameter count, input image hash,
native frame/time, predicted waypoints, inference time and policy-step time.
These describe computations and observations, not a model's internal reasoning.
Synchronous simulation can run slower than wall time; meeting a one-second budget
does not imply maintaining 20 real-time decisions per second.

The real rendered check also exposed a control-delivery issue in the shared
CARLA adapter. Ordinary actor control calls are
[asynchronous](https://github.com/carla-simulator/carla/blob/0.9.16/LibCarla/source/carla/client/detail/Client.cpp#L470).
A separate rendered diagnostic observed the previous input on 3 of 229 ordinary
submissions, compared with zero mismatches on 229 acknowledged submissions.
Ego commands now use an acknowledged batch with automatic ticking disabled,
followed by the normal single world tick. Missing or rejected acknowledgements
fail before that tick. This preserves the decision/frame relationship without
relaxing the native-control comparison or changing the model's outputs.

## Model bytes and terms

The app fetches only the stored `aim/best_model.pth` member from the
[official model archive](https://s3.eu-central-1.amazonaws.com/avg-projects/transfuser/models.zip),
using the fixed byte range 138–85,999,991 of its 697,106,220-byte source.
The extracted checkpoint is 85,999,854 bytes, SHA-256
`176d7a613bd476dd3be5863d42be4a1ea5d6697781c835d9e2a65e17cd89d748`.
A server that ignores this range is rejected before archive bytes can be saved
as a checkpoint. Resuming uses the exact remaining member range and verifies
the entire checkpoint before publication. The app never executes archive code.

The adapted code retains the upstream MIT notice. **Upstream does not separately
declare checkpoint terms in the reviewed release.** No checkpoint redistribution
license is inferred; private source download/integration validation does not close
the public distribution review. No weights are committed or bundled with the app.

The CPU environment shares S7's pinned dependency inputs but has its own directory,
ownership identity, readiness receipt and execution lease. It keeps the AIM notice,
source/checkpoint pins and dependency notices. Its shared-lock PlanT notices do
not grant a license to AIM weights. Registered identities fingerprint both the
environment receipt and the actual weights. Repair/removal affect owned assets;
imported models and experiment evidence are preserved.

See [managed AI](managed-ai.md) for lifecycle controls and the
[roadmap](scalability-plan.md) for remaining machine, performance and release gates.
This initial supported camera integration does not add a trained LiDAR policy,
CUDA installation, broad driving-quality evaluation or road-safety certification.

## Local validation

The [November 1 validation record](validation/s16-s17-sensor-hub-20261101.json)
documents a four-second rendered CARLA 0.9.16 / Town10HD_Opt run through the
packaged Companion and its managed Torch 2.6 CPU interpreter. All 80 decisions
use native RGB frames and exclude other actors' ground truth; all 80 native
control reads match the proposed/applied model outputs within 0.00002.
The initially stationary Tesla moves 1.3087 m. Policy-step p95 is 0.09235 s,
maximum 0.11139 s, with zero one-second budget misses. This does not meet or
claim 20 Hz wall-time performance.

The tested laptop has an AMD Ryzen 7 260 CPU and 15.31 GiB RAM. Available GPUs
and drivers are recorded separately; the learned policy runs on CPU. The test
reuses a private Low/offscreen server, scopes rendering on, restores its prior
no-rendering setting, actors and weather, closes the controller and stops only
the helper-owned server. All 6,317 bundle integrity files remain unchanged and
all 215 shipped application Python modules match source. Retained failed
attempts distinguish control-delivery, environment assembly, startup and
observer-timeout failures from the final successful run. Native visual and
independent-machine acceptance remain open.
