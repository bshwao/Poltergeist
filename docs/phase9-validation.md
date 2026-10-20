# Phase 9 implementation and validation

Follow-up on October 20, 2026: Phase 10 completed the deferred real-browser
desktop/mobile and report-print checks, a non-editable clean wheel workflow,
and fresh native CARLA comparisons with the starter and the trained reference
policy. See [Phase 10 validation](phase10-validation.md) for settings and limits.

Validated October 20, 2026 on Windows. Phase 9 extends the existing durable
optimization controller and single simulation queue. No database migration,
second simulator loop, model download or live CARLA launch was needed.

## Delivered

- Matched recipe/seed/repetition comparisons and single-agent repeated trials,
  bounded to 200 executions. Agent order, recipe, execution/scoring settings,
  registered agent snapshots and child links persist in the existing journal.
- Agent-aware search with optional driving tasks, and reproduction/minimization
  that inherit the source agent. Each new child resolves the same active
  registration and verifies fingerprints. Drift or deactivation stops further
  trials without switching to the baseline.
- `poltergeist-comparison-v1` read-only summaries and JSON download. Observed
  denominators, unavailable values, unknown failure assessments, partial
  coverage, task/input-source counts and paired exclusions remain explicit.
- Experiments dashboard: plans, history, detail polling, cancellation,
  revision-checked resume, per-run evidence links, registered agent selection,
  advanced settings, comparison tables and export. Failure details link into
  source-aware reproduction and minimization forms.
- Shared schemas/types and the [usage guide](agent-comparisons.md).

## Checks

All 20 focused Phase 9 cases passed across the final suite and search-fixture
recheck. They use short labelled synthetic scenes and real SQLite,
artifact storage, controller/worker threads, HTTP routes and external Python
agent processes. Coverage includes:

- Unique agents/seeds, total budgets, exact recipe/seed pairing and journal
  coordinate constraints.
- A baseline plus two separately registered configurations, with their exact
  snapshots retained in each trial; source-aware reproduction and minimization.
- Registered search identity/task propagation to an external worker. That
  coordination test uses a short sampled fixture; sampler behavior and ordinary
  601-frame template executions have separate regression coverage.
- Unknown observations remaining unknown, metric-specific denominators, paired
  partial-coverage/runtime-version exclusions and descriptive sample spread.
- Cancellation preventing remaining submissions; interrupted resume reusing an
  existing child without duplicate trials or budget reset; changed agent files
  stopping the next trial; inactive source agents being rejected.
- Historical comparison reads after restarting an application, and JSON export
  retaining the same evidence as the durable detail endpoint.

Additional regression results: 109 optimization repository/policy/sampler tests
and all 27 controller integration tests passed. On this host the controller
suite took 374 seconds. Initial runs hit the old 30-second test polling window
while the synthetic runner was evaluating 3D geometry. Test-only integration
wait allowances were increased; production worker and inference deadlines were
not changed.

The full frontend suite passed 301 tests, including comparison rendering,
source links, navigation, stale reads, uncertain mutation handling,
revision-safe resume and unavailable comparison observations. Production builds
and shared schema/type freshness checks passed. The actual frontend HTTP
validators also accepted six persisted comparison responses, their detail
journals and history records across completed, failed, cancelled, resumed and
partially observed operations.

## Limits and Phase 10 handoff

These checks establish integration behavior with synthetic fixtures. They do
not benchmark learned-model inference or validate live CARLA driving, model
quality, statistical significance or real-world safety. Earlier live pilot
evidence remains in its own validation documents.

Visual browser verification remains blocked: the browser tool fails during
initialization with `failed to write kernel assets ... (os error 3)`. Component
rendering, responsive CSS source and production compilation were checked, but
an actual viewport/layout review is still required when that tool recovers.

Phase 10 remains installation/integration validation across supported setups,
maintained examples and measured hardware guidance. Include a fresh live CARLA
comparison using compatible newly registered agents, a clean-machine install
walkthrough, and the deferred browser/layout/print checks. Local registrations
remain trusted executable code; hosted/public uploads require a separate design.
