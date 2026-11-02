# S18 implementation validation — November 1, 2026

Implemented from local `main` at `ee8a765` (managed AI model-controls polish).
The optional local dashboard/API integration is delivered with explicit provider
configuration, exact disclosure review, usage approval, bounded tool selection,
durable spending reservations and independently checked evidence output.
See [setup, scope and API behavior](../openai-assistance.md).

## Verified behavior

- Disabled configuration and local configuration reads create no database,
  journal, simulator worker or provider traffic. Planning previews create only
  their local journal record. The main database is obtained lazily when needed.
- Assistant routes reject non-loopback callers before persistence or provider
  access, including clients spoofing a localhost Host header. Paired-account
  commands cannot forward assistant/provider actions.
- Exact disclosure digests, explicit boolean approval, preview expiry and a
  stable configured model are required before sending. Common sensitive strings
  are redacted and the serialized payload is reviewable without the API key.
- Requests permit one function response with fixed input/output limits. Daily
  reservations survive failures, concurrent sends, missing acknowledgements and
  controller reopening. An existing request is never sent again automatically.
- Discovery tools select a supported scenario/failure policy. They preserve
  the supplied mode, agent, seed, duration, execution/scoring settings and budgets,
  and produce an ordinary frozen plan requiring separate execution approval.
- Analysis discloses only selected observations plus coverage. Output is built
  from verified original statements, measurements, references, unknowns and
  source hashes. Undisclosed IDs, invented wording, duplicates, corrupt/changed
  evidence and invalid tool sequences cannot produce an accepted report.
- The HTTP adapter fixes the HTTPS origin, disables redirects/proxy inheritance,
  bounds response size/time and rejects error bodies without exposing credentials
  or raw provider diagnostics. No provider retry or autonomous tool loop exists.

## Results

| Check | Result |
| --- | --- |
| Final S18 Python tests | **30 passed**, warnings treated as errors; `validation-evidence/s18-final-assistant-tests.xml` |
| Discovery/evidence/configuration/contracts and S18 integration subset | **161 passed**, with one registered-agent case separately deselected after retaining its failure; `validation-evidence/s18-rest-tests.xml` |
| Frontend unit/client/component suite | **386 passed**; also passed with one test-file worker |
| S18 real local API browser journeys | **3 passed**: reviewed draft with separate execution approval, disabled configuration, selected-evidence analysis |
| Browser accessibility | Full-page WCAG A/AA automated scans pass for desktop draft, 390-pixel mobile draft and recorded-evidence analysis, with no rule exclusions |
| Builds | Local and hosted production builds pass; the local bundle retains a >500 kB size warning |
| Generated contracts | Python schema drift and TypeScript drift checks pass |

The browser provider fixture replaces only the external OpenAI call. It exercises
real disclosure, journal, checked plan/report routes and the final plan-review
link. Its saved baseline is explicitly synthetic; no CARLA or trained model
execution is implied. Screenshots/traces/reports are under
`frontend/test-results/` and `frontend/playwright-report/`.

The initial accessibility scan found that the expanded disclosure's scrollable
`pre` lacked a keyboard focus target. It now has a named region and `tabIndex=0`;
the complete draft desktop/mobile and analysis journeys then passed.

## Retained limitations and failures

The broader `test_registered_agent_search_confirmation_and_suite_comparisons`
did **not** pass locally. The initial integration run recorded 48 passes and that
failure; a separate recheck also failed. The saved simulations identify
`AgentTimeoutError: Agent load exceeded 30 seconds`. The original receipts are
`s18-integration-tests.xml` and `s18-sdk-recheck.xml`; the SDK source and production
deadlines were not changed. These failures are not converted into positive
discovery evidence or hidden by the passing assistant tests.

The standard browser installation seed also hit that SDK load deadline before
serving the app. The explicit S18 fixture now seeds its own baseline evidence
instead of requiring an SDK installation benchmark. The normal browser fixture
and its SDK checks remain in place. The initial sandbox run was interrupted
without a complete result; the final Python checks ran outside that sandbox.

No OpenAI API key/model was configured locally. **No paid/live OpenAI request was
made.** Provider behavior is checked with controlled responses and HTTP transport
fixtures. Actual account/model compatibility, factual-quality evaluation beyond
the constrained verified-observation presentation, independent users/machines,
and hosted provider/account integration are not established by these results.
No public deployment, signed package update or GitHub publication was performed.
