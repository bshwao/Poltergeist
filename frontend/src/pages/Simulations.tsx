import { useEffect, useId, useRef, useState } from "react";
import { MetricsChart } from "../components/MetricsChart";
import { SafetyMetrics } from "../components/SafetyMetrics";
import { SimulationViewer } from "../components/SimulationViewer";
import { FailureReportForm } from "../components/FailureReportForm";
import { ReportDownloads } from "../components/ReportDownloads";
import { AgentIdentity } from "../components/AgentIdentity";
import { DecisionTimeline } from "../components/DecisionTimeline";
import { BehaviorReview } from "../components/BehaviorReview";
import type { EvidenceFocus } from "../components/evidence-navigation";
import { isTerminal, useSimulation } from "../hooks/useSimulation";
import { simulationLink } from "../navigation";
import { api, ApiError } from "../services/api";
import type { SimulationRecord, SimulationStatus } from "../types/generated";

const PAGE_SIZE = 10;
const statuses: SimulationStatus[] = ["queued", "running", "completed", "failed", "cancelled"];
export const modeName = (mode: "carla" | "test") => mode === "test" ? "Synthetic test" : "CARLA";
const date = (value: string | null) => value ? new Date(value).toLocaleString() : "Not recorded";

export function Simulations({ active, selectedId }: { active: boolean; selectedId: string | null }) {
  const prefix = useId();
  const [mode, setMode] = useState<"" | "carla" | "test">("");
  const [status, setStatus] = useState<"" | SimulationStatus>("");
  const [offset, setOffset] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const [history, setHistory] = useState<{ key: string; rows: SimulationRecord[]; loading: boolean; error: string | null; at: string | null } | null>(null);
  const key = `${mode}/${status}/${offset}`;
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    let current = true;
    const deadline = setTimeout(() => controller.abort(), 12_000);
    setHistory({ key, rows: [], loading: true, error: null, at: null });
    void api.listSimulations({ limit: PAGE_SIZE + 1, offset, ...(mode ? { mode } : {}), ...(status ? { status } : {}) }, { signal: controller.signal }).then(rows => {
      if (current && !controller.signal.aborted) setHistory({ key, rows, loading: false, error: null, at: new Date().toLocaleTimeString() });
    }, error => {
      if (current) setHistory({ key, rows: [], loading: false, at: null, error: controller.signal.aborted ? "Run history timed out. Retry when the API is available." : error instanceof ApiError ? error.message : "Could not load run history." });
    }).finally(() => clearTimeout(deadline));
    return () => { current = false; clearTimeout(deadline); controller.abort(); };
  }, [active, mode, status, offset, attempt, key]);
  const visible = history?.key === key ? history : null;
  const loading = !visible || visible.loading;
  const rows = visible?.rows.slice(0, PAGE_SIZE) ?? [];

  return <div className="simulation-page">
    <header className="page-heading"><div><p className="eyebrow">SIMULATION WORKSPACE</p><h1>Follow every run<span className="title-dot">.</span></h1><p className="page-description">Inspect execution, measured results, and the recipe that produced them.</p></div><a href="#scenarios" className="button secondary">New simulation</a></header>
    <section className="panel run-history" aria-labelledby={`${prefix}-history`}>
      <div className="panel-header"><div><p className="eyebrow">RUN HISTORY</p><h2 id={`${prefix}-history`}>Saved executions</h2></div><button type="button" className="button secondary small" disabled={loading} onClick={() => setAttempt(value => value + 1)}>Refresh history</button></div>
      <div className="filter-bar">
        <div className="field"><label htmlFor={`${prefix}-mode`}>Execution mode</label><select id={`${prefix}-mode`} value={mode} onChange={event => { setMode(event.target.value as typeof mode); setOffset(0); }}><option value="">All modes</option><option value="carla">CARLA</option><option value="test">Synthetic test</option></select></div>
        <div className="field"><label htmlFor={`${prefix}-status`}>Status</label><select id={`${prefix}-status`} value={status} onChange={event => { setStatus(event.target.value as typeof status); setOffset(0); }}><option value="">All statuses</option>{statuses.map(item => <option key={item} value={item}>{item}</option>)}</select></div>
        <p className="field-hint">History is a snapshot{visible?.at ? ` from ${visible.at}` : ""}. Open a run for live progress. Creation time and recipe seed can help identify an uncertain launch.</p>
      </div>
      <div aria-live="polite">{loading && <p className="library-status" role="status">Loading run history…</p>}{visible?.error && <div className="notice error-notice" role="alert"><p>{visible.error}</p><button type="button" className="button secondary small" onClick={() => setAttempt(value => value + 1)}>Retry history</button></div>}{!loading && !visible?.error && rows.length === 0 && <p className="library-status">No runs match these filters. Launch a scenario to create a run.</p>}</div>
      {rows.length > 0 && <div className="table-scroll"><table className="data-table"><caption className="sr-only">Run history, page {Math.floor(offset / PAGE_SIZE) + 1}</caption><thead><tr><th scope="col">Scenario / run</th><th scope="col">Mode</th><th scope="col">Agent</th><th scope="col">Status</th><th scope="col">Created</th><th scope="col">Severity</th></tr></thead><tbody>{rows.map(row => <tr key={row.simulation.id} className={row.simulation.id === selectedId ? "selected-row" : undefined}><th scope="row"><a href={simulationLink(row.simulation.id)}>{row.simulation.scenario.name}</a><small className="record-id">{row.simulation.id}</small><small>Seed {row.simulation.scenario.seed} · {row.simulation.scenario.map}</small></th><td><span className={`mode-label mode-${row.simulation.mode}`}>{modeName(row.simulation.mode)}</span></td><td><AgentIdentity simulation={row.simulation} /></td><td><span className={`status-badge status-${row.simulation.status}`}>{row.simulation.status}</span></td><td>{date(row.created_at)}</td><td>{row.result?.metrics.severity === null || !row.result ? "Unscored" : `${row.result.metrics.severity.toFixed(1)} / 100`}</td></tr>)}</tbody></table></div>}
      <div className="pagination"><button type="button" className="button secondary small" disabled={offset === 0 || loading} onClick={() => setOffset(value => Math.max(0, value - PAGE_SIZE))}>Previous runs</button><span>Page {Math.floor(offset / PAGE_SIZE) + 1} · up to {PAGE_SIZE} runs</span><button type="button" className="button secondary small" disabled={loading || !visible || visible.rows.length <= PAGE_SIZE} onClick={() => setOffset(value => value + PAGE_SIZE)}>Next runs</button></div>
    </section>
    {selectedId ? <RunDetail key={selectedId} id={selectedId} active={active} /> : <div className="notice run-selection">Select a run to follow its progress, inspect results, and view available captures.</div>}
  </div>;
}

export function RunDetail({ id, active }: { id: string; active: boolean }) {
  const [focus, setFocus] = useState<(EvidenceFocus & { source: "decisions" | "telemetry" }) | null>(null);
  const findingButton = useRef<HTMLElement | null>(null);
  const showElement = (element: HTMLElement | null) => {
    const reducedMotion = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    element?.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "start" });
    element?.focus({ preventScroll: true });
  };
  const inspect = (source: "decisions" | "telemetry", frame: number) => {
    setFocus(current => ({ source, frame, token: (current?.token ?? 0) + 1 }));
    if (typeof document !== "undefined") {
      findingButton.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      const element = document.getElementById(`evidence-${id}-${source}`);
      showElement(element);
    }
  };
  const returnToFinding = () => {
    if (typeof document !== "undefined") showElement(findingButton.current?.isConnected ? findingButton.current : document.getElementById(`behavior-${id}`));
  };
  const state = useSimulation(id, active);
  const { record, progress } = state;
  const simulation = record?.simulation;
  const status = progress?.status ?? simulation?.status;
  const fraction = progress?.progress ?? simulation?.progress ?? 0;
  const cancelRequested = Boolean(progress?.cancel_requested || record?.job?.cancel_requested);
  const terminal = status ? isTerminal(status) : false;
  const error = progress?.error ?? simulation?.error;
  const queuePosition = progress ? progress.queue_position : record?.job?.queue_position;

  return <section className="run-detail" aria-label="Selected simulation">
    <div className="panel">
      <div className="panel-header"><div><p className="eyebrow">SELECTED RUN</p><h2>{simulation?.scenario.name ?? "Loading simulation"}</h2></div><button type="button" className="button secondary small" disabled={state.loading} onClick={state.refresh}>Refresh run</button></div>
      <div className="detail-content"><code className="record-id">{id}</code>
        {state.loading && <p role="status">Refreshing run…</p>}
        {state.error && <p className="notice error-notice" role="alert">{state.error}{record ? " Displaying the last confirmed observation." : ""}</p>}
        {simulation && <>
          <div className="run-status-heading"><span className={`status-badge status-${status}`} role="status">{status}</span><span className={`mode-label mode-${simulation.mode}`}>{modeName(simulation.mode)}</span><AgentIdentity simulation={simulation} /></div>
          <label className="progress-label">Execution progress <strong>{(fraction * 100).toFixed(0)}%</strong><progress max={1} value={fraction} /></label>
          <p className="field-hint">{terminal ? "Execution has ended. Completion alone does not establish safety or full observation coverage." : "Progress reflects the current execution window. Setup and cleanup may take additional time."}</p>
          <dl className="detail-facts"><div><dt>Queue position</dt><dd>{queuePosition ?? "Not waiting"}</dd></div><div><dt>Started</dt><dd>{date(progress?.started_at ?? simulation.started_at)}</dd></div><div><dt>Finished</dt><dd>{date(progress?.finished_at ?? simulation.finished_at)}</dd></div><div><dt>Seed / map</dt><dd>{simulation.scenario.seed} / {simulation.scenario.map}</dd></div><div><dt>Requested duration</dt><dd>{simulation.scenario.duration_s} s</dd></div><div><dt>Revision</dt><dd>{Math.max(record.revision, progress?.revision ?? 0)}</dd></div></dl>
          {error && <div className="notice error-notice" role="alert"><strong>Execution error</strong><p className="execution-error">{error}</p></div>}
          {!terminal && record.job && <div className="cancel-actions"><button type="button" className="button danger-button" disabled={state.cancelling || cancelRequested} onClick={() => void state.cancel()}>{state.cancelling ? "Requesting cancellation…" : cancelRequested ? "Cancellation requested" : "Cancel run"}</button><p className="field-hint">Cancellation is cooperative. A running native call must return before the worker can finish and persist partial observations.</p></div>}
          {cancelRequested && !terminal && <p className="notice" role="status">The API recorded a cancellation request. The run is still {status}.</p>}
          {state.cancelError && <p className="notice error-notice" role="alert">{state.cancelError}</p>}
          {!record.job && !terminal && <p className="field-hint">This manually stored record has no API job to cancel.</p>}
          <AgentIdentity simulation={simulation} detailed inputs={record.result?.agent_inputs} /><details className="result-settings"><summary>Recorded recipe and execution settings</summary><p>These are the immutable run snapshot and stored job configuration.</p><h3>Recipe</h3><pre>{JSON.stringify(simulation.scenario, null, 2)}</pre><h3>Execution options</h3>{record.job ? <pre>{JSON.stringify(record.job.options, null, 2)}</pre> : <p>Not recorded for this run.</p>}<h3>Requested evaluation settings</h3>{record.job ? <pre>{JSON.stringify(record.job.evaluation_settings, null, 2)}</pre> : <p>Not recorded for this run. Consult result provenance if available.</p>}</details>
        </>}
        {terminal && !record?.result && !state.loading && <p className="notice">Finalized output is not available yet. Refresh the run to check persistence.</p>}
      </div>
    </div>
    {record?.result && <>
      <SafetyMetrics result={record.result} />
      <div id={`behavior-${id}`} tabIndex={-1} className="evidence-destination"><BehaviorReview key={id} simulationId={id} active={active} inspect={inspect} /></div>
      <div id={`evidence-${id}-decisions`} tabIndex={-1} className="evidence-destination">{focus?.source === "decisions" && <button type="button" className="button secondary small evidence-return" onClick={returnToFinding}>Return to behavior finding</button>}<DecisionTimeline key={id} simulationId={id} active={active} focus={focus?.source === "decisions" ? focus : undefined} /></div>
      <div id={`evidence-${id}-telemetry`} tabIndex={-1} className="evidence-destination">{focus?.source === "telemetry" && <button type="button" className="button secondary small evidence-return" onClick={returnToFinding}>Return to behavior finding</button>}<MetricsChart simulationId={id} active={active} focus={focus?.source === "telemetry" ? focus : undefined} maxSampleGapS={record.result.metrics.evaluation_settings?.max_sample_gap_s ?? (record.result.runtime?.fixed_delta_seconds ?? record.result.simulation.scenario.fixed_delta_seconds) * 1.5} /></div>
      <SimulationViewer result={record.result} active={active} />
      <ReportDownloads simulationId={id} active={active} />
      <FailureReportForm result={record.result} active={active} />
      <div className="result-links"><a className="button secondary" href="#failures">Browse failure reports</a><span className="field-hint">Reports retain their parent run, mode, settings, and reproduction observations.</span></div>
    </>}
  </section>;
}
