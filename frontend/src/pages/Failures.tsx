import { useEffect, useRef, useState } from "react";
import { FailureTable } from "../components/FailureTable";
import { AgentIdentity } from "../components/AgentIdentity";
import { api } from "../services/api";
import { comparisonNote, failureKindLabel, metricRows, modeLabel, observation, reproductionLabel, settingsDifferences, validSimulationId } from "../services/failure-view";
import type { FailureReport, SimulationRecord } from "../types/generated";

type ReproductionFilter = "all" | "true" | "false" | "unknown";
interface Evidence { report: FailureReport; run: SimulationRecord }
const PAGE_SIZE = 8;
const errorMessage = (error: unknown): string => error instanceof Error ? error.message : "The failure report could not be loaded.";

async function readEvidence(id: string, signal: AbortSignal): Promise<Evidence> {
  const report = await api.getFailure(id, { signal });
  if (signal.aborted) throw new DOMException("Request superseded", "AbortError");
  const run = await api.getSimulation(report.simulation_id, { signal });
  return { report, run };
}

function ReproductionObservation({ report, active, onSaved }: { report: FailureReport; active: boolean; onSaved: (report: FailureReport) => void }) {
  const [value, setValue] = useState(report.reproduced === null ? "unknown" : String(report.reproduced));
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const request = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const activeRef = useRef(active);
  activeRef.current = active;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; request.current?.abort(); }; }, []);
  useEffect(() => { if (!active) request.current?.abort(); }, [active]);
  useEffect(() => { setValue(report.reproduced === null ? "unknown" : String(report.reproduced)); }, [report.reproduced]);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (request.current || !activeRef.current) return;
    const controller = new AbortController();
    request.current = controller;
    const id = report.id;
    const reproduced = value === "unknown" ? null : value === "true";
    setSaving(true); setError(null); setMessage(null);
    try {
      const updated = await api.updateReproduction(id, reproduced, { signal: controller.signal });
      if (!mounted.current || !activeRef.current || controller.signal.aborted) return;
      onSaved(updated);
      setMessage(`Saved manual observation: ${reproductionLabel(updated.reproduced)}. No simulation was run.`);
    } catch (failure) {
      if (!mounted.current || !activeRef.current) return;
      setError(`${errorMessage(failure)} The update may have reached the API; refresh this report before retrying.`);
    } finally {
      if (request.current === controller) request.current = null;
      if (mounted.current) setSaving(false);
    }
  }

  return <form className="reproduction-observation" onSubmit={event => { void save(event); }}>
    <h3>Record a manual reproduction observation</h3><p>This changes only the report’s evidence flag. It does not rerun the recipe or verify your observation. Use Unknown when evidence is insufficient.</p>
    <div className="filter-bar"><div className="field"><label htmlFor={`reproduction-observation-${report.id}`}>Recorded reproduction evidence</label><select id={`reproduction-observation-${report.id}`} value={value} disabled={saving || !active} onChange={event => { setValue(event.target.value); setMessage(null); }}><option value="unknown">Unknown</option><option value="true">Confirmed</option><option value="false">Not reproduced</option></select></div><button type="submit" className="button secondary small" disabled={saving || !active}>{saving ? "Saving observation…" : "Save reproduction observation"}</button></div>
    {error && <p className="notice error-notice" role="alert">{error}</p>}{message && <p className="notice" role="status">{message}</p>}
  </form>;
}

function EvidenceFacts({ evidence }: { evidence: Evidence }) {
  const { report, run } = evidence;
  const result = run.result;
  return <>
    <p className="mode-label"><strong>{modeLabel(run.simulation.mode)}</strong>{run.simulation.mode === "test" ? " · Synthetic observations; real driving behavior is unverified." : " · Recorded CARLA execution."}</p>
    <dl className="detail-facts"><div><dt>Report ID</dt><dd><code>{report.id}</code></dd></div><div><dt>Parent run</dt><dd><a href={`#simulations/${report.simulation_id}`}>{report.simulation_id}</a></dd></div><div><dt>Kind</dt><dd>{failureKindLabel(report.kind)}</dd></div><div><dt>Reported severity</dt><dd>{observation(report.severity)} / 100</dd></div><div><dt>Reproduction</dt><dd>{reproductionLabel(report.reproduced)}</dd></div><div><dt>Recipe seed</dt><dd>{report.scenario.seed}</dd></div><div><dt>Map</dt><dd>{report.scenario.map}</dd></div><div><dt>Run status</dt><dd>{run.simulation.status}</dd></div><div><dt>Measured duration</dt><dd>{observation(result?.measured_duration_s)} s</dd></div><div><dt>Telemetry frames</dt><dd>{observation(result?.telemetry_frames)}</dd></div></dl>
    <p>{report.summary}</p><div className="button-row"><a className="button secondary" href={`#experiments/reproduce/${report.id}`}>Run reproduction trials</a><a className="button secondary" href={`#experiments/minimize/${report.id}`}>Minimize this failure</a></div>
    <AgentIdentity simulation={run.simulation} detailed inputs={result?.agent_inputs} />
    <p className="muted">Reproduction records can be confirmed, not reproduced, or unknown. Opening a report does not rerun its scenario.</p>
    {result ? <>
      <div className="table-scroll"><table className="data-table"><caption>Parent run observations</caption><thead><tr><th scope="col">Metric</th><th scope="col">Observed value</th></tr></thead><tbody>{metricRows.map(row => <tr key={row.key}><th scope="row">{row.label}</th><td>{observation(result.metrics[row.key])}</td></tr>)}</tbody></table></div>
      <Coverage run={run} />
    </> : <p className="notice">No finalized result is available for the parent run. Observation coverage is unknown.</p>}
    <details><summary>Saved recipe and seed</summary><pre>{JSON.stringify(report.scenario, null, 2)}</pre></details>
    <details><summary>Execution and evaluation settings</summary><pre>{JSON.stringify({ mode: run.simulation.mode, agent: run.simulation.agent, options: run.job?.options ?? null, evaluation_settings: result?.metrics.evaluation_settings ?? run.job?.evaluation_settings ?? null }, null, 2)}</pre></details>
    <details><summary>Runtime provenance</summary>{result?.runtime ? <pre>{JSON.stringify(result.runtime, null, 2)}</pre> : <p>Runtime provenance is unavailable.</p>}</details>
  </>;
}

function Coverage({ run }: { run: SimulationRecord }) {
  const coverage = run.result?.metrics.coverage;
  if (!coverage) return <p>Observation coverage: Unknown.</p>;
  return <div className="evidence-coverage"><h3>Observation coverage</h3><p>Zero observed events does not establish absence when coverage is partial. Unknown values remain unknown.</p><dl className="detail-facts"><div><dt>Frames</dt><dd>{coverage.total_frames}</dd></div><div><dt>Sample gaps</dt><dd>{coverage.has_gaps ? "Present" : "None detected"}</dd></div><div><dt>Collision frames</dt><dd>{coverage.collision_frames} / {coverage.total_frames}</dd></div><div><dt>Braking frames</dt><dd>{coverage.braking_frames} / {coverage.total_frames}</dd></div><div><dt>Traffic rule frames</dt><dd>{coverage.traffic_rule_frames} / {coverage.total_frames}</dd></div><div><dt>Clearance frames</dt><dd>{coverage.clearance_frames} / {coverage.total_frames}</dd></div><div><dt>TTC frames</dt><dd>{coverage.ttc_frames} / {coverage.total_frames}</dd></div><div><dt>Clearance pairs</dt><dd>{coverage.clearance_pairs} / {coverage.actor_pairs}</dd></div><div><dt>TTC pairs</dt><dd>{coverage.ttc_pairs} / {coverage.actor_pairs}</dd></div></dl></div>;
}

function Comparison({ items }: { items: [Evidence, Evidence] }) {
  const [left, right] = items;
  const note = comparisonNote(left.run, right.run);
  const differences = settingsDifferences(left.run.result?.metrics.evaluation_settings ?? null, right.run.result?.metrics.evaluation_settings ?? null);
  return <>
    <p className={`notice${note.comparable ? "" : " settings-difference"}`}>{note.message}</p>
    <div className="table-scroll"><table className="data-table comparison-table"><caption>Observed metrics, side by side. No risk reduction is inferred.</caption><thead><tr><th scope="col">Observation</th>{items.map(item => <th key={item.report.id} scope="col">{item.report.scenario.name}<br /><strong>{modeLabel(item.run.simulation.mode)}</strong><br /><small>{item.report.id}</small></th>)}</tr></thead><tbody>
      <tr><th scope="row">Failure kind</th>{items.map(item => <td key={item.report.id}>{failureKindLabel(item.report.kind)}</td>)}</tr>
      <tr><th scope="row">Recorded agent</th>{items.map(item => <td key={item.report.id}><AgentIdentity simulation={item.run.simulation} /></td>)}</tr>
      <tr><th scope="row">Reproduction</th>{items.map(item => <td key={item.report.id}>{reproductionLabel(item.report.reproduced)}</td>)}</tr>
      <tr><th scope="row">Recipe seed</th>{items.map(item => <td key={item.report.id}>{item.report.scenario.seed}</td>)}</tr>
      <tr><th scope="row">Parent run</th>{items.map(item => <td key={item.report.id}><a href={`#simulations/${item.report.simulation_id}`}>{item.report.simulation_id}</a></td>)}</tr>
      {metricRows.map(row => <tr key={row.key}><th scope="row">{row.label}</th>{items.map(item => <td key={item.report.id}>{observation(item.run.result?.metrics[row.key])}</td>)}</tr>)}
    </tbody></table></div>
    {differences.length > 0 && <div className="settings-difference"><h3>Evaluation settings differences</h3><ul>{differences.map(difference => <li key={difference}>{difference}</li>)}</ul></div>}
    <div className="comparison-grid">{items.map(item => <section key={item.report.id}><h3>{item.report.scenario.name} · {modeLabel(item.run.simulation.mode)}</h3><Coverage run={item.run} /><details><summary>Execution and evaluation provenance</summary><pre>{JSON.stringify({ agent: item.run.simulation.agent, options: item.run.job?.options ?? null, evaluation_settings: item.run.result?.metrics.evaluation_settings ?? null, runtime: item.run.result?.runtime ?? null }, null, 2)}</pre></details></section>)}</div>
  </>;
}

export function Failures({ active = true }: { active?: boolean }) {
  const [reproduced, setReproduced] = useState<ReproductionFilter>("all");
  const [simulationDraft, setSimulationDraft] = useState("");
  const [simulationId, setSimulationId] = useState("");
  const [filterError, setFilterError] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [listRefresh, setListRefresh] = useState(0);
  const [records, setRecords] = useState<FailureReport[]>([]);
  const [hasNext, setHasNext] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [detail, setDetail] = useState<Evidence | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [comparisonIds, setComparisonIds] = useState<string[]>([]);
  const [comparison, setComparison] = useState<[Evidence, Evidence] | null>(null);
  const [comparisonError, setComparisonError] = useState<string | null>(null);
  const [comparisonLoading, setComparisonLoading] = useState(false);

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    let current = true;
    setLoading(true); setError(null); setRecords([]); setHasNext(false);
    void api.listFailures({ limit: PAGE_SIZE + 1, offset, reproduced, ...(simulationId ? { simulation_id: simulationId } : {}) }, { signal: controller.signal }).then(items => {
      if (!current) return;
      setRecords(items.slice(0, PAGE_SIZE)); setHasNext(items.length > PAGE_SIZE);
    }, failure => { if (current) setError(errorMessage(failure)); }).finally(() => { if (current) setLoading(false); });
    return () => { current = false; controller.abort(); };
  }, [active, offset, reproduced, simulationId, refresh, listRefresh]);

  useEffect(() => {
    if (!active || !detailId) return;
    const controller = new AbortController();
    let current = true;
    setDetail(null); setDetailError(null); setDetailLoading(true);
    void readEvidence(detailId, controller.signal).then(value => { if (current) setDetail(value); }, failure => { if (current) setDetailError(errorMessage(failure)); }).finally(() => { if (current) setDetailLoading(false); });
    return () => { current = false; controller.abort(); };
  }, [active, detailId, refresh]);

  useEffect(() => {
    if (!active || comparisonIds.length !== 2) return;
    const controller = new AbortController();
    let current = true;
    setComparison(null); setComparisonError(null); setComparisonLoading(true);
    void Promise.all(comparisonIds.map(id => readEvidence(id, controller.signal))).then(value => { if (current) setComparison(value as [Evidence, Evidence]); }, failure => { if (current) setComparisonError(errorMessage(failure)); }).finally(() => { if (current) setComparisonLoading(false); });
    return () => { current = false; controller.abort(); };
  }, [active, comparisonIds, refresh]);

  function toggleComparison(report: FailureReport) {
    setComparison(null); setComparisonError(null); setComparisonLoading(false);
    setComparisonIds(ids => ids.includes(report.id) ? ids.filter(id => id !== report.id) : ids.length < 2 ? [...ids, report.id] : ids);
  }

  return <div className="failure-page">
    <header className="page-heading"><div><p className="eyebrow">FAILURE EVIDENCE</p><h1>Failure reports<span className="title-dot">.</span></h1><p className="page-description">Inspect recorded failures, their original recipes, and the observations behind them.</p></div><button type="button" className="button secondary" disabled={!active || loading} onClick={() => setRefresh(value => value + 1)}>Refresh reports</button></header>
    <section className="panel" aria-labelledby="failure-library-heading"><div className="panel-header"><h2 id="failure-library-heading">Recorded reports</h2></div>
      <form className="filter-bar" onSubmit={event => { event.preventDefault(); const value = simulationDraft.trim(); if (value && !validSimulationId(value)) { setFilterError("Enter a valid simulation ID, or leave it empty to include all runs."); return; } setFilterError(null); setOffset(0); setSimulationId(value); }}>
        <div className="field"><label htmlFor="failure-reproduction">Reproduction evidence</label><select id="failure-reproduction" value={reproduced} onChange={event => { setOffset(0); setReproduced(event.target.value as ReproductionFilter); }}><option value="all">All evidence states</option><option value="true">Confirmed</option><option value="false">Not reproduced</option><option value="unknown">Unknown</option></select></div>
        <div className="field"><label htmlFor="failure-simulation">Parent simulation ID (optional)</label><input id="failure-simulation" value={simulationDraft} onChange={event => setSimulationDraft(event.target.value)} aria-invalid={Boolean(filterError)} aria-describedby={filterError ? "failure-filter-error" : undefined} placeholder="All parent runs" /></div><button type="submit" className="button secondary">Apply run filter</button>
      </form>
      {filterError && <p className="notice error-notice" role="alert" id="failure-filter-error">{filterError}</p>}
      <p className="muted">Filters apply to all stored reports. Mode and observation coverage are available in each report. Unknown reproduction evidence is distinct from a failed reproduction.</p>
      {error && <div className="notice error-notice" role="alert"><p>{error}</p><button type="button" className="button secondary small" onClick={() => setRefresh(value => value + 1)}>Retry reports</button></div>}
      {loading && <p role="status">Loading failure reports…</p>}
      {!loading && !error && records.length === 0 && <p>No failure reports on this page for the selected filters.</p>}
      {records.length > 0 && <FailureTable failures={records} selectedId={detailId} comparisonIds={comparisonIds} onSelect={setDetailId} onCompare={toggleComparison} />}
      <div className="pagination"><button type="button" className="button secondary small" disabled={!active || loading || offset === 0} onClick={() => setOffset(value => Math.max(0, value - PAGE_SIZE))}>Previous failure page</button><span>Page {Math.floor(offset / PAGE_SIZE) + 1}</span><button type="button" className="button secondary small" disabled={!active || loading || !hasNext} onClick={() => setOffset(value => value + PAGE_SIZE)}>Next failure page</button></div>
    </section>
    <section className="panel failure-detail" aria-labelledby="failure-detail-heading"><div className="panel-header"><h2 id="failure-detail-heading">Report detail</h2>{detailId && <button type="button" className="button secondary small" onClick={() => { setDetailId(null); setDetail(null); setDetailError(null); setDetailLoading(false); }}>Close report</button>}</div>
      {!detailId ? <p>Open a recorded report to see its parent run, recipe, execution settings, and evidence coverage.</p> : detailError ? <div className="notice error-notice" role="alert"><p>{detailError}</p><button type="button" className="button secondary small" onClick={() => setRefresh(value => value + 1)}>Retry report detail</button></div> : detailLoading || !detail ? <p role="status">Loading failure evidence…</p> : <><EvidenceFacts evidence={detail} /><ReproductionObservation key={detail.report.id} report={detail.report} active={active} onSaved={updated => {
        setDetail(current => current?.report.id === updated.id ? { ...current, report: updated } : current);
        setRecords(items => items.map(item => item.id === updated.id ? updated : item));
        setListRefresh(value => value + 1);
        setComparison(items => items ? items.map(item => item.report.id === updated.id ? { ...item, report: updated } : item) as [Evidence, Evidence] : null);
      }} /></>}
    </section>
    <section className="panel" aria-labelledby="failure-comparison-heading"><div className="panel-header"><h2 id="failure-comparison-heading">Compare two reports</h2>{comparisonIds.length > 0 && <button type="button" className="button secondary small" onClick={() => { setComparisonIds([]); setComparison(null); setComparisonError(null); setComparisonLoading(false); }}>Clear comparison</button>}</div>
      <p>Select up to two reports, including across pages. Comparisons show actual parent run observations and retain execution mode and evaluation provenance.</p>
      <ul aria-label="Selected comparison reports">{comparisonIds.map((id, index) => <li key={id}>Report {index + 1}: <code>{id}</code> <button type="button" className="text-button" onClick={() => { setComparisonIds(ids => ids.filter(value => value !== id)); setComparison(null); setComparisonError(null); setComparisonLoading(false); }} aria-label={`Remove comparison ${id}`}>Remove</button></li>)}</ul>
      {comparisonIds.length < 2 ? <p>{comparisonIds.length} of 2 reports selected.</p> : comparisonError ? <div className="notice error-notice" role="alert"><p>{comparisonError}</p><button type="button" className="button secondary small" onClick={() => setRefresh(value => value + 1)}>Retry comparison</button></div> : comparisonLoading || !comparison ? <p role="status">Loading comparison evidence…</p> : <Comparison items={comparison} />}
    </section>
  </div>;
}
