import assert from "node:assert/strict";
import test from "node:test";
import { loadTypeScript } from "../scripts/load-typescript.mjs";

const { readNumericField, parameterDefaults, makeGenerateRequest, parseRecipeJson } = await loadTypeScript(
  new URL("../src/components/scenario-form.ts", import.meta.url),
);

const parameter = { name: "speed", minimum: 0.5, maximum: 25, default: 12, unit: "m/s", integer: false, description: "Actor speed." };
const template = {
  template: "sudden_braking", parameters: [parameter],
  scenario: { duration_s: 30, fixed_delta_seconds: 0.05, events: [{ trigger_time_s: 2 }] },
};

test("numeric entries keep fractional values and both inclusive bounds", () => {
  for (const [raw, value] of [[".5", 0.5], ["25", 25], [" 12.75 ", 12.75], ["1.2e1", 12]]) {
    assert.deepEqual(readNumericField(raw, parameter), { value });
  }
});

test("empty fields never silently become zero", () => {
  const bounds = { minimum: 0, maximum: 100, integer: false };
  assert.match(readNumericField("", bounds).error, /Enter a value/);
  assert.match(readNumericField("   ", bounds).error, /Enter a value/);
  assert.deepEqual(readNumericField("0", bounds), { value: 0 });
});

test("non-finite and non-decimal numeric strings are rejected", () => {
  for (const raw of ["Infinity", "-Infinity", "NaN", "0x10", "0b10", "1_000", "12m", "1,2", "1e309", "--1"]) {
    assert.equal(typeof readNumericField(raw, parameter).error, "string", raw);
  }
});

test("whole-number metadata and range limits are enforced", () => {
  const bounds = { minimum: 1, maximum: 3, integer: true };
  assert.match(readNumericField("1.2", bounds).error, /whole number/);
  assert.match(readNumericField("0", bounds).error, /from 1 to 3/);
  assert.match(readNumericField("4", bounds).error, /from 1 to 3/);
  assert.deepEqual(readNumericField("3", bounds), { value: 3 });
});

test("defaults are editable strings sourced from current metadata", () => {
  const parameters = [parameter, { ...parameter, name: "delay", default: 0.75 }];
  const values = parameterDefaults(parameters);
  assert.deepEqual(values, { speed: "12", delay: "0.75" });
  values.speed = "20";
  assert.equal(parameters[0].default, 12);
});

test("generation uses fixed parameters and the exact seed without extra form keys", () => {
  assert.deepEqual(makeGenerateRequest(template, "2147483647", { speed: "13.5", obsolete: "3" }), {
    request: { template: "sudden_braking", seed: 2147483647, parameters: { speed: 13.5 }, sample: false, ranges: {}, duration_s: null, map: null },
  });
  assert.equal(makeGenerateRequest(template, "0", { speed: "12" }).request.seed, 0);
});

test("expanded vehicle speed metadata accepts values beyond the former template range unchanged", () => {
  const expanded = { ...template, parameters: [{ ...parameter, minimum: 0, maximum: 80 }] };
  for (const speed of [0, 40, 80]) {
    assert.equal(makeGenerateRequest(expanded, "0", { speed: String(speed) }).request.parameters.speed, speed);
  }
  for (const speed of ["-0.1", "80.1"]) {
    const result = makeGenerateRequest(expanded, "0", { speed });
    assert.match(result.errors.speed, /from 0 to 80/);
    assert.equal(result.request, undefined);
  }
});

test("generation rejects blank, fractional, negative, and overflowing seeds", () => {
  for (const seed of ["", "1.5", "-1", "2147483648", "Infinity"]) {
    const result = makeGenerateRequest(template, seed, { speed: "12" });
    assert.equal(typeof result.errors.seed, "string", seed);
    assert.equal(result.request, undefined);
  }
});

test("generation collects field errors and does not fill missing values with defaults", () => {
  const integer = { ...parameter, name: "count", default: 2, integer: true, minimum: 1, maximum: 10 };
  const result = makeGenerateRequest({ ...template, parameters: [parameter, integer] }, "-1", { count: "1.5" });
  assert.deepEqual(Object.keys(result.errors).sort(), ["count", "seed", "speed"]);
  assert.match(result.errors.speed, /Enter a value/);
  assert.equal(result.request, undefined);
});

test("an explicit duration is sent at the top level without changing parameter values", () => {
  for (const [raw, duration] of [["60", 60], [" 120.5 ", 120.5], ["3e2", 300]]) {
    assert.deepEqual(makeGenerateRequest(template, "7", { speed: "13.5" }, raw), {
      request: { template: "sudden_braking", seed: 7, parameters: { speed: 13.5 }, sample: false, ranges: {}, duration_s: duration, map: null },
    });
  }
});

test("generation rejects empty, non-finite, non-positive, and too-short durations", () => {
  for (const duration of ["", " ", "NaN", "Infinity", "1e309", "0x10", "60s", "0", "-1", "0.049"]) {
    const result = makeGenerateRequest(template, "0", { speed: "12" }, duration);
    assert.equal(typeof result.errors.duration_s, "string", duration);
    assert.equal(result.request, undefined);
  }
  const noEvents = { ...template, scenario: { ...template.scenario, events: [] } };
  assert.equal(makeGenerateRequest(noEvents, "0", { speed: "12" }, "0.05").request.duration_s, 0.05);
});

test("duration must include every configured event, including a changed delay", () => {
  const crossing = {
    template: "intersection",
    parameters: [{ ...parameter, name: "crossing_delay_s", minimum: 0, maximum: 60, default: 1 }],
    scenario: { duration_s: 30, fixed_delta_seconds: 0.05, events: [{ trigger_time_s: 1 }] },
  };
  for (const duration of ["2", "5"]) {
    const result = makeGenerateRequest(crossing, "0", { crossing_delay_s: "5" }, duration);
    assert.equal(typeof result.errors.duration_s, "string", duration);
    assert.equal(result.request, undefined);
  }
  assert.equal(makeGenerateRequest(crossing, "0", { crossing_delay_s: "5" }, "5.05").request.duration_s, 5.05);
  const laterDefault = { ...crossing, scenario: { ...crossing.scenario, events: [{ trigger_time_s: 5 }] } };
  assert.equal(makeGenerateRequest(laterDefault, "0", { crossing_delay_s: "1" }, "1.05").request.duration_s, 1.05);
  // Template events without an editable delay still constrain the duration.
  assert.equal(typeof makeGenerateRequest(template, "0", { speed: "12" }, "2").errors.duration_s, "string");
  assert.equal(makeGenerateRequest(template, "0", { speed: "12" }, "2.05").request.duration_s, 2.05);
});

test("JSON parsing requires an object and reports incomplete JSON", () => {
  for (const text of ["", "  ", "null", "[]", "12", "true", '"recipe"', '{"name":']) {
    assert.equal(typeof parseRecipeJson(text).error, "string", text);
  }
});

test("JSON parsing preserves the complete recipe for server normalization", () => {
  const recipe = { name: "custom", seed: 4, actors: [{ type: "ego_vehicle", id: null }], events: [], parameters: { speed: 3 }, weather: { fog_density: 0 } };
  assert.deepEqual(parseRecipeJson(JSON.stringify(recipe)), { recipe });
  // Structural recipe validation belongs to the real API, not the JSON parser.
  assert.deepEqual(parseRecipeJson("{}"), { recipe: {} });
});
