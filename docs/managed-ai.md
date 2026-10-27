# Managed AI preparation

S7 is in progress. **AI models** in the Companion prepares the official PlanT 2.0
weights; **Manage AI…** beside the existing Agent selector opens those controls.
The rule-based baseline remains available without downloads. Preparing weights
does not change the selected agent or claim a usable model environment.

The first catalogue entry is the [upstream PlanT model](https://huggingface.co/SimonGer/PlanT2/tree/e81b261f8b92b20225d020fe9b7a0470c1434d96),
model revision `e81b261f8b92b20225d020fe9b7a0470c1434d96`, 446,559,352 bytes
(425.9 MiB), SHA-256
`2f896d8d7640d4b7b68bcef8615a73e9761e850870a24f914c4c908f0f2199f4`.
The model card declares Apache-2.0; the adapted integration code has its existing
MIT notice. The integration source revision and model revision are separate pins.
Dependency redistribution and the eventual environment's complete notices still
need review.

PlanT consumes privileged structured simulator scene data and a supplied route.
The original upstream checkpoint requires Python pickle. These download controls
never import Torch or deserialize the checkpoint. The reference agent's existing
full-size/hash gate still precedes deserialization. These weights alone provide
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

## Remaining S7 completion work

The app still needs a separately verified model interpreter/dependency capsule,
supported CPU/GPU configurations, versioned package hashes and complete notices,
environment repair/removal and use leases, actual import/inference readiness,
trusted registration and execution through the Agent selector. The bundled core
has no pip/ensurepip, and a naive venv over its isolated prefix still sees core
site-packages; it must not be used to install conflicting model dependencies.

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
