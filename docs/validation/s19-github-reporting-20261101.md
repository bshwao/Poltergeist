# S19 implementation validation — November 1, 2026

Implemented from local `main` at `619871f` (bounded local OpenAI assistance).
The local dashboard/API and CLI now publish explicitly approved S15 regression
checks against verified exact commits. See [setup and scope](../github-reporting.md).
The implementation is delivered; live GitHub App and required-check acceptance
remain open.

## Verified behavior

- Configuration is disabled by default. Status/disabled requests do not create
  persistence, a simulator worker, a publication journal or provider traffic.
  Non-loopback peers are rejected, including spoofed localhost Host headers.
  No GitHub action is admitted by the account/device relay.
- Both full commit/tree identities and complete frozen package inventories are
  checked through bounded Git object reads. Exact blob hashes/sizes, explicit
  Windows line-ending transforms and committed LFS/JSON external-model pins are
  supported. Changed/extra files, links, truncated trees, ambiguous duplicate
  pin fields and invalid paths cannot create an accepted preview.
- Private names, local paths, configuration values and unselected evidence are
  absent from the default payload. Metrics, fingerprints, run IDs and scenario
  recipes are opt-in. The outgoing JSON is fully reviewable without the token.
- Explicit boolean approval binds the exact preview digest, App/repository,
  policy, source report, candidate and one-hour expiry. Changed evidence or
  configuration is rejected before the write. Policy changes receive distinct
  GitHub check contexts; the saved preview owns an independent policy copy.
- Pending suites cannot publish a completed verdict. Failed/cancelled execution
  and measured regression retain their appropriate conclusions. Inconclusive,
  missing or insufficient pairs produce `action_required`, never a passing or
  neutral check. Success requires all selected assessments' planned coverage.
- The SQLite reservation commits before external publication. Concurrent calls,
  restarts, duplicate previews and lost replies cannot repeat a same-journal
  write. A fresh journal can recover an existing identical external identity.
  Daily limits count reservations even after failure or reconciliation.
- Read-only reconciliation confirms the exact App, candidate commit, check
  identity/name, conclusion and output. Changed remote checks are not adopted or
  overwritten. Ambiguous/lost results stay uncertain and never auto-retry.
- The HTTP adapter uses a fixed official HTTPS origin/version, bounded
  requests/bytes/time, no redirects/proxy inheritance/retries and sanitized error
  messages. Browser guards reject malformed proofs, false successes, mismatched
  request identities and unsafe/cross-repository result links.

## Results

| Check | Result |
| --- | --- |
| Final Python integration subset | **177 passed**, warnings treated as errors: 38 S19, 30 S18, 78 shared-contract and 31 configuration cases; `validation-evidence/s19-final-tests.xml` |
| Frontend unit/client/component suite | **393 passed**, one test-file worker |
| Targeted S19/S18/Analyst/Regression frontend checks | **23 passed** |
| S19 browser journeys | **3 passed**: exact disclosure/approval/result link, uncertain outcome/read-only recovery, disabled configuration |
| Accessibility/responsiveness | Full-page WCAG A/AA automated desktop and 390-pixel mobile scans pass without exclusions; no document horizontal overflow |
| Production builds | Local and hosted builds pass; local bundle retains the existing >500 kB warning |
| Generated contracts | Python schema and TypeScript drift checks pass |
| Diff hygiene | `git diff --check` passes |

The Python service fixtures use typed S15 report exports, recorded-input shapes
and controlled Git objects. Transport fixtures exercise HTTPX without a real
token. Browser journeys run against the actual local app and a real saved
synthetic baseline suite; S15/GitHub replies are controlled serialized service
fixtures. They validate the UI consumer and consent sequence, not a claim that
the surrounding baseline executed those registered model versions. The explicit
`POLTERGEIST_GITHUB_FIXTURE=1` seed avoids requiring the unrelated SDK installation
benchmark. Normal browser installation checks remain unchanged. Browser artifacts
are under `frontend/test-results/` and `frontend/playwright-report/`.

## Corrections and remaining acceptance

The initial browser launch used the system Python, which lacked `uvicorn`.
Selecting the existing project Python environment resolved it. The first browser
fixture then returned HTTP 200 for preview instead of the route's required 201;
the browser correctly rejected it. After correcting the fixture status, all
three journeys passed. No production validation guard was weakened.

An expanded Python check found a mutable preview-policy alias. Preview creation
now copies that policy; the retained test verifies that later review changes
cannot alter the first preview. The earlier failure is in
`validation-evidence/s19-expanded-tests.xml`. Subsequent source/integration and
final receipts pass. Strict duplicate model-pin field rejection was added and
verified in the final run.

Local configuration inspection found GitHub disabled, with no App token,
repository or App ID set. **No live GitHub write, PR comment, repository change,
token acquisition or branch-protection update was made.** Actual token/repository
compatibility, GitHub's production check rendering and required-check behavior
need an authorized live App installation acceptance run. GitHub Enterprise,
hosted account publishing and autonomous token lifecycle are outside this scope.

Commit proofs certify the recorded package/model bytes or pins, not training
origin, runtime/dependency equivalence or effective configuration as Git contents.
Reproduction still requires the retained frozen local suite journal. Independent
publishers cannot achieve globally atomic create semantics through GitHub's API;
use a shared journal/single publisher or CI concurrency as documented. No CARLA,
trained policy, signed-package, native companion or independent-machine release
acceptance was repeated for this change. The previously recorded SDK-load
failure in the S18 receipt was not retested or reclassified by these checks.
