import type { Scenario, ScenarioRecord, TemplateInfo } from "../types/generated";

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value !== null && typeof value === "object") return Object.fromEntries(
    Object.entries(value).sort(([left], [right]) => left.localeCompare(right)).map(([key, item]) => [key, canonical(item)]),
  );
  return value;
}

export function sameRecipe(left: unknown, right: unknown): boolean {
  return JSON.stringify(canonical(left)) === JSON.stringify(canonical(right));
}

export function bindScenarioForm(scenario: Scenario, templates: TemplateInfo[]) {
  const template = templates.find((item) => item.template === scenario.template);
  return {
    selectedId: template?.template ?? null,
    seed: String(scenario.seed),
    duration: String(scenario.duration_s),
    values: Object.fromEntries((template?.parameters ?? []).map((parameter) => [
      parameter.name, String(scenario.parameters[parameter.name] ?? parameter.default),
    ])),
  };
}

/** A write receipt must not replace edits or a different recipe loaded meanwhile. */
export function reconcileSave(
  current: ScenarioRecord | null, source: ScenarioRecord | null, persisted: ScenarioRecord,
  kind: "create" | "update", draftUnchanged: boolean,
): { record: ScenarioRecord | null; replaceDraft: boolean } {
  const sameSource = current?.id === source?.id && current?.revision === source?.revision;
  if (!sameSource) return { record: current, replaceDraft: false };
  if (draftUnchanged) return { record: persisted, replaceDraft: true };
  // An update can advance its own expected revision while retaining newer edits.
  // Creating a copy must never attach an unrelated in-progress draft to that copy.
  return { record: kind === "update" ? persisted : current, replaceDraft: false };
}
