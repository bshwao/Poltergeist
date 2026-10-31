import { useState } from "react";
import { api } from "../services/api";
import type { LabClient } from "../services/lab-client";
import type { RegressionPolicy } from "../types/generated";
import { useLabReport } from "../hooks/useLab";
import { simulationLink } from "../navigation";
import { saveLabJson } from "./lab-download";

const measurements = [
  { key: "severity", label: "Heuristic severity", unit: "points", initial: "5" },
  { key: "collision_count", label: "Contact episodes", unit: "episodes", initial: "0" },
  { key: "min_ttc_s", label: "Minimum TTC", unit: "seconds", initial: "0.5" },
  { key: "min_clearance_m", label: "Minimum clearance", unit: "metres", initial: "0.5" },
  { key: "max_deceleration_mps2", label: "Peak deceleration", unit: "m/s²", initial: "1" },
  { key: "hard_braking_count", label: "Hard-braking episodes", unit: "episodes", initial: "0" },
  { key: "traffic_violation_count", label: "Rule-event episodes", unit: "episodes", initial: "0" },
] as const;
type Metric = typeof measurements[number]["key"];
const number = (value: number | null) => value === null ? "Unavailable" : Number(value.toPrecision(4)).toString();
const labels = { regression_observed: "Worsening pattern observed", no_regression_observed: "No selected regression observed", inconclusive: "Inconclusive", pending: "Suite still running" };

export function RegressionPanel({ operationId, active = true, available = true, client = api, runHref = simulationLink }: {
  operationId: string; active?: boolean; available?: boolean; client?: Pick<LabClient, "getRegressionReport">; runHref?: (id: string) => string }) {
  const [minimum, setMinimum] = useState("3"), [selected, setSelected] = useState<Metric[]>(["severity", "collision_count"]);
  const [thresholds, setThresholds] = useState<Record<string, string>>(() => Object.fromEntries(measurements.map(item => [item.key, item.initial])));
  const [policy, setPolicy] = useState<RegressionPolicy | null>(null), [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(0), [pairPage, setPairPage] = useState(0);
  const supported = available && typeof client.getRegressionReport === "function";
  const identity = policy ? JSON.stringify([operationId, policy]) : null;
  const state = useLabReport(identity, active && supported, (_, signal) => client.getRegressionReport(operationId, policy!, { signal }), value => value.state !== "pending");
  const report = state.value;
  function review() {
    const minimumPairs = Number(minimum);
    if (!minimum.trim() || !Number.isInteger(minimumPairs) || minimumPairs < 2 || minimumPairs > 200) { setError("Choose a minimum of 2–200 matched pairs."); return; }
    if (!selected.length) { setError("Select at least one measurement to review."); return; }
    const entries = selected.map(key => [key, Number(thresholds[key])] as const);
    if (entries.some(([key, value]) => !thresholds[key].trim() || !Number.isFinite(value) || value < 0)) { setError("Thresholds must be finite, nonnegative measurements."); return; }
    const next: RegressionPolicy = { minimum_pairs: minimumPairs, thresholds: Object.fromEntries(entries) };
    setError(null); setPage(0); setPairPage(0);
    if (JSON.stringify(next) === JSON.stringify(policy)) state.refresh(); else setPolicy(next);
  }
  return <section className="lab-surface panel regression-agent" aria-label="Regression Agent">
    <div className="panel-header"><h2>Regression Agent</h2><span className="lab-tag">Frozen versions · matched evidence</span></div>
    <div className="panel-body">
      <p>Review this suite's recorded comparisons. To collect more evidence, choose controller versions in reference order, adjust a saved suite and launch it explicitly.</p>
      {!supported ? <p role="status">Update and reconnect the companion to enable regression review.</p> : <>
        <fieldset className="review-policy"><legend>Regression review policy</legend>
          <label>Minimum observed matched pairs<input type="number" min="2" max="200" step="1" value={minimum} onChange={event => setMinimum(event.target.value)} /></label>
          <div className="review-metric-options">{measurements.map(item => <div key={item.key}>
            <label className="lab-check"><input type="checkbox" checked={selected.includes(item.key)} onChange={event => setSelected(previous => event.target.checked ? [...previous, item.key] : previous.filter(key => key !== item.key))} />{item.label}</label>
            {selected.includes(item.key) && <label>Worsening threshold · {item.label} ({item.unit})<input type="number" min="0" step="any" value={thresholds[item.key]} onChange={event => setThresholds(previous => ({ ...previous, [item.key]: event.target.value }))} /></label>}
          </div>)}</div>
        </fieldset>
        <button type="button" className="button secondary" disabled={!active || state.loading} onClick={review}>{state.loading ? "Reviewing matched evidence…" : "Review version regression"}</button>
      </>}
      {(error || state.error) && <p className="notice error-notice" role="alert">{error ?? state.error}{state.error && report && " Showing the previous review; the latest source could not be confirmed."}</p>}
      {report && <div className="review-stack">
        <div className="notice"><h3>{labels[report.state]}</h3><p>{report.summary}</p><p>This report uses {report.policy.minimum_pairs} minimum pairs and the thresholds below. Submitted executions: {report.execution_status}.</p></div>
        <ul className="review-controllers">{report.controllers.map(item => <li key={item.agent_index}><strong>{item.agent_index === 0 ? "Reference" : `Candidate ${item.agent_index}`}: {item.name}</strong>
          <span>Version {item.version ?? "not recorded"} · Artifact/configuration <code title={item.execution_digest ?? undefined}>{item.execution_digest ? item.execution_digest.slice(0, 12) + "…" : "not recorded"}</code></span></li>)}</ul>
        {report.context_notes.length > 0 && <ul className="notice">{report.context_notes.map(item => <li key={item}>{item}</li>)}</ul>}
        <p>Verified saved traces: {report.evidence_review.available_runs}. Unavailable: {report.evidence_review.unavailable_runs}. Review-limited: {report.evidence_review.limited_runs}. Missing observations are excluded from differences.</p>
        {report.assessments.length ? <>
          <div className="table-scroll" tabIndex={0} role="region" aria-label="Regression assessments"><table className="data-table"><thead><tr><th>Case / candidate</th><th>Measurement / threshold</th><th>Matched pairs</th><th>Mean change</th><th>Sample spread</th><th>Review</th></tr></thead>
            <tbody>{report.assessments.slice(page * 20, page * 20 + 20).map((item, index) => <tr key={index}>
              <th scope="row">{item.case_name}<small>Candidate {item.compared_agent_index}</small></th>
              <td>{measurements.find(metric => metric.key === item.metric)?.label}<small>{item.worsening_direction} is worse · threshold {item.threshold} {item.unit}</small></td>
              <td>{item.observed_pairs} / {item.planned_pairs}<small>{item.worsening_pairs} exceed threshold</small></td><td>{number(item.mean_change)}<small>compared minus reference</small></td><td>{number(item.sample_stddev)}</td>
              <td>{labels[item.decision]}<details><summary>Reason and uncertainty</summary><p>{item.explanation}</p><p>Range: {number(item.minimum_change)} to {number(item.maximum_change)}</p></details></td>
            </tr>)}</tbody></table></div>
          <div className="button-row"><button className="button secondary small" disabled={!page} onClick={() => setPage(value => value - 1)}>Previous assessments</button><span>Page {page + 1}</span><button className="button secondary small" disabled={(page + 1) * 20 >= report.assessments.length} onClick={() => setPage(value => value + 1)}>Next assessments</button></div>
        </> : <p>No version comparisons were recorded in this suite. Choose two registered versions in the local saved-suite workflow, with the reference first.</p>}
        <details><summary>Matched run evidence · {report.pairs.length} pairs</summary>
          <ul className="review-pairs">{report.pairs.slice(pairPage * 20, pairPage * 20 + 20).map((pair, index) => <li key={index}>
            <strong>Case {pair.case_index + 1} · candidate {pair.compared_agent_index} · seed {pair.seed} · repetition {pair.repeat_index + 1}</strong><p>{pair.reason}</p>
            <div className="button-row">{pair.reference_simulation_id && <a href={runHref(pair.reference_simulation_id)}>Inspect reference run</a>}{pair.compared_simulation_id && <a href={runHref(pair.compared_simulation_id)}>Inspect candidate run</a>}</div>
            <p>{Object.entries(pair.differences).map(([key, value]) => `${key}: ${number(value)}`).join(" · ")}</p>
          </li>)}</ul>
          <div className="button-row"><button className="button secondary small" disabled={!pairPage} onClick={() => setPairPage(value => value - 1)}>Previous matched pairs</button><span>Page {pairPage + 1}</span><button className="button secondary small" disabled={(pairPage + 1) * 20 >= report.pairs.length} onClick={() => setPairPage(value => value + 1)}>Next matched pairs</button></div>
        </details>
        <details><summary>Reproduction, source identity and limits</summary><ul>{report.limitations.map(item => <li key={item}>{item}</li>)}</ul>
          <p>Frozen suite fingerprint: <code>{report.plan_digest}</code></p><p>Reviewed source fingerprint: <code>{report.source_digest}</code></p><p>Download the existing suite comparison evidence as the complete frozen reproduction journal.</p></details>
        <button type="button" className="button secondary" onClick={() => saveLabJson(report, `poltergeist-regression-${operationId}.json`)}>Download regression review</button>
      </div>}
    </div>
  </section>;
}
