import type { ParameterInfo, ScenarioGenerate, TemplateInfo } from "../types/generated";

export type NumericResult = { value: number; error?: never } | { value?: never; error: string };

/** Keep empty, malformed, and out-of-range entries out of JSON requests. */
export function readNumericField(
  raw: string,
  bounds: { minimum: number; maximum: number; integer: boolean },
): NumericResult {
  const text = raw.trim();
  if (!text) return { error: "Enter a value." };
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(text)) {
    return { error: "Enter a finite number." };
  }
  const value = Number(text);
  if (!Number.isFinite(value)) return { error: "Enter a finite number." };
  if (bounds.integer && !Number.isInteger(value)) return { error: "Enter a whole number." };
  if (value < bounds.minimum || value > bounds.maximum) {
    return { error: `Use a value from ${bounds.minimum} to ${bounds.maximum}.` };
  }
  return { value };
}

export function parameterDefaults(parameters: ParameterInfo[]): Record<string, string> {
  return Object.fromEntries(parameters.map((parameter) => [parameter.name, String(parameter.default)]));
}

export function makeGenerateRequest(
  template: TemplateInfo,
  seed: string,
  values: Record<string, string>,
): { request: ScenarioGenerate; errors?: never } | { request?: never; errors: Record<string, string> } {
  const errors: Record<string, string> = {};
  const seedValue = readNumericField(seed, { minimum: 0, maximum: 2 ** 31 - 1, integer: true });
  if (seedValue.error) errors.seed = seedValue.error;
  const parameters: Record<string, number> = {};
  for (const parameter of template.parameters) {
    const result = readNumericField(values[parameter.name] ?? "", parameter);
    if (result.error !== undefined) errors[parameter.name] = result.error;
    else parameters[parameter.name] = result.value;
  }
  if (Object.keys(errors).length) return { errors };
  return { request: { template: template.template, seed: seedValue.value!, parameters, sample: false, ranges: {} } };
}

export function parseRecipeJson(text: string): { recipe: Record<string, unknown>; error?: never } | { recipe?: never; error: string } {
  if (!text.trim()) return { error: "Generate a recipe or paste a JSON object first." };
  let recipe: unknown;
  try {
    recipe = JSON.parse(text);
  } catch (error) {
    return { error: error instanceof SyntaxError ? `Invalid JSON: ${error.message}` : "Invalid JSON." };
  }
  if (recipe === null || typeof recipe !== "object" || Array.isArray(recipe)) {
    return { error: "A scenario recipe must be a JSON object." };
  }
  return { recipe: recipe as Record<string, unknown> };
}

export function readableName(value: string): string {
  return value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}
