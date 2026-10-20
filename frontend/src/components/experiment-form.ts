export type ExperimentKind = "compare" | "search" | "reproduce" | "minimize";
export interface ExperimentDraft {
  kind: ExperimentKind; mode: "carla" | "test"; template: string; recipe: string; agents: string[];
  seeds: string; repeats: string; trials: string; seed: string; strategy: "random" | "optuna";
  failureId: string; required: string; options: string; evaluation: string; parameters: string; ranges: string; task: string;
}
export const emptyExperiment = (): ExperimentDraft => ({ kind: "compare", mode: "test", template: "sudden_braking", recipe: "", agents: ["baseline"], seeds: "0, 1, 2", repeats: "1", trials: "10", seed: "0", strategy: "random", failureId: "", required: "", options: "{}", evaluation: "{}", parameters: "{}", ranges: "{}", task: "" });
function number(text: string, label: string, min: number, max: number) {
  if (!/^\d+$/.test(text.trim())) throw new Error(`${label} must be a whole number.`);
  const value = Number(text);
  if (!Number.isSafeInteger(value) || value < min || value > max) throw new Error(`${label} must be ${min}–${max}.`);
  return value;
}
function json(text: string, label: string): Record<string, unknown> {
  let value: unknown;
  try { value = JSON.parse(text); } catch { throw new Error(`${label} must be valid JSON.`); }
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label} must be a JSON object.`);
  function finite(v: unknown): boolean { return typeof v === "number" ? Number.isFinite(v) : v !== null && typeof v === "object" ? Object.values(v).every(finite) : true; }
  if (!finite(value)) throw new Error(`${label} must contain only finite numbers.`);
  return value as Record<string, unknown>;
}
export function experimentInput(d: ExperimentDraft) {
  if (d.kind === "reproduce" || d.kind === "minimize") {
    if (!/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(d.failureId)) throw new Error("Enter a valid source failure ID.");
    const repeats = number(d.repeats, "Repetitions", 1, d.kind === "reproduce" ? 20 : 10);
    if (d.kind === "minimize") return { kind: d.kind, failure_id: d.failureId, repeats, max_trials: number(d.trials, "Execution budget", repeats, 200) };
    return { kind: d.kind, failure_id: d.failureId, repeats, required_successes: d.required.trim() ? number(d.required, "Required matches", 1, repeats) : null };
  }
  if (!d.agents.length || new Set(d.agents).size !== d.agents.length) throw new Error("Select at least one distinct agent.");
  if (!d.agents.every(a => a === "baseline" || /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(a))) throw new Error("Select valid registered agents.");
  const execution = { mode: d.mode, options: json(d.options, "Simulation options"), evaluation_settings: json(d.evaluation, "Evaluation settings") };
  const agents = d.agents.map(a => a === "baseline" ? null : a);
  if (d.kind === "compare") {
    const seeds = d.seeds.split(",").map(s => number(s, "Seed", 0, 2147483647));
    const repeats = number(d.repeats, "Repetitions", 1, 10);
    if (seeds.length > 20 || new Set(seeds).size !== seeds.length) throw new Error("Choose 1–20 distinct seeds.");
    if (agents.length > 8 || agents.length * seeds.length * repeats > 200) throw new Error("Choose up to 8 agents and at most 200 executions in total.");
    return { kind: d.kind, ...execution, scenario: json(d.recipe, "Scenario recipe"), agents, seeds, repeats };
  }
  if (agents.length !== 1) throw new Error("Search requires exactly one agent.");
  return { kind: d.kind, ...execution, template: d.template, strategy: d.strategy, agent_id: agents[0],
    seed: number(d.seed, "Search seed", 0, 2147483647), trials: number(d.trials, "Execution budget", 1, 1000),
    parameters: json(d.parameters, "Fixed parameters"), ranges: json(d.ranges, "Search ranges"),
    driving_task: d.task.trim() ? json(d.task, "Driving task") : null };
}
