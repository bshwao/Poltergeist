import assert from "node:assert/strict";
import { after, test } from "node:test";
import { readFile } from "node:fs/promises";
import React from "react";
import TestRenderer, { act } from "react-test-renderer";
import { loadComponents } from "../scripts/load-components.mjs";

const fixture = JSON.parse(await readFile(new URL("./fixtures/review.json", import.meta.url), "utf8"));
const experiments = JSON.parse(await readFile(new URL("./fixtures/experimentation.json", import.meta.url), "utf8"));
const loaded = await loadComponents("pages/GuidedLab.tsx");
const { isEvidenceAnalysis, isRegressionReport, isRegressionPolicy } = await loaded.moduleAt("services/analysis-contract.ts");
const { EvidenceAnalystPanel } = await loaded.moduleAt("components/EvidenceAnalystPanel.tsx");
const { RegressionPanel } = await loaded.moduleAt("components/RegressionPanel.tsx");
const { SuiteResults } = await loaded.moduleAt("components/SuiteManager.tsx");
const { createHostedLabClient } = await loaded.moduleAt("hosted/lab-client.ts");
const { HostedError } = await loaded.moduleAt("hosted/client.ts");
const { api } = await loaded.moduleAt("services/api.ts");
after(() => loaded.cleanup());

const clone = value => structuredClone(value);
const text = node => typeof node === "string" || typeof node === "number" ? String(node) : (node?.children ?? []).map(text).join("");
const button = (view, label) => view.root.findAllByType("button").find(node => text(node) === label);
const deferred = () => { let resolve; const promise = new Promise(value => { resolve = value; }); return { promise, resolve }; };
async function render(t, Component, props) {
  let view; await act(async () => { view = TestRenderer.create(React.createElement(Component, props)); });
  t.after(() => act(() => view.unmount()));
  return view;
}

test("real Analyst and Regression API exports pass browser and relay contracts", () => {
  assert.equal(isEvidenceAnalysis(fixture.analysis), true);
  assert.equal(isRegressionReport(fixture.regression), true);
  assert.equal(api.validateLabResult({ kind: "analysis", analysis: fixture.analysis }), true);
  assert.equal(api.validateLabResult({ kind: "regression", report: fixture.regression }), true);
});

test("analysis contracts reject invented certainty, missing provenance and invalid frame links", () => {
  for (const mutate of [value => value.source_digest = null, value => value.coverage.safety_complete = false,
    value => value.observations[1].references[0].frame = -1, value => value.observations[1].references[0].time_s = null,
    value => value.observations[1].value = Infinity, value => value.method = "unbounded-inference", value => value.execution_status = "interrupted",
    value => value.hypotheses.push({ status: "verified", statement: "Made-up cause", review: "", references: [] })]) {
    const value = clone(fixture.analysis); mutate(value); assert.equal(isEvidenceAnalysis(value), false);
  }
});

test("regression contracts retain denominators and reject false clear results", () => {
  for (const mutate of [value => value.state = "no_regression_observed",
    value => value.assessments[0].observed_pairs = 4, value => value.policy.minimum_pairs = 1,
    value => value.policy.thresholds.severity = -1, value => value.pairs[0].comparable = false,
    value => value.evidence_review.bytes_read = value.evidence_review.byte_limit + 1,
    value => value.assessments[0].sample_stddev = NaN]) {
    const value = clone(fixture.regression); mutate(value); assert.equal(isRegressionReport(value), false);
  }
  assert.equal(isRegressionPolicy({ minimum_pairs: 3, thresholds: { severity: 0 } }), true);
  assert.equal(isRegressionPolicy({ minimum_pairs: 3, thresholds: {} }), false);
});

test("Analyst review starts only on request and frame zero retains an exact link", async t => {
  let calls = 0; const inspected = [];
  const client = { getEvidenceAnalysis: async id => { calls++; assert.equal(id, fixture.analysis.simulation_id); return fixture.analysis; } };
  const view = await render(t, EvidenceAnalystPanel, { simulationId: fixture.analysis.simulation_id, client,
    inspect: (frame, source) => inspected.push([frame, source]) });
  assert.equal(calls, 0);
  await act(async () => button(view, "Analyze recorded evidence").props.onClick());
  assert.equal(calls, 1);
  assert.match(text(view.toJSON()), /recorded frames contain contact events/);
  await act(async () => button(view, "Inspect frame 0").props.onClick());
  assert.deepEqual(inspected[0], [0, "telemetry"]);
  assert.match(text(view.toJSON()), /Decision tracesNot reviewed/);
});

test("unavailable evidence displays unknown coverage instead of zero observations", async t => {
  const unavailable = { ...clone(fixture.analysis), state: "unavailable", observations: [], source_digest: null,
    telemetry_digest: null, coverage: { telemetry_frames: null, telemetry_gaps: null, safety_complete: null,
      interventions_recorded: null, decision_trace: "not_reviewed" }, summary: "Evidence is unavailable." };
  const view = await render(t, EvidenceAnalystPanel, { simulationId: fixture.analysis.simulation_id,
    client: { getEvidenceAnalysis: async () => unavailable } });
  await act(async () => button(view, "Analyze recorded evidence").props.onClick());
  assert.match(text(view.toJSON()), /Recorded frames reviewedUnknown/);
  assert.doesNotMatch(text(view.toJSON()), /0 recorded frames|0.*contact events/);
});

test("hidden Analyst requests are cancelled and late data cannot replace the view", async t => {
  const pending = deferred(); let signal;
  const props = { simulationId: fixture.analysis.simulation_id, client: { getEvidenceAnalysis: async (_, options) => { signal = options.signal; return pending.promise; } } };
  const view = await render(t, EvidenceAnalystPanel, props);
  await act(async () => button(view, "Analyze recorded evidence").props.onClick());
  await act(async () => view.update(React.createElement(EvidenceAnalystPanel, { ...props, active: false })));
  assert.equal(signal.aborted, true);
  await act(async () => pending.resolve(fixture.analysis));
  assert.doesNotMatch(text(view.toJSON()), /recorded frames contain contact events/);
});

test("Regression review uses an explicit policy and never launches another run", async t => {
  const seen = [];
  const client = { getRegressionReport: async (id, policy) => { seen.push([id, policy]); return { ...fixture.regression, policy }; } };
  const view = await render(t, RegressionPanel, { operationId: fixture.regression.operation_id, client });
  assert.equal(seen.length, 0);
  await act(async () => button(view, "Review version regression").props.onClick());
  assert.equal(seen.length, 1); assert.deepEqual(seen[0][1], { minimum_pairs: 3, thresholds: { severity: 5, collision_count: 0 } });
  assert.match(text(view.toJSON()), /Worsening pattern observed/);
  assert.ok(view.root.findAllByType("a").some(node => node.props.href.endsWith(fixture.regression.pairs[0].reference_simulation_id)));
  assert.match(text(view.toJSON()), /Sample spread/);
});

test("invalid review policies are rejected before making a request", async t => {
  let calls = 0;
  const view = await render(t, RegressionPanel, { operationId: fixture.regression.operation_id,
    client: { getRegressionReport: async () => { calls++; return fixture.regression; } } });
  const minimum = view.root.findAllByType("input").find(node => node.props.max === "200");
  await act(async () => minimum.props.onChange({ target: { value: "1" } }));
  await act(async () => button(view, "Review version regression").props.onClick());
  assert.equal(calls, 0); assert.match(text(view.toJSON()), /2–200 matched pairs/);
});

test("switching saved suite results requires a fresh deliberate regression review", async t => {
  const first = clone(experiments.suite_report), second = clone(first), seen = [];
  second.evidence.operation.id = fixture.analysis.simulation_id;
  const client = { getRegressionReport: async (id, policy) => {
    seen.push(id); return { ...fixture.regression, operation_id: id, policy };
  } };
  const view = await render(t, SuiteResults, { report: first, client });
  await act(async () => button(view, "Review version regression").props.onClick());
  assert.deepEqual(seen, [first.evidence.operation.id]);
  await act(async () => view.update(React.createElement(SuiteResults, { report: second, client })));
  assert.deepEqual(seen, [first.evidence.operation.id]);
  assert.doesNotMatch(text(view.toJSON()), /Worsening pattern observed/);
  await act(async () => button(view, "Review version regression").props.onClick());
  assert.deepEqual(seen, [first.evidence.operation.id, second.evidence.operation.id]);
});

test("unsupported companions receive no report actions and can upgrade without rebuilding the adapter", async () => {
  let enabled = false; const seen = [];
  const target = { device: { device_id: fixture.analysis.simulation_id, policy: { max_ticks: 5000 } } };
  const adapter = createHostedLabClient({ lab: async (_, command) => { seen.push(command);
    return { kind: "lab", request_id: command.request_id, result: command.action.kind === "analysis_get"
      ? { kind: "analysis", analysis: fixture.analysis } : { kind: "regression", report: fixture.regression } };
  } }, target, () => true, undefined, () => enabled);
  try {
    await assert.rejects(adapter.getEvidenceAnalysis(fixture.analysis.simulation_id), /Update and reconnect/);
    assert.equal(seen.length, 0); enabled = true;
    await adapter.getEvidenceAnalysis(fixture.analysis.simulation_id);
    await adapter.getRegressionReport(fixture.regression.operation_id);
    assert.deepEqual(seen.map(item => item.action.kind), ["analysis_get", "regression_report"]);
    assert.ok(seen.every(item => item.mode === null));
  } finally { adapter.dispose(); }
});

test("losing report capability during backpressure prevents another dispatch", async () => {
  let enabled = true, calls = 0;
  const adapter = createHostedLabClient({ lab: async () => { calls++; enabled = false; throw new HostedError("transfer_busy", 429, 0); } },
    { device: { policy: { max_ticks: 5000 } } }, () => true, undefined, () => enabled);
  try { await assert.rejects(adapter.getEvidenceAnalysis(fixture.analysis.simulation_id), /Update and reconnect/); assert.equal(calls, 1); }
  finally { adapter.dispose(); }
});
