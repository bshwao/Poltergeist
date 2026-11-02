# Repository polish and regression review — November 2, 2026

Reviewed the source checkout at `a6f1044` across application boundaries, frontend
state/navigation, companion/coordinator workflows, simulator/evaluation tests,
configuration, generated contracts, build/CI entry points and current guides.
Generated installation evidence, local databases and virtual environments were
treated as validation inputs/artifacts rather than source to reformat.

## Improvements

- Dashboard pages load on first visit while mounted drafts survive navigation.
  Initial production JavaScript fell from 536.56 kB to **243.03 kB** (145.52 to
  74.19 kB gzip). This measures transferred entry code, not a benchmark of page
  latency. The existing >500 kB warning is gone without raising its threshold.
- Each deferred page has accessible loading and error states. A failed import
  leaves other pages usable and provides a deliberate dashboard reload, with a
  reminder to copy unsaved drafts first. Chromium caches a failed module import;
  repeatedly wrapping the same import in a new lazy component is not recovery.
- OpenAI disclosure approval binds the reviewed digest. Refreshing a changed
  preview clears the checked state and a queued send cannot use old approval.
  Existing backend approval, budget and journal boundaries remain in force.
- Assistant and GitHub requests share the experiment hooks' abortable promise
  behavior. Timeouts/navigation settle busy state even when a transport ignores
  its signal; its late result cannot overwrite a newer refresh. No automatic
  provider or publication retry was added.
- Keyboard outlines use the shared design tokens, including Windows forced
  colors. Field focus no longer removes the outline; selected templates,
  duration controls and secondary-button hover use the existing green palette.
- The private OpenAI journal and its SQLite sidecars are ignored by Git, matching
  the existing GitHub publication journal policy.
- Both browser runners choose an explicit `POLTERGEIST_TEST_PYTHON`, otherwise
  the checkout's `.venv`, otherwise `python` from `PATH`. The exact integration
  fixture command passes without an override on this Windows checkout.
- CI now explicitly exercises optional OpenAI/GitHub disclosure and approval
  journeys with controlled providers instead of leaving the enabled OpenAI
  cases skipped in the default run. Artifacts use a separate integration folder.
  `make check` also builds the hosted dashboard.
- Dashboard, setup, first-run, API and architecture guides describe the current
  pages, registered controllers, optional integrations and environment selection.
  The old claim that optimization controls were unavailable in the dashboard
  was removed. Existing release and independent-machine gates remain open.

## Verified results

| Check | Result |
| --- | --- |
| Full Python regression suite | **3,616 passed, 10 skipped, 5 warnings** in 25 minutes; `validation-evidence/repo-polish-baseline-20261102.xml` |
| Frontend unit/client/component suite | **397 passed**; includes changed-preview approval, ignored cancellation, timeout recovery and deferred-page failure isolation |
| Full local browser suite, normal SDK/sensor seed | **33 passed, 2 skipped**; provider-dependent OpenAI cases covered separately below |
| Explicit optional integration browser run | **6 passed**; real local OpenAI disclosure/journal routes with a controlled provider and serialized GitHub service fixtures |
| Hosted HTTPS/WSS browser suite | **3 passed**; paired-computer guidance, unknown/failed loading states, separate accounts, lost reply, cancellation, reload, pause and revocation |
| Accessibility | Full-page automated WCAG A/AA scans on all eight local pages at desktop/mobile widths; forced-color editor focus and mobile page-load failure recovery also pass; no rule exclusions |
| Builds | Local and hosted production builds pass without the previous local chunk-size warning |
| Contracts/environment | Python and TypeScript generated-contract checks, `pip check` and `scripts.doctor --frontend` pass |
| Documentation/configuration | All 74 Markdown files, including this receipt, have no broken relative file links; updated workflow YAML parses and retains read-only workflow permissions |
| Journal hygiene | Git ignores the assistant database, rollback journal and WAL paths, plus the publication journal |

The Python skips require optional Torch, SciPy, OpenCV or explicitly enabled
prepared model weights. Five pytest warnings concern `record_property` metadata
in the coordinator crash fixtures with the default JUnit `xunit2` family; they
are retained in the XML receipt and no warning filter was added. This run includes
native companion callback tests and real local process-loss/TLS/WSS recovery.
The previously failing
`test_registered_agent_search_confirmation_and_suite_comparisons` passes in this
run. Earlier failures remain recorded; no SDK deadline was extended or production
change made to claim that environment-dependent timing cannot recur.

Browser evidence remains in ignored `frontend/test-results/polish-full-local/`,
`polish-integrations-final/`, `frontend/test-results/hosted/` and corresponding
`frontend/playwright-report/` subdirectories. Screenshots were inspected for the
scenario layout and mobile failed-page state. Automated accessibility checks
are not a substitute for screen-reader/human acceptance.

## Corrections and scope

The initial deferred-page helper had generic type-inference errors; the production
TypeScript check caught them and both builds pass after correction. A first
browser recovery attempt also confirmed Chromium's failed-import caching (and
had an incorrect expected Agents heading); the final journey verifies continued
access to a retained scenario draft and successful explicit reload instead.
The initial failure artifacts remain under `polish-recovery/`.

The hosted fixture logs Windows/Python 3.14 `ConnectionResetError` callbacks
during connection teardown; all three journeys complete successfully. These
diagnostics were not suppressed. Existing SDK deadlines, evidence thresholds,
execution budgets, remote scopes and model verification were not relaxed.

No live provider call, GitHub publication, public deployment, signed release,
new model download or CARLA drive was performed. This pass does not close native
visual, independent-machine, live-provider or release acceptance gates.
