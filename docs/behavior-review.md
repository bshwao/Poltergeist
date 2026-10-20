# Dashboard behavior review (Phase 8.2)

Open **Simulations**, select a finished run, and scroll below its safety metrics
to **How the vehicle responded**. Completed, failed and cancelled runs with
persisted output use the same read-only analysis endpoint. Opening this panel
does not execute CARLA, load an agent or alter the saved result.

The panel shows:

- Maximum deceleration, hard-braking episodes and completed stopping episodes.
- Recorded route progress/length/completion and maximum/mean route deviation.
  Route distance is not lane compliance. An independently recorded route is
  required; agent-owned navigation can have unavailable adherence measurements.
- Recorded task outcome, kept distinct from safety scores and execution status.
- Event anchor markers and paged selection, with proposed/applied braking and
  steering changes, measured deceleration and measured heading change.

Each response shows its threshold, attribution and observation window. A real
zero interval is displayed as **0.000 s**. Already-active behavior has unknown
onset; unavailable data and no response observed remain separate states. An
incomplete window is labelled even when an earlier response was observed.
Evaluator hazards do not establish what the agent perceived. Script influence
is not credited as an independent agent response.

Expand **Supporting recorded frames** on a finding. Decision links open the
preceding input and following measured result in the Phase 7 timeline. Telemetry
links open the measured frame in the motion inspector. Where decision records
exist, telemetry evidence also offers a separate input lookup at that native
frame. A final measured packet may have no subsequent decision input; use its
telemetry link in that case. Missing frames produce an explicit message rather
than silently showing a nearby frame. The destination receives keyboard focus;
**Return to behavior finding** restores the original button, or the review panel
if that button has since been replaced. Scrolling respects reduced-motion
preferences. Response and stopping tables support keyboard horizontal scrolling.

Native CARLA frame IDs are not pagination offsets. The client verifies a likely
contiguous offset, then uses exact binary search if frames are sparse. This
avoids downloading the entire trace to the browser. Each existing API page read
still validates persisted evidence, so large sensor recordings can incur extra
disk I/O. Frame lookups time out after 30 seconds and can be retried.

**Coverage & analysis settings** lists the effective default analysis thresholds,
units, retained-output caps, recorded braking settings and coverage counts.
Refresh analysis recomputes the defaults from immutable saved evidence. Custom
thresholds remain available through the POST API documented in the
[analysis guide](behavior-analysis.md); this dashboard does not edit thresholds.

Agent-reported diagnostics are separately labelled in the decision timeline.
They are supplied by the agent and are not verified internal reasoning. The
behavior panel's descriptions summarize recorded measurements, not causality.
Synthetic runs are visibly identified. Legacy runs retain available telemetry
measurements without inventing historical agent inputs or diagnostics.

Reload the frontend after updating; restart the backend if the Phase 8.1 analysis
routes are not loaded. No dependencies or database migrations are added by
8.2.1 or 8.2.2.

Under **Export this run**, behavior analysis and recorded agent diagnostics are
enabled by default for JSON, CSV and HTML. These exports use exactly the default
analysis settings shown above. Agent statements and qualified descriptions remain
separate from measured facts. Optional full telemetry is available for JSON.
See [report formats, retention limits and custom-settings exports](reports.md).

Downloads show progress, errors, cancel and retry states. A 60-second dashboard
timeout can be extended using the CLI's `--timeout` option. Changing the selected
run or leaving the workspace cancels the browser request; a late response cannot
save a different run's file. Cancellation stops the download; the server may
finish its read-only calculation. When required evidence is unavailable, disable
behavior analysis (and full telemetry) to export saved result metadata instead.

Restart the backend and reload the dashboard after updating. A proxy must forward
the `X-Poltergeist-Report-Schema` response header; the backend exposes it through
CORS. The dashboard checks it before claiming that a file includes behavior.
