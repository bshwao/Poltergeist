import type { DiscoveryProposal, SuiteSave } from "../types/generated";
import { DEFAULT_EVALUATION, DEFAULT_OPTIONS } from "./launch-form";

export function labNumber(raw: string, name: string, min: number, max: number, whole = true): number {
  if (!raw.trim() || !/^(?:\d+\.?\d*|\.\d+)$/.test(raw.trim())) throw new Error(`Enter ${name.toLowerCase()} as a positive decimal number.`);
  const value = Number(raw);
  if (!Number.isFinite(value) || whole && !Number.isSafeInteger(value) || value < min || value > max) throw new Error(`${name} must be ${min}–${max}${whole ? " (whole numbers)" : ""}.`);
  return value;
}
export function labObject(raw: string, name: string): Record<string, unknown> {
  let value: unknown;
  try { value = JSON.parse(raw); } catch { throw new Error(`${name} must be valid JSON.`); }
  const finite = (v: unknown): boolean => typeof v === "number" ? Number.isFinite(v) : v !== null && typeof v === "object" ? Object.values(v).every(finite) : true;
  if (value === null || typeof value !== "object" || Array.isArray(value) || !finite(value)) throw new Error(`${name} must be an object containing finite JSON values.`);
  return value as Record<string, unknown>;
}
const agentId = (value: string) => { if (value === "baseline") return null; if (!/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value)) throw new Error("Select a saved agent registration."); return value; };

export interface DiscoveryDraft {
  description: string; failure: DiscoveryProposal["goal"]["failure_kind"]; template: DiscoveryProposal["goal"]["template"];
  mode: "test" | "carla"; agent: string; seed: string; duration: string; parameters: string; ranges: string; task: string;
  executions: string; search: string; confirmations: string; required: string; candidates: string; seconds: string; simulated: string; severity: string;
}
export const discoveryDefaults = (): DiscoveryDraft => ({ description: "Find repeatable collisions while following a braking lead vehicle", failure: "collision", template: "sudden_braking",
  mode: "test", agent: "baseline", seed: "7", duration: "8", parameters: '{"brake_delay_s":0.5}', ranges: '{"ego_speed_mps":[8,25],"lead_distance_m":[5.5,35]}', task: "",
  executions: "12", search: "6", confirmations: "2", required: "2", candidates: "3", seconds: "300", simulated: "96", severity: "0" });
export function discoveryProposal(draft: DiscoveryDraft): DiscoveryProposal {
  if (!draft.description.trim() || draft.description.trim().length > 500) throw new Error("Describe an evaluation goal in 1–500 characters.");
  const budget = { max_executions: labNumber(draft.executions, "Execution budget", 3, 200), max_search_trials: labNumber(draft.search, "Search trials", 1, 100),
    confirmation_repeats: labNumber(draft.confirmations, "Confirmation repetitions", 2, 10), required_successes: labNumber(draft.required, "Required matches", 2, 10),
    max_candidates: labNumber(draft.candidates, "Candidate limit", 1, 20), max_wall_time_s: labNumber(draft.seconds, "Wall-clock budget", 1, 7200, false),
    max_simulated_time_s: labNumber(draft.simulated, "Simulated-time budget", .005, 36000, false) };
  if (budget.required_successes > budget.confirmation_repeats) throw new Error("Required matches cannot exceed confirmation repetitions.");
  if (budget.max_executions < 1 + budget.confirmation_repeats) throw new Error("Reserve an execution for a candidate and every confirmation repetition.");
  const duration = labNumber(draft.duration, "Candidate duration", .005, 120, false);
  if (duration * (1 + budget.confirmation_repeats) > budget.max_simulated_time_s + 1e-9) throw new Error("Reserve simulated time for a full candidate and its confirmations.");
  return { mode: draft.mode, options: { ...DEFAULT_OPTIONS }, evaluation_settings: { ...DEFAULT_EVALUATION }, budget,
    goal: { description: draft.description.trim(), failure_kind: draft.failure, template: draft.template,
      agent_id: agentId(draft.agent), seed: labNumber(draft.seed, "Seed", 0, 2147483647), duration_s: duration,
      parameters: labObject(draft.parameters, "Fixed parameters") as DiscoveryProposal["goal"]["parameters"], ranges: labObject(draft.ranges, "Parameter ranges") as DiscoveryProposal["goal"]["ranges"],
      driving_task: draft.task.trim() ? labObject(draft.task, "Driving task") as unknown as NonNullable<DiscoveryProposal["goal"]["driving_task"]> : null,
      minimum_severity: labNumber(draft.severity, "Minimum severity", 0, 100, false) } };
}

export interface SuiteCaseDraft { key: string; name: string; recipe: string }
export interface SuiteDraft { name: string; description: string; mode: "test" | "carla"; agents: string[]; seeds: string; repeats: string; cases: SuiteCaseDraft[]; options: string; evaluation: string; failure: "" | NonNullable<SuiteSave["definition"]["failure_kind"]>; severity: string }
export const suiteDefaults = (): SuiteDraft => ({ name: "My experiment suite", description: "", mode: "test", agents: ["baseline"], seeds: "7", repeats: "1", cases: [], options: JSON.stringify(DEFAULT_OPTIONS, null, 2), evaluation: JSON.stringify(DEFAULT_EVALUATION, null, 2), failure: "", severity: "0" });
export function suiteInput(draft: SuiteDraft): SuiteSave {
  if (!draft.name.trim() || draft.name.trim().length > 128) throw new Error("Name the suite in 1–128 characters.");
  if (!draft.cases.length || draft.cases.length > 20) throw new Error("Add 1–20 scenarios to the suite.");
  const agents = draft.agents.map(agentId), seeds = draft.seeds.split(",").map(raw => labNumber(raw, "Seed", 0, 2147483647));
  const repeats = labNumber(draft.repeats, "Repetitions", 1, 10);
  if (!agents.length || agents.length > 8 || new Set(agents).size !== agents.length || seeds.length > 20 || new Set(seeds).size !== seeds.length) throw new Error("Select 1–8 distinct agents and 1–20 distinct seeds.");
  if (draft.cases.length * agents.length * seeds.length * repeats > 200) throw new Error("A saved suite supports up to 200 executions in total.");
  if (draft.cases.some(c => !c.name.trim()) || new Set(draft.cases.map(c => c.name.trim().toLocaleLowerCase())).size !== draft.cases.length) throw new Error("Give every suite scenario a distinct name.");
  return { description: draft.description, definition: { kind: "suite", name: draft.name.trim(), mode: draft.mode, agents: agents as SuiteSave["definition"]["agents"], seeds: seeds as SuiteSave["definition"]["seeds"], repeats,
    suite_id: null, suite_revision: null, failure_kind: draft.failure || null, minimum_severity: labNumber(draft.severity, "Minimum suite severity", 0, 100, false),
    options: labObject(draft.options, "Suite simulation options") as unknown as SuiteSave["definition"]["options"], evaluation_settings: labObject(draft.evaluation, "Suite evaluation settings") as unknown as SuiteSave["definition"]["evaluation_settings"],
    cases: draft.cases.map(c => ({ name: c.name.trim(), scenario: labObject(c.recipe, `Recipe for ${c.name}`) as unknown as SuiteSave["definition"]["cases"][number]["scenario"] })) as SuiteSave["definition"]["cases"] } };
}
