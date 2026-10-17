import test from "node:test";
import assert from "node:assert/strict";
import { loadTypeScript } from "../scripts/load-typescript.mjs";

const { sameRecipe, bindScenarioForm, reconcileSave } = await loadTypeScript(new URL("../src/components/scenario-workspace.ts", import.meta.url));
const recipe = { name: "Saved crossing", seed: 7, duration_s: 90, template: "intersection", parameters: { ego_speed_mps: 9 }, actors: [{ id: "ego", speed_mps: 9 }] };
const source = { id: "source", revision: 2, scenario: recipe };
const persisted = { ...source, revision: 3 };
const templates = [{ template: "intersection", parameters: [{ name: "ego_speed_mps", default: 12 }] }];

test("saved recipe comparison ignores object-key order while retaining executable differences", () => {
  assert.equal(sameRecipe(recipe, { actors: [{ speed_mps: 9, id: "ego" }], parameters: { ego_speed_mps: 9 }, template: "intersection", seed: 7, duration_s: 90, name: "Saved crossing" }), true);
  assert.equal(sameRecipe(recipe, { ...recipe, seed: 8 }), false);
  assert.equal(sameRecipe(recipe, { ...recipe, duration_s: 120 }), false);
  assert.equal(sameRecipe(recipe, { ...recipe, actors: [{ id: "ego", speed_mps: 8 }] }), false);
});

test("loading a bound recipe restores saved duration, parameters, and seed rather than template defaults", () => {
  assert.deepEqual(bindScenarioForm(recipe, templates), { selectedId: "intersection", seed: "7", duration: "90", values: { ego_speed_mps: "9" } });
});

test("custom recipes never borrow an unrelated template form", () => {
  assert.deepEqual(bindScenarioForm({ ...recipe, template: null, parameters: {} }, templates), { selectedId: null, seed: "7", duration: "90", values: {} });
  assert.equal(bindScenarioForm(recipe, []).selectedId, null);
});

test("a completed update advances the expected revision without replacing newer draft edits", () => {
  assert.deepEqual(reconcileSave(source, source, persisted, "update", false), { record: persisted, replaceDraft: false });
});

test("a completed create or copy does not attach newer edits to the persisted snapshot", () => {
  assert.deepEqual(reconcileSave(null, null, persisted, "create", false), { record: null, replaceDraft: false });
  assert.deepEqual(reconcileSave(source, source, { ...persisted, id: "copy" }, "create", false), { record: source, replaceDraft: false });
});

test("a late save cannot replace a newly loaded or deleted source association", () => {
  const newer = { ...source, id: "different" };
  assert.deepEqual(reconcileSave(newer, source, persisted, "update", true), { record: newer, replaceDraft: false });
  assert.deepEqual(reconcileSave(null, source, persisted, "update", true), { record: null, replaceDraft: false });
  const concurrentRevision = { ...source, revision: 5 };
  assert.deepEqual(reconcileSave(concurrentRevision, source, persisted, "update", true), { record: concurrentRevision, replaceDraft: false });
});

test("an unchanged draft adopts the authoritative saved snapshot", () => {
  assert.deepEqual(reconcileSave(null, null, persisted, "create", true), { record: persisted, replaceDraft: true });
  assert.deepEqual(reconcileSave(source, source, persisted, "update", true), { record: persisted, replaceDraft: true });
});
