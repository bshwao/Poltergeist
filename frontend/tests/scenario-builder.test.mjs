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
  parameters: [{ name: "ego_speed_mps", minimum: 1, maximum: 25, default: 12, unit: "m/s", description: "Initial ego vehicle speed.", integer: false }],
}];
const source = {
  id: sourceId, revision: 3, created_at: timestamp, updated_at: timestamp,
  scenario: { ...structuredClone(templateRecipe), name: "Saved crossing", seed: 47, parameters: { ...templateRecipe.parameters, ego_speed_mps: 9 }, actors: [{ ...templateRecipe.actors[0], speed_mps: 9 }, templateRecipe.actors[1]] },
};
const custom = {
  ...source, id: otherId, revision: 7,
  scenario: { ...structuredClone(templateRecipe), name: "Custom experiment", seed: 79, template: null, parameters: {} },
};
let renderer;
let records;
let listCalls;

beforeEach(() => {
  records = [structuredClone(source), structuredClone(custom)];
  listCalls = 0;
  globalThis.requestAnimationFrame = callback => { callback(0); return 0; };
  api.listScenarios = async () => { listCalls++; return structuredClone(records); };
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
async function mount() {
  await act(async () => { renderer = create(React.createElement(ScenarioBuilder, { templates })); });
}
async function click(label) {
  const target = button(label);
  assert.notEqual(target.props.disabled, true, `Button ${label} should be available`);
  await act(async () => { target.props.onClick(); });
}
async function edit(id, value) {
  await act(async () => field(id).props.onChange({ target: { value } }));
}
async function openAndLoad(record = source) {
  if (button("Open library").props["aria-expanded"] === false) await click("Open library");
  await click(`Load ${record.scenario.name}`);
}
async function editNameAndValidate(name) {
  await edit("recipe-name", name);
  await click("Validate JSON");
}

test("loading a stored custom recipe clears unrelated template parameters and restores its name and seed", async () => {
  await mount();
  assert.equal(listCalls, 0, "The library and worker are not queried just by mounting the builder");
  assert.equal(field("parameter-ego_speed_mps").props.value, "12");
  await openAndLoad(custom);
  assert.equal(field("recipe-name").props.value, "Custom experiment");
  assert.deepEqual(JSON.parse(jsonDraft()), custom.scenario);
  assert.equal(JSON.parse(jsonDraft()).seed, 79);
  assert.equal(renderer.root.findAllByProps({ id: "parameter-ego_speed_mps" }).length, 0);
  assert.equal(renderer.root.findAllByProps({ id: "scenario-seed" }).length, 0);
  assert.match(text(renderer.root), /Edit the complete scenario/);
  assert.match(text(renderer.root), /Revision 7 · Saved/);
  assert.equal(button("Save a copy").props.disabled, false);
});

test("loading a bound saved recipe restores its parameter values and random seed", async () => {
  await mount();
  await openAndLoad();
  assert.equal(field("recipe-name").props.value, source.scenario.name);
  assert.equal(field("scenario-seed").props.value, "47");
  assert.equal(field("parameter-ego_speed_mps").props.value, "9");
  assert.deepEqual(JSON.parse(jsonDraft()), source.scenario);
  assert.equal(button("Update saved recipe").props.disabled, true, "An unchanged saved recipe does not need another update");
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
