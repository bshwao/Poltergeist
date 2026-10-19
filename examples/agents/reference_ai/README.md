# Poltergeist reference AI: PlanT 2.0

This first-party integration uses the actual pretrained **PlanT 2.0** planning
transformer from the [Autonomous Vision Group](https://github.com/autonomousvision/plant2).
The network predicts a local path and timed waypoints; the authors' deterministic
tracking controllers convert those predictions into steer, throttle, and brake.
Poltergeist does not train this model, and this package does not wrap the
rule-based baseline or substitute a hazard-braking controller.

The observation is **privileged simulator ground truth**: objects (including
hidden ones), map road/lane geometry, speed limit, relevant signs/lights, and a
supplied navigation route. It evaluates learned planning and behavior, not
camera perception, occlusion detection, or independent global route selection.

## Prepare an isolated Python 3.12 worker

From the repository root in PowerShell:

```powershell
py -3.12 -m venv .agent-venv
.\.agent-venv\Scripts\python.exe -m pip install -e .
# NVIDIA CUDA 12.4 wheels (choose the CPU alternative below if needed):
.\.agent-venv\Scripts\python.exe -m pip install torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cu124
.\.agent-venv\Scripts\python.exe -m pip install -r examples/agents/reference_ai/requirements.txt
.\.agent-venv\Scripts\python.exe -m scripts.setup_reference_ai --download
```

The CPU alternative for the wheel-install step is:

```powershell
.\.agent-venv\Scripts\python.exe -m pip install torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cpu
```

The manifest selects `device: auto`; the actual device is recorded in every
decision's diagnostics. The supported checkpoint is approximately **426 MiB**;
PyTorch's CUDA wheels are substantially larger. Neither an ordinary package
inspection nor starting the agent downloads or installs anything.

`setup_reference_ai` downloads only the official checkpoint into
`storage/models/plant2/epoch=029_final_1.ckpt`. It checks the published size and
SHA-256 before accepting the file. Loading verifies the hash again **before**
deserializing its trusted upstream Lightning/OmegaConf metadata. The reference
integration rejects arbitrary replacement checkpoints. Optional
`checkpoint_path` configuration can identify another copy of the same bytes.

## Run through Poltergeist

Start CARLA 0.9.16 first. Use the existing CARLA-compatible parent Python
environment; the worker above does not need the `carla` package:

```powershell
& $carlaPy -m simulator --template intersection --mode carla --agent-package examples/agents/reference_ai --agent-python .agent-venv/Scripts/python.exe --agent-control-mode authoritative --agent-load-timeout 120 --agent-step-timeout 10
```

`$carlaPy` must be assigned in the current PowerShell terminal to the absolute
path of the Python executable that has Poltergeist and CARLA installed. The
`authoritative` flag is required to demonstrate model control without scripted
ego braking or route steering overriding its predictions. Scenario actors still
execute their scripts. Requested timeouts stop a hung worker; they do not make
an over-budget model real-time. This release's tracker is used at **20 Hz**
(`fixed_delta_seconds=0.05`).

The synthetic `--mode test` adapter does not provide a measured map raster and
is intentionally rejected by this checkpoint. A hand-authored synthetic raster
is used only in the explicit neural inference fixture/tests, never substituted
for missing live map context. Dashboard model selection is a later phase.

The maintained pilot validation command and report are documented in
[the launch guide](../../../docs/reference-ai-pilot.md) and
[the actual validation record](../../../docs/reference-ai-validation.md).
The learned controller has completed live CARLA trials and passed the 20 Hz
complete-cycle gate with the no-rendering performance profile on the tested
RTX 2060 laptop. See [performance acceptance](../../../docs/reference-ai-performance.md)
and launch the pilot with `--performance --require-real-time` and two seeds to
measure the same gate on another machine.

## Integration provenance and adaptations

Upstream code revision:
`6269e5a43c6f6b984d2bd97e3ec4a025ebefbeef`.
Checkpoint repository: [SimonGer/PlanT2](https://huggingface.co/SimonGer/PlanT2).
Checkpoint SHA-256:
`2f896d8d7640d4b7b68bcef8615a73e9761e850870a24f914c4c908f0f2199f4`.
The upstream repository is MIT licensed; the full notice accompanies the
adapted neural and tracking code in `simulator/agents/reference_ai/LICENSE.plant2`.
The [checkpoint model card](https://huggingface.co/SimonGer/PlanT2/blob/main/README.md)
declares **Apache 2.0** for the weights. The weights are fetched from their
official public release rather than redistributed in this repository.

The inference architecture is constructed offline, then **every model parameter
is strictly loaded** from the checkpoint. No pretrained BERT/ResNet download is
needed, and no randomly initialized parameter is left active. Training,
Lightning, CARLA Leaderboard, and upstream CARLA client/ticking code are removed.

Object tokens use explicit classes, ego-relative metres/yaw, signed speed in
km/h, and measured box width/length. The model receives 20 route samples from
2.5 to 21.5 metres ahead, following the supplied map polyline. Road labels are
rendered from the installed CARLA map at 2 pixels/metre, converted to the
upstream semantic RGB palette and affine orientation. This map renderer is an
adaptation from upstream's precomputed map files, so benchmark equivalence is
not implied. Non-training speed limits are mapped to the nearest supported
50/80/100/120 km/h category; that category is reported. At most 480 nearest
objects fit the released transformer's positional capacity; any omissions are
counted in diagnostics. Stationary walker filtering and stop-sign clearing
retain the authors' preprocessing behavior.

Per-step diagnostics contain the real learned path/waypoints, model-selected
target speed, actual device, checkpoint identity, object counts, and measured
inference duration. They are predictions and timing evidence, not a generated
explanation of the neural network's internal reasoning.

Phase 3 updates this package to SDK 1.2 with framework-provided navigation.
Explicit tasks supply an actual destination and finite route. Endpoint input
padding, upstream startup limits, task speed caps and destination approach
tracking are declared assistance. Diagnostics distinguish raw neural speed,
upstream tracking speed and the final capped target; the model still runs every
decision. See [driving tasks](../../../docs/driving-tasks.md) for a mapped Town03
example and measured completion criteria.
