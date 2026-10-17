import assert from "node:assert/strict";
import { after, test } from "node:test";
import React from "react";
import TestRenderer, { act } from "react-test-renderer";
import { loadComponents } from "../scripts/load-components.mjs";

const loaded = await loadComponents("components/ReportDownloads.tsx");
const { ReportDownloads } = loaded.module;
after(() => loaded.cleanup());
test("run exports request full telemetry only after the user selects it for JSON", t => {
  const id = "00000000-0000-4000-8000-000000000001";
  let view;
  act(() => { view = TestRenderer.create(React.createElement(ReportDownloads, { simulationId: id })); });
  t.after(() => act(() => view.unmount()));
  const links = () => view.root.findAllByType("a");
  assert.equal(links().length, 3);
  assert.ok(links().every(link => link.props.download === true && link.props.href.includes(`/reports/simulations/${id}?format=`)));
  assert.ok(links().every(link => !link.props.href.includes("include_telemetry")));
  act(() => view.root.findByType("input").props.onChange({ target: { checked: true } }));
  assert.match(links()[0].props.href, /format=json&include_telemetry=true$/);
  assert.match(links()[1].props.href, /format=csv$/);
  assert.match(links()[2].props.href, /format=html$/);
});
