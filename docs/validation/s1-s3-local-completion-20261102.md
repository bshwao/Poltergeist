# S1–S3 local completion — November 2, 2026

Scope: current `publicity` checkout based on `b9dcadb`, with the accompanying
uncommitted S1–S3 changes. The owner requested acceptance on this PC only. This
record covers private packaging, Companion execution and installer/onboarding;
the independent-PC and external-tester completion gates remain open.

## Changes

The first live experiment now has a **CARLA installation check** preset. It uses
the map selected in Setup and the scenario framework's relative road placement,
rather than reusing the synthetic check's fixed geometry. Loading selects the
baseline controller and CARLA mode without submitting work. The user reviews and
explicitly runs the two-second recipe. A later map change does not mutate an
already loaded recipe; reload it to use the new map. Submitting the original
synthetic installation recipe in CARLA mode produces an actionable message.

A fresh install → repair → uninstall check reproduced a recovery ownership bug.
The original conditional `[UninstallDelete]` entry was stored during the first
install, when no recovery directory existed. A subsequent repair retained the
old runtime, but uninstall still deleted `installation.ini`. This left a nonempty
folder that the installer correctly refused to adopt.

The marker decision now runs during uninstall. Recovery copies retain their
ownership marker; without recovery, the owned marker is removed. For upgrades
whose older uninstall logs still contain the deletion entry, the current
uninstaller captures and restores the exact owned marker contents. The existing
nonempty-folder and link protections remain in place. The October 29 receipt is
retained, but its claim that a conditional entry fully resolved this case is
superseded by this reproduction and fix.

## Local evidence

| Check | Result |
| --- | --- |
| Focused source regression | **86 passed**: packaging, installer builder, onboarding, Companion/controller, native GUI callbacks and ownership-aware profile removal |
| Offline reproducibility | Two fresh portable builds have identical archive bytes and SHA-256 |
| Relocation | Passed outside the checkout in a path containing spaces, with only Windows system tools on PATH and hostile Python environment variables |
| Private runtime | Bundled Python 3.12.15, native CARLA 0.9.16/Tk imports, real SDK subprocess, synthetic sensor baseline/starter, exports and persistence checks pass |
| Installed Companion callbacks | Synthetic baseline and SDK starter complete; report export/import, diagnostics, shutdown and reopening pass using the installed interpreter |
| Installed live baseline | CARLA 0.9.16, Town10HD_Opt, Low graphics: **2.00000003 simulated seconds, 41 frames**, completed baseline, JSON/HTML exports, one saved completed run after reopening |
| Simulator cleanup | Map, settings and weather match the pre-run state; no owned actors remain; the controller releases its profile |
| Bundle integrity | All **6,329** manifest files remain unchanged through installed execution |
| Native installer lifecycle | **Eight steps pass**: old install, corrected upgrade/repair, uninstall with the older deletion log, reinstall, maintenance repair, uninstall, fresh corrected install and fresh uninstall |
| Data preservation | All **25** test-profile files remain byte-identical, including a copy of the completed real CARLA run and its exports; recovery sentinel retained and registration/shortcuts removed |
| Fresh uninstall | Without recovery copies, the application folder and its owned marker are removed |

The machine is Windows 11 build 26300, AMD64, 12 logical CPUs, approximately
63.8 GiB RAM and an RTX 2060 alongside Intel UHD graphics. These observations do
not establish a supported hardware matrix or performance qualification. CARLA
was started only for explicit testing, using Low graphics and its existing local
installation; it is not included in either delivery artifact.
After validation, the idle task-owned CARLA processes were identified and stopped.
The disposable validation installations were uninstalled; test profiles and
recovery copies remain for review.

Native installer lifecycle results and exact artifact identities are recorded
in the accompanying [machine-readable receipt](s1-s3-local-completion-20261102.json).
The normal private delivery installer and the isolated validation installer use
the same verified bundle; validation has a distinct application identity and
compiled test profile. The normal installer is built, not installed over the
owner's normal profile.

## Reproducible inputs and artifacts

| Artifact | SHA-256 |
| --- | --- |
| Portable ZIP, 87,970,150 bytes | `3dc3f1b50c17b093a89c5c74b3eb8be74958d27f48f772786b92024e341452ef` |
| Normal private installer, 58,971,171 bytes | `4e0143ac04b378f8bcad862a87120c46de7100fa1f7039e8120da3ff62482b42` |
| Isolated validation installer, 58,971,232 bytes | `b3d65d7787ce3802fb6825584a9044d49e29fc059015f3107d774a3025ed66ea` |

Bundle manifest SHA-256:
`1172c345b22091f94f02ffb129918ecef739302286bc2e1b4e720317cac25794`;
runtime build ID: `private-1172c345b22091f9`.
The builder used Python 3.12.10 and the pinned Inno Setup 7.1.0 compiler. The
portable runtime is Python 3.12.15. All delivery artifacts are **unsigned**.
Checksums identify these private test artifacts; they do not authenticate a
publisher.

Raw local logs, XML and evidence are in ignored
`validation-evidence/s1-s3-20261102/` and its sibling XML files. Relocated bundles,
installers, native setup logs, recovery copies and disposable profiles are in
the owner's separate `PoltergeistQA/S1S3-20261102` directory. No normal user
profile, repository database or historical human acceptance verdict was replaced.

## Retained failures and limits

- The first offline build lacked the pinned sniffio wheel. Fetching and verifying
  that input enabled subsequent offline delivery/reproducibility builds.
- An initial focused suite had 85 passes and one native keyboard test failure.
  The test sent a key before its queued native focus event settled. The final
  test waits for focus, verifies the focused widget and queues the key, retaining
  all scrolling assertions and both tested window sizes. The final suite has
  86 passes; the failed XML is retained.
- The first live evidence helper used nonexistent serialization/result fields
  after the real run had already completed. Corrected helpers reopened the same
  recorded run and verified its existing result and exports. No second run or
  synthetic replacement was used to claim success. All helper attempts remain.
- The original native uninstall failed the recovery-marker assertion while
  preserving all 19 original profile files. The failed install folder and logs
  remain; the corrected candidate is checked in separate, fresh QA folders.
- Native Computer Use initially refused access to `pythonw`. After the owner
  granted app access and restarted ChatGPT, the interactive check below ran on
  the same PC.

Independent Windows-without-developer-tools acceptance, the supported native
matrix, redistribution review,
signing and external beta acceptance remain open. The owner's PC-only scope
defers the independent-machine test; it does not turn local isolation into that
test. Public hosting and live OpenAI/GitHub acceptance belong to later phases.

## Interactive Companion check on this PC

Computer Use operated the packaged portable Companion in a fresh isolated
profile on November 2. Setup displayed the detected CARLA installation,
hardware details and Low graphics. **Use synthetic for now** saved setup.
After starting the runtime, the synthetic installation check reached 100%
and exported a JSON report.

The Setup **Start / reuse CARLA** action launched CARLA 0.9.16 at port 2000
with Low graphics. Setup reported the active map as `Town10HD_Opt`. Loading
**CARLA installation check** displayed CARLA mode, the selected map, the
baseline controller and a two-second recipe. The queue stayed empty until
the explicit **Run recipe** action. The run reached 100% and exported JSON
and HTML. CARLA returned to asynchronous mode with no test actors remaining.

The window closed cleanly. Reopening the same profile displayed both completed
runs with their original modes. Both initial pointer attempts on **Run recipe**
focused the button without a visible submission, and pressing Space submitted
each run. A subsequent pointer click after reopening submitted an additional
synthetic check successfully. During the first manual runtime start, the
header still read “stopped” while integrity verification disabled the controls.

The JSON receipt lists run IDs and export hashes. This review used the packaged
runtime with an explicit test profile. The earlier installer and shortcut
receipts cover the installed entry point. The
[historical human report](../HUMAN_TEST_REPORT.md) retains its original verdict.
