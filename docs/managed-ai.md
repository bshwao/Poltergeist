# Managed AI preparation

**AI models** in the Companion installs supported **PlanT 2.0**, **AIM camera**
and **TransFuser RGB + LiDAR** CPU packs on
Windows x64: a separate Python 3.12.10 interpreter, pinned dependencies and official
weights. **Manage AI…** beside the Agent selector opens these controls. Users need
no manual Python or pip setup. The baseline remains available without downloads;
**Enable teaching starter** adds the included trusted starter without downloads.
S7's implementation and local evidence are described in the
[environment validation record](validation/s7-environment.md). Independent-machine
acceptance remains a release gate. CUDA installation is outside this CPU pack.
Choose the supported model before preparation; the choice survives reopening.
The [fusion policy guide](transfuser-policy.md) documents trained RGB/LiDAR
inputs, their timing and checkpoint terms. The [AIM guide](sensor-driving-policy.md) documents its actual camera inputs,
checkpoint terms and separate driving validation. The PlanT details below retain
their existing structured-scene scope.

The first catalogue entry is the [upstream PlanT model](https://huggingface.co/SimonGer/PlanT2/tree/e81b261f8b92b20225d020fe9b7a0470c1434d96),
model revision `e81b261f8b92b20225d020fe9b7a0470c1434d96`, 446,559,352 bytes
(425.9 MiB), SHA-256
`2f896d8d7640d4b7b68bcef8615a73e9761e850870a24f914c4c908f0f2199f4`.
The model card declares Apache-2.0; the adapted integration code has its existing
MIT notice. The integration source revision and model revision are separate pins.
The environment retains original dependency metadata/notices, CPython's license,
the model's Apache-2.0 text, the adapted code's MIT text and ANTLR's BSD notice.
Public release signing/redistribution review remains separate release work.

PlanT consumes privileged structured simulator scene data and a supplied route.
The original upstream checkpoint requires Python pickle. These download controls
never import Torch or deserialize the checkpoint. The reference agent's existing
full-size/hash gate still precedes deserialization. These weights alone provide
neither camera perception nor a road-safety validation claim.

## Through the app

The panel groups model weights, source/account controls and the AI environment.
**Install / resume AI** is the full preparation path; downloading weights alone
does not check inference. Next-step guidance explains readiness and enabling.
Tab keeps focused controls in view; focus the scroll surface to use Page Up/Down
or Home/End. Actions that require a stopped local runtime are disabled while it
runs. Source checks, weight downloads and weight inspection remain available.

- **Check pinned source** checks the curated PlanT Hugging Face revision, file
  size, LFS SHA-256 and declared license against the built-in pins. It never
  resolves `main`, changes a pin, or marks saved weights/inference ready. Failed
  or paused source checks preserve existing inference readiness.
- **Configure account…** optionally saves an explicitly entered Hugging Face read
  token in a separate current-user Windows DPAPI-protected provider record.
  Public PlanT downloads work anonymously. No ambient `HF_TOKEN`, CLI login or
  developer credential cache is read. **Forget account** clears that provider
  record without touching enrollment, model files or results. Configure/clear is
  blocked while a model action is active.
- **Download / resume** saves bounded chunks outside the installed bundle, in
  `<companion profile>/models/plant2-e81b261f8b92/`. After an interrupted transfer,
  restarting the app and choosing the same action resumes the owned partial file.
- **Pause download / check** requests cooperative cancellation between network
  reads or hash chunks. Network reads have a five-second socket timeout; this is
  not an unconditional operating-system/DNS shutdown deadline. Closing requests
  pause and retains profile ownership while I/O remains active. Retry Close after
  it returns rather than opening the same profile in another process.
- **Check saved weights** explicitly rehashes completed bytes. Startup does not
  read hundreds of megabytes on the UI thread or assume that a prior check still
  proves current integrity. Partial files remain unverified until completion.
- **Repair weights…** first verifies an existing completed file. If damaged, it
  removes only these owned weights/partials and starts over. **Remove weights…**
  removes only those catalogue files. Stop the local runtime before either action.
  Imported models, results, recordings, credentials and the core runtime remain
  outside these operations.

Transfers use HTTPS with certificate and hostname verification, no ambient proxy
credentials, bounded chunks and exact size limits. Resume accepts an exact
`Content-Range`; if a server ignores Range with a full 200 response, the owned
partial is safely restarted. Every completed file, including its resumed prefix,
is checked against the built-in SHA-256 before publication. Corrupt bytes are
retained for explicit repair, and a disconnect can leave a resumable partial.
Signed CDN URLs are omitted from app errors. No credentials or checkpoint bytes
are written into progress messages.
An explicitly configured token is sent only to the official HTTPS Hugging Face
origin; it is stripped on redirects, including signed CDN redirects. Access
denials and rate limits produce actionable errors without exposing tokens or
signed URLs. The catalogue is curated, rather than an arbitrary repository
browser. AIM and TransFuser use the official project archive and never receive Hub credentials.
The provider behavior follows Hugging Face's
[revision-pinned downloads](https://huggingface.co/docs/huggingface_hub/guides/download)
and [read-token access](https://huggingface.co/docs/hub/security-tokens).

Each directory carries a fixed catalogue ownership record and an OS writer lease.
Linked/hardlinked files, mismatched ownership and unrecognized contents are
refused. An interrupted initial ownership-record creation can leave a directory
requiring manual inspection; the app does not silently claim that directory.
These safeguards protect routine file ownership, not hostile code running as
the same local user. Custom agent code still requires explicit local trust.

## Install and run the CPU pack

1. Stop the local runtime and open **AI models**. Allow at least 3 GiB free disk
   space. The environment download is about 296 MiB in addition to the weights.
2. Choose **Install / resume AI**. The app verifies every archive, installs the
   separate interpreter/dependencies, checks import locations and dependency
   compatibility, and runs an actual pretrained inference in its SDK worker.
3. After **AI ready**, choose **Enable selected AI for local runs…** and review the trust
   notice. This starts the local runtime and registers the verified pack. Select
   **Managed PlanT 2.0 (CPU)** in the Agent selector and choose **CARLA**.
4. Run a compatible scenario and inspect the normal decision timeline, recorded
   controls, provenance and reports. Synthetic execution is rejected before
   queueing: it lacks the observed road raster this checkpoint requires.

Weights alone never enable a model. **Check AI readiness** repeats integrity, imports
and real inference after reopening. Existing registrations reacquire a verified
environment use lease when submitted. Repeated enabling reuses an unchanged active
registration; changed inputs require a new registration, preserving history.

**Repair AI…** rebuilds the owned environment from verified downloads and repairs
damaged assets. **Remove AI…** removes its interpreter, dependencies and cached
environment assets. Its completion state explicitly says saved weights are kept;
those bytes are not marked verified by removing the environment. Weight removal
remains separate. Stop the local runtime before
preparation, readiness checks, repair or removal. An OS lease excludes mutation
through execution until the worker stops. Missing or damaged packs fail explicitly
without switching to the baseline. Interrupted assembly is never executable;
retry rebuilds it from verified cached archives. App source/location changes
require environment repair and a new readiness check.

`companion/plant-cpu.lock.json` pins each HTTPS URL, version, size and SHA-256.
The app executes no pip resolver, build hooks or setup scripts. The pure-Python
ANTLR source archive is installed as data. The capsule's dependency search path
excludes core/user/developer site-packages. A narrow import finder exposes only
Poltergeist's six first-party runtime packages from the installed app. A manifest
covers every installed file and first-party source and is fingerprinted in the
agent registration. Original notices and the input lock are retained in the
environment's `runtime/notices` and `runtime/site-packages/*.dist-info`.
The actual checkpoint file is also fingerprinted in new managed registrations.

The supported profile explicitly permits 180 s cold load, 30 s reset and 15 s per
decision. CPU inference may be slower than simulation; earlier manual CUDA
throughput results do not apply. Windows/CARLA native prerequisites and drivers
remain system requirements. Failed native imports fail readiness explicitly.

Custom packages remain explicitly trusted local SDK code with a separately
prepared compatible interpreter via the registry/API/CLI. This catalogue is not
an arbitrary pip installer; never add custom dependencies to the core or managed
pack. Hosted execution keeps its existing baseline-only policy. Process/file
ownership checks do not sandbox hostile code running as the same local user.

With an idle matching CARLA server already running, repeat **PlanT** acceptance
using the bundled interpreter (or the development environment). AIM uses the
separate [camera workflow](sensor-driving-policy.md):

```powershell
runtime/python.exe -I -B -m scripts.validate_managed_ai --data-dir C:/YourWritableFolder/ai-check --prepare
```

The command calls the Companion's own actions and retains readiness, registration,
CARLA controls, reports and persistence-reopen evidence in that profile. Omit
`--prepare` to recheck an existing installation. It does not substitute synthetic
road observations or another controller for a failed run.

## Validation

The [November 1 completion review](validation/s16-s17-completion-review-20261101.md)
separates implemented S16/S17 scope from remaining acceptance gates and records
the native model-panel usability and environment-removal corrections.

S17's November 1, 2026 check repeated the real public lifecycle through the final
portable build's isolated interpreter: confirm pinned Hub metadata, pause at
265,818 bytes, close/reopen, observe `206` with
`Content-Range: bytes 265818-446559351/446559352`, verify the complete checkpoint,
rehash it and remove owned assets while preserving an unrelated imported-file
sentinel. Native DPAPI save/read/clear used a generated dummy token that was
removed before any transfer. No personal token or gated-account access was used.
An additional check reopened the protected dummy account in a fresh packaged
controller instance, verified it could be read back, then cleared it without any
network request.
All 6,317 bundle integrity files were unchanged; all 215 shipped application
Python modules matched the candidate source. This is CLI/controller acceptance,
separate from native visual and independent-machine acceptance.
The [S16/S17 record](validation/s16-s17-sensor-hub-20261101.json) retains the final
model/control measurements, source hashes, asset lifecycle and earlier failed
attempts. Native GUI callbacks and the full platform matrix are checked separately
in GitHub CI.

The [managed weight record](validation/s7-managed-weights.json) retains source
report hashes, the initial close-poll failure and its regression, and private
Runtime K's real HTTPS acceptance. Native Tk callbacks paused at 8,440,134 bytes,
closed the process, reopened the profile in another process, resumed with an
honored 206 Range response, and verified all 446,559,352 bytes. An explicit second
saved-file check also passed. The 176 shipped first-party Python files match
source; all 6,087 integrity files remained unchanged before and after both runs.

The weight-only checks above are withdrawn native callbacks on the development computer, with synthetic
model-byte fixtures for destructive/fault cases. They do not constitute visual
inspection, an installed shortcut launch, learned inference or external-machine
acceptance. The original ordinary user profile and imported files were not used.
The new interpreter/inference/execution checks are recorded separately in
[S7 environment validation](validation/s7-environment.md).
