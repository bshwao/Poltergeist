import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../services/api";
import { emptyExperiment, experimentInput } from "../components/experiment-form";
import type { ExperimentDraft, ExperimentKind } from "../components/experiment-form";
import type { ComparisonReport, OptimizationRecord, OptimizationView, RegisteredAgent, TemplateInfo } from "../types/generated";
import { simulationLink, suiteRunLink } from "../navigation";

const message = (e: unknown) => e instanceof ApiError && e.issues.length ? `${e.message} ${e.issues.map(issue => `${issue.path}: ${issue.message}`).join("; ")}` : e instanceof Error ? e.message : "The experiment request failed.";
const ongoing = (r: OptimizationRecord) => r.status === "queued" || r.status === "running";
const observed = (v: number | null) => v === null ? "Unavailable" : Number(v.toPrecision(5)).toLocaleString();
interface Props { active?: boolean; selectedId: string | null; sourceFailureId?: string; initialKind?: "reproduce" | "minimize"; templates: TemplateInfo[]; registryRevision?: number }

function Comparison({ report }: { report: ComparisonReport }) {
  return <section aria-label="Comparison results">
    <h3>Observed results</h3><p>Agent zero is the paired reference. Results describe these runs; they do not establish real-world safety or statistical significance.</p>
    {report.groups.map(g => <section className="experiment-group" key={g.agent_index}>
      <h4>{g.agent_index}: {g.name}{g.version ? ` · ${g.version}` : ""}</h4>
      <p>{g.completed_runs} completed of {g.planned_runs} planned · {g.matching_runs} matching · {g.negative_runs} negative · {g.unknown_runs} unknown · {g.unassessed_runs} awaiting assessment · {g.partial_coverage_results} results with partial coverage</p>
      <p className="muted">Statuses: {JSON.stringify(g.status_counts)} · Inputs: {JSON.stringify(g.input_sources)} · Task outcomes: {JSON.stringify(g.task_outcomes)}</p>
      <div className="table-scroll" tabIndex={0} role="region" aria-label={`Metrics for ${g.name}`}><table className="data-table"><thead><tr><th>Metric</th><th>Observed / finalized</th><th>Mean</th><th>Minimum</th><th>Maximum</th><th>Sample spread</th></tr></thead><tbody>{Object.entries(g.metrics).map(([name, m]) => <tr key={name}><th scope="row">{name.replace(/_/g, " ")} ({m.unit})</th><td>{m.observed_results} / {m.eligible_results}</td><td>{observed(m.mean)}</td><td>{observed(m.minimum)}</td><td>{observed(m.maximum)}</td><td>{observed(m.sample_stddev)}</td></tr>)}</tbody></table></div>
    </section>)}
    {report.paired_comparisons.map(p => <details key={p.compared_agent_index}><summary>Agent {p.compared_agent_index} minus agent 0: mean severity difference {observed(p.difference.mean)} ({p.difference.observed_results} / {p.difference.eligible_results} eligible pairs observed)</summary><p>Positive means higher heuristic severity. Sample spread: {observed(p.difference.sample_stddev)}.</p><ul>{p.pairs.map(pair => <li key={`${pair.seed}/${pair.repeat_index}`}>Seed {pair.seed}, repetition {pair.repeat_index + 1}: {observed(pair.severity_difference)} — {pair.reason}{pair.reference_simulation_id && <> · <a href={simulationLink(pair.reference_simulation_id)}>Reference run</a></>}{pair.compared_simulation_id && <> · <a href={simulationLink(pair.compared_simulation_id)}>Compared run</a></>}</li>)}</ul></details>)}
    <details><summary>Interpretation and limitations</summary>{report.notes.map(n => <p key={n}>{n}</p>)}</details>
    <a className="button secondary" href={api.comparisonExportUrl(report.evidence.operation.id)} download>Download comparison JSON</a>
  </section>;
}

export function Experiments({ active = true, selectedId, sourceFailureId, initialKind, templates, registryRevision }: Props) {
  const [draft, setDraft] = useState(emptyExperiment);
  const [agents, setAgents] = useState<RegisteredAgent[]>([]);
  const [history, setHistory] = useState<OptimizationRecord[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [hasNext, setHasNext] = useState(false);
  const [offset, setOffset] = useState(0);
  const [view, setView] = useState<OptimizationView | null>(null);
  const [comparison, setComparison] = useState<ComparisonReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [agentsError, setAgentsError] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [trialPage, setTrialPage] = useState(0);
  const generation = useRef(0);
  const recipeGeneration = useRef(0);
  const mutation = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const visible = useRef(active);
  visible.current = active;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; recipeGeneration.current++; mutation.current?.abort(); }; }, []);
  useEffect(() => { if (sourceFailureId && initialKind) setDraft(d => ({ ...d, kind: initialKind, failureId: sourceFailureId, repeats: "3" })); }, [sourceFailureId, initialKind]);
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    setAgentsError(null);
    void api.listAgents({ active: true, limit: 100 }, { signal: controller.signal })
      .then(a => { if (!controller.signal.aborted) setAgents(a); })
      .catch(e => { if (!controller.signal.aborted) setAgentsError(message(e)); });
    return () => controller.abort();
  }, [active, refresh, registryRevision]);
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    setHistoryLoading(true); setHistory([]); setHasNext(false); setListError(null);
    void api.listOptimizations({ limit: 11, offset }, { signal: controller.signal })
      .then(h => { if (!controller.signal.aborted) { setHistory(h.slice(0, 10)); setHasNext(h.length > 10); } })
      .catch(e => { if (!controller.signal.aborted) setListError(message(e)); })
      .finally(() => { if (!controller.signal.aborted) setHistoryLoading(false); });
    return () => controller.abort();
  }, [active, offset, refresh]);
  useEffect(() => {
    setView(null); setComparison(null); setTrialPage(0); setError(null);
    if (!active || !selectedId) return;
    const token = ++generation.current, controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        let next = await api.getOptimization(selectedId!, { signal: controller.signal });
        const report = next.operation.request.kind === "compare" ? await api.getComparison(selectedId!, { signal: controller.signal }) : null;
        if (controller.signal.aborted || token !== generation.current) return;
        if (report) next = report.evidence;
        setView(next); setComparison(report); setError(null);
        setHistory(previous => previous.map(item => item.id === next.operation.id ? next.operation : item));
        if (ongoing(next.operation)) timer = setTimeout(load, 1500);
      } catch (e) { if (!controller.signal.aborted && token === generation.current) setError(message(e)); }
    }
    void load();
    return () => { controller.abort(); clearTimeout(timer); generation.current++; };
  }, [active, selectedId, refresh]);
  function field<K extends keyof ExperimentDraft>(key: K, value: ExperimentDraft[K]) { setDraft(d => ({ ...d, [key]: value })); }
  async function mutate(action: () => Promise<OptimizationRecord>) {
    if (mutation.current) return;
    const origin = window.location.hash;
    mutation.current = new AbortController(); setBusy(true); setError(null);
    try {
      const result = await action();
      if (mounted.current) { if (visible.current && window.location.hash === origin) window.location.hash = `experiments/${result.id}`; setRefresh(r => r + 1); }
    } catch (e) {
      if (mounted.current) {
        const uncertain = !(e instanceof ApiError) || e.status === 0 || e.status >= 500 || e.status < 400;
        setError(message(e) + (uncertain ? " The request may have been accepted. Refresh experiment history before retrying; submissions are never retried automatically." : ""));
      }
    }
    finally { mutation.current = null; if (mounted.current) setBusy(false); }
  }
  async function generate() {
    const token = ++recipeGeneration.current;
    setBusy(true); setError(null);
    try {
      const scenario = await api.generateScenario({ template: draft.template as TemplateInfo["template"], seed: 0, parameters: {}, duration_s: 5, ranges: {}, sample: false, map: null });
      if (mounted.current && token === recipeGeneration.current) field("recipe", JSON.stringify(scenario, null, 2));
    } catch (e) { if (mounted.current && token === recipeGeneration.current) setError(message(e)); }
    finally { if (mounted.current) setBusy(false); }
  }
  const source = draft.kind === "reproduce" || draft.kind === "minimize";
  let planned: string = "Invalid plan";
  try { const input = experimentInput(draft); planned = "agents" in input ? `${input.agents.length * input.seeds.length * input.repeats} executions` : "trials" in input ? `${input.trials} executions` : "max_trials" in input ? `Up to ${input.max_trials} executions` : `${input.repeats} executions`; } catch { /* Inline submit validation gives the precise error. */ }
  const operation = view?.operation;
  return <div className="experiments-page">
    <header className="page-heading"><div><p className="eyebrow">MATCHED TRIALS & FAILURE SEARCH</p><h1>Experiments</h1><p>Compare registered agents, repeat matched seeds, and investigate recorded failures.</p></div></header>
    {agentsError && <p className="error-banner" role="alert">{agentsError} Agent selections could not be refreshed. Use Refresh history to retry.</p>}
    <section className="panel"><div className="panel-header"><h2>Plan an experiment</h2></div><form className="experiment-form" onSubmit={e => { e.preventDefault(); try { const input = experimentInput(draft); void mutate(() => api.createOptimization(input, { signal: mutation.current!.signal })); } catch (e) { setError(message(e)); } }}>
      <label>Experiment type<select value={draft.kind} disabled={busy} onChange={e => field("kind", e.target.value as ExperimentKind)}><option value="compare">Agent comparison / repeated trials</option><option value="search">Search for failures</option><option value="reproduce">Reproduce a recorded failure</option><option value="minimize">Minimize a recorded failure</option></select></label>
      {source ? <><label>Source failure ID<input value={draft.failureId} onChange={e => field("failureId", e.target.value)} required /></label><p className="notice">The source run's registered agent, simulation options and scoring settings are inherited. Changed or inactive agents cannot be silently substituted. Find source IDs in the Failures tab.</p></> : <>
        <div className="form-grid"><label>Execution mode<select value={draft.mode} onChange={e => field("mode", e.target.value as "carla" | "test")}><option value="test">Synthetic test — no CARLA</option><option value="carla">Live CARLA</option></select></label><label>Scenario template<select value={draft.template} onChange={e => field("template", e.target.value)}>{templates.map(t => <option key={t.template} value={t.template}>{t.template.replace(/_/g, " ")}</option>)}</select></label></div>
        <fieldset><legend>Agents, in reference order</legend><p>Agent zero is the first selection below. Search accepts one agent. Only the newest 100 active registrations are listed; refresh after registering another version.</p>{[{ id: "baseline", name: "Built-in baseline" }, ...agents.map(a => ({ id: a.id, name: `${a.snapshot.manifest.name} · ${a.snapshot.manifest.version} · ${a.id}` }))].map(a => <label className="experiment-agent" key={a.id}><input type="checkbox" checked={draft.agents.includes(a.id)} onChange={e => field("agents", e.target.checked ? [...draft.agents, a.id] : draft.agents.filter(id => id !== a.id))} />{a.name}{draft.agents.includes(a.id) ? ` (agent ${draft.agents.indexOf(a.id)})` : ""}</label>)}</fieldset>
        <p className="notice">{draft.mode === "test" ? "Synthetic fixtures test the integration. They do not demonstrate driving or perception in CARLA." : "This starts real CARLA runs, one at a time. Runtime depends on the simulator and each agent's inference speed."}</p>
      </>}
      {draft.kind === "compare" && <><button className="button secondary" type="button" disabled={busy || !templates.length} onClick={() => void generate()}>Load template recipe</button><label>Scenario recipe JSON<textarea rows={12} value={draft.recipe} onChange={e => field("recipe", e.target.value)} required placeholder="Load a template recipe, or paste a saved scenario including its driving_task." /></label><p>All agents receive this recipe. The seed changes per matched cell. Include the driving task required by your agent; edits to the template selector take effect when you load a recipe.</p><label>Matched seeds (comma separated)<input value={draft.seeds} onChange={e => field("seeds", e.target.value)} /></label></>}
      <div className="form-grid">{draft.kind !== "search" && <label>Repetitions per seed / candidate<input type="number" min="1" max={draft.kind === "reproduce" ? 20 : 10} value={draft.repeats} onChange={e => field("repeats", e.target.value)} /></label>}{(draft.kind === "search" || draft.kind === "minimize") && <label>Maximum executions<input type="number" min="1" max={draft.kind === "search" ? 1000 : 200} value={draft.trials} onChange={e => field("trials", e.target.value)} /></label>}{draft.kind === "reproduce" && <label>Required matching trials (blank = all)<input type="number" min="1" value={draft.required} onChange={e => field("required", e.target.value)} /></label>}{draft.kind === "search" && <><label>Search strategy<select value={draft.strategy} onChange={e => field("strategy", e.target.value as "random" | "optuna")}><option value="random">Random</option><option value="optuna">Optuna (optional installation)</option></select></label><label>Search seed<input value={draft.seed} onChange={e => field("seed", e.target.value)} /></label></>}</div>
      {draft.kind === "search" && <>{([ ["parameters", "Fixed parameters JSON"], ["ranges", "Search ranges JSON (each [minimum, maximum])"], ["task", "Driving task JSON (optional; required by sensor agents)"] ] as const).map(([key, label]) => <label key={key}>{label}<textarea rows={4} value={draft[key]} onChange={e => field(key, e.target.value)} /></label>)}</>}
      {!source && <details><summary>Simulation and scoring settings</summary><p>Empty objects use server defaults. These settings are frozen for every execution.</p><label>Simulation options JSON<textarea rows={4} value={draft.options} onChange={e => field("options", e.target.value)} /></label><label>Evaluation settings JSON<textarea rows={4} value={draft.evaluation} onChange={e => field("evaluation", e.target.value)} /></label></details>}
      <div className="button-row"><button className="button primary" disabled={busy}>{busy ? "Working…" : `Start ${draft.kind} · ${planned}${source ? "" : ` · ${draft.mode}`}`}</button><button type="button" className="button secondary" disabled={busy} onClick={() => setRefresh(r => r + 1)}>Refresh history</button></div>
    </form></section>
    {error && <p className="error-banner" role="alert">{error} <button type="button" onClick={() => setRefresh(r => r + 1)}>Refresh</button></p>}
    <section className="panel"><div className="panel-header"><h2>Experiment history</h2></div>{listError && <p role="alert">{listError}</p>}<div className="panel-body">{historyLoading ? <p role="status">Loading experiment history…</p> : history.length ? <ul className="experiment-history">{history.map(r => <li key={r.id}><a href={`#experiments/${r.id}`}>{r.request.kind} · {r.execution.mode} · {r.status} · {r.id}</a><span>{new Date(r.created_at).toLocaleString()}</span></li>)}</ul> : !listError && <p>No experiments on this page.</p>}<div className="button-row"><button className="button secondary" disabled={historyLoading || offset === 0} onClick={() => setOffset(n => Math.max(0, n - 10))}>Previous</button><span>Page {offset / 10 + 1}</span><button className="button secondary" disabled={historyLoading || !hasNext} onClick={() => setOffset(n => n + 10)}>Next</button></div></div></section>
    {selectedId && !view && !error && <p role="status">Loading experiment…</p>}
    {operation?.request.kind === "suite" && <p><a className="button secondary" href={suiteRunLink(operation.id)}>Open saved suite comparison</a></p>}
    {operation?.request.kind === "discover" && <p><a className="button secondary" href={`#discovery/${operation.request.plan_id}`}>Open approved discovery report</a></p>}
    {operation && view && <section className="panel"><div className="panel-header"><h2>{operation.request.kind} · {operation.status}</h2></div><div className="panel-body"><p><code>{operation.id}</code> · Revision {operation.revision} · {view.trials.length} executions submitted</p><p>{operation.outcome?.summary ?? "Execution is in progress; unknown outcomes are preserved."}</p>{operation.error && <p role="alert">{operation.error}</p>}<div className="button-row">{ongoing(operation) && <button className="button secondary" disabled={busy || operation.cancel_requested} onClick={() => void mutate(() => api.cancelOptimization(operation.id, { signal: mutation.current!.signal }))}>{operation.cancel_requested ? "Cancellation requested" : "Cancel experiment"}</button>}{operation.status === "interrupted" && <button className="button primary" disabled={busy} onClick={() => void mutate(() => api.resumeOptimization(operation.id, operation.revision, { signal: mutation.current!.signal }))}>Resume remaining trials</button>}</div>
      <details><summary>Frozen recipe, agents and execution settings</summary><pre>{JSON.stringify(operation, null, 2)}</pre></details>
      {comparison && <Comparison report={comparison} />}
      <h3>Trial evidence</h3><p>Open each run for controls, behavior analysis and recorded frame evidence.</p><div className="table-scroll" tabIndex={0} role="region" aria-label="Experiment trials"><table className="data-table"><thead><tr><th>Trial</th><th>Agent</th><th>Seed / repeat</th><th>Observation</th><th>Evidence</th></tr></thead><tbody>{view.trials.slice(trialPage * 20, (trialPage + 1) * 20).map(t => <tr key={t.number}><td>{t.number + 1} · {t.role}</td><td>{t.agent_index}</td><td>{t.scenario.seed} / {t.repeat_index + 1}</td><td>{!t.assessed ? "Awaiting assessment" : t.matches === null ? "Unknown" : t.matches ? "Matching" : "Negative"}<br />{t.reason}</td><td><a href={simulationLink(t.simulation_id)}>Review run</a>{t.failure_id && <> · Failure <code>{t.failure_id}</code></>}</td></tr>)}</tbody></table></div><div className="button-row"><button className="button secondary" disabled={!trialPage} onClick={() => setTrialPage(p => p - 1)}>Previous trials</button><span>Page {trialPage + 1}</span><button className="button secondary" disabled={(trialPage + 1) * 20 >= view.trials.length} onClick={() => setTrialPage(p => p + 1)}>Next trials</button></div>
    </div></section>}
  </div>;
}
