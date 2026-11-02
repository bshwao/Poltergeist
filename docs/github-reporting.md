# S19 — GitHub regression checks

The local dashboard and CLI can attach an S15 regression verdict to an exact
candidate commit. Both recorded controller packages are verified against full
GitHub commit SHAs before the user reviews and approves an outgoing check.
Publishing is optional and disabled by default. No simulation is launched.

This is a private local implementation. Controlled GitHub transport, service and
browser fixtures validate the workflow; a live App installation publication and
repository branch-protection acceptance remain open. See the
[validation receipt](validation/s19-github-reporting-20261101.md).

## Configure one repository

Install a GitHub App on the selected repository with **Contents: read** and
**Checks: write**. Supply its installation access token through the local API's
environment or private `.env`, along with the App's numeric ID:

```dotenv
GITHUB_ENABLED=true
GITHUB_CHECKS_TOKEN=
GITHUB_REPOSITORY=owner/model-repository
GITHUB_APP_ID=12345
GITHUB_CHECK_NAME=Poltergeist regression
GITHUB_DAILY_PUBLISH_LIMIT=20
```

Set the token privately; never put it in a preview, suite, committed file or CI
artifact. The blank value above is deliberately unusable. Short-lived tokens
must be refreshed externally. Poltergeist does not mint tokens, request OAuth,
read GitHub CLI credentials or reuse `GH_TOKEN`/`GITHUB_TOKEN`. GitHub documents
the supported token types and permissions in its
[Checks API](https://docs.github.com/en/rest/checks/runs).

Only `https://api.github.com` is supported, with API version `2026-03-10`.
Enterprise origins and personal-access-token compatibility are not claimed.
Keep the local API bound to loopback. The `/github` routes also reject callers
whose peer address is not loopback; they are not a hosted authentication system
and should not be exposed through a reverse proxy. The hosted dashboard and
account/device relay have no GitHub publishing commands or token forwarding.

## Review and publish

1. Run a saved suite with at least two **registered, frozen controller versions**,
   reference first. Finish the suite, then open its saved results and select
   **Review version regression** with the desired measurement policy.
2. Open **GitHub reporting**. Select a candidate and enter the reference and
   candidate's full lowercase 40-character commit SHAs. Branches, tags and short
   SHAs are rejected. Both commits must be accessible in the configured repository.
3. Set each package's directory in that repository, or leave it empty for the
   repository root. Inspect **frozen local inputs** for the recorded package and
   the ordered external-model hashes/sizes. If there are external weights, supply
   one committed model/pin path per displayed external model as a JSON array.
4. Choose evidence categories deliberately; all start unchecked. Optionally add
   a shared HTTPS report link. Select **Verify commits and preview GitHub check**.
   This reads immutable Git objects and saves a local preview; it publishes nothing.
5. Inspect the exact outgoing JSON, repository, App, verdict, commits and local
   bound policy. Check the approval box and select **Publish approved GitHub check**.
   Approval binds the complete preview digest and expires after one hour.

Editing inputs or a new evidence/policy review clears approval. Publication
rebuilds the saved S15 report, rejects changed evidence and checks that the pinned
GitHub commit/tree identities still agree. It creates one completed check on the
**candidate SHA**, never on the current branch tip. The result is accepted only
when GitHub returns the expected App, commit, check name, external identity,
conclusion and output, with a same-repository HTTPS GitHub result link.

## What commit verification establishes

The verifier reads direct [Git trees](https://docs.github.com/en/rest/git/trees)
and blobs without checkout or code execution. The complete package inventory
must match the frozen SDK inventory, using the SDK's same excluded cache,
virtual-environment and version-control directories. Missing/extra files,
symlinks, submodules, truncated trees and unsupported paths are rejected.

Each blob's Git identity and size are verified, then its bytes are compared with
the recorded SHA-256 and size. Windows UTF-8 text may match the explicit
LF-to-CRLF transformation; its count is recorded separately. Large files can
instead match a committed Git LFS pointer carrying the exact frozen SHA-256 and
size. External model paths also accept a JSON object containing exactly:

```json
{"sha256":"<64 lowercase hexadecimal characters>","size_bytes":123456}
```

No weight is downloaded from LFS, uploaded or executed during verification.
Pins identify exact local model bytes, not their training origin or license.
Managed/generated wrappers require an equivalent committed wrapper package and
matching model pins; an unrelated upstream source commit cannot substitute for
the recorded package. Runtime dependencies, interpreter and effective controller
configuration remain identified by the frozen local suite, not certified as
contents of the selected Git commit. The check always states this limit.

Verification is bounded to 256 combined package/model inputs per controller,
20 directory levels, 1 MiB per fetched blob and 8 MiB total decoded blob bytes.
One remote session allows at most 128 requests, 2 MiB per JSON response, 16 MiB
total response bytes and a 60-second deadline checked between requests/chunks,
with 5-second connection and 10-second socket timeouts. Redirects, inherited
proxies and retries are disabled. A repository outside these limits needs a
smaller committed package/pin layout; it is not silently accepted.

## Disclosure choices

Every check shares the reference/candidate commit SHAs, policy-specific check
name, verdict, fixed scope/reproduction limits and an opaque deterministic
external identity. The external identity hashes the private source; it does not
contain readable suite or run IDs. The exact outgoing JSON is the authority for
what will be shared.

| Choice | Additional outgoing information |
| --- | --- |
| None (default) | Only the always-shared fields above |
| Metrics | Policy, selected candidate's per-case measurement counts, changes and uncertainty; case names excluded |
| Fingerprints | Report/plan hashes, the two controllers' fingerprints and commit proofs; controller names, versions and registration IDs excluded |
| Run identifiers | Suite and matched-run IDs, case/seed/repetition indices and pair state |
| Scenarios | Recipes, case/scenario names, map positions, seeds/repeats, execution and scoring settings |
| Optional report link | The explicitly entered HTTPS URL, without credentials, query, fragment or custom port |

Raw traces, recordings, local package/weight paths and controller configuration
values are excluded by the payload builder. Scenario metadata can contain user
text and private locations, so review it before selecting that category.
Common credential-like strings and the configured token are blocked, but this
is not a universal secret detector. Keep secrets out of experiment metadata.
The outgoing payload is capped at 60,000 bytes. The local publication record
also contains undisclosed source hashes and commit proofs; downloading that
record is a local export, not an instruction to upload it.

## Reliable CI meanings

Verdicts apply to the chosen candidate and the entire selected measurement
policy, using S15's measured coverage and matched-pair requirements:

| Recorded state | GitHub conclusion |
| --- | --- |
| Queued/running/pending | Publication refused; no completed check |
| Failed suite | `failure` |
| Cancelled suite | `cancelled` |
| Interrupted/other non-completed suite | `action_required` |
| Completed; any selected assessment observes regression | `failure` |
| Completed; every selected assessment observes all planned pairs, meets the minimum, and observes no regression | `success` |
| Completed; missing, insufficient or otherwise inconclusive evidence | `action_required` |

Incomplete evidence never produces `success` or `neutral`. A pass describes the
selected measurements and coverage, not safety, causality or unselected metrics.
The check name is `<GITHUB_CHECK_NAME> / <policy-hash-first-12> / c<candidate-index>`.
Changing thresholds/minimum pairs or the candidate changes the context. Configure
required checks for the **exact policy/candidate context** and expected App; do
not treat an arbitrary Poltergeist-prefixed success as satisfying a fixed policy.
Branch-protection setup is performed separately and has not been live-validated.

## Lost replies, restarts and concurrent publication

`STORAGE_DIR/github-publications.sqlite3` stores previews, private report context,
approval reservations and outcomes independently of the main simulation schema.
The reservation commits before a remote write. Concurrent/repeated publication
of that record cannot send another check; identical checks share a unique local
publication key. The configured UTC daily limit counts reserved attempts,
including failures and recovered publications. The journal retains at most
10,000 records; the UI/API history returns the latest 30.

If a reply is lost, a process stops or the remote result differs, refresh saved
history and use **Reconcile GitHub check (read only)**. It searches by exact
commit/name/external identity/App (at most three pages of 100), or verifies the
saved check ID. It never creates or overwrites a check. Missing, ambiguous,
changed or unconfirmed results stay explicit; no automatic write retry occurs.
Definite failures can be corrected and reviewed as a new preview.

The external identity is deterministic across journal restoration, so a later
publisher can adopt an already-existing *identical* check after verifying it.
GitHub's create-check API has no atomic idempotency key. Two independent journals
can race between lookup and create: use **one publisher with a shared durable
journal**, or a CI concurrency group scoped to the repository/candidate/policy.
Do not claim global exactly-once writes across independent publishers.

## Local API and CLI / CI handoff

The generated `GitHubPreviewRequest` contract accepts `operation_id`,
`candidate_index`, the S15 `policy`, `reference` and `candidate` bindings
(`commit`, `package_prefix`, ordered `model_paths`), `disclose`, optional
`details_url` and optional UUID `request_id`. Keep the saved database, artifact
directory and separate publication journal available to the publishing process.

```powershell
python -m scripts.publish_github_check status
python -m scripts.publish_github_check preview request.json
python -m scripts.publish_github_check get <preview-uuid>
# After reviewing the displayed exact payload and digest:
python -m scripts.publish_github_check publish <preview-uuid> --approve-digest <64-character-digest> --approve
python -m scripts.publish_github_check reconcile <preview-uuid>
python -m scripts.publish_github_check history
```

Use the project environment's Python. Preview makes read-only GitHub calls;
publish requires both flags and the saved preview. The command prints a typed
publication JSON record. Exit 1 means failed, uncertain or still publishing;
exit 0 means the command completed, **not** that the regression verdict passed.
CI must inspect `state == "published"` and `preview.conclusion == "success"`
when it needs a passing measured result. The GitHub check itself carries the
mapped CI conclusion. `get`, `history` and `status` do not contact GitHub.

For CI, prepare a terminal suite and preview using trusted publishing code,
retain the private workspace/journal, then use a protected approval stage to
publish that exact digest. Mint the App installation token in that trusted stage
with repository-scoped Contents read / Checks write permissions. Serialize that
stage using CI concurrency and keep the journal durable. Never execute untrusted
fork/controller code in a process that holds the publishing token. The repository's
existing validation workflow is unchanged; there is no automatic publishing hook.

The equivalent loopback routes are `GET /github/status`,
`GET /github/inputs/{suite-run-id}`, `POST /github/previews`,
`GET /github/publications`, `GET /github/publications/{id}`,
`POST /github/publications/{id}/publish` with `{ "digest": "...", "approve": true }`,
and `POST /github/publications/{id}/reconcile`. Responses use `Cache-Control:
no-store`. Input hints expose local frozen paths only to the local dashboard;
they are not added to the GitHub check.
