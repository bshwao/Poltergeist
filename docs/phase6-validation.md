# Phase 6 validation — October 19, 2026

Dashboard agent registration and launch selection are implemented. This phase
connects the Phase 5 registry/API to the existing scenario and results workspace.

## Automated checks

- **256 frontend tests passed**, including registration forms, pagination,
  static checks, deactivation, cross-page refresh, preserved drafts/selections,
  preflight drift, compatibility checks, immutable acceptance receipts,
  cancellation/late responses, historical identity and named sensor streams.
- **88 backend/API regressions passed** for agent registrations, external worker
  execution and the existing simulation endpoints.
- TypeScript checking, the production frontend build, shared-schema checks and
  generated-TypeScript checks passed. No dependency installation was required.
- Documentation link and whitespace checks passed.

Registration error tests distinguish rejection from a lost acknowledgement and
preserve editable drafts. Controller tests keep missing/deactivated selections
visible rather than silently choosing another agent. Current file checks are
repeated before a launch, and the returned snapshot must match the entire selected
configuration, model/runtime fingerprints and environment declaration.

History and result views use recorded snapshots without reading the current
registration. Older custom runs display unavailable identity. Existing failure
comparisons flag different or unknown registered identities; this does not
implement Phase 9's agent-aware experiment comparisons.

## Browser acceptance

The real dashboard and API ran on isolated local ports 5176 and 8016, with SQLite
and artifacts under the ignored `.pytest_cache/phase6-browser-data` directory.
The user's normal database and simulator were not used.

1. Registered the existing starter package and selected its prepared Python
   interpreter in **Agents**. Its static readiness check passed without loading
   the agent.
2. Generated and validated a one-second intersection recipe, selected the starter
   under **Ego controller**, chose synthetic mode and launched from the dashboard.
   The external worker completed the run with 21 telemetry frames. Its receipt,
   history and results retained the selected registration and authoritative
   control declaration.
3. Registered the camera/LiDAR teaching package. Confirmed that a missing driving
   task and CARLA RGB rendering requirements appear before submission. No CARLA
   experiment was submitted.
4. Validated an explicit task and launched a 0.10-second synthetic sensor run.
   It completed with three telemetry frames and six named sensor samples totalling
   694,416 bytes. The dashboard labelled the inputs as deterministic fixtures and
   disclosed ego-localization/navigation assistance. Its camera sample loaded,
   passed SHA-256/length verification and displayed its actual 320-by-180 pixels.
5. Deactivated the sensor registration through the dashboard. The existing
   scenario selection remained visible and blocked further launches. The saved
   completed run still displayed the original name, version, registration and
   fixture provenance.

The desktop layout was inspected in the in-app browser. Local screenshots are
ignored at `.pytest_cache/phase6-dashboard.png` and
`.pytest_cache/phase6-dashboard-completed.png`. This phase does not claim a new
mobile-browser acceptance pass.

## Scope

Both example controllers used for browser acceptance are rule based. No CARLA
instance, GPU inference, learned model or model download was started in this
phase. Static readiness does not establish dependency availability, successful
inference, simulator readiness, driving quality or real-time performance. Existing
live model and sensor evidence remains in the earlier validation guides.

The UI uses backend-local paths for trusted SDK packages. It does not accept
arbitrary uploaded model files as a complete driving stack. Full persisted
decision timelines, behavior reports and agent-aware automated comparisons and
optimization remain Phases 7–9. See [dashboard usage](dashboard.md).
