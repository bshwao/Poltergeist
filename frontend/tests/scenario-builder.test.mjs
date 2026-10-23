import test, { after, afterEach, beforeEach } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, create } from "react-test-renderer";
import { loadComponents } from "../scripts/load-components.mjs";

const components = await loadComponents("components/ScenarioBuilder.tsx");
const { ScenarioBuilder } = components.module;
const { api, ApiError } = await components.moduleAt("services/api.ts");
const originalApi = { ...api };
const originalAnimationFrame = globalThis.requestAnimationFrame;
const sourceId = "1676a66c-4b04-4a6b-a2a4-37f5175cc52d";
const otherId = "96508df4-83e6-44c9-b3ea-805e5c4d1a26";
const copyId = "fd170810-a282-4dc0-9901-ef39f7415cd5";
const timestamp = "2026-10-16T12:00:00Z";
// Complete recipe shape from the bundled intersection template. Its form is
// deliberately small here so assertions can distinguish bound and custom forms.
const templateRecipe = {
  schema_version: "1.0", name: "intersection", template: "intersection",
  description: "A crossing vehicle approaches a perpendicular conflict point ahead of the ego vehicle.",
  map: "Town03", seed: 0, duration_s: 30, fixed_delta_seconds: 0.05,
  weather: { cloudiness: 0, precipitation: 0, wetness: 0, fog_density: 0, sun_altitude_deg: 70 },
  actors: [
    { type: "ego_vehicle", id: "ego", blueprint: "vehicle.tesla.model3", spawn: null, spawn_point_index: 0, relative_spawn: null, speed_mps: 12 },
    { type: "crossing_vehicle", id: "crossing", blueprint: "vehicle.audi.tt", spawn: null, spawn_point_index: null, relative_spawn: { anchor_actor_id: "ego", offset_m: { x: 30, y: -25, z: 0 }, yaw_offset_deg: 90 }, speed_mps: 0 },
  ],
  events: [{ action: "set_speed", actor_id: "crossing", trigger_time_s: 0, speed_mps: 10 }],
  location: "intersection", event: null, occlusion: false,
  parameters: { ego_speed_mps: 12, precipitation_pct: 0, fog_density_pct: 0, sun_altitude_deg: 70, crossing_speed_mps: 10, intersection_distance_m: 30, crossing_distance_m: 25, crossing_delay_s: 0 },
};
const templates = [{
  template: "intersection", scenario: templateRecipe,
  parameters: [{ name: "ego_speed_mps", minimum: 0, maximum: 80, default: 12, unit: "m/s", description: "Initial ego vehicle speed.", integer: false }],
}];
const source = {
  id: sourceId, revision: 3, created_at: timestamp, updated_at: timestamp,
  scenario: { ...structuredClone(templateRecipe), name: "Saved crossing", seed: 47, duration_s: 90, parameters: { ...templateRecipe.parameters, ego_speed_mps: 9 }, actors: [{ ...templateRecipe.actors[0], speed_mps: 9 }, templateRecipe.actors[1]] },
};
const custom = {
  ...source, id: otherId, revision: 7,
  scenario: { ...structuredClone(templateRecipe), name: "Custom experiment", seed: 79, duration_s: 120, template: null, parameters: {} },
};
let renderer;
let records;
let listCalls;

beforeEach(() => {
  records = [structuredClone(source), structuredClone(custom)];
  listCalls = 0;
  globalThis.requestAnimationFrame = callback => { callback(0); return 0; };
  api.listScenarios = async () => { listCalls++; return structuredClone(records); };
  api.listStressPresets = async () => [];
  api.getScenario = async id => {
    const record = records.find(item => item.id === id);
    assert.ok(record, `Unexpected saved scenario ${id}`);
    return structuredClone(record);
  };
  api.validateScenario = async recipe => structuredClone(recipe);
  api.generateScenario = async () => assert.fail("Generating was not requested");
  api.createScenario = async () => assert.fail("Creating was not requested");
  api.updateScenario = async () => assert.fail("Updating was not requested");
  api.deleteScenario = async () => assert.fail("Deleting was not requested");
  api.getSimulationQueue = async () => assert.fail("Queue checks must be explicit");
  api.launchSimulation = async () => assert.fail("Launching was not requested");
});

afterEach(async () => {
  if (renderer) await act(async () => renderer.unmount());
  renderer = undefined;
  Object.assign(api, originalApi);
  if (originalAnimationFrame === undefined) delete globalThis.requestAnimationFrame;
  else globalThis.requestAnimationFrame = originalAnimationFrame;
});
after(() => components.cleanup());

function text(node) {
  if (typeof node === "string" || typeof node === "number") return String(node);
  return node?.children?.map(text).join("") ?? "";
}
function button(label) {
  const matches = renderer.root.findAllByType("button").filter(node => node.props["aria-label"] === label || text(node) === label);
  assert.equal(matches.length, 1, `Expected one button named ${label}; found ${matches.length}`);
  return matches[0];
}
function field(id) { return renderer.root.findByProps({ id }); }
function jsonDraft() { return field("scenario-json").props.value; }
function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
async function mount(availableTemplates = templates) {
  await act(async () => { renderer = create(React.createElement(ScenarioBuilder, { templates: availableTemplates })); });
}
async function click(label) {
  const target = button(label);
  assert.notEqual(target.props.disabled, true, `Button ${label} should be available`);
  await act(async () => { target.props.onClick(); });
}
async function edit(id, value) {
  await act(async () => field(id).props.onChange({ target: { value } }));
}
async function generate() {
  await act(async () => renderer.root.findByProps({ className: "panel builder-form" }).props.onSubmit({ preventDefault() {} }));
}
async function openAndLoad(record = source) {
  if (button("Open library").props["aria-expanded"] === false) await click("Open library");
  await click(`Load ${record.scenario.name}`);
}
async function editNameAndValidate(name) {
  await edit("recipe-name", name);
  await click("Validate JSON");
}

test("parameters use expanded metadata bounds without displaying the old template ranges", async () => {
  await mount();
  const speed = field("parameter-ego_speed_mps");
  assert.equal(speed.props.min, 0);
  assert.equal(speed.props.max, 80);
  assert.equal(text(field("parameter-ego_speed_mps-help")), "Initial ego vehicle speed.");
  assert.doesNotMatch(text(renderer.root), /Range \d|within the template bounds/);
  const calls = [];
  api.generateScenario = async request => {
    calls.push(structuredClone(request));
    return {
      ...structuredClone(templateRecipe),
      parameters: { ...templateRecipe.parameters, ego_speed_mps: request.parameters.ego_speed_mps },
      actors: [{ ...templateRecipe.actors[0], speed_mps: request.parameters.ego_speed_mps }, templateRecipe.actors[1]],
    };
  };
  await edit("parameter-ego_speed_mps", "40");
  await generate();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].parameters.ego_speed_mps, 40);
  assert.equal(field("parameter-ego_speed_mps").props.value, "40");
  assert.equal(JSON.parse(jsonDraft()).actors[0].speed_mps, 40);
  await click("Reset defaults");
  assert.equal(field("parameter-ego_speed_mps").props.value, "12");
});

test("map selector is included in generation, validation, saved recipes and restored form state", async () => {
  await mount();
  assert.equal(field("scenario-map").props.value, "Town10HD_Opt");
  const calls = [];
  api.generateScenario = async input => { calls.push(input); return { ...structuredClone(templateRecipe), map: input.map }; };
  await edit("scenario-map", "Town02"); await generate();
  assert.equal(calls[0].map, "Town02");
  assert.equal(JSON.parse(jsonDraft()).map, "Town02");
  await click("Validate JSON");
  assert.equal(field("scenario-map").props.value, "Town02");
  await edit("scenario-map", "Town03");
  assert.equal(button("Save new recipe").props.disabled, true);
  await generate(); await click("Validate JSON");
  let saved;
  api.createScenario = async recipe => { saved = recipe; return { ...source, scenario: recipe }; };
  await click("Save new recipe");
  assert.equal(saved.map, "Town03");
});

test("loading saved and custom maps preserves their recorded selection", async () => {
  await mount(); await openAndLoad();
  assert.equal(field("scenario-map").props.value, "Town03");
  const recipe = { ...JSON.parse(jsonDraft()), template: null, parameters: {}, map: "MyCustomTown" };
  await edit("scenario-json", JSON.stringify(recipe)); await click("Validate JSON");
  assert.equal(field("scenario-map").props.value, "MyCustomTown");
  await edit("scenario-map", "Town10HD_Opt");
  assert.equal(JSON.parse(jsonDraft()).map, "Town10HD_Opt");
  assert.equal(button("Update saved recipe").props.disabled, true);
});

test("values outside the expanded parameter bounds are still rejected before generation", async () => {
  await mount();
  let calls = 0;
  api.generateScenario = async () => { calls++; assert.fail("Invalid parameter must not reach the API"); };
  for (const value of ["-0.1", "80.1"]) {
    await edit("parameter-ego_speed_mps", value);
    await generate();
    assert.equal(field("parameter-ego_speed_mps").props["aria-invalid"], true);
    assert.match(text(field("parameter-ego_speed_mps-error")), /from 0 to 80/);
  }
  assert.equal(calls, 0);
});

test("loading a stored custom recipe clears unrelated template parameters and restores its name, seed, and duration", async () => {
  await mount();
  assert.equal(listCalls, 0, "The library and worker are not queried just by mounting the builder");
  assert.equal(field("parameter-ego_speed_mps").props.value, "12");
  await openAndLoad(custom);
  assert.equal(field("recipe-name").props.value, "Custom experiment");
  assert.deepEqual(JSON.parse(jsonDraft()), custom.scenario);
  assert.equal(JSON.parse(jsonDraft()).seed, 79);
  assert.equal(field("scenario-duration").props.value, "120");
  assert.equal(renderer.root.findAllByProps({ id: "parameter-ego_speed_mps" }).length, 0);
  assert.equal(renderer.root.findAllByProps({ id: "scenario-seed" }).length, 0);
  assert.match(text(renderer.root), /Edit the complete scenario/);
  assert.match(text(renderer.root), /Revision 7 · Saved/);
  assert.equal(button("Save a copy").props.disabled, false);
});

test("loading a bound saved recipe restores its duration, parameter values, and random seed", async () => {
  await mount();
  await openAndLoad();
  assert.equal(field("recipe-name").props.value, source.scenario.name);
  assert.equal(field("scenario-seed").props.value, "47");
  assert.equal(field("scenario-duration").props.value, "90");
  assert.equal(field("parameter-ego_speed_mps").props.value, "9");
  assert.deepEqual(JSON.parse(jsonDraft()), source.scenario);
  assert.equal(button("Update saved recipe").props.disabled, true, "An unchanged saved recipe does not need another update");
});

test("duration controls keep presets and an edited duration in the generated recipe", async () => {
  await mount();
  assert.equal(field("scenario-duration").props.value, "30");
  for (const [label, duration] of [["1 min", "60"], ["2 min", "120"], ["5 min", "300"], ["30 s", "30"]]) {
    await click(label);
    assert.equal(field("scenario-duration").props.value, duration);
  }
  const calls = [];
  api.generateScenario = async request => {
    calls.push(structuredClone(request));
    return { ...structuredClone(templateRecipe), duration_s: request.duration_s };
  };
  await edit("scenario-duration", "75.5");
  await generate();
  assert.deepEqual(calls, [{ template: "intersection", seed: 0, parameters: { ego_speed_mps: 12 }, sample: false, ranges: {}, duration_s: 75.5, map: "Town10HD_Opt" }]);
  assert.equal(JSON.parse(jsonDraft()).duration_s, 75.5);
  assert.equal(field("scenario-duration").props.value, "75.5");
  assert.equal(button("Save new recipe").props.disabled, true, "Generation still requires validation");
  await click("Validate JSON");
  assert.equal(button("Save new recipe").props.disabled, false);
});

test("invalid duration entries and events beyond the duration block generation", async () => {
  const delayedTemplate = {
    ...structuredClone(templates[0]),
    parameters: [...templates[0].parameters, { name: "crossing_delay_s", minimum: 0, maximum: 60, default: 1, unit: "s", description: "Crossing delay.", integer: false }],
  };
  delayedTemplate.scenario.events[0].trigger_time_s = 1;
  await mount([delayedTemplate]);
  await edit("parameter-crossing_delay_s", "5");
  let calls = 0;
  api.generateScenario = async () => { calls++; assert.fail("Invalid duration must not reach the API"); };
  for (const duration of ["", "NaN", "Infinity", "0", "-1", "0.049", "5"]) {
    await edit("scenario-duration", duration);
    await generate();
    assert.equal(field("scenario-duration").props["aria-invalid"], true, duration);
    assert.equal(calls, 0);
  }
  api.generateScenario = async request => { calls++; return { ...structuredClone(delayedTemplate.scenario), duration_s: request.duration_s }; };
  await edit("scenario-duration", "5.05");
  await generate();
  assert.equal(calls, 1);
  assert.equal(field("scenario-duration").props["aria-invalid"], false);
});

test("a bound recipe duration edit requires regeneration and JSON validation restores its recorded duration", async () => {
  await mount();
  await openAndLoad();
  await edit("scenario-duration", "180");
  assert.equal(JSON.parse(jsonDraft()).duration_s, 90, "The existing JSON is kept until regeneration");
  assert.equal(button("Update saved recipe").props.disabled, true);
  assert.match(text(renderer.root), /Form changed/);
  await click("Validate JSON");
  assert.equal(field("scenario-duration").props.value, "90");
  assert.equal(button("Update saved recipe").props.disabled, true, "Validation restored the unchanged saved recipe");
  const edited = { ...JSON.parse(jsonDraft()), duration_s: 135 };
  await edit("scenario-json", JSON.stringify(edited, null, 2));
  await click("Validate JSON");
  assert.equal(field("scenario-duration").props.value, "135");
  assert.equal(button("Update saved recipe").props.disabled, false);
});

test("custom recipe duration edits update JSON and require validation, with invalid input kept out of requests", async () => {
  await mount();
  await openAndLoad(custom);
  await edit("scenario-duration", "180");
  assert.equal(JSON.parse(jsonDraft()).duration_s, 180);
  assert.equal(button("Update saved recipe").props.disabled, true);
  assert.equal(button("Save a copy").props.disabled, true);
  assert.match(text(renderer.root), /Draft · needs validation/);
  await edit("scenario-duration", "");
  assert.equal(field("scenario-duration").props["aria-invalid"], true);
  assert.equal(button("Validate JSON").props.disabled, true);
  const validated = [];
  api.validateScenario = async recipe => { validated.push(structuredClone(recipe)); return structuredClone(recipe); };
  await click("2 min");
  assert.equal(JSON.parse(jsonDraft()).duration_s, 120);
  assert.equal(field("scenario-duration").props.value, "120");
  assert.equal(button("Validate JSON").props.disabled, false);
  await edit("scenario-duration", "180");
  await click("Validate JSON");
  assert.equal(validated.length, 1);
  assert.equal(validated[0].duration_s, 180);
  assert.equal(field("scenario-duration").props.value, "180");
  assert.equal(button("Update saved recipe").props.disabled, false);
  const updates = [];
  api.updateScenario = async (id, recipe, revision) => {
    updates.push({ id, recipe: structuredClone(recipe), revision });
    return { ...custom, revision: 8, scenario: structuredClone(recipe) };
  };
  await click("Update saved recipe");
  assert.equal(updates[0].id, custom.id);
  assert.equal(updates[0].revision, 7);
  assert.equal(updates[0].recipe.duration_s, 180);
  assert.match(text(renderer.root), /Revision 8 · Saved/);
});

test("duration-only edits in a new form are protected when loading a saved recipe", async () => {
  await mount();
  await edit("scenario-duration", "60");
  await openAndLoad();
  assert.equal(renderer.root.findAllByProps({ "aria-label": "Replace workspace draft" }).length, 1);
  assert.equal(field("scenario-duration").props.value, "60");
  await click("Keep current draft");
  assert.equal(field("scenario-duration").props.value, "60");
  await click(`Load ${source.scenario.name}`);
  await click("Load saved recipe");
  assert.equal(field("scenario-duration").props.value, "90");
});

test("reset defaults and selecting a template restore the correct bundled duration", async () => {
  const second = { ...structuredClone(templates[0]), template: "pedestrian_crossing", scenario: { ...structuredClone(templateRecipe), template: "pedestrian_crossing", duration_s: 45 } };
  await mount([...templates, second]);
  await edit("scenario-duration", "120");
  await click("Reset defaults");
  assert.equal(field("scenario-duration").props.value, "30");
  await edit("scenario-duration", "180");
  const next = renderer.root.findAllByType("button").find(node => node.props.className?.startsWith("template-card") && text(node).includes("Pedestrian Crossing"));
  assert.ok(next);
  await act(async () => next.props.onClick());
  assert.equal(field("scenario-duration").props.value, "45");
});

test("loading over a dirty workspace requires an explicit choice and Keep current draft preserves edits", async () => {
  await mount();
  await openAndLoad();
  await edit("recipe-name", "Unsaved local name");
  const edited = jsonDraft();
  await click(`Load ${custom.scenario.name}`);
  assert.equal(renderer.root.findAllByProps({ "aria-label": "Replace workspace draft" }).length, 1);
  assert.equal(jsonDraft(), edited, "Fetching the selected record does not replace the draft");
  await click("Keep current draft");
  assert.equal(jsonDraft(), edited);
  assert.equal(renderer.root.findAllByProps({ "aria-label": "Replace workspace draft" }).length, 0);
  assert.equal(field("recipe-name").props.value, "Unsaved local name");
  await click(`Load ${custom.scenario.name}`);
  await click("Load saved recipe");
  assert.deepEqual(JSON.parse(jsonDraft()), custom.scenario);
  assert.equal(field("recipe-name").props.value, custom.scenario.name);
  assert.match(text(renderer.root), /Revision 7 · Saved/);
});

test("a revision conflict preserves validated JSON and offers a separate copy using the exact draft", async () => {
  await mount();
  await openAndLoad();
  await editNameAndValidate("My conflict-safe draft");
  const edited = jsonDraft();
  const calls = [];
  api.updateScenario = async (id, recipe, revision) => {
    calls.push({ id, recipe: structuredClone(recipe), revision });
    throw new ApiError("Scenario revision changed", 409);
  };
  await click("Update saved recipe");
  assert.deepEqual(calls, [{ id: sourceId, recipe: JSON.parse(edited), revision: 3 }]);
  assert.equal(jsonDraft(), edited);
  assert.match(text(renderer.root), /This saved recipe changed elsewhere\. Your draft is preserved/);
  assert.equal(button("Load latest revision").props.disabled, false);
  assert.equal(button("Save a copy").props.disabled, false);
  api.createScenario = async recipe => ({ ...source, id: copyId, revision: 1, scenario: structuredClone(recipe) });
  await click("Save a copy");
  assert.equal(jsonDraft(), edited);
  assert.match(text(renderer.root), /Revision 1 · Saved/);
  assert.match(text(renderer.root), new RegExp(copyId));
});

test("deleting the currently loaded scenario preserves JSON, detaches its saved association, and checks its revision", async () => {
  await mount();
  await openAndLoad();
  const before = jsonDraft();
  const calls = [];
  api.deleteScenario = async (id, revision) => {
    calls.push({ id, revision });
    records = records.filter(item => item.id !== id);
  };
  await click(`Delete ${source.scenario.name}`);
  assert.equal(calls.length, 0, "Deleting requires the inline confirmation");
  await click("Confirm delete");
  assert.deepEqual(calls, [{ id: sourceId, revision: 3 }]);
  assert.equal(jsonDraft(), before);
  assert.match(text(renderer.root), /The workspace draft is unchanged/);
  assert.equal(button("Save new recipe").props.disabled, false);
  assert.equal(renderer.root.findAllByType("button").filter(node => text(node) === "Save a copy").length, 0);
  assert.match(text(renderer.root), /New draft/);
});

test("a delayed update keeps newer JSON edits while advancing only its source revision for the next update", async () => {
  await mount();
  await openAndLoad();
  await editNameAndValidate("First submitted snapshot");
  const submitted = JSON.parse(jsonDraft());
  const waiting = deferred();
  const updates = [];
  api.updateScenario = (id, recipe, revision) => {
    updates.push({ id, recipe: structuredClone(recipe), revision });
    return waiting.promise;
  };
  await click("Update saved recipe");
  assert.equal(button("Saving…").props.disabled, true);
  await edit("scenario-json", JSON.stringify({ ...submitted, name: "Newer in-progress JSON", seed: 101 }, null, 2));
  const newer = jsonDraft();
  await act(async () => waiting.resolve({ ...source, revision: 4, scenario: submitted }));
  assert.equal(jsonDraft(), newer);
  assert.equal(field("recipe-name").props.value, "Newer in-progress JSON");
  assert.match(text(renderer.root), /Revision 4 · Unsaved edits/);
  assert.match(text(renderer.root), /Your newer workspace edits are preserved/);
  await click("Validate JSON");
  api.updateScenario = async (id, recipe, revision) => {
    updates.push({ id, recipe: structuredClone(recipe), revision });
    return { ...source, revision: 5, scenario: structuredClone(recipe) };
  };
  await click("Update saved recipe");
  assert.equal(updates[0].revision, 3);
  assert.equal(updates[0].recipe.name, "First submitted snapshot");
  assert.equal(updates[1].revision, 4);
  assert.equal(updates[1].recipe.name, "Newer in-progress JSON");
  assert.equal(updates[1].recipe.seed, 101);
  assert.match(text(renderer.root), /Revision 5 · Saved/);
});

test("a delayed new save never attaches an edited workspace to the older saved snapshot", async () => {
  await mount();
  const initial = { ...structuredClone(templateRecipe), name: "New draft snapshot" };
  await edit("scenario-json", JSON.stringify(initial, null, 2));
  await click("Validate JSON");
  const waiting = deferred();
  api.createScenario = () => waiting.promise;
  await click("Save new recipe");
  await edit("recipe-name", "Newer unrelated draft");
  const newer = jsonDraft();
  await act(async () => waiting.resolve({ ...source, id: copyId, revision: 1, scenario: initial }));
  assert.equal(jsonDraft(), newer);
  assert.equal(field("recipe-name").props.value, "Newer unrelated draft");
  assert.equal(button("Save new recipe").props.disabled, true, "New JSON still requires validation");
  assert.equal(renderer.root.findAllByType("button").filter(node => text(node) === "Save a copy").length, 0);
  assert.match(text(renderer.root), /New draft/);
  assert.match(text(renderer.root), /Your newer workspace edits are preserved/);
});

test("a delayed copy preserves newer edits and keeps the original source revision for a later update", async () => {
  await mount();
  await openAndLoad();
  const waiting = deferred();
  const copied = JSON.parse(jsonDraft());
  api.createScenario = () => waiting.promise;
  await click("Save a copy");
  await edit("recipe-name", "Newer edits on original");
  const newer = jsonDraft();
  await act(async () => waiting.resolve({ ...source, id: copyId, revision: 1, scenario: copied }));
  assert.equal(jsonDraft(), newer);
  assert.match(text(renderer.root), /Revision 3 · Unsaved edits/);
  await click("Validate JSON");
  const updates = [];
  api.updateScenario = async (id, recipe, revision) => {
    updates.push({ id, revision });
    return { ...source, revision: 4, scenario: structuredClone(recipe) };
  };
  await click("Update saved recipe");
  assert.deepEqual(updates, [{ id: sourceId, revision: 3 }]);
  assert.equal(field("recipe-name").props.value, "Newer edits on original");
});

test("editing while a saved recipe is being fetched aborts the read and ignores its late result", async () => {
  await mount();
  await openAndLoad();
  const waiting = deferred();
  let readSignal;
  api.getScenario = (_id, options) => { readSignal = options.signal; return waiting.promise; };
  await click(`Load ${custom.scenario.name}`);
  assert.equal(readSignal.aborted, false);
  await edit("recipe-name", "Draft typed during read");
  const before = jsonDraft();
  assert.equal(readSignal.aborted, true);
  await act(async () => waiting.resolve(structuredClone(custom)));
  assert.equal(jsonDraft(), before);
  assert.equal(field("recipe-name").props.value, "Draft typed during read");
  assert.equal(renderer.root.findAllByProps({ "aria-label": "Replace workspace draft" }).length, 0);
  assert.match(text(renderer.root), /Revision 3 · Unsaved edits/);
});

test("a delayed validation result cannot validate or replace JSON edited while it was pending", async () => {
  await mount();
  await openAndLoad();
  await edit("recipe-name", "Submitting validation");
  const submitted = JSON.parse(jsonDraft());
  const waiting = deferred();
  let signal;
  api.validateScenario = (_recipe, options) => { signal = options.signal; return waiting.promise; };
  await click("Validate JSON");
  await edit("recipe-name", "Still editing");
  assert.equal(signal.aborted, true);
  const newer = jsonDraft();
  await act(async () => waiting.resolve(submitted));
  assert.equal(jsonDraft(), newer);
  assert.equal(button("Update saved recipe").props.disabled, true);
  assert.match(text(renderer.root), /Draft · needs validation/);
});

const stressRecipe = { ...structuredClone(templateRecipe), name: "intersection-medium", seed: 7, map: "Town10HD_Opt", duration_s: 8 };

test("a stress preset binds the complete validated recipe without saving or launching", async () => {
  api.listStressPresets = async () => [structuredClone(stressRecipe)];
  await mount();
  await edit("stress-preset", stressRecipe.name);
  await click("Load stress preset");
  assert.deepEqual(JSON.parse(jsonDraft()), stressRecipe);
  assert.equal(field("scenario-seed").props.value, "7");
  assert.equal(field("scenario-duration").props.value, "8");
  assert.equal(field("scenario-map").props.value, "Town10HD_Opt");
  assert.equal(button("Save new recipe").props.disabled, false);
});

test("loading a stress preset protects unsaved edits and detaches the saved identity only after confirmation", async () => {
  api.listStressPresets = async () => [structuredClone(stressRecipe)];
  await mount();
  await openAndLoad();
  await edit("recipe-name", "Keep these edits");
  const before = jsonDraft();
  await edit("stress-preset", stressRecipe.name);
  await click("Load stress preset");
  assert.equal(jsonDraft(), before);
  await click("Keep current draft");
  assert.equal(jsonDraft(), before);
  await click("Load stress preset");
  await click("Replace with stress preset");
  assert.deepEqual(JSON.parse(jsonDraft()), stressRecipe);
  assert.equal(button("Save new recipe").props.disabled, false);
  assert.deepEqual(records[0], source);
});

test("stress catalogue failures can be retried without changing the workspace", async () => {
  api.listStressPresets = async () => { throw new Error("Catalogue unavailable"); };
  await mount();
  assert.match(text(renderer.root), /Catalogue unavailable/);
  api.listStressPresets = async () => [structuredClone(stressRecipe)];
  await click("Retry stress presets");
  assert.equal(field("stress-preset").findAllByType("option").length, 2);
  assert.equal(jsonDraft(), "");
});

test("leaving the workspace aborts a stress catalogue read and ignores its late response", async () => {
  const waiting = deferred();
  let signal;
  api.listStressPresets = options => { signal = options.signal; return waiting.promise; };
  await mount();
  await act(async () => renderer.update(React.createElement(ScenarioBuilder, { templates, active: false })));
  assert.equal(signal.aborted, true);
  await act(async () => waiting.resolve([structuredClone(stressRecipe)]));
  assert.equal(field("stress-preset").findAllByType("option").length, 1);
  assert.equal(jsonDraft(), "");
});

test("a hung stress catalogue request times out and cannot replace a successful retry", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const waiting = deferred();
  let signal;
  api.listStressPresets = options => { signal = options.signal; return waiting.promise; };
  await mount();
  await act(async () => t.mock.timers.tick(11_000));
  assert.equal(signal.aborted, true);
  assert.match(text(renderer.root), /Loading stress presets timed out/);
  api.listStressPresets = async () => [structuredClone(stressRecipe)];
  await click("Retry stress presets");
  await act(async () => waiting.resolve([]));
  assert.equal(field("stress-preset").findAllByType("option").length, 2);
});
