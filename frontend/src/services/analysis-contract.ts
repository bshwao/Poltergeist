import type { EvidenceAnalysis, RegressionPolicy, RegressionReport } from "../types/generated";

const object = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const text = (v: unknown, max = 10000) => typeof v === "string" && v.trim().length > 0 && Array.from(v).length <= max;
const count = (v: unknown): v is number => typeof v === "number" && Number.isSafeInteger(v) && v >= 0;
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const nonnegative = (v: unknown) => finite(v) && v >= 0;
const uuid = (v: unknown) => typeof v === "string" && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(v);
const hash = (v: unknown) => typeof v === "string" && /^[0-9a-f]{64}$/.test(v);
const optional = (v: unknown, valid: (v: unknown) => boolean) => v === null || valid(v);
const list = (v: unknown, max: number, valid: (v: unknown) => boolean): v is unknown[] => Array.isArray(v) && v.length <= max && v.every(valid);
const statements = (v: unknown, max: number) => list(v, max, item => text(item, 600));
const metrics = new Set(["severity", "collision_count", "min_ttc_s", "min_clearance_m", "max_deceleration_mps2", "hard_braking_count", "traffic_violation_count"]);
const status = (v: unknown) => typeof v === "string" && ["queued", "running", "completed", "failed", "cancelled", "interrupted"].includes(v);

function reference(v: unknown): boolean {
  return object(v) && ["telemetry", "result", "control_audit"].includes(String(v.source)) && text(v.field, 120)
    && optional(v.frame, count) && optional(v.time_s, nonnegative) && (v.frame === null) === (v.time_s === null)
    && (v.source === "result" || v.frame !== null);
}
const references = (v: unknown) => list(v, 8, reference) && v.length > 0;

export function isEvidenceAnalysis(v: unknown): v is EvidenceAnalysis {
  if (!object(v) || v.schema_version !== "poltergeist-evidence-analysis-v1" || v.method !== "recorded-evidence-rules-v1"
      || !uuid(v.simulation_id) || !["test", "carla"].includes(String(v.mode)) || !status(v.execution_status) || v.execution_status === "interrupted"
      || !["pending", "complete", "partial", "unavailable"].includes(String(v.state))
      || !optional(v.source_digest, hash) || !optional(v.telemetry_digest, hash) || !text(v.summary, 600)
      || !object(v.coverage) || !optional(v.coverage.telemetry_frames, count) || !optional(v.coverage.telemetry_gaps, count)
      || !optional(v.coverage.safety_complete, item => typeof item === "boolean")
      || !optional(v.coverage.interventions_recorded, item => typeof item === "boolean")
      || !["not_reviewed", "unavailable"].includes(String(v.coverage.decision_trace))) return false;
  if ((v.state === "pending") !== ["queued", "running"].includes(String(v.execution_status))) return false;
  if (v.state === "complete" && (!hash(v.source_digest) || !hash(v.telemetry_digest)
      || !count(v.coverage.telemetry_frames) || !v.coverage.telemetry_frames || v.coverage.safety_complete !== true)) return false;
  return list(v.observations, 32, item => object(item) && ["execution", "contact", "braking", "intervention", "traffic_rule", "coverage"].includes(String(item.kind))
      && text(item.statement, 600) && optional(item.value, nonnegative) && optional(item.unit, value => text(value, 40))
      && ["complete", "partial", "unavailable"].includes(String(item.coverage)) && references(item.references))
    && list(v.hypotheses, 4, item => object(item) && item.status === "unverified" && text(item.statement, 600) && text(item.review, 600) && references(item.references))
    && statements(v.unresolved_questions, 8) && statements(v.limitations, 10);
}

export function isRegressionPolicy(v: unknown): v is RegressionPolicy {
  return object(v) && count(v.minimum_pairs) && v.minimum_pairs >= 2 && v.minimum_pairs <= 200 && object(v.thresholds)
    && Object.keys(v.thresholds).length >= 1 && Object.keys(v.thresholds).length <= 7
    && Object.entries(v.thresholds).every(([key, value]) => metrics.has(key) && nonnegative(value));
}

export function isRegressionReport(v: unknown): v is RegressionReport {
  if (!object(v) || v.schema_version !== "poltergeist-regression-v1" || v.method !== "matched-observation-rules-v1"
      || !uuid(v.operation_id) || !status(v.execution_status) || !hash(v.source_digest) || !hash(v.plan_digest)
      || !isRegressionPolicy(v.policy) || !["regression_observed", "no_regression_observed", "inconclusive", "pending"].includes(String(v.state))
      || !text(v.summary, 600) || !statements(v.context_notes, 16) || !statements(v.limitations, 10) || !object(v.evidence_review)) return false;
  const policy = v.policy, keys = Object.keys(policy.thresholds);
  const numericMap = (value: unknown, positive = false) => object(value) && Object.keys(value).length === keys.length
    && keys.every(key => key in value && optional(value[key], positive ? nonnegative : finite));
  const coverage = v.evidence_review;
  if (!["available_runs", "unavailable_runs", "limited_runs", "frames_read", "bytes_read", "frame_limit", "byte_limit"].every(key => count(coverage[key]))) return false;
  if (Number(coverage.frames_read) > Number(coverage.frame_limit) || Number(coverage.bytes_read) > Number(coverage.byte_limit)
      || Number(coverage.frame_limit) > 100001 || Number(coverage.byte_limit) > 128 * 1024**2
      || Number(coverage.limited_runs) > Number(coverage.unavailable_runs)) return false;
  if (!list(v.controllers, 8, item => object(item) && count(item.agent_index) && optional(item.registration_id, uuid)
      && text(item.name) && optional(item.version, value => text(value))
      && ["snapshot_digest", "execution_digest", "python_digest", "dependencies_digest", "configuration_digest"].every(key => optional(item[key], hash)))) return false;
  if (!v.controllers.every((item, index) => object(item) && item.agent_index === index)) return false;
  const controllers = v.controllers.length;
  if (!list(v.pairs, 200, item => object(item) && count(item.case_index) && count(item.compared_agent_index)
      && item.compared_agent_index > 0 && item.compared_agent_index < controllers && count(item.seed) && count(item.repeat_index)
      && optional(item.reference_simulation_id, uuid) && optional(item.compared_simulation_id, uuid) && typeof item.comparable === "boolean"
      && text(item.reason, 600) && numericMap(item.differences) && numericMap(item.reference_values, true) && numericMap(item.compared_values, true)
      && (item.comparable || object(item.differences) && Object.values(item.differences).every(value => value === null)))) return false;
  if (!list(v.assessments, 980, item => {
    if (!object(item) || !count(item.case_index) || !text(item.case_name) || !count(item.compared_agent_index)
        || item.compared_agent_index <= 0 || item.compared_agent_index >= controllers || !keys.includes(String(item.metric))
        || !text(item.unit) || !["higher", "lower"].includes(String(item.worsening_direction)) || !nonnegative(item.threshold)
        || item.threshold !== policy.thresholds[item.metric as keyof typeof policy.thresholds]
        || !["regression_observed", "no_regression_observed", "inconclusive"].includes(String(item.decision))
        || !count(item.planned_pairs) || !count(item.observed_pairs) || !count(item.worsening_pairs)
        || item.worsening_pairs > item.observed_pairs || item.observed_pairs > item.planned_pairs
        || !["mean_change", "minimum_change", "maximum_change"].every(key => optional(item[key], finite))
        || !optional(item.sample_stddev, nonnegative) || !text(item.explanation, 600)) return false;
    if (["mean_change", "minimum_change", "maximum_change"].some(key => (item[key] === null) !== (item.observed_pairs === 0))) return false;
    return item.decision === "inconclusive" || item.observed_pairs >= policy.minimum_pairs
      && (item.decision === "regression_observed" ? item.worsening_pairs === item.observed_pairs
        : item.worsening_pairs === 0 && item.observed_pairs === item.planned_pairs);
  })) return false;
  if ((v.state === "pending") !== ["queued", "running"].includes(String(v.execution_status))) return false;
  if (v.state === "regression_observed" && !v.assessments.some(item => object(item) && item.decision === "regression_observed")) return false;
  return v.state !== "no_regression_observed" || v.execution_status === "completed" && v.assessments.length > 0
    && v.assessments.every(item => object(item) && item.decision === "no_regression_observed");
}
