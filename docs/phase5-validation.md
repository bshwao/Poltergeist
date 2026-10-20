# Phase 5 validation — October 19, 2026

The local registry and queued API selection are implemented. The tests below
exercise real external Python workers with synthetic simulation and labelled
sensor fixtures. This phase did not launch CARLA, load the learned reference
checkpoint for inference, download weights, or measure live driving performance.
Earlier live model/sensor results remain documented in the Phase 2.5 and 4 guides.

## Functional acceptance

Core regression coverage totals **2,438 passing tests**, with **10 optional
dependency/checkpoint checks skipped**, across the broad run and final reruns.
The broad run passed 2,436 tests and exposed two test-only issues: Windows newline
translation in a stale-bytecode fixture, and an assertion expecting database
revision 3 rather than the new revision 4. Both were corrected; all **33 tests**
in the two affected files then passed. Product sources were unchanged during
those final corrections.

The registered-agent integration suite passed **22 tests**. It verifies that:

- Registration UUID, effective configuration, interpreter, deadlines, control
  ownership, observation mode and file fingerprints survive queuing and storage.
- A real external worker receives the selected configuration and controls the
  measured ego vehicle. State inputs and named camera/LiDAR fixture streams keep
  their distinct provenance; sensor samples persist without legacy recorder flags.
- Waiting jobs retain their accepted selection across restart and deactivation.
  Changed code, manifests and model assets fail before import/world setup.
- Loading errors, inference errors, deadlines and active/pending cancellation
  finalize explicitly, clean up workers, and leave the queue usable.
- Direct runner calls cannot substitute an unregistered/different agent or change
  the registered control mode. Results cannot relabel the controller or sensor rig.
- Custom-agent failure reproduction/minimization is rejected until agent-aware
  optimization exists, preventing silent replacement with the baseline.

Registry and migration checks also cover static inspection without agent imports,
immutable/deactivated versions, canonical JSON configuration identity, added or
missing files, external model/interpreter/runtime changes, linked model paths,
automatic reference-checkpoint fingerprints, and migration rollback. Existing
revision 1–3 databases retain their historical payloads and queued jobs.

Four bytecode regressions passed: stale timestamp-valid entry/sibling/runtime
caches cannot replace the inspected source, and private cache directories are
removed after startup failures, worker timeouts and normal shutdown. The existing
64 process tests passed with the fresh-cache startup path.

## Frontend and packaging

- **221 frontend tests passed**, with TypeScript checking and production build.
- Shared JSON Schema and generated TypeScript drift checks passed.
- Package checks accepted the starter and sensor pilot without executing code.
- A wheel built successfully using the existing build environment. Ten final
  registry/runtime modules were compared byte for byte with their wheel entries.
- Local documentation links and whitespace checks passed.

The local wheel is ignored at
`.pytest_cache/phase5-wheel/poltergeist-0.1.0-py3-none-any.whl`; its SHA-256 is
`9479c779f72fb9886940818bb1c2b57b2b7ac4333247632fc233f4a8781c6ab9`.

## Scope

Readiness is static file/declaration inspection, not dependency or inference
validation. Fingerprints describe the declared files at inspection checkpoints;
they do not freeze files or attest third-party dependencies, drivers, hardware
or the full execution environment. Generic agents must declare external weights
in `model_files`. Registration is for trusted local code.

The dashboard Agents page and launch selector remain Phase 6. Full persisted
decision timelines, behavior reports, and agent-aware automated comparisons and
optimization remain Phases 7–9. See [registry usage](agent-registry.md).
