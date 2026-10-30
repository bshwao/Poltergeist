import type { DiscoveryPlan, DiscoveryReport, GalleryScenario, ReplayHighlights, SavedFeedback, SavedSuite, SuiteReport } from "../types/generated";
import { isDiscoveryBudget, isDiscoveryGoal, sameJson } from "./experiment-contract";

const object = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === "object" && !Array.isArray(v);
const id = (v: unknown): v is string => typeof v === "string" && /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(v);
const number = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v) && v >= 0;
const count = (v: unknown): v is number => number(v) && Number.isSafeInteger(v);
const text = (v: unknown): v is string => typeof v === "string";
const texts = (v: unknown): v is string[] => Array.isArray(v) && v.every(text);
const date = (v: unknown): v is string => text(v) && /^\d{4}-\d\d-\d\dT/.test(v) && Number.isFinite(Date.parse(v));
const digest = (v: unknown) => text(v) && /^[0-9a-f]{64}$/.test(v);
type Checks = { scenario: (v: unknown) => boolean; snapshot: (v: unknown) => boolean };
type Experiments = { suite: (v: unknown) => boolean; execution: (v: unknown) => boolean; view: (v: unknown, owner?: string) => boolean; distribution: (v: unknown) => boolean };

export function labContracts(check: Checks, experiments: Experiments) {
  function gallery(v: unknown): v is GalleryScenario[] {
    return Array.isArray(v) && v.length <= 32 && new Set(v.map(row => object(row) ? row.id : null)).size === v.length
      && v.every(row => object(row) && text(row.id) && /^[a-z0-9_-]{1,80}$/.test(row.id) && text(row.title) && text(row.description)
        && ["starter", "low", "medium", "high"].includes(String(row.level)) && text(row.learning_goal) && check.scenario(row.scenario)
        && object(row.scenario) && row.family === row.scenario.template);
  }
  function suite(v: unknown, owner?: string): v is SavedSuite {
    return object(v) && id(v.id) && (!owner || v.id === owner) && count(v.revision) && v.revision >= 1
      && text(v.description) && date(v.created_at) && date(v.updated_at) && experiments.suite(v.definition)
      && object(v.definition) && v.definition.kind === "suite" && v.definition.suite_id === null && v.definition.suite_revision === null;
  }
  function plan(v: unknown, owner?: string): v is DiscoveryPlan {
    if (!object(v) || v.schema_version !== "poltergeist-discovery-plan-v1" || !id(v.id) || owner && v.id !== owner
      || !count(v.revision) || v.revision < 1 || !digest(v.digest) || !object(v.proposal) || !experiments.execution(v.proposal)
      || !isDiscoveryGoal(v.proposal.goal) || !isDiscoveryBudget(v.proposal.budget) || !object(v.search) || v.search.kind !== "search"
      || !Array.isArray(v.scenarios) || v.scenarios.length !== v.proposal.budget.max_search_trials
      || !v.scenarios.every((scene, index) => check.scenario(scene) && object(scene)
        && scene.template === (v.proposal as Record<string, Record<string, unknown>>).goal.template
        && scene.duration_s === (v.proposal as Record<string, Record<string, unknown>>).goal.duration_s
        && scene.seed === (Number((v.proposal as Record<string, Record<string, unknown>>).goal.seed) + index) % 2147483648)
      || v.search.strategy !== "random" || v.search.trials !== v.proposal.budget.max_search_trials
      || !["mode", "options", "evaluation_settings"].every(key => sameJson((v.proposal as Record<string, unknown>)[key], (v.search as Record<string, unknown>)[key]))
      || !["template", "seed", "agent_id", "parameters", "ranges", "failure_kind", "minimum_severity", "driving_task", "duration_s"].every(key => sameJson((v.proposal as Record<string, Record<string, unknown>>).goal[key], (v.search as Record<string, unknown>)[key]))
      || !(v.agent_snapshot === null || check.snapshot(v.agent_snapshot))
      || (v.proposal.goal.agent_id === null ? v.agent_snapshot !== null : !object(v.agent_snapshot) || v.agent_snapshot.registration_id !== v.proposal.goal.agent_id)
      || !count(v.maximum_executions) || v.maximum_executions !== Math.min(v.proposal.budget.max_executions, v.proposal.budget.max_search_trials + v.proposal.budget.max_candidates * v.proposal.budget.confirmation_repeats)
      || !number(v.per_run_duration_s) || v.per_run_duration_s !== v.proposal.goal.duration_s
      || !texts(v.steps) || !v.steps.length || !texts(v.limitations) || !v.limitations.length || !date(v.created_at) || !date(v.expires_at) || v.expires_at <= v.created_at) return false;
    return v.operation_id === null ? v.approved_at === null && v.deadline_at === null
      : v.operation_id === v.id && date(v.approved_at) && date(v.deadline_at)
        && Math.abs(Date.parse(v.deadline_at) - Date.parse(v.approved_at) - v.proposal.budget.max_wall_time_s * 1000) <= 1;
  }
  function discovery(v: unknown, owner: string): v is DiscoveryReport {
    if (!object(v) || v.schema_version !== "poltergeist-discovery-report-v1" || !plan(v.plan, owner)
      || !experiments.view(v.evidence, v.plan.operation_id ?? "") || !object(v.evidence) || !object(v.evidence.operation)
      || !object(v.evidence.operation.request) || v.evidence.operation.request.kind !== "discover"
      || v.evidence.operation.request.plan_id !== v.plan.id || v.evidence.operation.request.plan_digest !== v.plan.digest
      || !sameJson(v.evidence.operation.request.search, v.plan.search) || !sameJson(v.evidence.operation.request.scenarios, v.plan.scenarios)
      || !sameJson(v.evidence.operation.agent_snapshot, v.plan.agent_snapshot)
      || !Array.isArray(v.evidence.trials) || !Array.isArray(v.candidates) || !count(v.confirmed_failures) || !count(v.executions_used)
      || v.executions_used !== v.evidence.trials.length || v.executions_used > v.plan.proposal.budget.max_executions
      || !number(v.simulated_time_reserved_s) || v.simulated_time_reserved_s > v.plan.proposal.budget.max_simulated_time_s + 1e-9
      || ![v.negative_trials, v.unknown_trials, v.unassessed_trials].every(count) || !text(v.summary) || !texts(v.limitations)) return false;
    const trials = v.evidence.trials as Record<string, unknown>[];
    if (v.negative_trials !== trials.filter(t => t.assessed && t.matches === false).length
      || v.unknown_trials !== trials.filter(t => t.assessed && t.matches === null).length
      || v.unassessed_trials !== trials.filter(t => !t.assessed).length
      || Math.abs(v.simulated_time_reserved_s - trials.reduce((sum, t) => sum + Number((t.scenario as Record<string, unknown>).duration_s), 0)) > 1e-8) return false;
    const budget = v.plan.proposal.budget;
    const valid = v.candidates.every(c => {
      if (!object(c) || !count(c.candidate_index) || !id(c.search_simulation_id) || !(c.failure_id === null || id(c.failure_id))
        || !check.scenario(c.scenario) || !Array.isArray(c.confirmation_simulation_ids) || !c.confirmation_simulation_ids.every(id)
        || new Set(c.confirmation_simulation_ids).size !== c.confirmation_simulation_ids.length
        || !["confirmed", "not_reproduced", "inconclusive", "pending"].includes(String(c.status))
        || ![c.required_successes, c.matching_confirmations, c.negative_confirmations, c.unknown_confirmations, c.pending_confirmations].every(count)
        || c.required_successes !== budget.required_successes) return false;
      const search = trials.find(t => t.simulation_id === c.search_simulation_id && t.role === "discovery_search" && t.matches === true && t.candidate_index === c.candidate_index);
      const confirmations = trials.filter(t => t.role === "discovery_confirm" && t.candidate_index === c.candidate_index);
      if (!search || !sameJson(search.scenario, c.scenario) || search.failure_id !== c.failure_id
        || !sameJson(confirmations.map(t => t.simulation_id), c.confirmation_simulation_ids) || confirmations.some(t => !sameJson(t.scenario, c.scenario))
        || c.matching_confirmations !== confirmations.filter(t => t.matches === true).length
        || c.negative_confirmations !== confirmations.filter(t => t.matches === false).length
        || c.unknown_confirmations !== confirmations.filter(t => t.assessed && t.matches === null).length
        || c.pending_confirmations !== budget.confirmation_repeats - confirmations.filter(t => t.assessed).length) return false;
      return c.status === "confirmed" ? c.pending_confirmations === 0 && Number(c.matching_confirmations) >= budget.required_successes
        && object(c.reproduction) && c.reproduction.kind === "reproduce" && c.reproduction.failure_id === c.failure_id
        : c.reproduction === null;
    });
    return valid && v.confirmed_failures === v.candidates.filter(c => object(c) && c.status === "confirmed").length;
  }
  function suiteReport(v: unknown, owner: string): v is SuiteReport {
    if (!object(v) || v.schema_version !== "poltergeist-suite-report-v1" || !experiments.view(v.evidence, owner)
      || !object(v.evidence) || !object(v.evidence.operation) || !object(v.evidence.operation.request) || v.evidence.operation.request.kind !== "suite"
      || !Array.isArray(v.evidence.operation.request.cases) || !Array.isArray(v.evidence.operation.comparison_agents)
      || !Array.isArray(v.cases) || v.cases.length !== v.evidence.operation.request.cases.length) return false;
    const agents = v.evidence.operation.comparison_agents as Record<string, unknown>[];
    return v.cases.every((c, i) => object(c) && c.case_index === i && text(c.name) && texts(c.notes) && Array.isArray(c.groups)
      && c.groups.length === agents.length && c.groups.every((g, index) => object(g) && g.agent_index === index
        && g.registration_id === agents[index].agent_id && text(g.name) && (g.version === null || text(g.version))
        && Array.isArray(g.simulation_ids) && g.simulation_ids.every(id) && object(g.status_counts) && Object.values(g.status_counts).every(count)
        && object(g.input_sources) && Object.values(g.input_sources).every(count) && object(g.task_outcomes) && Object.values(g.task_outcomes).every(count)
        && [g.planned_runs, g.assessed_runs, g.matching_runs, g.negative_runs, g.unknown_runs, g.unassessed_runs, g.completed_runs, g.partial_coverage_results].every(count)
        && object(g.metrics) && Object.values(g.metrics).every(experiments.distribution))
      && Array.isArray(c.paired_comparisons) && c.paired_comparisons.length === agents.length - 1
      && c.paired_comparisons.every((p, index) => object(p) && p.reference_agent_index === 0 && p.compared_agent_index === index + 1 && experiments.distribution(p.difference)
        && Array.isArray(p.pairs) && p.pairs.every(pair => object(pair) && count(pair.seed) && count(pair.repeat_index)
          && [pair.reference_simulation_id, pair.compared_simulation_id].every(value => value === null || id(value)) && (pair.severity_difference === null || typeof pair.severity_difference === "number" && Number.isFinite(pair.severity_difference)) && text(pair.reason))));
  }
  function highlights(v: unknown, owner: string): v is ReplayHighlights {
    return object(v) && v.simulation_id === owner && ["test", "carla"].includes(String(v.mode)) && count(v.total_frames)
      && typeof v.coverage_complete === "boolean" && text(v.explanation) && texts(v.limitations) && Array.isArray(v.highlights) && v.highlights.length <= 12
      && v.highlights.every(h => object(h) && h.source === "telemetry" && ["collision", "near_miss", "hard_braking", "traffic_violation", "finish"].includes(String(h.kind))
        && count(h.frame) && number(h.time_s) && text(h.title) && text(h.description));
  }
  function feedback(v: unknown): v is SavedFeedback {
    return object(v) && id(v.id) && date(v.created_at) && object(v.feedback) && v.feedback.consent_to_save === true
      && (v.feedback.simulation_id === null || id(v.feedback.simulation_id)) && typeof v.feedback.could_explain_outcome === "boolean"
      && text(v.feedback.confusing_step) && text(v.feedback.reason_to_return);
  }
  return { gallery, suite, plan, discovery, suiteReport, highlights, feedback };
}
