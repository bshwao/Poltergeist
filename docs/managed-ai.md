# Managed AI preparation

S7 is in progress. **AI models** prepares the official PlanT 2.0 weights and, in
builds containing the CPU model pack, installs its independent Python environment.
**Manage AI…** beside the Agent selector opens those controls. The baseline
remains available without downloads. Preparing weights alone does not enable AI.

The first catalogue entry is the [upstream PlanT model](https://huggingface.co/SimonGer/PlanT2/tree/e81b261f8b92b20225d020fe9b7a0470c1434d96),
model revision `e81b261f8b92b20225d020fe9b7a0470c1434d96`, 446,559,352 bytes
(425.9 MiB), SHA-256
`2f896d8d7640d4b7b68bcef8615a73e9761e850870a24f914c4c908f0f2199f4`.
The model card declares Apache-2.0; the adapted integration code has its existing
MIT notice. The integration source revision and model revision are separate pins.
Runtime, wheel and supplemental upstream notices accompany the model pack.
Complete transitive binary redistribution review remains a release gate.

PlanT consumes privileged structured simulator scene data and a supplied route.
The original upstream checkpoint requires Python pickle. Download/check-weight
actions never import Torch or deserialize the checkpoint. Environment checks run
the pinned model in a separate process after its full-size/hash gate. These weights provide
neither camera perception nor a road-safety validation claim.

## Through the app

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
- **Install and check CPU** extracts the bundled, hash-pinned model pack into a
  new profile generation. It verifies the exact inventory before activating that
  generation, then starts the separate interpreter with isolated imports and
  runs two synthetic inference fixtures. **Check environment** repeats the checks;
  readiness is not inherited from a prior app session.
- After the CPU check passes, start the local runtime and choose **Use PlanT CPU**.
  This records an immutable agent registration and selects CARLA. Actual recipe
  execution still requires compatible CARLA and a measured road raster.
- **Repair environment…** builds a fresh generation from the bundled archive.
  **Remove environment…** keeps weights and results. Active worker leases and
  saved waiting/running model jobs prevent removal of referenced files. Preparation,
  repair and removal require the local runtime to be stopped and block restarting
  it until the operation ends. Ordinary weight downloads can run beside baseline work.

Transfers use HTTPS with certificate and hostname verification, no ambient proxy
credentials, bounded chunks and exact size limits. Resume accepts an exact
`Content-Range`; if a server ignores Range with a full 200 response, the owned
partial is safely restarted. Every completed file, including its resumed prefix,
is checked against the built-in SHA-256 before publication. Corrupt bytes are
retained for explicit repair, and a disconnect can leave a resumable partial.
Signed CDN URLs are omitted from app errors. No credentials or checkpoint bytes
are written into progress messages.

Each directory carries a fixed catalogue ownership record and an OS writer lease.
Linked/hardlinked files, mismatched ownership and unrecognized contents are
refused. An interrupted initial ownership-record creation can leave a directory
requiring manual inspection; the app does not silently claim that directory.
These safeguards protect routine file ownership, not hostile code running as
the same local user. Custom agent code still requires explicit local trust.

## Model pack and custom agents

The private model pack targets Windows x64, CPython 3.12 and CPU inference. Its
runtime archive, SDK and dependency wheels have independent hashes in
[publisher inputs](../packaging/windows-x64/model-cpu-inputs.json). The publisher
reconstructs it from those archives, checks ABI/dependency metadata and wheel
records, and collects upstream notices. The app performs no pip or source build.
The pack catalogue lives outside the SDK wheel, avoiding a circular SDK hash.
The core bundle contains the model ZIP as data and keeps its own dependencies.

Installed generations have an exact, pinned file inventory. Agent snapshots bind
both the model SDK and the separate parent framework; the model worker verifies
its actual interpreter/import origins. Third-party runtime bytes are checked
under a use lease before the worker starts. A failed process cleanup retains its
lease and the profile until cleanup succeeds. These integrity checks do not
authenticate an unsigned publisher or sandbox trusted local code.

Custom-agent registration retains explicit local trust and static inspection.
The desktop does not resolve arbitrary custom dependencies. Dependency-free
agents can use the core interpreter; agents needing additional packages need
their own compatible interpreter and installed SDK. API registrations can declare
an isolated Windows CPython 3.12 prefix; that option requires the SDK inside that
prefix and separately fingerprints the calling framework. App-managed generations
are immutable and must not be edited to install custom packages.

## Remaining S7 completion work

Live packaged CARLA execution, fresh supported-machine acceptance, hardware and
native path coverage, complete redistribution review and any supported GPU packs
remain separate gates. The bundled core has no pip/ensurepip. Keep model
environments separate from it and use short profile paths for native libraries.

The existing manually prepared reference-AI CLI remains a separate developer
workflow. Hosted remote execution remains subject to its existing baseline-only
policy. S7 completes only when a fresh supported computer can prepare **and run**
the reference AI through the app without manual Python/dependency installation.

## Validation

The [managed weight record](validation/s7-managed-weights.json) retains source
report hashes, the initial close-poll failure and its regression, and private
Runtime K's real HTTPS acceptance. Native Tk callbacks paused at 8,440,134 bytes,
closed the process, reopened the profile in another process, resumed with an
honored 206 Range response, and verified all 446,559,352 bytes. An explicit second
saved-file check also passed. The 176 shipped first-party Python files match
source; all 6,087 integrity files remained unchanged before and after both runs.

These are withdrawn native callbacks on the development computer, with synthetic
model-byte fixtures for destructive/fault cases. They do not constitute visual
inspection, an installed shortcut launch, learned inference or external-machine
acceptance. The original ordinary user profile and imported files were not used.
