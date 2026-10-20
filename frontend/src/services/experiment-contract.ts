import type { ComparisonReport, OptimizationRecord, OptimizationView } from "../types/generated";

const object = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === "object" && !Array.isArray(v);
const id = (v: unknown): v is string => typeof v === "string" && /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(v);
const integer = (v: unknown): v is number => typeof v === "number" && Number.isSafeInteger(v) && v >= 0;
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const nullableNumber = (v: unknown) => v === null || finite(v);
const timestamp = (v: unknown) => typeof v === "string" && /^\d{4}-\d\d-\d\dT/.test(v) && Number.isFinite(Date.parse(v));
const counts = (v: unknown) => object(v) && Object.values(v).every(integer);
type Checks = { scenario: (v: unknown) => boolean; options: (v: unknown) => boolean; evaluation: (v: unknown) => boolean; snapshot: (v: unknown) => boolean; failure: (v: unknown) => boolean };

export function experimentContracts(check: Checks) {
  const snapshot = (v: unknown) => v === null || check.snapshot(v);
  const execution = (v: unknown) => object(v) && ["carla", "test"].includes(String(v.mode)) && check.options(v.options) && check.evaluation(v.evaluation_settings);
  function record(v: unknown): v is OptimizationRecord {
    if (!object(v) || !id(v.id) || !integer(v.revision) || v.revision < 1 || !timestamp(v.created_at)
      || !["queued", "running", "completed", "failed", "cancelled", "interrupted"].includes(String(v.status))
      || typeof v.cancel_requested !== "boolean" || !execution(v.execution) || !snapshot(v.agent_snapshot)
      || !Array.isArray(v.comparison_agents) || !object(v.request)) return false;
    const terminal = !["queued", "running"].includes(String(v.status));
    if (terminal !== timestamp(v.finished_at) || (!terminal && v.finished_at !== null)
      || !(v.started_at === null || timestamp(v.started_at)) || (v.status === "running" && v.started_at === null)
      || (v.status === "failed" ? typeof v.error !== "string" : v.error !== null)
      || !(v.engine_version === null || typeof v.engine_version === "string")) return false;
    const r = v.request;
    if (r.kind === "compare") {
      if (!execution(r) || !check.scenario(r.scenario) || !Array.isArray(r.agents) || !r.agents.length || r.agents.length > 8
        || !r.agents.every(a => a === null || id(a)) || new Set(r.agents).size !== r.agents.length
        || !Array.isArray(r.seeds) || !r.seeds.length || r.seeds.length > 20 || !r.seeds.every(s => integer(s) && s <= 2147483647)
        || new Set(r.seeds).size !== r.seeds.length || !integer(r.repeats) || r.repeats < 1 || r.repeats > 10
        || r.agents.length * r.seeds.length * r.repeats > 200 || v.agent_snapshot !== null || v.source_failure !== null
        || v.comparison_agents.length !== r.agents.length) return false;
      if (!v.comparison_agents.every((a, i) => object(a) && a.agent_id === (r.agents as unknown[])[i] && snapshot(a.snapshot)
        && (a.agent_id === null ? a.snapshot === null : object(a.snapshot) && a.snapshot.registration_id === a.agent_id))) return false;
    } else {
      if (v.comparison_agents.length) return false;
      if (r.kind === "search") {
        if (!execution(r) || !["intersection", "pedestrian_crossing", "sudden_braking", "occluded_pedestrian"].includes(String(r.template))
          || !["random", "optuna"].includes(String(r.strategy)) || !integer(r.trials) || r.trials < 1 || r.trials > 1000
          || !integer(r.seed) || !object(r.parameters) || !object(r.ranges) || v.source_failure !== null
          || !((r.agent_id ?? null) === null ? v.agent_snapshot === null : object(v.agent_snapshot) && v.agent_snapshot.registration_id === r.agent_id)) return false;
      } else if (r.kind === "reproduce" || r.kind === "minimize") {
        if (!id(r.failure_id) || !check.failure(v.source_failure) || !object(v.source_failure) || v.source_failure.id !== r.failure_id
          || !integer(r.repeats) || r.repeats < 1 || r.repeats > (r.kind === "reproduce" ? 20 : 10)) return false;
        if (r.kind === "minimize" && (!integer(r.max_trials) || r.max_trials < r.repeats || r.max_trials > 200)) return false;
        if (r.kind === "reproduce" && !(r.required_successes === null || integer(r.required_successes) && r.required_successes >= 1 && r.required_successes <= r.repeats)) return false;
      } else return false;
    }
    if (v.outcome !== null && (!object(v.outcome) || !integer(v.outcome.trials_used) || !integer(v.outcome.successes) || !integer(v.outcome.unknowns)
      || typeof v.outcome.summary !== "string" || !(v.outcome.reproduced === null || typeof v.outcome.reproduced === "boolean")
      || ![v.outcome.best_failure_id, v.outcome.best_simulation_id].every(x => x === null || id(x))
      || !(v.outcome.best_scenario === null || check.scenario(v.outcome.best_scenario)))) return false;
    return true;
  }
  function view(v: unknown, owner?: string): v is OptimizationView {
    if (!object(v) || !record(v.operation) || (owner && v.operation.id.toLowerCase() !== owner.toLowerCase()) || !Array.isArray(v.trials)) return false;
    return v.trials.every((t, i) => object(t) && t.operation_id === (v.operation as OptimizationRecord).id && t.number === i
      && id(t.simulation_id) && integer(t.candidate_index) && integer(t.repeat_index) && integer(t.agent_index)
      && ["search", "compare", "reproduce", "minimize_baseline", "minimize_candidate"].includes(String(t.role)) && check.scenario(t.scenario)
      && typeof t.assessed === "boolean" && (t.matches === null || typeof t.matches === "boolean") && nullableNumber(t.score)
      && (t.failure_id === null || id(t.failure_id)) && (t.reason === null || typeof t.reason === "string"));
  }
  function distribution(v: unknown) {
    return object(v) && integer(v.eligible_results) && integer(v.observed_results) && integer(v.unavailable_results)
      && v.observed_results + v.unavailable_results === v.eligible_results && typeof v.unit === "string"
      && [v.mean, v.minimum, v.maximum, v.sample_stddev].every(nullableNumber)
      && (v.observed_results === 0 ? v.mean === null && v.minimum === null && v.maximum === null : finite(v.mean) && finite(v.minimum) && finite(v.maximum) && v.minimum <= v.mean && v.mean <= v.maximum)
      && (v.observed_results < 2 ? v.sample_stddev === null : finite(v.sample_stddev) && v.sample_stddev >= 0);
  }
  function comparison(v: unknown, owner: string): v is ComparisonReport {
    if (!object(v) || v.schema_version !== "poltergeist-comparison-v1" || !view(v.evidence, owner) || v.evidence.operation.request.kind !== "compare"
      || !Array.isArray(v.groups) || v.groups.length !== v.evidence.operation.comparison_agents.length || !Array.isArray(v.notes) || !v.notes.every(n => typeof n === "string")
      || !Array.isArray(v.paired_comparisons) || v.paired_comparisons.length !== v.groups.length - 1) return false;
    return v.groups.every((g, i) => object(g) && g.agent_index === i && typeof g.name === "string" && (g.version === null || typeof g.version === "string")
      && g.registration_id === (v.evidence as OptimizationView).operation.comparison_agents[i].agent_id
      && Array.isArray(g.simulation_ids) && g.simulation_ids.every(id) && counts(g.status_counts) && counts(g.input_sources) && counts(g.task_outcomes)
      && [g.planned_runs, g.assessed_runs, g.matching_runs, g.negative_runs, g.unknown_runs, g.unassessed_runs, g.completed_runs, g.partial_coverage_results].every(integer)
      && object(g.metrics) && Object.values(g.metrics).every(distribution))
      && v.paired_comparisons.every((p, i) => object(p) && p.reference_agent_index === 0 && p.compared_agent_index === i + 1 && distribution(p.difference)
        && Array.isArray(p.pairs) && p.pairs.every(pair => object(pair) && integer(pair.seed) && integer(pair.repeat_index)
          && [pair.reference_simulation_id, pair.compared_simulation_id].every(x => x === null || id(x)) && nullableNumber(pair.severity_difference) && typeof pair.reason === "string"));
  }
  return { record, view, comparison };
}
