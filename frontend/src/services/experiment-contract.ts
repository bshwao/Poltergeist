import type { ComparisonReport, OptimizationRecord, OptimizationView } from "../types/generated";

const object = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === "object" && !Array.isArray(v);
const id = (v: unknown): v is string => typeof v === "string" && /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(v);
const integer = (v: unknown): v is number => typeof v === "number" && Number.isSafeInteger(v) && v >= 0;
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const nullableNumber = (v: unknown) => v === null || finite(v);
const timestamp = (v: unknown) => typeof v === "string" && /^\d{4}-\d\d-\d\dT/.test(v) && Number.isFinite(Date.parse(v));
const counts = (v: unknown) => object(v) && Object.values(v).every(integer);
const bounded = (v: unknown, minimum: number, maximum: number) => finite(v) && v >= minimum && v <= maximum;
const templates = ["intersection", "pedestrian_crossing", "sudden_braking", "occluded_pedestrian"];
export const sameJson = (a: unknown, b: unknown): boolean => {
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((item, i) => sameJson(item, b[i]));
  return object(a) && object(b) && Object.keys(a).length === Object.keys(b).length && Object.keys(a).every(key => Object.prototype.hasOwnProperty.call(b, key) && sameJson(a[key], b[key]));
};
export const isDiscoveryBudget = (v: unknown): v is Record<string, number> => object(v)
  && [["max_executions", 3, 200], ["max_search_trials", 1, 100], ["confirmation_repeats", 2, 10], ["required_successes", 2, 10], ["max_candidates", 1, 20]].every(([key, low, high]) => integer(v[key]) && bounded(v[key], Number(low), Number(high)))
  && Number(v.required_successes) <= Number(v.confirmation_repeats) && Number(v.max_executions) >= 1 + Number(v.confirmation_repeats)
  && bounded(v.max_wall_time_s, 1, 7200) && bounded(v.max_simulated_time_s, Number.MIN_VALUE, 36000);
export const isDiscoveryGoal = (v: unknown): v is Record<string, unknown> => object(v)
  && typeof v.description === "string" && v.description.trim().length > 0 && v.description.length <= 500
  && templates.includes(String(v.template)) && ["collision", "near_miss", "hard_braking", "traffic_violation"].includes(String(v.failure_kind))
  && (v.agent_id === null || id(v.agent_id)) && integer(v.seed) && v.seed <= 2147483647
  && bounded(v.duration_s, Number.MIN_VALUE, 120) && bounded(v.minimum_severity, 0, 100)
  && object(v.parameters) && Object.values(v.parameters).every(p => typeof p === "boolean" || typeof p === "string" || finite(p))
  && object(v.ranges) && Object.values(v.ranges).every(r => Array.isArray(r) && r.length === 2 && r.every(finite) && r[0] <= r[1])
  && (v.driving_task === null || object(v.driving_task));
type Checks = { scenario: (v: unknown) => boolean; options: (v: unknown) => boolean; evaluation: (v: unknown) => boolean; snapshot: (v: unknown) => boolean; failure: (v: unknown) => boolean };

export function experimentContracts(check: Checks) {
  const snapshot = (v: unknown) => v === null || check.snapshot(v);
  const execution = (v: unknown) => object(v) && ["carla", "test"].includes(String(v.mode)) && check.options(v.options) && check.evaluation(v.evaluation_settings);
  const suite = (r: unknown) => object(r) && execution(r) && typeof r.name === "string" && r.name.trim().length > 0 && r.name.length <= 128
    && Array.isArray(r.cases) && r.cases.length >= 1 && r.cases.length <= 20
    && r.cases.every(c => object(c) && typeof c.name === "string" && c.name.trim().length > 0 && c.name.length <= 128 && check.scenario(c.scenario))
    && new Set(r.cases.map(c => String(c.name).toLocaleLowerCase())).size === r.cases.length
    && Array.isArray(r.agents) && r.agents.length > 0 && r.agents.length <= 8 && r.agents.every(a => a === null || id(a)) && new Set(r.agents).size === r.agents.length
    && Array.isArray(r.seeds) && r.seeds.length > 0 && r.seeds.length <= 20 && r.seeds.every(s => integer(s) && s <= 2147483647) && new Set(r.seeds).size === r.seeds.length
    && integer(r.repeats) && r.repeats >= 1 && r.repeats <= 10 && r.cases.length * r.agents.length * r.seeds.length * r.repeats <= 200;
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
    if (r.kind === "compare" || r.kind === "suite") {
      if (!execution(r) || (r.kind === "suite" ? !suite(r) : !check.scenario(r.scenario)) || !Array.isArray(r.agents) || !r.agents.length || r.agents.length > 8
        || !r.agents.every(a => a === null || id(a)) || new Set(r.agents).size !== r.agents.length
        || !Array.isArray(r.seeds) || !r.seeds.length || r.seeds.length > 20 || !r.seeds.every(s => integer(s) && s <= 2147483647)
        || new Set(r.seeds).size !== r.seeds.length || !integer(r.repeats) || r.repeats < 1 || r.repeats > 10
        || r.agents.length * r.seeds.length * r.repeats * (r.kind === "suite" && Array.isArray(r.cases) ? r.cases.length : 1) > 200 || v.agent_snapshot !== null || v.source_failure !== null
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
      } else if (r.kind === "discover") {
        if (!execution(r) || !isDiscoveryBudget(r.budget) || !isDiscoveryGoal(r.goal) || !object(r.search)
          || !Array.isArray(r.scenarios) || r.scenarios.length !== r.budget.max_search_trials
          || !r.scenarios.every((scene, index) => check.scenario(scene) && object(scene) && scene.template === (r.goal as Record<string, unknown>).template
            && scene.duration_s === (r.goal as Record<string, unknown>).duration_s && scene.seed === (Number((r.goal as Record<string, unknown>).seed) + index) % 2147483648)
          || !id(r.plan_id) || r.plan_id !== v.id || typeof r.plan_digest !== "string" || !/^[0-9a-f]{64}$/.test(r.plan_digest)
          || !timestamp(r.approved_at) || !timestamp(r.deadline_at) || Math.abs(Date.parse(String(r.deadline_at)) - Date.parse(String(r.approved_at)) - r.budget.max_wall_time_s * 1000) > 1
          || r.search.kind !== "search" || r.search.strategy !== "random" || r.search.trials !== r.budget.max_search_trials
          || v.source_failure !== null || !((r.goal.agent_id === null) ? v.agent_snapshot === null : object(v.agent_snapshot) && v.agent_snapshot.registration_id === r.goal.agent_id)
          || !["mode", "options", "evaluation_settings"].every(key => sameJson(r[key], (r.search as Record<string, unknown>)[key]))
          || !["template", "seed", "agent_id", "parameters", "ranges", "failure_kind", "minimum_severity", "driving_task", "duration_s"].every(key => sameJson((r.goal as Record<string, unknown>)[key], (r.search as Record<string, unknown>)[key]))) return false;
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
      && ["search", "compare", "suite", "discovery_search", "discovery_confirm", "reproduce", "minimize_baseline", "minimize_candidate"].includes(String(t.role)) && check.scenario(t.scenario)
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
  return { record, view, comparison, suite, execution, distribution };
}
