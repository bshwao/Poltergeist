# S11 — Visual design and usability

Started October 29, 2026 from GitHub `main` at
`7c92e828eb4d06bac86ace3cefb0bee53554960b` on
`codex/s11-visual-usability`. S11 is in progress; this first checkpoint does not
close the roadmap's first-run or supported-display acceptance gate.

## S11.1 — Shared design and accessible workspace foundation

The local and connected dashboards share a light forest-green palette, system
typography, focus color and surface tokens in `frontend/src/design-tokens.css`.
Small explanatory text is at least 12 CSS pixels; the compact local brand caption
is 10 pixels. Existing recorded measurements, units and unknown values retain
their meaning. The companion uses matching Segoe UI typography with native
Windows control colors and display scaling. It retains the system theme rather
than overriding Windows contrast settings. Ctrl+Tab and Ctrl+Shift+Tab traverse
the companion's tabs.
Local execution also has a scroll surface: Tab reveals offscreen controls, the
run list retains native scrolling, and the surface supports the wheel, Page
Up/Down and Home/End. Native regression checks exercise execution and export
actions at 960 × 800 and 700 × 600.
The status footer reserves space before the main notebook; tall tabs cannot
push recovery messages out of view. The full profile path is in Diagnostics.

The expanded S12/S13 browser navigation also needs a bounded vertical scroll
surface. At short desktop heights, the fixed sidebar previously let its pale
note and footer extend past the dark background; the discovery-report CI audit
found insufficient contrast. The sidebar now scrolls independently. A 1280 × 600
regression scrolls to the footer, focuses navigation back into view and audits
the complete page at both positions. The existing discovery-report audit is
retained without exclusions.

Both browser workspaces provide a visible-on-focus Skip to content link. Local
API failures have a recovery message and Retry connection action. The connected
workspace distinguishes loading computers, an empty paired-device list and
unknown availability after a request fails. Failed refreshes do not display a
zero-computer count or enable new experiments. The telemetry table has its own
named keyboard focus target so phone users can scroll recorded columns with the
arrow keys. Shared styles preserve reduced motion and browser forced colors.

Validation uses the existing synthetic workspace and private HTTPS/WSS fixture,
with pinned development-only `@axe-core/playwright` checks. The checks inspect
the complete page and use no violation exclusions. Their WCAG A/AA rule tags
are an automated subset, not a certification or a replacement for human review.
Screenshots and detailed accessibility reports remain in the browser artifacts.
The earlier missing-browser prerequisite, contrast failures and keyboard-scroll
failure remain in private validation logs beside the corrected results.
The first Windows visual smoke review covered Setup and Local execution at
960 × 800, including Ctrl+Tab after focusing the notebook. It found and corrected
the clipped status footer. Automated minimum-window checks cover the footer and
update actions at 700 × 600; the complete native visual matrix is still open.

The Windows checkpoint passed 340 frontend unit tests, both production builds,
the generated-contract check, all 19 local browser tests, both hosted browser
tests and all 22 native companion cases. The first GitHub run passed the twelve
local page accessibility scans but exposed a race in the new keyboard recovery
test: it could try to focus Retry before template loading enabled the button.
The corrected test deliberately holds that initial request, verifies the disabled
state, releases it, then waits for enabled and confirmed keyboard focus before
pressing Enter. Its focused rerun passes; subsequent GitHub status is tracked
per revision. No rule or assertion was disabled.

Run the checks from `frontend`:

```powershell
npm test
npm run contracts:check
npm run build
npm run build:hosted
npx playwright install chromium
npm run test:e2e
npm run test:e2e:hosted
```

The browser fixtures use disposable accounts/data and synthetic execution. They
do not start CARLA, deploy a public site or validate a signed installer. Native
companion callback regression checks are in `tests/test_companion_ui.py`.

## S11.2 — Principal workflow visual review

The [November 2 repository polish](validation/repository-polish-20261102.md) adds
deferred page loading with an accessible failure/reload state, digest-bound
assistant approval, cancellation recovery and consistent keyboard outlines.
The full local/hosted browser journeys pass, including an explicit forced-color
editor-focus check and mobile failed-page review. These browser improvements do
not close the native visual or independent new-tester acceptance gates.

The [S16/S17 completion review](validation/s16-s17-completion-review-20261101.md)
adds grouped AI model controls, explicit preparation guidance, keyboard focus
scrolling and accurate environment-removal states. Native callback checks cover
both supported window sizes. The user stopped the computer-use review before
usable panel captures were established; that attempt does not close visual
acceptance. The new focus test exposed ancestor notifications scrolling a
container over its focused control. The shared scroll handler now ignores those
notifications; model and existing Local execution keyboard checks pass.

Continue with populated scenario editing, model preparation, experiment progress,
failure investigation and recording review. Review loading, empty, offline,
error and recovery states for each workflow. Exercise keyboard-only entry,
validation errors, dialogs, charts and long result tables. Retain exact recorded
frame navigation and unknown measurement coverage.

The first browser coverage was Chromium at 1440- and 390-pixel widths. The
November 4 checks below add narrow phones, tablet widths, Firefox, dark
appearance and Chromium browser zoom. Screen-reader review remains open. Native visual
inspection of all Companion tabs at supported display scales and Windows
contrast settings remains open. Callback tests establish control reachability.

## S11.3 — First-run acceptance

Observe a new tester on this PC installing or opening the Companion, finding
setup problems, pairing the Companion, launching a run and explaining the result
without help. Record the candidate version, Windows display scale, viewport,
Windows and CARLA versions, and the step where the tester hesitates or needs
help. Resolve observed issues and repeat the affected workflow before closing
S11. The [S12 test session](guided-experimentation.md#acceptance-record) covers
the dashboard portion of the review.

## November 4 checks on this PC

The Connection tab now scrolls focused controls into view, including remote
consent at the 700 × 600 minimum size. A native regression reproduced focus
landing below the visible panel before the fix. All 29 native callback cases
passed after the scroll fix. A follow-up verified four targeted cases after
adding compact labels and a measured minimum width for large text. At test font
scales equivalent to 150% and 200%, the labels fit at their measured minimums.
In the Guided lab, keyboard focus follows the gallery and saved-suite shortcuts
and moves to the run heading after choosing a scene. The progress indicator
marks evidence review as the current step when results arrive.

An unsigned validation installer installed into an isolated profile and its
bundled Windows runtime probe passed. In the installed window, synthetic setup
saved and the bundled installation check completed. The wait while starting
revealed a missing integrity-check status message. The source now shows that
message during manual startup, with three focused native checks passing after
the change. The installer used for the visual check predates that message fix.

Chromium and Firefox each passed 39 browser checks, including axe audits and
no page-level horizontal overflow for all eight local routes at 320, 390, 768
and 1,440 pixels.
The Windows automation tree for the native Updates screen exposed unlabeled
panes for the Tk controls. Keyboard reachability and browser axe results do
not establish native screen-reader usability. Visual review, theme coverage
and first-run observations remain open.

A second pass reproduced clipped update and setup actions at 200% Tk text
scaling. Setup, Settings, Diagnostics, Imported Reports and Updates now use
the existing keyboard-aware scroll surface. The narrow Updates layout also
keeps the full rollback and cache action labels. The complete native callback
suite passed all 32 cases, including the default minimum window size and the
scaled focus checks. The rebuilt portable runtime passed its bundled Windows
probe, and the unsigned validation installer compiled successfully.

The telemetry frame slider now exposes the recorded frame number and time as
its accessible value. In forced colors, a text underline marks the selected
dashboard page and lab tab. Gallery filters announce the result count, and
each add-to-suite button names its scenario. Focused browser checks and the
rebuilt connected lab at 320 pixels passed. These checks still leave native
screen-reader review open.

Both browser dashboards now follow the browser's dark appearance preference.
The shared palette covers forms, run states, evidence panels and telemetry
plots. Windows forced colors keep their separate system palette and selected
navigation cues. The telemetry plots explicitly use system colors in that mode
because embedded SVGs can preserve their own paints. [CSS Color Adjustment](https://drafts.csswg.org/css-color-adjust/#forced-color-adjust-prop)
The Companion continues to use its Windows theme.
Chromium passed 52 local accessibility, guided lab and workspace checks.
Firefox passed 10 dark and forced-colors checks. All three connected-dashboard
checks, both production browser builds and all 397 frontend unit tests passed.

Chromium's native tab zoom reduced a 1,280 CSS pixel viewport to 640 at 200%
and 320 at 400%. All eight local routes fit at both settings. At 400%, the
guided result, telemetry inspector and populated suite comparison fit without
page-level horizontal scrolling. The connected fixture also passed sign-in,
pairing, a guided run and exact frame review at 400%. The measurements use the
document's visible width and scroll width, including after the data loads.
[Chromium tab zoom](https://developer.chrome.com/docs/extensions/reference/api/tabs#method-setZoom) and
[W3C reflow guidance](https://www.w3.org/WAI/WCAG22/Understanding/reflow.html)

The regular browser suites also exercise populated screens at 320 CSS pixels.
The local comparison test moves the table horizontally with the keyboard while
the surrounding page stays within the viewport. The paired fixture runs its
accessibility audit after loading the selected frame at the same width.

The Companion's recent-run table now sizes its rows from the active font. At
200% Tk text scaling on this PC, the old 28-pixel row was shorter than the
37-pixel line height. The new 45-pixel row leaves room for the full line and
padding. The Mode column similarly had 70 pixels for the 108-pixel word
“Synthetic.” The table now measures headings and the supported mode, status and
progress values. The populated regression checks the rendered cell bounds at
the minimum window size.

Windows rendered all eight tabs on an isolated desktop with the native Vista
theme. The capture matrix used 100% text at 700 × 600 and 960 × 800, 150% text
at 718 × 600, and 200% text at 891 × 600 and 960 × 800. The narrow widths at
150% and 200% are the Companion's measured minimums at those text sizes. The
review included both Setup pages, the pairing consent controls, and a completed
synthetic run in the history table. The interactive desktop stayed available.

At 200% and the minimum size, the fixed header left about 110 pixels of tab
content visible. Removing repeated header copy increased it to about 250
pixels. Start and Stop now reflect the current runtime state. When a table or
text pane is taller than the viewport, keyboard focus brings its top into view.
The preset and pairing forms now keep their actions full width, including
the recording permission and the saved-approval recovery action. The follow-up
captures and native geometry checks verified those fixes. All 34 native
callback cases and 12 signed-update unit checks passed in the same run.

The review covers the normal Windows theme at the listed Tk text scales.
Windows contrast themes, native screen-reader interaction and a first-time
user session remain open.

The revised Companion compiled into an unsigned validation installer with
its own test profile. The corresponding portable bundle passed the bundled
Windows dependency check and completed a synthetic experiment.

An isolated compatibility probe loaded Tcl/Tk 9.1.0 with the bundled Python
3.12.15. After enumerating child controls, both Tk's accessibility property and
Windows MSAA returned a dummy value from an upstream masked entry. The guarded
test build omitted masked values, reported the Windows protected state, and
preserved ordinary field values. It passed after mapping, focusing, selecting
text and exercising the key handlers. It also passed in the Companion's own
Hugging Face token dialog with a dummy value and cancellation. [Tk 9.1 accessibility bindings](https://github.com/tcltk/tk/blob/core-9-1-0/library/accessibility.tcl)

Tk 9.1's Windows accessibility mode can use classic checkboxes in place of
themed ones. The Companion's pairing panel now sets a state option both support.
The regression reproduced the previous startup error and checked that permissions
and remote consent still follow the pairing state. All 35 native callback cases
and 12 update unit cases passed with Tcl finalization warnings treated as errors.

The experimental provider initially omitted nested controls. With a recursive
enumeration fix, MSAA found the sampled Setup, Run, Diagnostics, Connection and
Updates actions, and omitted the inactive Run tab's action. The Companion opened
and completed a synthetic run in that fixture. The ordinary Companion build
uses Tk 8.6. Full screen-reader interaction, Windows contrast-theme review and
first-time user acceptance remain open.

A two-window MSAA fixture found that an activation could reach the other window
when both used the same child ID. The experimental provider now scopes requests
to the originating window and rechecks a queued action's target before running
it. Both fixture actions reached their intended window, and a queued action was
skipped when its target closed. A separate test hook exercised the same request
queue on a worker thread. Twelve unstarted requests timed out without executing
after the UI resumed. An already-started request returned after its result was
ready. The process handle count did not grow once the test released its own
thread objects.

The Tk 9.1 fixture now has a separate validation package. Two derivations from
the same hash-verified base and compiled toolkit files produced byte-identical
archives with SHA-256
`ff7616d6aa4b73c0538e6c2211b8870a228846dba65a640549cfe407d74970ab`.
The archived notices retain the toolkit patch, input hashes and licenses.
Both consumers use the base bundle's pinned zlib. The packaged Python and Tcl
each completed a compression round trip, and the native dependency probe passed.

Using the normal portable launcher on a private Windows desktop, the fixture
activated **Use synthetic for now**, **Start local runtime** and **Run recipe**
through in-process MSAA. The launcher verified the bundle and saved exactly one
completed synthetic run in the new profile.

On November 4, the unsigned `private-630a55d95246599a` installer passed a clean
per-user installation and its native dependency probe. The validation installer
used the separate Windows app identity
`Poltergeist.Private.Companion.Validation.Tk91QA`, an isolated profile and an
installer SHA-256 of
`808a1f751cc7df32024d53ec505939dc97572d77616e0f3a8f419326ea1afbb4`.
The previous validation installation kept its own registration and marker.

A separate Windows process used MSAA to activate each of two fixture windows
with overlapping child IDs, with and without explicit COM initialization in
the target process. The client then activated **Use synthetic for now**,
**Start local runtime** and **Run recipe** in the installed Companion. The
launcher verified its installed bundle, loaded Tcl/Tk 9.1.0 and saved exactly
one completed synthetic run, `f4441af5-13ff-4032-901a-59e13fac4043`.
The test enabled the accessibility bindings within the fixture process.

The installed **Repair** action passed its native dependency probe and kept the
previous runtime in the recovery directory. The isolated run database had the
same SHA-256 before and after repair. The repaired launcher verified its bundle,
started the runtime and showed the same single run in history.

The native scroll panel now displays a two-pixel system-colored focus border
when it receives keyboard focus. Captures of the old control showed identical
border pixels before and after focus. The full Companion's AI models panel
showed the border at 100% text scaling and 700 × 600, and at 200% and 891 × 600.
The content fits inside the border, and Page Down and Home still scroll the
focused panel. These captures used a bounded event loop on a private Windows
desktop after the initial unbounded test pump stalled.

The guided retry now moves focus to the run heading while its action button is
temporarily removed. The browser palettes use system colors for navigation,
actions and status text in forced-colors mode. Chromium checked the saved
submission at 320 pixels in both dashboards, including keyboard retry and
recovery of the same run. All 51 local accessibility and guided browser checks
and three paired-workspace browser checks passed. Native Windows contrast-theme
and end-to-end screen-reader acceptance remain open.

The **Poltergeist Companion Reviewed QA** Start menu shortcut was verified on
this PC. It opens the fresh validation installation and its dedicated first-run
profile, which contained only the installation ownership marker at verification.
The automated baseline and SDK runs used a separate profile. The first-time
tester can open this shortcut for the native portion of the review.
