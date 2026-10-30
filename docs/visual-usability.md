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
The status footer reserves space before the main notebook; tall tabs cannot
push recovery messages out of view. The full profile path is in Diagnostics.

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

Continue with populated scenario editing, model preparation, experiment progress,
failure investigation and recording review. Review loading, empty, offline,
error and recovery states for each workflow. Exercise keyboard-only entry,
validation errors, dialogs, charts and long result tables. Retain exact recorded
frame navigation and unknown measurement coverage.

The first browser coverage is Chromium at 1440- and 390-pixel widths. Smaller
phones, intermediate tablet widths, browser zoom, dark appearance, additional
browsers and screen-reader review remain open. Native visual inspection of all
companion tabs at supported resolutions/DPI and Windows contrast settings also
remains open; hidden callback tests do not establish visual acceptance.

## S11.3 — First-run acceptance

Observe new testers installing/opening the companion, finding setup problems,
pairing a computer, launching a run and explaining the result without help.
Record display/DPI, Windows/CARLA/hardware profile and concrete usability issues.
Resolve those issues and repeat the affected workflow before closing S11.
Earlier S6–S10 release and independent-machine gates remain separate.
