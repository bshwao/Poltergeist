import type { EvaluationSettings, FailureReport, SafetyMetrics, SimulationRecord } from "../types/generated";

export const failureKindLabel = (kind: FailureReport["kind"]): string => kind.replace(/_/g, " ");
export const reproductionLabel = (value: boolean | null): string => value === true ? "Confirmed" : value === false ? "Not reproduced" : "Unknown";
export const modeLabel = (mode: "carla" | "test"): string => mode === "test" ? "Synthetic test" : "CARLA";
export const observation = (value: number | null | undefined, digits = 2): string => typeof value === "number" && Number.isFinite(value) ? Number.isInteger(value) ? String(value) : value.toFixed(digits) : "Unknown";
export const validSimulationId = (value: string): boolean => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);

export const metricRows: ReadonlyArray<{ key: keyof Omit<SafetyMetrics, "coverage" | "evaluation_settings">; label: string }> = [
  { key: "severity", label: "Severity (0–100)" },
  { key: "collision_count", label: "Observed collisions" },
  { key: "min_ttc_s", label: "Minimum TTC (s)" },
  { key: "min_clearance_m", label: "Minimum clearance (m)" },
  { key: "hard_braking_count", label: "Observed hard braking episodes" },
  { key: "max_deceleration_mps2", label: "Maximum deceleration (m/s²)" },
  { key: "traffic_violation_count", label: "Observed traffic violations" },
  { key: "max_stopping_time_s", label: "Maximum stopping time (s)" },
  { key: "max_stopping_distance_m", label: "Maximum stopping distance (m)" },
];

export function fullCoverage(metrics: SafetyMetrics): boolean {
  const c = metrics.coverage;
  return c.total_frames > 0 && !c.has_gaps
    && [c.collision_frames, c.braking_frames, c.clearance_frames, c.ttc_frames, c.traffic_rule_frames].every(value => value === c.total_frames)
    && c.clearance_pairs === c.actor_pairs && c.ttc_pairs === c.actor_pairs;
}

export function settingsDifferences(left: EvaluationSettings | null, right: EvaluationSettings | null): string[] {
  if (!left || !right) return ["Evaluation settings are unavailable for one or both runs."];
  return (Object.keys(left) as Array<keyof EvaluationSettings>).filter(key => left[key] !== right[key]).map(key => `${key.replace(/_/g, " ")}: ${left[key]} → ${right[key]}`);
}

/** Score compatibility is deliberately stricter than having a numeric collision score. */
export function comparisonNote(left: SimulationRecord, right: SimulationRecord): { comparable: boolean; message: string } {
  if (left.simulation.mode !== right.simulation.mode) return { comparable: false, message: "Different execution modes — compare observations separately. Synthetic tests do not establish CARLA driving behavior." };
  if (left.simulation.agent !== right.simulation.agent) return { comparable: false, message: "Different driving agents — compare observations separately." };
  if (left.simulation.agent === "custom") {
    const first = left.simulation.agent_snapshot, second = right.simulation.agent_snapshot;
    if (!first || !second) return { comparable: false, message: "A custom agent's registered identity is unavailable — compare observations separately." };
    const canonical = (value: unknown) => JSON.stringify(value, (_key, item) => item && typeof item === "object" && !Array.isArray(item)
      ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item);
    if (canonical(first) !== canonical(second)) return { comparable: false, message: "Different registered agents or agent settings — compare observations separately." };
  }
  if (!left.job || !right.job || (Object.keys(left.job.options) as Array<keyof typeof left.job.options>).some(key => left.job?.options[key] !== right.job?.options[key])) return { comparable: false, message: "Different or unknown execution settings — compare observations separately." };
  if (!left.result || !right.result) return { comparable: false, message: "A finalized result is unavailable — compare observations separately." };
  if (settingsDifferences(left.result.metrics.evaluation_settings, right.result.metrics.evaluation_settings).length) return { comparable: false, message: "Different or unknown evaluation settings — compare observations separately." };
  if (left.simulation.status !== "completed" || right.simulation.status !== "completed" || !fullCoverage(left.result.metrics) || !fullCoverage(right.result.metrics)) return { comparable: false, message: "Partial observations or an incomplete run — scores are not directly comparable." };
  if (left.result.metrics.severity === null || right.result.metrics.severity === null) return { comparable: false, message: "An unscored run is present — scores are not directly comparable." };
  return { comparable: true, message: "Matched execution mode, agent, execution settings, and evaluation settings with complete observation coverage. These are observations from two individual runs." };
}
