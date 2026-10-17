import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { loadTypeScript } from "../scripts/load-typescript.mjs";

const {
  DEFAULT_OPTIONS, DEFAULT_EVALUATION, OPTION_FIELDS, EVALUATION_FIELDS,
  defaultOptionDraft, defaultEvaluationDraft, makeLaunchRequest, acceptanceMayBeUncertain,
} = await loadTypeScript(new URL("../src/components/launch-form.ts", import.meta.url));
const contracts = JSON.parse(await readFile(new URL("../../contracts/models.schema.json", import.meta.url), "utf8"));
const scenario = { name: "Current recipe", seed: 12, actors: [{ id: "ego", speed_mps: 8 }], events: [], parameters: {}, weather: { fog_density: 0 } };
function build(options = {}, evaluation = {}, mode = "test", recipe = scenario) {
  return makeLaunchRequest(recipe, mode, { ...defaultOptionDraft(), ...options }, { ...defaultEvaluationDraft(), ...evaluation });
}

test("launch defaults exactly follow every backend runtime and evaluation contract field", () => {
  for (const [definition, defaults] of [["SimulationOptions", DEFAULT_OPTIONS], ["EvaluationSettings", DEFAULT_EVALUATION]]) {
    const properties = contracts.$defs[definition].properties;
    assert.deepEqual(Object.keys(defaults).sort(), Object.keys(properties).sort());
    for (const [key, property] of Object.entries(properties)) assert.equal(defaults[key], property.default, `${definition}.${key}`);
  }
  assert.deepEqual(build().request.options, DEFAULT_OPTIONS);
  assert.deepEqual(build().request.evaluation_settings, DEFAULT_EVALUATION);
});

test("mode selection and a validated recipe are explicit launch requirements", () => {
  assert.match(build({}, {}, null).errors.mode, /Choose CARLA or synthetic/);
  assert.match(build({}, {}, "test", null).errors.scenario, /Generate or validate/);
  assert.equal(build().request.mode, "test");
  assert.equal(build({}, {}, "carla").request.mode, "carla");
});

test("launch sends an independent inline snapshot including nested recipe/settings and no saved ID", () => {
  const recipe = structuredClone(scenario);
  const options = defaultOptionDraft();
  const evaluation = defaultEvaluationDraft();
  const result = makeLaunchRequest(recipe, "carla", options, evaluation).request;
  assert.deepEqual(result.scenario, recipe);
  assert.equal(result.scenario_id, null);
  recipe.actors[0].speed_mps = 99;
  recipe.weather.fog_density = 50;
  options.max_ticks = "2";
  evaluation.ttc_weight = "0";
  assert.equal(result.scenario.actors[0].speed_mps, 8);
  assert.equal(result.scenario.weather.fog_density, 0);
  assert.equal(result.options.max_ticks, 100000);
  assert.equal(result.evaluation_settings.ttc_weight, 45);
});

test("each numeric field rejects blank, malformed and infinite input without coercing to zero", () => {
  for (const [fields, prefix] of [[OPTION_FIELDS, "options"], [EVALUATION_FIELDS, "evaluation_settings"]]) {
    for (const field of fields) {
      for (const raw of ["", "  ", "NaN", "Infinity", "0x10", "4 units", "1e309", "1,2"]) {
        const result = prefix === "options" ? build({ [field.key]: raw }) : build({}, { [field.key]: raw });
        assert.equal(typeof result.errors[`${prefix}.${field.key}`], "string", `${field.key}: ${JSON.stringify(raw)}`);
        assert.equal(result.request, undefined);
      }
    }
  }
});

test("runtime limits enforce positive timing, whole ticks and dimensions up to 4096", () => {
  for (const key of ["sensor_timeout_s", "sensor_settle_s"]) {
    for (const value of ["0", "-1"]) assert.ok(build({ [key]: value }).errors[`options.${key}`]);
    assert.equal(build({ [key]: "0.001" }).request.options[key], 0.001);
  }
  for (const key of ["max_ticks", "camera_width", "camera_height"]) {
    for (const value of ["0", "-1", "1.5", "9007199254740992"]) assert.ok(build({ [key]: value }).errors[`options.${key}`]);
    assert.equal(build({ [key]: "1" }).request.options[key], 1);
  }
  for (const key of ["camera_width", "camera_height"]) {
    assert.equal(build({ [key]: "4096" }).request.options[key], 4096);
    assert.ok(build({ [key]: "4097" }).errors[`options.${key}`]);
  }
});

test("evaluation accepts zero only for nonnegative metrics and weights", () => {
  for (const field of EVALUATION_FIELDS.filter(field => !field.key.endsWith("_weight"))) {
    assert.ok(build({}, { [field.key]: "-1" }).errors[`evaluation_settings.${field.key}`], field.key);
    const result = build({}, { [field.key]: "0" });
    if (field.positive || field.integer) assert.ok(result.errors[`evaluation_settings.${field.key}`], field.key);
    else assert.equal(result.request.evaluation_settings[field.key], 0, field.key);
  }
  assert.equal(build({}, { ttc_weight: "0", clearance_weight: "0", braking_weight: "0", traffic_rule_weight: "100" }).request.evaluation_settings.ttc_weight, 0);
  assert.ok(build({}, { violations_for_full_severity: "1.5" }).errors["evaluation_settings.violations_for_full_severity"]);
});

test("cross-field horizon and braking constraints match backend semantics", () => {
  assert.ok(build({}, { ttc_horizon_s: "2", ttc_warning_s: "3" }).errors["evaluation_settings.ttc_horizon_s"]);
  assert.equal(build({}, { ttc_horizon_s: "3", ttc_warning_s: "3" }).request.evaluation_settings.ttc_horizon_s, 3);
  assert.ok(build({}, { hard_braking_threshold_mps2: "8", braking_full_severity_mps2: "8" }).errors["evaluation_settings.braking_full_severity_mps2"]);
  assert.ok(build({}, { hard_braking_threshold_mps2: "9", braking_full_severity_mps2: "8" }).errors["evaluation_settings.braking_full_severity_mps2"]);
  assert.equal(build({}, { hard_braking_threshold_mps2: "7.9", braking_full_severity_mps2: "8" }).request.evaluation_settings.braking_full_severity_mps2, 8);
});

test("severity weights total 100 within the contract floating point tolerance", () => {
  assert.match(build({}, { ttc_weight: "44" }).errors.weights, /sum to 100/);
  assert.ok(build({}, { ttc_weight: "45.0000000001" }).request);
  assert.match(build({}, { ttc_weight: "45.00000001" }).errors.weights, /sum to 100/);
  assert.ok(build({}, { ttc_weight: "-1", traffic_rule_weight: "61" }).errors["evaluation_settings.ttc_weight"]);
});

test("test mode rejects capture and CARLA preserves explicitly selected options", () => {
  assert.match(build({ camera: true }).errors.capture, /require CARLA/);
  assert.match(build({ lidar: true }).errors.capture, /require CARLA/);
  const { request } = build({ camera: true, lidar: true, allow_map_reload: true, camera_width: "1280", camera_height: "720" }, {}, "carla");
  assert.deepEqual(request.options, { ...DEFAULT_OPTIONS, camera: true, lidar: true, allow_map_reload: true, camera_width: 1280, camera_height: 720 });
});

test("each new form gets independent editable defaults", () => {
  const first = defaultOptionDraft();
  first.sensor_timeout_s = "9";
  first.camera = true;
  const second = defaultEvaluationDraft();
  second.ttc_weight = "0";
  assert.equal(defaultOptionDraft().sensor_timeout_s, "2");
  assert.equal(defaultOptionDraft().camera, false);
  assert.equal(defaultEvaluationDraft().ttc_weight, "45");
});

test("lost transport, malformed success and unexpected server errors warn about uncertain acceptance", () => {
  for (const status of [0, 200, 201, 202, 204, 500, 502, 504]) assert.equal(acceptanceMayBeUncertain(status), true, String(status));
  for (const status of [400, 404, 409, 422, 429, 503]) assert.equal(acceptanceMayBeUncertain(status), false, String(status));
});
