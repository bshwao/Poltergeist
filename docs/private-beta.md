# Private beta acceptance record

Status: signed beta distribution pending. Updated November 4, 2026.

The owner has no signing identity yet. The current acceptance pass uses the
owner's Windows PC. Signed install, upgrade, compatible rollback and public feed
activation are pending. Broader beta enrollment and hardware qualification
are separate from this pass. Record public certificate/service configuration
and test-PC profiles when supplied. Keep keys, passwords, pairing secrets,
account credentials and unselected private recordings out of this file and the
repository.

| Check on this PC | Current evidence |
| --- | --- |
| Per-user install, repair and reinstall | The [unsigned native lifecycle check](validation/s10-native-lifecycle-20261029.json) passed, including reinstall after repair retained recovery copies. |
| Packaged runtime and CARLA setup | The [S1–S3 check](validation/s1-s3-local-completion-20261102.md) verified the relocated private runtime and CARLA setup. |
| Pairing, execution and evidence | The [public pairing check](validation/s5-vps-deployment-20261103.md) and [recording review](validation/s8-public-wan-20261104.md) passed on this PC. |
| Signed install, upgrade and compatible rollback | Awaiting a publisher signing identity. |

Windows 10/11 versions, GPU/RAM, driver versions, storage and graphics settings
must be recorded exactly. Development-laptop proofs do not establish support
for an untested OS or hardware profile. Publish the support matrix only after
the relevant rows have evidence.

For each signed validation, retain the candidate installer hash/version, metadata hash,
signature/publisher/timestamp verification, PC/CARLA versions and a dated result
for each step. Each step is pass, fail, unavailable or not tested; never infer
success from installer launch or from a missing recording.

1. Verify the publisher through Windows and the independently supplied public
   fingerprint. Install under an ordinary per-user path using the bundled runtime.
2. Discover/configure CARLA with Low graphics, perform compatibility diagnostics,
   run synthetic baseline and a short CARLA baseline, then reopen their results.
3. Prepare the managed CPU reference agent through the app and execute actual
   inference; preserve exact model/environment identities and license notices.
4. Pair the reviewed account, approve only chosen permissions and explicitly
   enable remote requests. Launch/cancel/read a run through the intended HTTPS
   website. [Public pairing and live execution](validation/s5-vps-deployment-20261103.md)
   and [public recording review](validation/s8-public-wan-20261104.md) passed on
   the owner's PC.
5. Check another account cannot enumerate results, submit work or read evidence.
   Revoke the pairing and confirm the former account loses access.
6. Review telemetry and saved camera/LiDAR samples on request, cancel a chunk
   transfer, disconnect/reconnect and
   check unavailable evidence remains unknown. Export local recordings and test
   storage exhaustion and explicit evidence removal on disposable experiments.
7. Review a staged release in Updates, download explicitly, cancel/retry, then
   update from the first signed baseline while idle. Verify that open/queued
   runs prevent the update, the backup is valid and results/model identities
   survive. Inject a failed candidate and retain its failure/rollback receipts.
8. Roll back to a signed schema-compatible release without losing new results.
   Confirm an incompatible schema is refused. Repair, uninstall while preserving
   data and reinstall; verify the retained results again.
9. Supply only selected diagnostic reports/recordings using an agreed private
   channel. No automatic cloud sharing, invitations or upload is enabled.

Do not expand the beta until critical install, ownership, duplicate-execution,
data-loss, evidence-integrity and update failures are resolved and rerun. Agree
the initial device/transfer targets using measured hosted service latency,
storage and bandwidth costs. Preserve failed receipts beside successful reruns.
