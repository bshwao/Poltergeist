import type { EvaluationSettings, Scenario, SimulationLaunch, SimulationOptions } from "../types/generated";

export type RunMode = "carla" | "test";
export const DEFAULT_OPTIONS: SimulationOptions = {
  allow_map_reload: false, sensor_timeout_s: 2, sensor_settle_s: 0.02,
  max_ticks: 100_000, camera: false, lidar: false, camera_width: 640, camera_height: 360,
};
export const DEFAULT_EVALUATION: EvaluationSettings = {
  method_version: "1.0", collision_episode_gap_s: 1, violation_episode_gap_s: 1,
  speeding_tolerance_mps: 0.5, hard_braking_threshold_mps2: 4, stopped_speed_mps: 0.1,
  max_sample_gap_s: 0.5, ttc_horizon_s: 10, ttc_warning_s: 3, clearance_warning_m: 2,
  braking_full_severity_mps2: 8, violations_for_full_severity: 3,
  ttc_weight: 45, clearance_weight: 25, braking_weight: 15, traffic_rule_weight: 15,
};

type NumericField<K extends string> = {
  key: K; label: string; minimum: number; positive?: boolean; maximum?: number; integer?: boolean;
};
type OptionNumberKey = "sensor_timeout_s" | "sensor_settle_s" | "max_ticks" | "camera_width" | "camera_height";
type EvaluationNumberKey = Exclude<keyof EvaluationSettings, "method_version">;
export const OPTION_FIELDS: NumericField<OptionNumberKey>[] = [
  { key: "sensor_timeout_s", label: "Sensor timeout (s)", minimum: 0, positive: true },
  { key: "sensor_settle_s", label: "Sensor settle time (s)", minimum: 0, positive: true },
  { key: "max_ticks", label: "Maximum ticks", minimum: 1, integer: true },
  { key: "camera_width", label: "Camera width (px)", minimum: 1, maximum: 4096, integer: true },
  { key: "camera_height", label: "Camera height (px)", minimum: 1, maximum: 4096, integer: true },
];
export const EVALUATION_FIELDS: NumericField<EvaluationNumberKey>[] = [
  { key: "collision_episode_gap_s", label: "Collision episode gap (s)", minimum: 0 },
  { key: "violation_episode_gap_s", label: "Violation episode gap (s)", minimum: 0 },
  { key: "speeding_tolerance_mps", label: "Speeding tolerance (m/s)", minimum: 0 },
  { key: "hard_braking_threshold_mps2", label: "Hard braking threshold (m/s²)", minimum: 0, positive: true },
  { key: "stopped_speed_mps", label: "Stopped speed (m/s)", minimum: 0 },
  { key: "max_sample_gap_s", label: "Maximum sample gap (s)", minimum: 0, positive: true },
  { key: "ttc_horizon_s", label: "Time-to-collision horizon (s)", minimum: 0, positive: true },
  { key: "ttc_warning_s", label: "Time-to-collision warning (s)", minimum: 0, positive: true },
  { key: "clearance_warning_m", label: "Clearance warning (m)", minimum: 0, positive: true },
  { key: "braking_full_severity_mps2", label: "Full-severity braking (m/s²)", minimum: 0, positive: true },
  { key: "violations_for_full_severity", label: "Violations for full severity", minimum: 1, integer: true },
  { key: "ttc_weight", label: "Time-to-collision weight (%)", minimum: 0 },
  { key: "clearance_weight", label: "Clearance weight (%)", minimum: 0 },
  { key: "braking_weight", label: "Braking weight (%)", minimum: 0 },
  { key: "traffic_rule_weight", label: "Traffic-rule weight (%)", minimum: 0 },
];

export type OptionDraft = Record<OptionNumberKey, string> & Pick<SimulationOptions, "allow_map_reload" | "camera" | "lidar">;
export type EvaluationDraft = Record<EvaluationNumberKey, string>;
export function defaultOptionDraft(): OptionDraft {
  return { ...Object.fromEntries(OPTION_FIELDS.map(field => [field.key, String(DEFAULT_OPTIONS[field.key])])),
    allow_map_reload: false, camera: false, lidar: false } as OptionDraft;
}
export function defaultEvaluationDraft(): EvaluationDraft {
  return Object.fromEntries(EVALUATION_FIELDS.map(field => [field.key, String(DEFAULT_EVALUATION[field.key])])) as EvaluationDraft;
}

function numberValue(raw: string, field: NumericField<string>): number | string {
  const text = raw.trim();
  if (!text) return "Enter a value.";
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(text) || !Number.isFinite(Number(text))) return "Enter a finite number.";
  const value = Number(text);
  if (field.integer && !Number.isSafeInteger(value)) return "Enter a whole number within the safe integer range.";
  if (field.positive && value <= 0) return "Enter a value greater than zero.";
  if (value < field.minimum) return `Enter a value of at least ${field.minimum}.`;
  if (field.maximum !== undefined && value > field.maximum) return `Enter a value no greater than ${field.maximum}.`;
  return value;
}

export type LaunchBuild = { request: SimulationLaunch; errors?: never } | { request?: never; errors: Record<string, string> };
/** The request owns a deep copy, including nested actors/events, before a POST starts. */
export function makeLaunchRequest(scenario: Scenario | null, mode: RunMode | null, options: OptionDraft, evaluation: EvaluationDraft): LaunchBuild {
  const errors: Record<string, string> = {};
  if (!scenario) errors.scenario = "Generate or validate the current recipe before launching.";
  if (!mode) errors.mode = "Choose CARLA or synthetic test mode explicitly.";
  const optionValues: Record<string, number> = {};
  const evaluationValues: Record<string, number> = {};
  for (const [fields, rawValues, parsed, prefix] of [
    [OPTION_FIELDS, options, optionValues, "options"],
    [EVALUATION_FIELDS, evaluation, evaluationValues, "evaluation_settings"],
  ] as const) {
    for (const field of fields) {
      const value = numberValue(rawValues[field.key as keyof typeof rawValues] as string, field);
      if (typeof value === "string") errors[`${prefix}.${field.key}`] = value;
      else parsed[field.key] = value;
    }
  }
  if (mode === "test" && (options.camera || options.lidar)) errors.capture = "Camera and LiDAR capture require CARLA mode.";
  if (evaluationValues.ttc_horizon_s < evaluationValues.ttc_warning_s) errors["evaluation_settings.ttc_horizon_s"] = "The horizon must cover the warning time.";
  if (evaluationValues.braking_full_severity_mps2 <= evaluationValues.hard_braking_threshold_mps2) errors["evaluation_settings.braking_full_severity_mps2"] = "Full severity must exceed the hard braking threshold.";
  const weightKeys = ["ttc_weight", "clearance_weight", "braking_weight", "traffic_rule_weight"];
  if (weightKeys.every(key => evaluationValues[key] !== undefined) && Math.abs(weightKeys.reduce((total, key) => total + evaluationValues[key], 0) - 100) > 1e-9) {
    errors.weights = "Severity weights must sum to 100%.";
  }
  if (Object.keys(errors).length) return { errors };
  return { request: JSON.parse(JSON.stringify({
    scenario, scenario_id: null, mode,
    options: { ...optionValues, allow_map_reload: options.allow_map_reload, camera: options.camera, lidar: options.lidar },
    evaluation_settings: { method_version: "1.0", ...evaluationValues },
  })) as SimulationLaunch };
}

export function acceptanceMayBeUncertain(status: number): boolean {
  return status === 0 || (status >= 200 && status < 300) || (status >= 500 && status !== 503);
}
