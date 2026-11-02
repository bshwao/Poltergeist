import assert from "node:assert/strict";
import { after, test } from "node:test";
import React from "react";
import TestRenderer, { act } from "react-test-renderer";
import { loadComponents } from "../scripts/load-components.mjs";

const loaded = await loadComponents("components/lazyPage.tsx");
const { lazyPage } = loaded.module;
after(() => loaded.cleanup());
const text = node => typeof node === "string" ? node : (node?.children ?? []).map(text).join("");

test("a failed page import leaves sibling drafts intact and offers an explicit reload", async t => {
  t.mock.method(console, "error", () => {}); // Expected React error-boundary diagnostic.
  const originalWindow = globalThis.window; let reloads = 0;
  globalThis.window = { location: { reload: () => reloads++ } };
  t.after(() => { if (originalWindow === undefined) delete globalThis.window; else globalThis.window = originalWindow; });
  let reject, calls = 0;
  const pending = new Promise((_, no) => { reject = no; });
  const Page = lazyPage(() => { calls++; return pending; }, "Experiments");
  function Workspace() {
    const [draft, setDraft] = React.useState("");
    return React.createElement(React.Fragment, null,
      React.createElement("input", { value: draft, onChange: e => setDraft(e.target.value) }), React.createElement(Page));
  }
  let view; await act(async () => { view = TestRenderer.create(React.createElement(Workspace)); });
  t.after(() => act(() => view.unmount()));
  assert.match(text(view.root.findByProps({ role: "status" })), /Loading Experiments/);
  act(() => view.root.findByType("input").props.onChange({ target: { value: "Unsaved recipe" } }));
  await act(async () => reject(new Error("A failed import must not expose raw diagnostic data")));
  const alert = view.root.findByProps({ role: "alert" });
  assert.match(text(alert), /Unable to open Experiments/);
  assert.doesNotMatch(text(alert), /raw diagnostic data/);
  assert.match(text(alert), /Copy any unsaved edits first/);
  assert.equal(view.root.findByType("input").props.value, "Unsaved recipe");
  act(() => alert.findByType("button").props.onClick());
  assert.equal(calls, 1); assert.equal(reloads, 1);
});
