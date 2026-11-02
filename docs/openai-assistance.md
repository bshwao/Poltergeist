# S18 — Optional OpenAI experiment assistance

The local dashboard offers **Optional OpenAI assistance** in Discovery and in a
completed Evidence Analyst review. Opening the panel reads local configuration;
it sends nothing to OpenAI. The deterministic workflows continue to work with
this feature disabled, without an API key or additional Python dependencies.

## Configure explicitly

Set these on the trusted local API process, using its environment or untracked
root `.env`, then restart that process:

```dotenv
OPENAI_ENABLED=true
OPENAI_API_KEY=your-project-api-key
OPENAI_MODEL=your-available-responses-model
OPENAI_DAILY_REQUEST_LIMIT=10
OPENAI_DAILY_OUTPUT_TOKEN_LIMIT=8192
```

Choose an available model that supports Responses API strict function tools.
There is deliberately no default model or implicit account activation. The key
is a masked server setting. It is never returned to the dashboard, stored in an
assistant record or read from a browser/CLI login cache. Requests go only to
`https://api.openai.com/v1/responses`; ambient proxy settings, base-URL overrides
and redirects are not used. No provider SDK or automatic retry is installed.

This delivery supports the existing **trusted local dashboard/API**. The
assistant routes require a loopback client address and reject LAN/remote callers,
even when their Host header says localhost. Loopback reverse proxies belong to
that trusted local boundary; no account authentication is added to that boundary.
The Companion's isolated runtime does not inherit the developer `.env`, and the
hosted account/device relay exposes no assistant or external-disclosure actions.
Its existing device scopes do not authorize exporting evidence to a new provider.
This implementation does not add a hosted API key vault, new remote permissions
or a companion account-configuration panel.

## Plan from a question

1. In Discovery, choose the execution mode, registered agent, seed, duration,
   scoring/settings, parameter space and experiment budget as usual.
2. Open language assistance, enter a question and choose a 128–2048 output-token
   limit. **Preview OpenAI disclosure** creates a local record without a provider
   call. Inspect its exact JSON payload and limits.
3. Explicitly approve that disclosure, then send it. One function response can
   select a supported scenario family and observed failure policy, or decline.
4. The application validates and freezes a normal discovery plan. The agent,
   mode, seed, duration, scoring/settings and execution/time budgets remain the
   ones supplied locally. A changed family uses its default parameter space;
   ranges from a different family are not transferred silently.
5. Open the drafted plan, inspect its complete recipes and limitations, and use
   the existing **separate execution approval** if you want to run it.

No model tool can approve a plan, start CARLA, launch a simulation, select an
arbitrary agent, alter execution budgets, access the filesystem or execute code.
Invalid arguments, unsupported tools, multiple calls, non-finite/duplicate JSON,
truncated responses and unsupported output sequences produce a failed record.

## Ask about selected recorded evidence

Open a saved run's deterministic Evidence Analyst review, then its language
assistance panel. Choose 1–16 observations and ask a question. Nothing is selected
by default. The preview discloses only the selected statements, values, units,
coverage and frame references, the recorded coverage context and your question.
It excludes raw telemetry, images, agent/configuration records, model files,
run UUIDs, filesystem paths and unselected observations from those records.
Planning discloses only the question and current family/failure selectors.

Common token, email, URL and path patterns in strings are redacted. This is a
limited aid, not a comprehensive sensitive-data detector. Inspect the exact
preview before approving it; it includes all provider JSON except Authorization.

The language model selects relevant disclosed observation IDs. The application
independently validates that selection and constructs the report using the
**original verified wording, values and native frame/time references**. Arbitrary
model-written factual claims are not accepted. Full recorded coverage, unknowns,
limitations, synthetic/CARLA mode and source/telemetry fingerprints remain in the
local report. Unselected observations are not cleared. Evidence is checked again
before and after the provider call; changed or corrupt source evidence prevents
publication. Current-run control-audit references retain the existing separate
control/decision viewer through the Evidence Analyst's inspection callback.

This is question-guided selection and presentation of verified observations,
rather than an unrestricted chat or causal explanation of model intent.

## Usage, interrupted requests and history

Each disclosure permits **one** provider call, up to **32 KiB** of complete input
JSON (the UI uses **16 KiB**) and **128–2048 output tokens** (UI default **1024**).
The configured daily request/output-token limits apply across callers and API
restarts sharing the same storage directory. A short SQLite transaction reserves
one call and the full output limit before network access. Failed, declined and
uncertain requests retain that reservation. Reported token usage remains unknown
when unavailable; it is not replaced with zero.

These are call, input-byte and output-token limits, **not a currency cap**. Input
tokens may also be billed; model pricing and provider account limits apply.
Days use UTC. A new storage directory has a separate budget journal.

The opt-in journal is `<STORAGE_DIR>/openai-assistant.sqlite3`, separate from the
main application schema. It retains disclosure/context snapshots, outcomes and
reservations, with at most 10,000 records and the latest 30 exposed in history.
No main-schema migration or packaged release compatibility claim is needed.
The user-controlled local journal is not a billing authority or a sandbox against
other code running with the same filesystem permissions.

Approve the preview's SHA-256 digest, which binds its provider JSON including
model and output limit. Previews expire after an hour. A model change, expired
preview or changed digest requires new review. Concurrent sends and retries of
the same request return its saved state; they do not call the provider again.
**Refresh assistant history**, reopen the request or download its JSON record.
If a process exits after reserving/sending, the record remains unconfirmed and
is never resumed automatically. A saved plan uses that same request UUID and can
also be found in normal Discovery history if final receipt storage was interrupted.

The adapter bounds response size to **256 KiB**, checks a **45-second** read
deadline, and applies **5-second connect / 10-second socket** timeouts. DNS/OS
behavior is not an unconditional shutdown guarantee. The browser stops waiting
after 65 seconds and directs the user to saved history. `store: false` is sent;
this does not make a separate zero-retention claim about provider policies.

## Local API

| Route | Purpose |
| --- | --- |
| `GET /assistant/status` | Configuration/scope/limits; no storage or provider side effects |
| `POST /assistant/previews` | Validate local inputs and save an exact disclosure; no provider call |
| `POST /assistant/requests/{id}/send` | Require `{digest, approve: true}` and reserve/send once |
| `GET /assistant/requests/{id}` | Read a durable outcome without provider traffic |
| `GET /assistant/requests` | Read the latest 30 local records without provider traffic |

Responses use `Cache-Control: no-store`. Invalid inputs return 422; disabled
configuration, changed/expired disclosures or exhausted budgets return 409.
Provider/output failures are retained as failed request records with safe
messages, without raw provider bodies, tokens or signed URLs in errors/logs.

## Implementation and validation

The provider uses the official
[Responses API](https://developers.openai.com/api/reference/python/resources/responses/methods/create)
and [strict function-calling contract](https://developers.openai.com/api/docs/guides/function-calling).
Only `draft_discovery` / `report_evidence` plus `decline_request` are supplied,
with `strict: true`, `parallel_tool_calls: false` and required tool choice.
One response is processed locally; there is no autonomous model/tool loop.

Tests use a controlled provider response or HTTP transport, without external
network access, paid calls or real credentials. The explicit browser fixture
uses real preview/journal/checked-plan routes while replacing only the provider:

```powershell
$env:POLTERGEIST_ASSISTANT_FIXTURE = '1'
$env:POLTERGEIST_TEST_PYTHON = (Resolve-Path ../.venv/Scripts/python.exe).Path
npx playwright test e2e/openai-assistant.spec.ts
```

Actual account/model acceptance is separate and must use a deliberately
configured model, reviewed disclosure and explicit usage budget. No live
account acceptance or general assistant factual-quality certification is claimed
by mocked tests. See the [S18 validation receipt](validation/s18-openai-assistance-20261101.md).
