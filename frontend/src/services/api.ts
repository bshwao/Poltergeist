import type { AgentReadiness, AgentRegistration, AnalyticsSummary, FailureReport, QueueStatus, RegisteredAgent, Scenario, ScenarioGenerate, ScenarioRecord, SimulationLaunch, SimulationProgress, SimulationRecord, SimulationResult, SimulationStatus, TelemetryPage, TemplateInfo } from "../types/generated";

import type { DecisionPage } from "../types/generated";
import type { BehaviorAnalysis } from "../types/generated";
import { isBehaviorAnalysis } from "./behavior-contract";
import { experimentContracts } from "./experiment-contract";
import { labContracts } from "./lab-contract";
import type { DiscoveryPlan, DiscoveryProposal, DiscoveryReport, GalleryScenario, GuidanceFeedback, ReplayHighlights, SavedFeedback, SavedSuite, SuiteReport, SuiteSave } from "../types/generated";
import type { LabReply } from "../types/generated";
type LabResult = LabReply["result"];
import { isReadinessReport } from "./readiness-contract";
import type { ReadinessReport } from "../types/generated";
import type { ComparisonReport, OptimizationRecord, OptimizationView } from "../types/generated";

export interface ApiIssue {
  path: string;
  message: string;
}

export interface RequestOptions {
  signal?: AbortSignal;
}

export interface ScenarioPagination {
  limit?: number;
  offset?: number;
}

export interface AgentFilters extends ScenarioPagination {
  active?: boolean;
}

export interface SimulationFilters extends ScenarioPagination {
  mode?: "carla" | "test";
  status?: SimulationStatus;
}

export interface FailureFilters extends ScenarioPagination {
  simulation_id?: string;
  reproduced?: "all" | "true" | "false" | "unknown";
}

export interface RawCaptureSample {
  kind: "camera" | "lidar";
  frame: number;
  timestamp: number;
  file: string;
  byte_length: number;
  sha256: string;
  metadata: { encoding: "bgra8"; width: number; height: number; [key: string]: unknown }
    | { encoding: "float32_xyzi"; channels: number; [key: string]: unknown };
}

export interface RawCaptureManifest {
  format: "raw-sensor-v1";
  simulation_id: string;
  artifact_id: string;
  sample_count: number;
  total_bytes: number;
  samples: RawCaptureSample[];
}

export interface HealthStatus {
  status: string;
  service: string;
}

/** Errors presented by the dashboard never include a response body or traceback. */
export class ApiError extends Error {
  readonly status: number;
  readonly issues: ApiIssue[];

  constructor(message: string, status = 0, issues: ApiIssue[] = []) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.issues = issues;
  }
}

function normalizeBaseUrl(value: string): string {
  const input = value.trim();
  if (input.startsWith("/") && !input.startsWith("//")) {
    if (!/^\/[A-Za-z0-9._~\/-]*$/.test(input) || input.split("/").some(segment => segment === "." || segment === "..")) {
      throw new Error("VITE_API_URL must be an HTTP(S) URL or a root-relative API path without query, fragment, or traversal segments.");
    }
    return input.replace(/\/+$/, "") || "/";
  }
  let parsed: URL;
  try {
    parsed = new URL(input);
  } catch {
    throw new Error("VITE_API_URL must be an absolute HTTP or HTTPS URL.");
  }
  if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error("VITE_API_URL must be an absolute HTTP or HTTPS URL without credentials, a query, or a fragment.");
  }
  return parsed.toString().replace(/\/+$/, "");
}

const configuredUrl = import.meta.env?.VITE_API_URL?.trim() || "http://localhost:8000";
export const API_BASE_URL = normalizeBaseUrl(configuredUrl);

function fallbackMessage(status: number): string {
  if (status === 404) return "The requested resource was not found.";
  if (status === 409) return "The request conflicts with the current saved state. Refresh and try again.";
  if (status === 422) return "The recipe contains invalid values. Check the highlighted fields.";
  if (status === 429) return "The simulation queue is full. Try again after a run finishes.";
  if (status === 503) return "The Poltergeist service is temporarily unavailable. Try again shortly.";
  return status >= 500 ? "The Poltergeist API could not complete this request." : `The request failed (HTTP ${status}).`;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function safeMessage(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  // A misconfigured proxy may send HTML, and unexpected server errors may send
  // diagnostic output. Neither is useful as a field error in the dashboard.
  if (/<\/?[a-z][^>]*>|<!doctype|traceback|stack trace|\bat\s+[\w.$]+\s*\(|\bFile\s+["']/i.test(value)) return undefined;
  // A domain validator can wrap another Pydantic error inside its message.
  // Keep its field names and reasons, without echoing submitted input values
  // or linking the user to implementation diagnostics.
  const normalized = value
    .replace(/^\s*Value error,\s*/i, "")
    .replace(/\s*\[(?:type|input_value|input_type)=[^\r\n]*\]/g, "")
    .replace(/\bFor further information visit\s+https?:\/\/errors\.pydantic\.dev\/\S+/gi, "")
    .replace(/https?:\/\/errors\.pydantic\.dev\/\S+/gi, "")
    .replace(/\d+\s+validation errors?\s+for\s+[^\r\n]+/gi, "")
    .replace(/\r?\n([A-Za-z_][\w.[\]-]*)\r?\n[ \t]+/g, "\n$1: ");
  const lines = normalized.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  const message = lines.join("; ").replace(/:; /g, ": ")
    .replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim();
  return message && message.length <= 400 ? message : undefined;
}

function issuePath(value: unknown): string {
  if (!Array.isArray(value)) return "";
  const parts = value.filter((part): part is string | number => typeof part === "string" || (typeof part === "number" && Number.isInteger(part)));
  if (parts[0] === "body") parts.shift();
  return parts.reduce<string>((path, part) => typeof part === "number" ? `${path}[${part}]` : `${path}${path ? "." : ""}${part}`, "");
}

function responseError(status: number, payload: unknown): ApiError {
  const detail = isObject(payload) ? payload.detail : undefined;
  const issues: ApiIssue[] = [];
  if (Array.isArray(detail)) {
    for (const issue of detail.slice(0, 100)) {
      if (!isObject(issue)) continue;
      const message = safeMessage(issue.msg);
      if (message) issues.push({ path: issuePath(issue.loc), message });
    }
  }
  // Validation, conflict and service-unavailable details are intentionally
  // exposed by the API. Unexpected 5xx errors use a stable generic message.
  const message = status < 500 || status === 503 ? safeMessage(detail) : undefined;
  return new ApiError(message || fallbackMessage(status), status, issues);
}

/** Create an isolated client for alternate API deployments and request tests. */
export function createApiClient(baseUrl = API_BASE_URL) {
  const base = normalizeBaseUrl(baseUrl).replace(/\/+$/, "");

  async function request<T>(path: string, options: RequestOptions, valid: (value: unknown) => boolean, method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE" = "GET", body?: unknown, expectedStatus?: number): Promise<T> {
    let serialized: string | undefined;
    const hasBody = method === "POST" || method === "PUT" || method === "PATCH";
    if (hasBody) {
      try {
        serialized = JSON.stringify(body);
        if (serialized === undefined) throw new Error("Missing JSON value");
      } catch {
        throw new ApiError("The request must contain a valid JSON value.", 422);
      }
    }
    let response: Response;
    try {
      response = await fetch(`${base}${path}`, {
        method,
        headers: hasBody ? { Accept: "application/json", "Content-Type": "application/json" } : { Accept: "application/json" },
        signal: options.signal,
        ...(hasBody ? { body: serialized } : {}),
      });
    } catch (error) {
      // Preserve abort identity so callers can ignore superseded requests.
      if (options.signal?.aborted || (error instanceof Error && error.name === "AbortError")) throw error;
      throw new ApiError("Could not reach the Poltergeist API. Check that the backend is running and the API URL is correct.");
    }

    if (response.ok && expectedStatus !== undefined && response.status !== expectedStatus) {
      throw new ApiError("The Poltergeist API returned an unexpected response.", response.status);
    }
    // A successful revision-checked deletion deliberately has no JSON body.
    if (response.status === 204 && expectedStatus === 204) return undefined as T;
    let payload: unknown;
    try {
      payload = await response.json();
    } catch (error) {
      if (options.signal?.aborted || (error instanceof Error && error.name === "AbortError")) throw error;
      if (!response.ok) throw responseError(response.status, undefined);
      throw new ApiError("The Poltergeist API returned an invalid JSON response.", response.status);
    }
    if (!response.ok) throw responseError(response.status, payload);
    if (!valid(payload)) {
      throw new ApiError("The Poltergeist API returned an unexpected response.", response.status);
    }
    return payload as T;
  }

  const finiteNumber = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
  const integer = (value: unknown, minimum = 0): value is number => finiteNumber(value) && Number.isSafeInteger(value) && value >= minimum;
  const isUuid = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
  const nullableUuid = (value: unknown) => value === null || isUuid(value);
  const isTimestamp = (value: unknown) => typeof value === "string" && /^\d{4}-\d{2}-\d{2}T/.test(value) && Number.isFinite(Date.parse(value));
  const nullableTimestamp = (value: unknown) => value === null || isTimestamp(value);
  const nullableText = (value: unknown) => value === null || typeof value === "string";
  const actorTypes = new Set(["ego_vehicle", "vehicle", "lead_vehicle", "crossing_vehicle", "pedestrian"]);
  const templateTypes = new Set(["intersection", "pedestrian_crossing", "occluded_pedestrian", "sudden_braking"]);
  const isActor = (value: unknown) => isObject(value)
    && (value.id === null || typeof value.id === "string")
    && typeof value.type === "string" && actorTypes.has(value.type)
    && finiteNumber(value.speed_mps) && value.speed_mps >= 0;
  const isScenario = (value: unknown) => isObject(value)
    && typeof value.name === "string" && typeof value.description === "string" && typeof value.map === "string"
    && finiteNumber(value.seed) && Number.isInteger(value.seed) && value.seed >= 0 && value.seed <= 2 ** 31 - 1
    && finiteNumber(value.duration_s) && value.duration_s > 0
    && finiteNumber(value.fixed_delta_seconds) && value.fixed_delta_seconds > 0
    && Array.isArray(value.actors) && value.actors.length > 0 && value.actors.every(isActor)
    && Array.isArray(value.events) && value.events.every(isObject) && isObject(value.weather)
    && (value.driving_task === undefined || value.driving_task === null || isDrivingTask(value.driving_task))
    && isObject(value.parameters) && Object.values(value.parameters).every(parameter =>
      typeof parameter === "string" || typeof parameter === "boolean" || finiteNumber(parameter));
  const isHealth = (value: unknown) => isObject(value) && typeof value.status === "string" && typeof value.service === "string";
  const isParameter = (value: unknown) => isObject(value)
    && typeof value.name === "string" && value.name.trim().length > 0
    && typeof value.unit === "string" && typeof value.description === "string" && typeof value.integer === "boolean"
    && finiteNumber(value.minimum) && finiteNumber(value.maximum) && value.minimum <= value.maximum
    && finiteNumber(value.default) && value.default >= value.minimum && value.default <= value.maximum
    && (!value.integer || Number.isInteger(value.default));
  const isTemplates = (value: unknown) => Array.isArray(value)
    && value.every(item => isObject(item)
      && typeof item.template === "string" && templateTypes.has(item.template) && isScenario(item.scenario)
      && isObject(item.scenario) && item.scenario.template === item.template
      && Array.isArray(item.parameters) && item.parameters.every(isParameter)
      && new Set(item.parameters.map(parameter => parameter.name)).size === item.parameters.length)
    && new Set(value.map(item => item.template)).size === value.length;
  const isScenarioRecord = (value: unknown) => isObject(value)
    && isUuid(value.id) && integer(value.revision, 1) && isScenario(value.scenario)
    && isTimestamp(value.created_at) && isTimestamp(value.updated_at);
  const isOptions = (value: unknown) => isObject(value)
    && (value.carla_quality === undefined || value.carla_quality === null || value.carla_quality === "Low" || value.carla_quality === "Epic")
    && ["allow_map_reload", "camera", "lidar"].every(key => typeof value[key] === "boolean")
    && ["camera_height", "camera_width", "max_ticks"].every(key => integer(value[key], 1))
    && (value.camera_height as number) <= 4096 && (value.camera_width as number) <= 4096
    && finiteNumber(value.sensor_settle_s) && value.sensor_settle_s > 0
    && finiteNumber(value.sensor_timeout_s) && value.sensor_timeout_s > 0;
  const evaluationFields = [
    "braking_full_severity_mps2", "braking_weight", "clearance_warning_m", "clearance_weight",
    "collision_episode_gap_s", "hard_braking_threshold_mps2", "max_sample_gap_s", "speeding_tolerance_mps",
    "stopped_speed_mps", "traffic_rule_weight", "ttc_horizon_s", "ttc_warning_s", "ttc_weight", "violation_episode_gap_s",
  ];
  const isEvaluation = (value: unknown) => isObject(value) && value.method_version === "1.0"
    && evaluationFields.every(key => finiteNumber(value[key]) && value[key] >= 0)
    && integer(value.violations_for_full_severity, 1)
    && ["hard_braking_threshold_mps2", "max_sample_gap_s", "ttc_horizon_s", "ttc_warning_s", "clearance_warning_m", "braking_full_severity_mps2"].every(key => (value[key] as number) > 0)
    && (value.ttc_horizon_s as number) >= (value.ttc_warning_s as number)
    && (value.braking_full_severity_mps2 as number) > (value.hard_braking_threshold_mps2 as number)
    && Math.abs(["ttc_weight", "clearance_weight", "braking_weight", "traffic_rule_weight"].reduce<number>((sum, key) => sum + (value[key] as number), 0) - 100) <= 1e-9;
  const isJob = (value: unknown) => isObject(value) && typeof value.cancel_requested === "boolean"
    && (value.queue_position === null || integer(value.queue_position, 1))
    && isOptions(value.options) && isEvaluation(value.evaluation_settings)
    && (value.agent_snapshot === undefined || value.agent_snapshot === null || isAgentSnapshot(value.agent_snapshot));
  const simulationStatuses = new Set(["queued", "running", "completed", "failed", "cancelled"]);
  const isSimulation = (value: unknown) => isObject(value) && isUuid(value.id)
    && typeof value.mode === "string" && ["carla", "test"].includes(value.mode)
    && typeof value.agent === "string" && ["baseline", "custom"].includes(value.agent)
    && (value.agent_snapshot === undefined || value.agent_snapshot === null
      || (value.agent === "custom" && isAgentSnapshot(value.agent_snapshot)))
    && typeof value.status === "string" && simulationStatuses.has(value.status)
    && finiteNumber(value.progress) && value.progress >= 0 && value.progress <= 1 && isScenario(value.scenario)
    && isTimestamp(value.created_at) && nullableTimestamp(value.started_at) && nullableTimestamp(value.finished_at)
    && nullableText(value.error);
  const nonnegative = (value: unknown) => finiteNumber(value) && value >= 0;
  const bounded = (value: unknown, minimum: number, maximum: number) => finiteNumber(value) && value >= minimum && value <= maximum;
  const nullableNumber = (value: unknown) => value === null || nonnegative(value);
  const nullableCount = (value: unknown) => value === null || integer(value);
  const nonemptyText = (value: unknown) => typeof value === "string" && value.trim().length > 0;
  const isVector = (value: unknown) => isObject(value) && ["x", "y", "z"].every(key => finiteNumber(value[key]));
  const isTransform = (value: unknown) => isObject(value) && isVector(value.position)
    && isObject(value.rotation) && ["pitch_deg", "roll_deg", "yaw_deg"].every(key => finiteNumber((value.rotation as Record<string, unknown>)[key]));
  const isDrivingTask = (value: unknown) => isObject(value)
    && ["framework", "agent"].includes(value.route_owner as string) && isTransform(value.destination)
    && Array.isArray(value.route) && value.route.length <= 4096 && value.route.every(isTransform)
    && value.route.length !== 1 && (value.route_owner !== "agent" || value.route.length === 0)
    && nullableNumber(value.target_speed_mps) && nullableNumber(value.completion_speed_mps)
    && finiteNumber(value.completion_radius_m) && value.completion_radius_m > 0
    && finiteNumber(value.completion_height_tolerance_m) && value.completion_height_tolerance_m > 0
    && nonnegative(value.completion_hold_s) && typeof value.stop_on_completion === "boolean";
  const isControl = (value: unknown) => isObject(value) && bounded(value.throttle, 0, 1)
    && bounded(value.brake, 0, 1) && bounded(value.steer, -1, 1)
    && typeof value.hand_brake === "boolean" && typeof value.reverse === "boolean";
  const isIntervention = (value: unknown) => isObject(value)
    && integer(value.frame) && nonnegative(value.time_s) && nullableNumber(value.target_speed_mps)
    && ["scripted_ego_speed_target", "scripted_ego_braking"].includes(value.kind as string)
    && isControl(value.proposed_control) && isControl(value.applied_control);
  const isControlAudit = (value: unknown) => isObject(value)
    && ["scripted", "authoritative"].includes(value.mode as string)
    && ["baseline", "legacy_custom", "sdk_agent"].includes(value.controller as string)
    && ["legacy_assisted", "framework_route", "agent_planner"].includes(value.navigation as string)
    && Array.isArray(value.interventions) && value.interventions.length <= 200000 && value.interventions.every(isIntervention);
  const isAgentSensorSpec = (value: unknown) => {
    if (!isObject(value) || !nonemptyText(value.id) || !isTransform(value.mount) || !isObject(value.mount)
      || !isObject(value.mount.position)
      || !isObject(value.attributes)) return false;
    const position = value.mount.position;
    if (!["x", "y", "z"].every(axis => Math.abs(position[axis] as number) <= 10)) return false;
    const attrs = value.attributes;
    const keys = value.type === "sensor.camera.rgb" ? ["image_size_x", "image_size_y", "fov", "sensor_tick"]
      : value.type === "sensor.lidar.ray_cast" ? ["channels", "range", "points_per_second", "rotation_frequency", "upper_fov", "lower_fov", "sensor_tick"] : [];
    if (!keys.length || Object.keys(attrs).length !== keys.length || !keys.every(key => finiteNumber(attrs[key])) || attrs.sensor_tick !== 0) return false;
    if (value.type === "sensor.camera.rgb") return integer(attrs.image_size_x, 1) && attrs.image_size_x <= 1024
      && integer(attrs.image_size_y, 1) && attrs.image_size_y <= 768 && bounded(attrs.fov, 10, 140);
    return integer(attrs.channels, 1) && attrs.channels <= 128 && bounded(attrs.range, 1, 200)
      && integer(attrs.points_per_second, 1000) && attrs.points_per_second <= 500000
      && bounded(attrs.rotation_frequency, 1, 100) && bounded(attrs.lower_fov, -89, 89)
      && bounded(attrs.upper_fov, -89, 89) && (attrs.lower_fov as number) < (attrs.upper_fov as number);
  };
  const isAgentInputInfo = (value: unknown) => {
    if (!isObject(value) || !Array.isArray(value.sensors) || value.sensors.length > 4
      || !Array.isArray(value.assistance) || value.assistance.length > 64 || !value.assistance.every(nonemptyText)
      || new Set(value.assistance).size !== value.assistance.length) return false;
    if (value.observation_mode === "simulator_state") return ["carla_ground_truth", "synthetic_state"].includes(value.source as string)
      && value.ego_state_source === "simulator_state" && value.ground_truth_actor_inputs === true && value.sensors.length === 0;
    if (value.observation_mode !== "sensors" || !["carla_sensors", "deterministic_fixture"].includes(value.source as string)
      || value.ego_state_source !== "simulated_ego_kinematics" || value.ground_truth_actor_inputs !== false
      || !value.assistance.includes("ideal_ego_kinematics") || !value.sensors.length || !value.sensors.every(isAgentSensorSpec)) return false;
    const sensors = value.sensors as Record<string, unknown>[];
    if (new Set(sensors.map(spec => spec.id)).size !== sensors.length) return false;
    const rawBytes = sensors.reduce((total, spec) => {
      const attrs = spec.attributes as Record<string, number>;
      return total + (spec.type === "sensor.camera.rgb" ? attrs.image_size_x * attrs.image_size_y * 4 : Math.ceil(attrs.points_per_second * .1) * 16);
    }, 0);
    return rawBytes <= 2 * 1024 * 1024;
  };
  const consistentAgentInputs = (value: Record<string, unknown>) => {
    if (value.agent_inputs === undefined || value.agent_inputs === null) return true;
    if (!isObject(value.agent_inputs) || !isObject(value.simulation) || !isObject(value.simulation.scenario)) return false;
    const info = value.agent_inputs;
    const sources = value.simulation.mode === "carla" ? ["carla_ground_truth", "carla_sensors"] : ["synthetic_state", "deterministic_fixture"];
    if (!sources.includes(info.source as string)) return false;
    if (info.observation_mode === "sensors") {
      const task = value.simulation.scenario.driving_task;
      if (!isObject(task) || !Array.isArray(info.assistance)
        || info.assistance.includes("framework_navigation") !== (task.route_owner === "framework")) return false;
      if (value.control_audit !== undefined && value.control_audit !== null
        && (!isObject(value.control_audit) || value.control_audit.mode !== "authoritative")) return false;
    }
    return true;
  };
  const isDrivingOutcome = (value: unknown) => isObject(value)
    && ["reached", "timed_out", "cancelled", "failed", "unavailable"].includes(value.status as string)
    && ["framework", "agent"].includes(value.route_owner as string)
    && ["scripted", "authoritative"].includes(value.control_mode as string) && isTransform(value.destination)
    && ["goal_reached", "duration_elapsed", "cancelled", "execution_failed"].includes(value.termination_reason as string)
    && ["target_speed_mps", "initial_speed_mps", "final_distance_m", "final_height_error_m", "reached_at_s", "route_length_m"].every(key => nullableNumber(value[key]))
    && nullableNumber(value.route_progress_m)
    && ((value.status === "reached") === (value.reached_at_s !== null))
    && ((value.route_progress_m === null) === (value.route_length_m === null))
    && (value.route_progress_m === null || (value.route_progress_m as number) <= (value.route_length_m as number) + 1e-6)
    && Array.isArray(value.resolved_route) && value.resolved_route.length <= 4096 && value.resolved_route.every(isTransform)
    && (value.route_owner !== "agent" || (value.resolved_route.length === 0 && value.route_progress_m === null))
    && Array.isArray(value.interventions) && value.interventions.length <= 200000 && value.interventions.every(isIntervention);
  const isState = (value: unknown) => isObject(value) && nonemptyText(value.actor_id)
    && (value.transform === null || isTransform(value.transform))
    && ["velocity_mps", "acceleration_mps2"].every(key => value[key] === null || isVector(value[key]))
    && (value.bounding_box_extent === null || (isVector(value.bounding_box_extent)
      && isObject(value.bounding_box_extent) && ["x", "y", "z"].every(key => (value.bounding_box_extent as Record<string, number>)[key] > 0)))
    && (value.bounding_box_transform === null || isTransform(value.bounding_box_transform))
    && (value.speed_limit_mps === null || (finiteNumber(value.speed_limit_mps) && value.speed_limit_mps > 0));
  const rules = new Set(["red_light", "stop_sign", "speeding", "wrong_way", "sidewalk"]);
  const isRule = (value: unknown) => typeof value === "string" && rules.has(value);
  const isFrame = (value: unknown) => {
    if (!isObject(value) || !integer(value.frame) || !nonnegative(value.time_s) || !isState(value.ego)
      || !(value.actors === null || (Array.isArray(value.actors) && value.actors.every(isState)))
      || !isObject(value.control) || !bounded(value.control.throttle, 0, 1) || !bounded(value.control.brake, 0, 1)
      || !bounded(value.control.steer, -1, 1) || typeof value.control.hand_brake !== "boolean" || typeof value.control.reverse !== "boolean"
      || !(value.collisions === null || (Array.isArray(value.collisions) && value.collisions.every(event => isObject(event)
        && nonemptyText(event.other_actor_id) && nonnegative(event.impulse_n_s) && nullableText(event.event_id))))
      || !(value.traffic_violations === null || (Array.isArray(value.traffic_violations) && value.traffic_violations.every(event => isObject(event)
        && isRule(event.rule) && nonemptyText(event.actor_id) && typeof event.details === "string" && nullableText(event.source_id) && nullableText(event.event_id))))
      || !(value.rule_checks === null || (Array.isArray(value.rule_checks) && value.rule_checks.every(isRule)
        && new Set(value.rule_checks).size === value.rule_checks.length))) return false;
    const ego = value.ego as Record<string, unknown>;
    const ids = [ego.actor_id, ...(value.actors as Record<string, unknown>[] | null || []).map(actor => actor.actor_id)];
    return new Set(ids).size === ids.length && (value.collisions as Record<string, unknown>[] | null || []).every(event => event.other_actor_id !== ego.actor_id);
  };
  const frameCoverage = ["collision_frames", "traffic_rule_frames", "braking_frames", "clearance_frames", "ttc_frames"];
  const isCoverage = (value: unknown) => isObject(value) && integer(value.total_frames)
    && [...frameCoverage, "actor_pairs", "clearance_pairs", "ttc_pairs"].every(key => integer(value[key]))
    && frameCoverage.every(key => (value[key] as number) <= (value.total_frames as number))
    && (value.ttc_pairs as number) <= (value.clearance_pairs as number) && (value.clearance_pairs as number) <= (value.actor_pairs as number)
    && typeof value.has_gaps === "boolean";
  const isMetrics = (value: unknown) => isObject(value)
    && ["collision_count", "traffic_violation_count", "hard_braking_count"].every(key => nullableCount(value[key]))
    && ["min_clearance_m", "min_ttc_s", "max_deceleration_mps2", "max_stopping_distance_m", "max_stopping_time_s"].every(key => nullableNumber(value[key]))
    && (value.severity === null || bounded(value.severity, 0, 100)) && isCoverage(value.coverage)
    && (value.evaluation_settings === null || isEvaluation(value.evaluation_settings));
  const isArtifact = (value: unknown) => isObject(value) && typeof value.kind === "string" && ["telemetry", "recording", "report", "decisions"].includes(value.kind)
    && nonemptyText(value.path) && typeof value.path === "string" && !value.path.replace(/\\/g, "/").startsWith("/")
    && !value.path.includes(":") && !value.path.replace(/\\/g, "/").split("/").includes("..") && nonemptyText(value.media_type);
  const isRuntime = (value: unknown) => isObject(value) && (value.adapter === "test" || value.adapter === "carla")
    && nullableText(value.server_version) && nullableText(value.client_version) && nonemptyText(value.map_name)
    && finiteNumber(value.fixed_delta_seconds) && value.fixed_delta_seconds > 0 && integer(value.seed) && value.seed <= 2 ** 31 - 1
    && integer(value.sensor_samples_count) && isObject(value.resolved_spawns)
    && Object.entries(value.resolved_spawns).every(([key, transform]) => nonemptyText(key) && isTransform(transform));
  const isJsonValue = (value: unknown): boolean => value === null || typeof value === "string" || typeof value === "boolean"
    || finiteNumber(value) || (Array.isArray(value) ? value.every(isJsonValue) : isObject(value) && Object.values(value).every(isJsonValue));
  const canonicalJson = (value: unknown): string => JSON.stringify(value, (_key, item) => isObject(item)
    ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item);
  const hash = (value: unknown) => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
  const isFingerprint = (value: unknown) => isObject(value) && nonemptyText(value.path) && hash(value.sha256) && integer(value.size_bytes);
  const fingerprintList = (value: unknown, max: number) => Array.isArray(value) && value.length <= max
    && value.every(isFingerprint) && value.every((item, i) => i === 0 || value[i - 1].path < item.path);
  const isAgentSnapshot = (value: unknown) => {
    if (!isObject(value) || !isUuid(value.registration_id) || !nonemptyText(value.package_path)
      || !nonemptyText(value.python_executable) || !hash(value.python_sha256) || !integer(value.python_size_bytes)
      || !hash(value.package_sha256) || !["simulator_state", "sensors"].includes(value.observation_mode as string)
      || !["authoritative", "scripted"].includes(value.agent_control_mode as string)
      || !isObject(value.configuration) || !isJsonValue(value.configuration)
      || !Array.isArray(value.files) || !fingerprintList(value.files, 20000) || value.files.length === 0
      || !fingerprintList(value.model_files, 256) || !fingerprintList(value.runtime_files, 20000)
      || !isObject(value.process_options) || !isObject(value.manifest)) return false;
    const timeouts = value.process_options;
    const manifest = value.manifest;
    return ["load_timeout_s", "reset_timeout_s", "step_timeout_s", "close_timeout_s", "terminate_timeout_s"].every(key => finiteNumber(timeouts[key]) && timeouts[key] > 0)
      && integer(timeouts.max_message_bytes, 1) && timeouts.max_message_bytes <= 64 * 1024 * 1024
      && ["1.0", "1.1", "1.2", "1.3"].includes(manifest.protocol_version as string)
      && typeof manifest.agent_id === "string" && /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(manifest.agent_id)
      && nonemptyText(manifest.name) && nonemptyText(manifest.version) && nonemptyText(manifest.entry_point)
      && manifest.observation_mode === value.observation_mode && manifest.control_mode === "vehicle_control"
      && ["provided_route", "agent_planner"].includes(manifest.navigation_mode as string)
      && canonicalJson(manifest.configuration) === canonicalJson(value.configuration)
      && Array.isArray(manifest.required_features) && manifest.required_features.every(item => item === "structured_scene")
      && Array.isArray(manifest.assets) && manifest.assets.every(nonemptyText) && nullableText(manifest.requirements_file)
      && Array.isArray(manifest.sensors) && (value.observation_mode === "sensors"
        ? manifest.protocol_version === "1.3" && manifest.sensors.length > 0 && manifest.sensors.length <= 4 && manifest.sensors.every(isAgentSensorSpec)
        : manifest.sensors.length === 0);
  };
  const isRegisteredAgent = (value: unknown) => isObject(value) && isUuid(value.id) && typeof value.active === "boolean"
    && isTimestamp(value.created_at) && isAgentSnapshot(value.snapshot) && isObject(value.snapshot) && value.snapshot.registration_id === value.id;
  const isReadiness = (value: unknown) => isObject(value) && isUuid(value.agent_id) && typeof value.ready === "boolean"
    && value.inspection === "static_only" && value.inference_checked === false && isTimestamp(value.checked_at)
    && Array.isArray(value.issues) && value.issues.every(nonemptyText)
    && Array.isArray(value.limitations) && value.limitations.every(nonemptyText);
  const consistentRegisteredResult = (value: Record<string, unknown>) => {
    if (!isObject(value.simulation) || value.simulation.agent_snapshot == null) return true;
    const snapshot = value.simulation.agent_snapshot;
    if (!isObject(snapshot) || !isObject(snapshot.manifest)) return false;
    if (value.control_audit != null && (!isObject(value.control_audit)
      || value.control_audit.controller !== "sdk_agent" || value.control_audit.mode !== snapshot.agent_control_mode)) return false;
    return value.agent_inputs == null || (isObject(value.agent_inputs)
      && value.agent_inputs.observation_mode === snapshot.observation_mode
      && canonicalJson(value.agent_inputs.sensors) === canonicalJson(snapshot.manifest.sensors));
  };
  const isResult = (value: unknown) => isObject(value) && isSimulation(value.simulation)
    && isObject(value.simulation) && ["completed", "failed", "cancelled"].includes(value.simulation.status as string)
    && isMetrics(value.metrics) && nonnegative(value.measured_duration_s)
    && integer(value.telemetry_frames) && Array.isArray(value.artifacts) && value.artifacts.every(isArtifact)
    && (value.driving_outcome === undefined || value.driving_outcome === null || isDrivingOutcome(value.driving_outcome))
    && (value.control_audit === undefined || value.control_audit === null || isControlAudit(value.control_audit))
    && (value.agent_inputs === undefined || value.agent_inputs === null || isAgentInputInfo(value.agent_inputs))
    && consistentAgentInputs(value)
    && consistentRegisteredResult(value)
    && (value.runtime === null || (isRuntime(value.runtime) && isObject(value.runtime) && value.runtime.adapter === value.simulation.mode));
  const isSimulationRecord = (value: unknown) => isObject(value) && isSimulation(value.simulation)
    && nullableUuid(value.scenario_id) && integer(value.revision, 1)
    && isTimestamp(value.created_at) && isTimestamp(value.updated_at)
    && (value.job === null || (isJob(value.job) && isObject(value.job) && isObject(value.simulation)
      && canonicalJson(value.job.agent_snapshot ?? null) === canonicalJson(value.simulation.agent_snapshot ?? null)))
    && (value.result === null || (isResult(value.result)
      && isObject(value.result) && isObject(value.result.simulation) && isObject(value.simulation)
      && value.result.simulation.id === value.simulation.id && value.result.simulation.mode === value.simulation.mode
      && value.result.simulation.status === value.simulation.status
      && canonicalJson(value.result.simulation.agent_snapshot ?? null) === canonicalJson(value.simulation.agent_snapshot ?? null)));
  const isQueue = (value: unknown) => isObject(value)
    && ["started", "accepting", "worker_alive"].every(key => typeof value[key] === "boolean")
    && integer(value.pending) && nullableUuid(value.active_id) && nullableText(value.error);
  const isProgress = (value: unknown) => isObject(value) && isUuid(value.simulation_id)
    && typeof value.status === "string" && simulationStatuses.has(value.status) && bounded(value.progress, 0, 1)
    && integer(value.revision, 1) && nullableTimestamp(value.started_at) && nullableTimestamp(value.finished_at)
    && typeof value.cancel_requested === "boolean" && (value.queue_position === null || integer(value.queue_position, 1)) && nullableText(value.error);
  const isTelemetry = (value: unknown) => isObject(value) && integer(value.total) && integer(value.offset)
    && integer(value.limit, 1) && value.limit <= 1000 && Array.isArray(value.frames) && value.frames.length <= value.limit
    && (value.frames.length === 0 || value.offset + value.frames.length <= value.total) && value.frames.every(isFrame)
    && value.frames.every((frame, index, frames) => index === 0 || (frame.frame > frames[index - 1].frame && frame.time_s > frames[index - 1].time_s));
  const failureKinds = new Set(["collision", "near_miss", "hard_braking", "traffic_violation"]);
  const isDecisionPage = (value: unknown, id: string) => {
    if (!isObject(value) || !sameId(value.simulation_id, id) || typeof value.available !== "boolean"
      || !["unavailable", "empty", "partial", "complete"].includes(value.coverage as string)
      || !integer(value.total) || !integer(value.applied) || !integer(value.transitions)
      || value.applied > value.transitions || value.applied > value.total || value.total > value.applied + 1
      || !integer(value.offset) || !integer(value.limit, 1) || value.limit > 100
      || !(value.recording_artifact_index === null || integer(value.recording_artifact_index))
      || !Array.isArray(value.records) || value.records.length !== Math.min(value.limit, Math.max(0, value.total - value.offset))
      || !Array.isArray(value.telemetry) || !value.telemetry.every(isFrame)
      || !(value.reset === null || (isObject(value.reset) && ["1.0", "1.1", "1.2", "1.3"].includes(value.reset.protocol_version as string)
        && integer(value.reset.seed) && nonemptyText(value.reset.map_name) && nonemptyText(value.reset.ego_actor_id)
        && finiteNumber(value.reset.fixed_delta_seconds) && value.reset.fixed_delta_seconds > 0 && value.reset.fixed_delta_seconds <= .1
        && Array.isArray(value.reset.route) && value.reset.route.every(isTransform) && Array.isArray(value.reset.sensors) && value.reset.sensors.every(isAgentSensorSpec)
        && (value.reset.destination === null || isTransform(value.reset.destination))
        && (value.reset.driving_task === null || isDrivingTask(value.reset.driving_task))))) return false;
    if (!value.available) return value.coverage === "unavailable" && value.total === 0 && value.applied === 0
      && value.records.length === 0 && value.telemetry.length === 0 && value.reset === null && value.recording_artifact_index === null;
    if (value.coverage === "unavailable" || (value.coverage === "empty") !== (value.total === 0 && value.transitions === 0)
      || (value.coverage === "complete") !== (value.total > 0 && value.total === value.applied && value.applied === value.transitions)
      || value.telemetry.length < value.records.length || value.telemetry.length > value.records.length + 1) return false;
    const measured = value.telemetry;
    const pageOffset = value.offset;
    const pageTotal = value.total;
    return value.records.every((record, index) => {
      if (!isObject(record) || !integer(record.frame) || !nonnegative(record.time_s)
        || !["scripted", "authoritative"].includes(record.control_mode as string)
        || !isObject(record.observation) || record.observation.frame !== record.frame || record.observation.time_s !== record.time_s
        || !["1.0", "1.1", "1.2", "1.3"].includes(record.observation.protocol_version as string)
        || !finiteNumber(record.observation.dt_s) || record.observation.dt_s <= 0
        || !isState(record.observation.ego) || !Array.isArray(record.observation.sensors) || record.observation.sensors.length
        || !(record.observation.actors === null || (Array.isArray(record.observation.actors) && record.observation.actors.every(isState)))
        || !Array.isArray(record.sensors) || record.sensors.length > 4
        || !Array.isArray(record.interventions) || !record.interventions.every(kind => ["scripted_ego_speed_target", "scripted_ego_braking"].includes(kind as string))
        || !["worker_step_wall_time_s", "round_trip_wall_time_s", "full_step_wall_time_s"].every(key => nullableNumber(record[key]))
        || ["worker_step_wall_time_s", "round_trip_wall_time_s", "full_step_wall_time_s"].some(key => typeof record[key] === "number" && record[key] < 0)) return false;
      const before = measured[index];
      if (!isObject(before) || before.frame !== record.frame || before.time_s !== record.time_s
        || !isObject(before.ego) || !isObject(record.observation.ego) || before.ego.actor_id !== record.observation.ego.actor_id) return false;
      const sensorMode = record.observation.observation_mode === "sensors";
      if (!sensorMode && record.observation.observation_mode !== "simulator_state") return false;
      if (sensorMode ? !record.sensors.length || record.observation.actors !== null || record.observation.scene !== null
          || record.observation.traffic_light_red !== null || value.recording_artifact_index === null
          || !["carla_sensors", "deterministic_fixture"].includes(record.observation.sensor_source as string)
        : record.sensors.length > 0) return false;
      if (!record.sensors.every(sensor => isObject(sensor) && integer(sensor.sample_index) && integer(sensor.byte_length)
        && isObject(sensor.header) && nonemptyText(sensor.header.sensor_id) && !Object.prototype.hasOwnProperty.call(sensor.header, "data_base64")
        && sensor.header.frame === record.frame && sensor.header.timestamp_s === record.time_s && hash(sensor.header.sha256)
        && (sensor.header.type === "sensor.camera.rgb" ? sensor.header.encoding === "bgra8"
          && integer(sensor.header.width, 1) && integer(sensor.header.height, 1) && sensor.header.width <= 2048 && sensor.header.height <= 2048
          && sensor.byte_length === sensor.header.width * sensor.header.height * 4 && bounded(sensor.header.fov_deg, .001, 179.999)
          : sensor.header.type === "sensor.lidar.ray_cast" && sensor.header.encoding === "float32_xyzi"
          && integer(sensor.header.channels, 1) && sensor.header.channels <= 128 && sensor.byte_length % 16 === 0))) return false;
      if (new Set(record.sensors.map(sensor => (sensor as { header: { sensor_id: string } }).header.sensor_id)).size !== record.sensors.length) return false;
      if (record.status === "unresolved") return pageOffset + index === pageTotal - 1 && record.agent_result === null
        && ["proposed_control", "applied_control", "resulting_frame", "resulting_ego", "worker_step_wall_time_s", "round_trip_wall_time_s", "full_step_wall_time_s"].every(key => record[key] === null)
        && record.interventions.length === 0;
      const after = measured[index + 1];
      if (record.status !== "applied" || !isControl(record.proposed_control) || !isControl(record.applied_control)
        || !nonnegative(record.full_step_wall_time_s) || record.resulting_frame !== record.frame + 1 || !isState(record.resulting_ego)
        || !isObject(after) || after.frame !== record.resulting_frame || canonicalJson(after.ego) !== canonicalJson(record.resulting_ego)
        || canonicalJson(after.control) !== canonicalJson(record.applied_control)
        || record.control_mode === "authoritative" && (record.interventions.length || canonicalJson(record.proposed_control) !== canonicalJson(record.applied_control))) return false;
      if (record.agent_result === null) return true;
      if (!isObject(record.agent_result) || record.agent_result.frame !== record.frame || record.agent_result.protocol_version !== record.observation.protocol_version
        || canonicalJson(record.agent_result.control) !== canonicalJson(record.proposed_control)) return false;
      const diagnostics = record.agent_result.diagnostics;
      return diagnostics === null || (isObject(diagnostics) && nullableText(diagnostics.maneuver) && nullableNumber(diagnostics.target_speed_mps)
        && ["assistance", "reason_codes", "hazard_actor_ids"].every(key => Array.isArray(diagnostics[key]) && diagnostics[key].every(nonemptyText))
        && Array.isArray(diagnostics.planned_trajectory) && diagnostics.planned_trajectory.every(isTransform) && (isObject(diagnostics.metadata) && isJsonValue(diagnostics.metadata)));
    });
  };
  const isFailure = (value: unknown) => isObject(value) && isUuid(value.id) && isUuid(value.simulation_id)
    && isScenario(value.scenario) && typeof value.kind === "string" && failureKinds.has(value.kind)
    && nonemptyText(value.summary) && bounded(value.severity, 0, 100) && (value.reproduced === null || typeof value.reproduced === "boolean") && isTimestamp(value.created_at);
  const isAnalytics = (value: unknown) => isObject(value) && Array.isArray(value.groups) && value.groups.every(group => isObject(group)
    && (group.mode === "test" || group.mode === "carla")
    && ["total_runs", "result_count", "scored_runs", "unscored_results", "collision_runs", "collision_observed_runs", "traffic_violation_runs", "traffic_observed_runs", "failure_reports"].every(key => integer(group[key]))
    && (group.mean_severity === null || bounded(group.mean_severity, 0, 100))
    && isObject(group.status_counts) && Object.entries(group.status_counts).every(([key, count]) => simulationStatuses.has(key) && integer(count))
    && isObject(group.failure_kinds) && Object.entries(group.failure_kinds).every(([key, count]) => failureKinds.has(key) && integer(count)))
    && new Set(value.groups.map(group => group.mode)).size === value.groups.length;

  function checkedId(id: string): string {
    if (!isUuid(id)) throw new ApiError("A valid resource ID is required.", 422);
    return id;
  }
  function checkedRevision(revision: number): number {
    if (!integer(revision, 1)) throw new ApiError("A positive saved revision is required.", 422);
    return revision;
  }
  function paginationQuery({ limit, offset }: ScenarioPagination): string {
    const query = new URLSearchParams();
    if (limit !== undefined) {
      if (!integer(limit, 1) || limit > 1000) throw new ApiError("The scenario page size must be between 1 and 1000.", 422);
      query.set("limit", String(limit));
    }
    if (offset !== undefined) {
      if (!integer(offset)) throw new ApiError("The scenario page offset must be a nonnegative integer.", 422);
      query.set("offset", String(offset));
    }
    return query.size ? `?${query}` : "";
  }

  function checkedMode(mode: unknown): "carla" | "test" {
    if (mode !== "carla" && mode !== "test") throw new ApiError("Choose CARLA or test mode.", 422);
    return mode;
  }
  function agentFilterQuery(filters: AgentFilters): string {
    const query = new URLSearchParams(paginationQuery(filters).slice(1));
    if (filters.active !== undefined) {
      if (typeof filters.active !== "boolean") throw new ApiError("Choose an active or inactive agent filter.", 422);
      query.set("active", String(filters.active));
    }
    return query.size ? `?${query}` : "";
  }
  function filterQuery(filters: SimulationFilters | FailureFilters, kind: "simulation" | "failure"): string {
    if (!isObject(filters)) throw new ApiError("Valid list filters are required.", 422);
    const query = new URLSearchParams(paginationQuery(filters).slice(1));
    if (kind === "simulation") {
      if (filters.mode !== undefined) query.set("mode", checkedMode(filters.mode));
      if (filters.status !== undefined) {
        if (typeof filters.status !== "string" || !simulationStatuses.has(filters.status)) throw new ApiError("Choose a valid simulation status.", 422);
        query.set("status", filters.status);
      }
    } else {
      if (filters.simulation_id !== undefined) query.set("simulation_id", checkedId(filters.simulation_id as string));
      if (filters.reproduced !== undefined) {
        if (typeof filters.reproduced !== "string" || !["all", "true", "false", "unknown"].includes(filters.reproduced)) throw new ApiError("Choose a valid reproduction filter.", 422);
        query.set("reproduced", filters.reproduced);
      }
    }
    return query.size ? `?${query}` : "";
  }
  const sameId = (left: unknown, right: string) => typeof left === "string" && left.toLowerCase() === right.toLowerCase();
  const recordFor = (id: string) => (value: unknown) => isSimulationRecord(value) && isObject(value)
    && isObject(value.simulation) && sameId(value.simulation.id, id);
  function checkedIndex(index: number): number {
    if (!integer(index)) throw new ApiError("A nonnegative artifact index is required.", 422);
    return index;
  }
  function artifactPath(id: string, index: number): string {
    return `/simulations/${checkedId(id)}/artifacts/${checkedIndex(index)}`;
  }
  function samplePath(id: string, artifactIndex: number, sampleIndex: number): string {
    return `${artifactPath(id, artifactIndex)}/samples/${checkedIndex(sampleIndex)}`;
  }
  const maxSampleBytes = 64 * 1024 * 1024;
  const isManifest = (value: unknown, id: string) => {
    if (!isObject(value) || value.format !== "raw-sensor-v1" || !sameId(value.simulation_id, id) || !isUuid(value.artifact_id)
      || !integer(value.sample_count) || !integer(value.total_bytes) || !Array.isArray(value.samples) || value.samples.length !== value.sample_count) return false;
    const last = new Map<string, { frame: number; timestamp: number }>();
    let total = 0;
    for (const [index, sample] of value.samples.entries()) {
      if (!isObject(sample) || (sample.kind !== "camera" && sample.kind !== "lidar") || !integer(sample.frame)
        || !nonnegative(sample.timestamp) || !integer(sample.byte_length) || sample.byte_length > maxSampleBytes
        || sample.file !== `${String(index).padStart(8, "0")}-${sample.kind}.bin`
        || typeof sample.sha256 !== "string" || !/^[0-9a-f]{64}$/.test(sample.sha256) || !isObject(sample.metadata)
        || new TextEncoder().encode(JSON.stringify(sample.metadata)).length > 16 * 1024) return false;
      if (sample.kind === "camera") {
        if (sample.metadata.encoding !== "bgra8" || !integer(sample.metadata.width, 1) || !integer(sample.metadata.height, 1)
          || sample.metadata.width > 4096 || sample.metadata.height > 4096 || sample.byte_length !== sample.metadata.width * sample.metadata.height * 4) return false;
      } else if (sample.metadata.encoding !== "float32_xyzi" || !integer(sample.metadata.channels, 1)
        || sample.metadata.channels > 4096 || sample.byte_length % 16 !== 0) return false;
      const sensorId = sample.metadata.agent_sensor_id;
      if (sensorId !== undefined && !nonemptyText(sensorId)) return false;
      const clock = JSON.stringify([sample.kind, sensorId ?? null]);
      const previous = last.get(clock);
      if (previous && (sample.frame <= previous.frame || (sample.timestamp as number) <= previous.timestamp)) return false;
      last.set(clock, { frame: sample.frame as number, timestamp: sample.timestamp as number });
      total += sample.byte_length;
      if (!Number.isSafeInteger(total)) return false;
    }
    return total === value.total_bytes;
  };

  async function sampleBytes(path: string, options: RequestOptions): Promise<ArrayBuffer> {
    let response: Response;
    try {
      response = await fetch(`${base}${path}`, { method: "GET", headers: { Accept: "application/octet-stream" }, signal: options.signal });
    } catch (error) {
      if (options.signal?.aborted || (error instanceof Error && error.name === "AbortError")) throw error;
      throw new ApiError("Could not reach the Poltergeist API. Check that the backend is running and the API URL is correct.");
    }
    if (!response.ok) {
      let payload: unknown;
      try { payload = await response.json(); } catch (error) {
        if (options.signal?.aborted || (error instanceof Error && error.name === "AbortError")) throw error;
      }
      throw responseError(response.status, payload);
    }
    if (response.status !== 200 || response.headers.get("Content-Type")?.split(";")[0].trim().toLowerCase() !== "application/octet-stream") {
      throw new ApiError("The Poltergeist API returned an unexpected sensor response.", response.status);
    }
    const lengthHeader = response.headers.get("Content-Length");
    if (lengthHeader !== null && (!/^\d+$/.test(lengthHeader) || !integer(Number(lengthHeader)) || Number(lengthHeader) > maxSampleBytes)) {
      await response.body?.cancel();
      throw new ApiError("The sensor sample exceeds the supported 64 MiB size.", response.status);
    }
    const reader = response.body?.getReader();
    const chunks: Uint8Array[] = [];
    let length = 0;
    try {
      if (reader) for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        length += value.byteLength;
        if (length > maxSampleBytes) {
          await reader.cancel();
          throw new ApiError("The sensor sample exceeds the supported 64 MiB size.", response.status);
        }
        chunks.push(value);
      }
    } catch (error) {
      if (options.signal?.aborted || (error instanceof Error && error.name === "AbortError") || error instanceof ApiError) throw error;
      throw new ApiError("The sensor sample could not be downloaded. Try again.", response.status);
    } finally { reader?.releaseLock(); }
    if (lengthHeader !== null && length !== Number(lengthHeader)) throw new ApiError("The sensor sample download was incomplete.", response.status);
    const bytes = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    return bytes.buffer;
  }

  function reportUrl(id: string, format: "json" | "csv" | "html", includeTelemetry = false, includeBehavior = false) {
    if (!["json", "csv", "html"].includes(format) || typeof includeTelemetry !== "boolean" || typeof includeBehavior !== "boolean" || includeTelemetry && format !== "json") throw new ApiError("Choose a supported report format. Telemetry is available in JSON reports only.", 422);
    return `${base}/reports/simulations/${checkedId(id)}?format=${format}${includeTelemetry ? "&include_telemetry=true" : ""}${includeBehavior ? "&include_behavior=true" : ""}`;
  }

  async function getReport(id: string, format: "json" | "csv" | "html", includeTelemetry = false, includeBehavior = true, options: RequestOptions = {}): Promise<Blob> {
    const url = reportUrl(id, format, includeTelemetry, includeBehavior);
    const mediaType = { json: "application/json", csv: "text/csv", html: "text/html" }[format];
    let response: Response;
    try { response = await fetch(url, { headers: { Accept: mediaType }, signal: options.signal }); }
    catch (error) {
      if (options.signal?.aborted || error instanceof Error && error.name === "AbortError") throw error;
      throw new ApiError("Could not reach the report service. Check that the backend is running.");
    }
    if (!response.ok) {
      let payload: unknown;
      try { payload = await response.json(); } catch (error) { if (options.signal?.aborted || error instanceof Error && error.name === "AbortError") throw error; }
      throw responseError(response.status, payload);
    }
    if (response.status !== 200 || response.headers.get("Content-Type")?.split(";")[0].trim().toLowerCase() !== mediaType) {
      await response.body?.cancel(); throw new ApiError("The report service returned an unexpected file type.");
    }
    if (includeBehavior && response.headers.get("X-Poltergeist-Report-Schema") !== "poltergeist-run-report-v2") {
      await response.body?.cancel(); throw new ApiError("The report service does not support behavior exports. Restart the backend after updating; check that any proxy forwards the report schema header.");
    }
    try {
      const blob = await response.blob();
      const length = response.headers.get("Content-Length"), encoding = response.headers.get("Content-Encoding");
      if (!blob.size || (!encoding || encoding === "identity") && length !== null && (!/^\d+$/.test(length) || !Number.isSafeInteger(Number(length)) || blob.size !== Number(length))) throw new ApiError("The report transfer was empty or incomplete. Retry the download.");
      return blob;
    } catch (error) {
      if (options.signal?.aborted || error instanceof ApiError || error instanceof Error && error.name === "AbortError") throw error;
      throw new ApiError("Could not download the complete report. Retry the download.");
    }
  }

  const experiments = experimentContracts({ scenario: isScenario, options: isOptions, evaluation: isEvaluation, snapshot: isAgentSnapshot, failure: isFailure });
  const lab = labContracts({ scenario: isScenario, snapshot: isAgentSnapshot }, experiments);
  const validLabResult = (value: unknown): value is LabResult => {
    if (!isObject(value)) return false;
    switch (value.kind) {
      case "gallery": return lab.gallery(value.items);
      case "suites": case "plans": case "feedbacks": return Array.isArray(value.items) && value.items.length <= 20
        && isTimestamp(value.snapshot_at) && (value.next_offset === null || integer(value.next_offset))
        && value.items.every(v => value.kind === "suites" ? lab.suite(v) : value.kind === "plans" ? lab.plan(v) : lab.feedback(v));
      case "suite_runs": return Array.isArray(value.items) && value.items.length <= 20 && isTimestamp(value.snapshot_at)
        && (value.next_offset === null || integer(value.next_offset)) && value.items.every(v => experiments.record(v) && v.request.kind === "suite");
      case "suite": return lab.suite(value.suite);
      case "plan": return lab.plan(value.plan);
      case "operation": return experiments.record(value.operation);
      case "discovery": return isObject(value.report) && isObject(value.report.plan) && isUuid(value.report.plan.id) && lab.discovery(value.report, value.report.plan.id);
      case "suite_report": return isObject(value.report) && isObject(value.report.evidence) && isObject(value.report.evidence.operation) && isUuid(value.report.evidence.operation.id) && lab.suiteReport(value.report, value.report.evidence.operation.id);
      case "run": return isSimulationRecord(value.record);
      case "highlights": return isObject(value.highlights) && isUuid(value.highlights.simulation_id) && lab.highlights(value.highlights, value.highlights.simulation_id);
      case "frame": return isUuid(value.simulation_id) && isObject(value.frame) && integer(value.frame.frame) && nonnegative(value.frame.time_s)
        && (value.frame.speed_mps === null || nonnegative(value.frame.speed_mps)) && isControl(value.frame.control)
        && (value.frame.collisions === null || integer(value.frame.collisions)) && (value.frame.traffic_violations === null || integer(value.frame.traffic_violations));
      case "feedback": return lab.feedback(value.feedback);
      case "deleted": return isUuid(value.id);
      default: return false;
    }
  };
  return {
    validateLabResult: validLabResult,
    getGallery: (options: RequestOptions = {}) => request<GalleryScenario[]>("/experimentation/gallery", options, lab.gallery),
    listSuites: (pagination: ScenarioPagination = {}, options: RequestOptions = {}) => request<SavedSuite[]>(`/experimentation/suites${paginationQuery(pagination)}`, options, v => Array.isArray(v) && v.every(row => lab.suite(row))),
    getSuite: (id: string, options: RequestOptions = {}) => request<SavedSuite>(`/experimentation/suites/${checkedId(id)}`, options, v => lab.suite(v, id)),
    saveSuite: (input: SuiteSave, options: RequestOptions = {}) => request<SavedSuite>("/experimentation/suites", options, v => lab.suite(v), "POST", input, 201),
    updateSuite: (id: string, input: SuiteSave, revision: number, options: RequestOptions = {}) => request<SavedSuite>(`/experimentation/suites/${checkedId(id)}`, options, v => lab.suite(v, id), "PUT", { ...input, expected_revision: checkedRevision(revision) }),
    deleteSuite: (id: string, revision: number, options: RequestOptions = {}) => request<void>(`/experimentation/suites/${checkedId(id)}?expected_revision=${checkedRevision(revision)}`, options, () => true, "DELETE", undefined, 204),
    runSuite: (id: string, revision: number, requestId: string, options: RequestOptions = {}) => request<OptimizationRecord>(`/experimentation/suites/${checkedId(id)}/runs`, options,
      v => experiments.record(v) && v.request.kind === "suite" && v.request.suite_id === id && v.request.suite_revision === revision && v.id === requestId,
      "POST", { expected_revision: checkedRevision(revision), request_id: checkedId(requestId) }, 202),
    listSuiteRuns: (id: string, pagination: ScenarioPagination = {}, options: RequestOptions = {}) => request<OptimizationRecord[]>(`/experimentation/suites/${checkedId(id)}/runs${paginationQuery(pagination)}`, options,
      v => Array.isArray(v) && v.every(row => experiments.record(row) && row.request.kind === "suite" && row.request.suite_id === id)),
    getSuiteReport: (id: string, options: RequestOptions = {}) => request<SuiteReport>(`/experimentation/suite-runs/${checkedId(id)}`, options, v => lab.suiteReport(v, id)),
    suiteExportUrl: (id: string) => `${base}/experimentation/suites/${checkedId(id)}?download=true`,
    suiteReportExportUrl: (id: string) => `${base}/experimentation/suite-runs/${checkedId(id)}?download=true`,
    proposeDiscovery: (input: DiscoveryProposal, options: RequestOptions = {}) => request<DiscoveryPlan>("/experimentation/discovery/plans", options, v => lab.plan(v), "POST", input, 201),
    listDiscoveryPlans: (pagination: ScenarioPagination = {}, options: RequestOptions = {}) => request<DiscoveryPlan[]>(`/experimentation/discovery/plans${paginationQuery(pagination)}`, options, v => Array.isArray(v) && v.every(row => lab.plan(row))),
    getDiscoveryPlan: (id: string, options: RequestOptions = {}) => request<DiscoveryPlan>(`/experimentation/discovery/plans/${checkedId(id)}`, options, v => lab.plan(v, id)),
    approveDiscovery: (plan: DiscoveryPlan, options: RequestOptions = {}) => request<OptimizationRecord>(`/experimentation/discovery/plans/${checkedId(plan.id)}/approve`, options,
      v => experiments.record(v) && v.request.kind === "discover" && v.request.plan_id === plan.id && v.request.plan_digest === plan.digest,
      "POST", { expected_revision: checkedRevision(plan.revision), digest: plan.digest, approve: true }, 202),
    getDiscoveryReport: (id: string, options: RequestOptions = {}) => request<DiscoveryReport>(`/experimentation/discovery/plans/${checkedId(id)}/report`, options, v => lab.discovery(v, id)),
    discoveryExportUrl: (id: string) => `${base}/experimentation/discovery/plans/${checkedId(id)}/report?download=true`,
    getReplayHighlights: (id: string, options: RequestOptions = {}) => request<ReplayHighlights>(`/experimentation/highlights/${checkedId(id)}`, options, v => lab.highlights(v, id)),
    saveGuidanceFeedback: (input: GuidanceFeedback, options: RequestOptions = {}) => request<SavedFeedback>("/experimentation/feedback", options, v => lab.feedback(v) && v.feedback.simulation_id === input.simulation_id, "POST", input, 201),
    listOptimizations: (pagination: ScenarioPagination = {}, options: RequestOptions = {}) => request<OptimizationRecord[]>(`/optimizations${paginationQuery(pagination)}`, options, v => Array.isArray(v) && v.every(experiments.record)),
    createOptimization: (input: unknown, options: RequestOptions = {}) => request<OptimizationRecord>("/optimizations", options, experiments.record, "POST", input, 202),
    getOptimization: (id: string, options: RequestOptions = {}) => request<OptimizationView>(`/optimizations/${checkedId(id)}`, options, v => experiments.view(v, id)),
    cancelOptimization: (id: string, options: RequestOptions = {}) => request<OptimizationRecord>(`/optimizations/${checkedId(id)}/cancel`, options, v => experiments.record(v) && v.id.toLowerCase() === id.toLowerCase(), "POST", {}, 202),
    resumeOptimization: (id: string, revision: number, options: RequestOptions = {}) => request<OptimizationRecord>(`/optimizations/${checkedId(id)}/resume`, options, v => experiments.record(v) && v.id.toLowerCase() === id.toLowerCase(), "POST", { expected_revision: checkedRevision(revision) }, 202),
    getComparison: (id: string, options: RequestOptions = {}) => request<ComparisonReport>(`/optimizations/${checkedId(id)}/comparison`, options, v => experiments.comparison(v, id)),
    comparisonExportUrl: (id: string) => `${base}/optimizations/${checkedId(id)}/comparison?download=true`,
    health: (options: RequestOptions = {}) => request<HealthStatus>("/health", options, isHealth),
    getReadiness: (options: RequestOptions = {}) => request<ReadinessReport>("/readiness", options, value => isReadinessReport(value) && !value.carla_connection_requested),
    checkCarlaConnection: (options: RequestOptions = {}) => request<ReadinessReport>("/readiness/carla-check", options, value => isReadinessReport(value) && value.carla_connection_requested, "POST", {}),
    registerAgent: (input: AgentRegistration, options: RequestOptions = {}) => request<RegisteredAgent>("/agents", options, isRegisteredAgent, "POST", input, 201),
    listAgents: (filters: AgentFilters = {}, options: RequestOptions = {}) => request<RegisteredAgent[]>(`/agents${agentFilterQuery(filters)}`, options, value => Array.isArray(value) && value.every(isRegisteredAgent)),
    getAgent: (id: string, options: RequestOptions = {}) => request<RegisteredAgent>(`/agents/${checkedId(id)}`, options, value => isRegisteredAgent(value) && isObject(value) && sameId(value.id, id)),
    getAgentReadiness: (id: string, options: RequestOptions = {}) => request<AgentReadiness>(`/agents/${checkedId(id)}/readiness`, options, value => isReadiness(value) && isObject(value) && sameId(value.agent_id, id)),
    deactivateAgent: (id: string, options: RequestOptions = {}) => request<RegisteredAgent>(`/agents/${checkedId(id)}/deactivate`, options, value => isRegisteredAgent(value) && isObject(value) && sameId(value.id, id) && value.active === false, "POST", {}),
    listTemplates: (options: RequestOptions = {}) => request<TemplateInfo[]>("/scenarios/templates", options, isTemplates),
    listStressPresets: (options: RequestOptions = {}) => request<Scenario[]>("/scenarios/stress-presets", options,
      value => Array.isArray(value) && value.every(isScenario) && new Set(value.map(item => item.name)).size === value.length),
    generateScenario: (input: ScenarioGenerate, options: RequestOptions = {}) => request<Scenario>("/scenarios/generate", options, isScenario, "POST", input),
    validateScenario: (recipe: unknown, options: RequestOptions = {}) => request<Scenario>("/scenarios/validate", options, isScenario, "POST", recipe),
    listScenarios: async (pagination: ScenarioPagination = {}, options: RequestOptions = {}) => request<ScenarioRecord[]>(`/scenarios${paginationQuery(pagination)}`, options, value => Array.isArray(value) && value.every(isScenarioRecord)),
    getScenario: async (id: string, options: RequestOptions = {}) => request<ScenarioRecord>(`/scenarios/${checkedId(id)}`, options, isScenarioRecord),
    createScenario: (recipe: unknown, options: RequestOptions = {}) => request<ScenarioRecord>("/scenarios", options, isScenarioRecord, "POST", recipe),
    updateScenario: async (id: string, recipe: unknown, expected_revision: number, options: RequestOptions = {}) => request<ScenarioRecord>(`/scenarios/${checkedId(id)}`, options, isScenarioRecord, "PUT", { scenario: recipe, expected_revision: checkedRevision(expected_revision) }),
    deleteScenario: async (id: string, expected_revision: number, options: RequestOptions = {}) => request<void>(`/scenarios/${checkedId(id)}?expected_revision=${checkedRevision(expected_revision)}`, options, () => true, "DELETE", undefined, 204),
    getSimulationQueue: (options: RequestOptions = {}) => request<QueueStatus>("/simulations/queue", options, isQueue),
    launchSimulation: (input: SimulationLaunch, options: RequestOptions = {}) => request<SimulationRecord>("/simulations", options, isSimulationRecord, "POST", input, 202),
    listSimulations: async (filters: SimulationFilters = {}, options: RequestOptions = {}) => request<SimulationRecord[]>(`/simulations${filterQuery(filters, "simulation")}`, options, value => Array.isArray(value) && value.every(isSimulationRecord), "GET", undefined, 200),
    getSimulation: async (id: string, options: RequestOptions = {}) => request<SimulationRecord>(`/simulations/${checkedId(id)}`, options, recordFor(id), "GET", undefined, 200),
    getSimulationProgress: async (id: string, options: RequestOptions = {}) => request<SimulationProgress>(`/simulations/${checkedId(id)}/progress`, options, value => isProgress(value) && isObject(value) && sameId(value.simulation_id, id), "GET", undefined, 200),
    getSimulationResult: async (id: string, options: RequestOptions = {}) => request<SimulationResult>(`/simulations/${checkedId(id)}/result`, options, value => isResult(value) && isObject(value) && isObject(value.simulation) && sameId(value.simulation.id, id), "GET", undefined, 200),
    getBehaviorAnalysis: async (id: string, options: RequestOptions = {}) => request<BehaviorAnalysis>(`/simulations/${checkedId(id)}/analysis`, options,
      value => isBehaviorAnalysis(value, id) && isMetrics(value.stored_safety_metrics) && isEvaluation(value.braking.settings)
        && (value.driving_outcome === null || isDrivingOutcome(value.driving_outcome))
        && (value.agent_inputs === null || isAgentInputInfo(value.agent_inputs))
        && (value.agent_snapshot === null || isAgentSnapshot(value.agent_snapshot))),
    cancelSimulation: async (id: string, options: RequestOptions = {}) => request<SimulationRecord>(`/simulations/${checkedId(id)}/cancel`, options, recordFor(id), "POST", {}, 202),
    getTelemetry: async (id: string, pagination: ScenarioPagination = {}, options: RequestOptions = {}) => request<TelemetryPage>(`/simulations/${checkedId(id)}/telemetry${paginationQuery(pagination)}`, options, value => isTelemetry(value)
      && isObject(value) && value.offset === (pagination.offset ?? 0) && value.limit === (pagination.limit ?? 1000), "GET", undefined, 200),
    getDecisions: async (id: string, pagination: ScenarioPagination = {}, options: RequestOptions = {}) => {
      const query = { offset: pagination.offset ?? 0, limit: pagination.limit ?? 25 };
      if (!integer(query.limit, 1) || query.limit > 100) throw new ApiError("Choose 1–100 decisions per page.", 422);
      return request<DecisionPage>(`/simulations/${checkedId(id)}/decisions${paginationQuery(query)}`, options,
        value => isDecisionPage(value, id) && isObject(value) && value.offset === query.offset && value.limit === query.limit);
    },
    listFailures: async (filters: FailureFilters = {}, options: RequestOptions = {}) => request<FailureReport[]>(`/failures${filterQuery(filters, "failure")}`, options, value => Array.isArray(value) && value.every(isFailure), "GET", undefined, 200),
    getFailure: async (id: string, options: RequestOptions = {}) => request<FailureReport>(`/failures/${checkedId(id)}`, options, value => isFailure(value) && isObject(value) && sameId(value.id, id), "GET", undefined, 200),
    createFailure: (report: FailureReport, options: RequestOptions = {}) => request<FailureReport>("/failures", options, value => isFailure(value) && isObject(value) && sameId(value.id, report.id) && sameId(value.simulation_id, report.simulation_id), "POST", report, 201),
    updateReproduction: async (id: string, reproduced: boolean | null, options: RequestOptions = {}) => {
      checkedId(id);
      if (reproduced !== null && typeof reproduced !== "boolean") throw new ApiError("A reproduction outcome must be true, false, or unknown.", 422);
      return request<FailureReport>(`/failures/${id}/reproduction`, options, value => isFailure(value) && isObject(value) && sameId(value.id, id) && value.reproduced === reproduced, "PATCH", { reproduced }, 200);
    },
    getAnalytics: async (mode?: "carla" | "test", options: RequestOptions = {}) => request<AnalyticsSummary>(`/analytics${mode === undefined ? "" : `?mode=${checkedMode(mode)}`}`, options, value => isAnalytics(value)
      && (mode === undefined || (isObject(value) && Array.isArray(value.groups) && value.groups.length === 1 && value.groups[0].mode === mode)), "GET", undefined, 200),
    artifactUrl: (id: string, index: number) => `${base}${artifactPath(id, index)}`,
    sampleUrl: (id: string, artifactIndex: number, sampleIndex: number) => `${base}${samplePath(id, artifactIndex, sampleIndex)}`,
    reportUrl, getReport,
    getCaptureManifest: async (id: string, index: number, options: RequestOptions = {}) => request<RawCaptureManifest>(artifactPath(id, index), options, value => isManifest(value, id), "GET", undefined, 200),
    getCaptureSample: async (id: string, artifactIndex: number, sampleIndex: number, options: RequestOptions = {}) => sampleBytes(samplePath(id, artifactIndex, sampleIndex), options),
  };
}

export const api = createApiClient();
export const {
  getReadiness, checkCarlaConnection,
  registerAgent, listAgents, getAgent, getAgentReadiness, deactivateAgent,
  health, listTemplates, listStressPresets, generateScenario, validateScenario, listScenarios, getScenario,
  createScenario, updateScenario, deleteScenario, getSimulationQueue, launchSimulation,
  listSimulations, getSimulation, getSimulationProgress, getSimulationResult, getBehaviorAnalysis, cancelSimulation, getTelemetry,
  listFailures, getFailure, createFailure, updateReproduction, getAnalytics, artifactUrl, sampleUrl, reportUrl, getReport, getCaptureManifest, getCaptureSample,
} = api;
