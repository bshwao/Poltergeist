import test, { after, afterEach, beforeEach } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, create } from "react-test-renderer";
import { loadComponents } from "../scripts/load-components.mjs";

const components = await loadComponents("components/SavedScenarios.tsx");
const { SavedScenarios } = components.module;
const { api, ApiError } = await components.moduleAt("services/api.ts");
const originalApi = { ...api };
const timestamp = "2026-10-16T12:00:00Z";
const recipe = {
  schema_version: "1.0", name: "Custom experiment", template: null, description: "A saved custom recipe.",
  map: "Town03", seed: 7, duration_s: 1, fixed_delta_seconds: 0.05,
  weather: { cloudiness: 0, precipitation: 0, wetness: 0, fog_density: 0, sun_altitude_deg: 70 },
  actors: [{ id: "ego", type: "ego_vehicle", blueprint: null, spawn: null, spawn_point_index: 0, relative_spawn: null, speed_mps: 5 }],
  events: [], location: null, event: null, occlusion: false, parameters: {},
};
function record(number, revision = 2) {
  return {
    id: `1676a66c-4b04-4a6b-a2a4-${String(number).padStart(12, "0")}`,
    revision, created_at: timestamp, updated_at: timestamp,
    scenario: { ...structuredClone(recipe), name: `Experiment ${number}` },
  };
}
let renderer;
let records;
let props;
let pageCalls;
let deleted;

beforeEach(() => {
  records = [record(1), record(2)];
  deleted = [];
  pageCalls = [];
  props = { currentId: null, refreshKey: 0, loadingId: null, writing: false, onLoad: () => {}, onDeleted: id => deleted.push(id) };
  api.listScenarios = async (page, options) => {
    pageCalls.push({ ...page });
    assert.ok(options.signal instanceof AbortSignal);
    return structuredClone(records.slice(page.offset, page.offset + page.limit));
  };
  api.deleteScenario = async () => assert.fail("Deleting was not requested");
});
afterEach(async () => {
  if (renderer) await act(async () => renderer.unmount());
  renderer = undefined;
  Object.assign(api, originalApi);
});
after(() => components.cleanup());

function text(node) {
  if (typeof node === "string" || typeof node === "number") return String(node);
  return node?.children?.map(text).join("") ?? "";
}
function button(label) {
  const matches = renderer.root.findAllByType("button").filter(node => node.props["aria-label"] === label || text(node) === label);
  assert.equal(matches.length, 1, `Expected one button named ${label}`);
  return matches[0];
}
function deferred() {
  let resolve;
  const promise = new Promise(yes => { resolve = yes; });
  return { promise, resolve };
}
async function click(label) {
  const target = button(label);
  assert.notEqual(target.props.disabled, true, `Button ${label} should be available`);
  await act(async () => target.props.onClick());
}
async function mount() {
  await act(async () => { renderer = create(React.createElement(SavedScenarios, props)); });
  await click("Open library");
}
async function update(changes) {
  props = { ...props, ...changes };
  await act(async () => renderer.update(React.createElement(SavedScenarios, props)));
}

test("delete actions and an existing confirmation are blocked throughout a pending recipe load", async () => {
  await mount();
  await click("Delete Experiment 1");
  assert.equal(button("Confirm delete").props.disabled, false);
  await update({ loadingId: records[1].id });
  assert.equal(button("Delete Experiment 1").props.disabled, true);
  assert.equal(button("Delete Experiment 2").props.disabled, true);
  assert.equal(button("Confirm delete").props.disabled, true);
  // Guard the handler as well as its disabled DOM state, so a load and delete
  // racing in the same render cannot send a destructive request.
  await act(async () => button("Confirm delete").props.onClick());
  assert.deepEqual(deleted, []);
  await update({ loadingId: null });
  assert.equal(button("Delete Experiment 1").props.disabled, false);
  assert.equal(button("Confirm delete").props.disabled, false);
  const calls = [];
  api.deleteScenario = async (id, revision) => {
    calls.push({ id, revision });
    records = records.filter(item => item.id !== id);
  };
  await click("Confirm delete");
  assert.deepEqual(calls, [{ id: record(1).id, revision: 2 }]);
  assert.deepEqual(deleted, [record(1).id]);
});

test("library pages use eight rows plus lookahead and disable navigation while a page or deletion is pending", async () => {
  records = Array.from({ length: 10 }, (_, index) => record(index + 1));
  await mount();
  assert.deepEqual(pageCalls, [{ limit: 9, offset: 0 }]);
  assert.equal(renderer.root.findAllByType("li").length, 8);
  assert.equal(button("Previous page").props.disabled, true);
  assert.equal(button("Next page").props.disabled, false);
  assert.equal(renderer.root.findAllByProps({ "aria-label": "Load Experiment 9" }).length, 0, "The lookahead row is not rendered on page one");
  const nextPage = deferred();
  const list = api.listScenarios;
  api.listScenarios = (page, options) => {
    if (page.offset === 8) { pageCalls.push({ ...page }); return nextPage.promise; }
    return list(page, options);
  };
  await click("Next page");
  assert.equal(button("Previous page").props.disabled, true);
  assert.equal(button("Next page").props.disabled, true);
  assert.equal(button("Refresh saved scenarios").props.disabled, true);
  assert.equal(renderer.root.findAllByType("li").length, 0, "Stale rows are cleared while the next page loads");
  await act(async () => nextPage.resolve(structuredClone(records.slice(8))));
  assert.equal(renderer.root.findAllByType("li").length, 2);
  assert.equal(button("Previous page").props.disabled, false);
  assert.equal(button("Next page").props.disabled, true);
  assert.match(text(renderer.root), /Page 2/);
  await click("Previous page");
  assert.deepEqual(pageCalls.map(item => item.offset), [0, 8, 0]);
  assert.equal(renderer.root.findAllByType("li").length, 8);
  const deletion = deferred();
  api.deleteScenario = () => deletion.promise;
  await click("Delete Experiment 1");
  await click("Confirm delete");
  assert.equal(button("Deleting…").props.disabled, true);
  assert.equal(button("Next page").props.disabled, true);
  assert.equal(button("Previous page").props.disabled, true);
  assert.equal(button("Refresh saved scenarios").props.disabled, true);
  assert.equal(button("Delete Experiment 2").props.disabled, true);
  assert.equal(button("Load Experiment 2").props.disabled, true);
  records = records.slice(1);
  await act(async () => deletion.resolve());
  assert.deepEqual(deleted, [record(1).id]);
  assert.equal(button("Next page").props.disabled, false);
  assert.equal(button("Delete Experiment 2").props.disabled, false);
});

test("an automatic library refresh retains the deletion confirmation's original revision and surfaces its conflict", async () => {
  records = [record(1, 2)];
  await mount();
  await click("Delete Experiment 1");
  const confirmation = () => renderer.root.findByProps({ "aria-label": "Confirm deletion of Experiment 1" });
  assert.match(text(confirmation()), /revision 2/);
  records = [record(1, 3)];
  await update({ refreshKey: 1 });
  assert.match(text(renderer.root), /Revision 3/);
  assert.match(text(confirmation()), /revision 2/, "The newer row must not rewrite what the user confirmed");
  const calls = [];
  api.deleteScenario = async (id, revision) => {
    calls.push({ id, revision });
    throw new ApiError("Scenario revision changed", 409);
  };
  await click("Confirm delete");
  assert.deepEqual(calls, [{ id: record(1).id, revision: 2 }]);
  assert.deepEqual(deleted, [], "A conflicted deletion must not detach the workspace");
  assert.match(text(renderer.root), /Nothing was deleted\. Refresh the library before trying again/);
  assert.equal(renderer.root.findAllByType("li").length, 1);
  assert.match(text(renderer.root), /Revision 3/);
  assert.equal(renderer.root.findAllByProps({ "aria-label": "Confirm deletion of Experiment 1" }).length, 0);
  assert.deepEqual(pageCalls, [{ limit: 9, offset: 0 }, { limit: 9, offset: 0 }], "A conflict does not retry the deletion or refresh automatically");
});
