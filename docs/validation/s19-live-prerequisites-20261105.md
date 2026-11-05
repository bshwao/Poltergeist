# S19 live publication prerequisites — November 5

The real trained-model regression suite is now available:
`97d512f2-8505-4e9c-a7f0-190c33bc3ff2`. Its native evidence and independent
report audit are documented in the [S15 receipt](s15-trained-models-20261105.json).
The selected six-measurement policy observes clear-case braking/clearance
worsening. A live Check should reflect that recorded verdict, rather than
publish a green result because the engineering tests passed.

The [frozen package bindings](s15-frozen-packages-20261105/bindings.json) prepare
equivalent committed SDK wrappers and sixteen ordered external-model pins.
Package files retain the exact registered bytes (including this PC's checkpoint
path); pins contain only SHA-256 and byte length. No weights or environment
contents are included. Later GitHub preview verification must still validate
the actual immutable GitHub trees/blobs and the selected commits. Preparing
these files is not an App publication result.

Read-only inspection with the existing Git sign-in found:

| Check | Observed result | Meaning |
| --- | --- | --- |
| Repository | `bshwao/Poltergeist`, default branch `main` | Intended repository confirmed |
| Available credential permissions | Push true; admin/maintain false | This sign-in can push validated commits; it cannot establish administrator-only branch configuration |
| Main protection endpoint | HTTP 404, `Not Found` | This response does not prove absence of protection when administrator access is unavailable |
| Effective branch rules endpoint | HTTP 200, empty list | No effective rules were returned to this credential; required-check acceptance remains unverified |
| Poltergeist App configuration | Disabled; no App ID or Checks token configured | A real App installation is still needed |

No check was published and no branch rule was changed. Existing Git credentials
were used only for repository/CI reads and normal authorized pushes; they are
not substituted for the S19 App installation credential.

Remaining live work needs the App's public ID, a private installation access
token with the documented repository permissions, and a repository administrator
for the required-check test. Keep tokens/private keys outside chat and Git.
After actual configuration, review a fresh exact check disclosure and immutable
commit bindings, publish through the App, verify GitHub's returned App/commit/
conclusion, and observe the required context's allowed/blocked branch behavior.
These are still acceptance gates.
