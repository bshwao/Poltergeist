# Agent comparisons and repeated trials

Phase 9 adds agent-aware experiments to the existing simulation worker. The
Experiments tab can compare active registrations, repeat one agent, search for
failures, reproduce a recorded failure, and reduce a failing recipe. Each trial
is an ordinary saved simulation with its own controls, interventions, metrics
and behavior review. No second CARLA tick loop is created.

## Dashboard workflow

1. Restart the backend and dashboard after updating. Register the agents in the
   Agents tab. Runtime code changes invalidate old executable registrations;
   create fresh registrations for new runs. Historical results remain readable.
2. Open **Experiments → Agent comparison / repeated trials**. Select synthetic
   test mode for an integration check, or explicitly choose live CARLA for
   driving experiments. Test fixtures do not establish model perception or
   physical driving performance.
3. Load a template recipe, or paste a saved recipe JSON. Edit the duration and
   driving task in that recipe. Sensor agents require the task/rig/control
   compatibility introduced in earlier phases; the same launch checks apply.
4. Select agents in the desired order. Each selection displays its zero-based
   index; agent zero is the paired reference. To compare configurations or model
   versions, register each separately. Selecting one agent performs repeated
   trials. The roster shows the newest 100 active registrations.
5. Set distinct seeds and repetitions. Inspect the number of executions and
   mode shown on the Start button. Optional simulation/scoring JSON fields use
   server defaults when `{}` and are frozen on submission.
6. Open the saved experiment in history. Running operations poll until terminal.
   Cancel stops further trials and requests cancellation of linked children.
   Interrupted operations require explicit, revision-checked resume. Resume
   reuses linked children and preserves the original execution budget.
7. Inspect the metric table, paired differences, input-source/task counts, and
   trial links. Each run opens the existing behavior review and report tools.
   Download comparison JSON to retain the plan, snapshots, journal and summary.

For search, select exactly one agent, a template, random or installed Optuna
sampling, and a trial budget. Optional fixed parameters, narrowed ranges and a
driving task are passed to the existing sampler. Unavailable objectives remain
unknown and are excluded from ranking. A search can complete without finding
a failure. A maximally scored trial is not automatically a confirmed failure.

The Failures tab has **Run reproduction trials** and **Minimize this failure**
links. These inherit the source run's agent and execution/scoring settings;
the form cannot replace them. Reproduction tests the recorded failure kind,
with an explicit matching threshold. Minimization includes confirmation repeats
within its total execution budget and produces a local reduction, with no
global minimality guarantee. A legacy custom run without a frozen registration
cannot be reproduced as the baseline.

## API

`POST /optimizations` accepts the existing search/reproduce/minimize variants
and a new `compare` request. It returns 202 with the durable operation. A minimal
comparison request uses `null` for the built-in baseline:

```json
{
  "kind": "compare",
  "mode": "test",
  "scenario": {
    "name": "short integration check",
    "duration_s": 0.2,
    "actors": [{"type": "ego_vehicle", "speed_mps": 2}]
  },
  "agents": [null],
  "seeds": [7, 11],
  "repeats": 2,
  "options": {},
  "evaluation_settings": {}
}
```

Replace/add array entries with active registered agent UUIDs. Limits are 1–8
distinct agents, 1–20 distinct seeds, 1–10 repetitions, and at most 200 total
executions. Seeds are integers from 0 through 2,147,483,647. The common scenario
is normalized before persistence; only its seed changes between matched cells.
Execution order is seed, repetition, then agent order. This is serial and is not
randomized/blinded experimental assignment.

`GET /optimizations?limit=10&offset=0` lists operations.
`GET /optimizations/{id}` returns `operation` and ordered `trials`; each trial
adds `agent_index` to the existing candidate/repetition coordinates.
`POST /optimizations/{id}/cancel` requests cancellation.
`POST /optimizations/{id}/resume` accepts `{"expected_revision": N}`.

`GET /optimizations/{id}/comparison` returns `poltergeist-comparison-v1`:

- `evidence`: the frozen operation and complete submitted trial journal.
- `groups`: agent identities, planned/submitted run counts, completion and
  assessment counts, observation sources, task statuses, metric distributions.
- `paired_comparisons`: agent zero against each other agent, paired by seed and
  repetition, with per-pair run IDs, descriptive severity difference or an
  explicit exclusion reason.
- `notes`: interpretation limits.

`?download=true` sets attachment headers for JSON export. Comparison reads use
only persisted evidence and do not launch inference or CARLA. Other operation
list/detail endpoints retain the existing controller restart-recovery behavior.
As before, submission has no idempotency key: after an uncertain connection
failure, refresh history before submitting again. The dashboard never
automatically retries a mutation.

## Identity and interpretation

Every selected registration is frozen with its manifest, configuration,
interpreter/package/model/runtime fingerprints, deadlines, sensors and control
authority. Each new trial resolves and verifies the same active identity; the
worker also verifies files before execution. Deactivation, file changes or a
different registration stop further execution rather than substitute another
model. Historical reports do not require the original agent files to exist.
Runtime fingerprints cannot certify every third-party dependency or hardware
state; inspect the recorded client/server runtime versions too.

Metric distributions use completed runs with an observed value. Finalized failed
or cancelled runs and null metrics count as unavailable. Unfinalized runs are
outside the metric denominator. Partial-coverage completed measures are retained
and the group reports how many results have partial coverage. Never interpret
unknown as zero or an unknown failure assessment as an observed negative.

Mean, minimum, maximum and sample standard deviation are descriptive. Sample
spread is unavailable below two observations. Per-pair severity differences
require both runs to complete with full safety coverage and compatible recorded
adapter/map/timestep/client/server versions; CARLA versions must be present.
The denominator for pairs includes every planned pair, including missing runs.
A positive difference means a higher recorded heuristic severity for the
compared agent. There is no automatic winner, confidence interval, significance
claim or safety certification.

Matching seeds does not guarantee deterministic inference/CARLA behavior or
identical resolved road layouts across simulator versions. Observation source,
route ownership, supplied ground truth and scripted assistance can differ
between agents. Compare those conditions before interpreting their results.
Phase 8 frame-level analysis remains available per run; the experiment summary
uses stored metrics and does not rerun raw-sensor analysis or infer private
model reasoning.
