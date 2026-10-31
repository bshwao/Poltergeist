import { useEffect, useRef, useState } from "react";
import { api } from "../services/api";
import type { LabClient } from "../services/lab-client";
import type { SavedSuite, SuiteCase, SuiteReport } from "../types/generated";
import { useLabMutation, useLabReport, labError } from "../hooks/useLab";
import { suiteDefaults, suiteInput } from "./lab-form";
import type { SuiteDraft } from "./lab-form";
import { simulationLink, suiteRunLink } from "../navigation";
import { RegressionPanel } from "./RegressionPanel";
import { saveLabJson } from "./lab-download";
export { saveLabJson } from "./lab-download";

const metric = (value: number | null) => value === null ? "Not measured" : Number(value.toFixed(2)).toString();

export function SuiteResults({ report, runHref = simulationLink, client = api, active = true, reviewAvailable = true }: { report: SuiteReport; runHref?: (id: string) => string; client?: LabClient; active?: boolean; reviewAvailable?: boolean }) {
  return <div className="lab-stack"><p>{report.evidence.operation.outcome?.summary ?? "Suite execution is in progress. Unfinished cells remain explicit."}</p>
    <button type="button" className="button secondary" onClick={() => saveLabJson(report, `poltergeist-suite-report-${report.evidence.operation.id}.json`)}>Download suite comparison evidence</button>
    {report.cases.map(c => <section className="lab-case-results" key={c.case_index}><h3>{c.name}</h3><p className="field-hint">Compare agents on this scenario's matched seeds and repetitions.</p>
      <div className="table-scroll" tabIndex={0} role="region" aria-label={`Comparison for ${c.name}`}><table className="data-table"><thead><tr><th>Agent / version</th><th>Completed / planned</th><th>Mean severity</th><th>Contacts (mean)</th><th>Unknown / unfinished</th></tr></thead><tbody>{c.groups.map(g => <tr key={g.agent_index}><th scope="row">{g.name}{g.version && <small>Version {g.version}</small>}</th><td>{g.completed_runs} / {g.planned_runs}</td><td>{metric(g.metrics.severity?.mean ?? null)}<small>{g.metrics.severity?.observed_results ?? 0} observed results</small></td><td>{metric(g.metrics.collision_count?.mean ?? null)}<small>{g.metrics.collision_count?.observed_results ?? 0} observed results</small></td><td>{g.unknown_runs} / {g.unassessed_runs}</td></tr>)}</tbody></table></div>
      {c.paired_comparisons.map(p => <p className="notice" key={p.compared_agent_index}>{c.groups[p.compared_agent_index]?.name} minus {c.groups[0]?.name}: {metric(p.difference.mean)} severity points across {p.difference.observed_results} / {p.difference.eligible_results} comparable pairs. Positive means higher measured heuristic severity. This is a descriptive comparison.</p>)}
      <details><summary>Coverage, measured distributions and run evidence</summary>{c.groups.map(g => <div key={g.agent_index}><h4>{g.name}</h4><p>{g.partial_coverage_results} partial-coverage results · Inputs: {JSON.stringify(g.input_sources)}</p><ul>{g.simulation_ids.map(id => <li key={id}><a href={runHref(id)}>Review run {id}</a></li>)}</ul><pre>{JSON.stringify(g.metrics, null, 2)}</pre></div>)}{c.notes.map(note => <p key={note}>{note}</p>)}</details>
    </section>)}<RegressionPanel key={report.evidence.operation.id} operationId={report.evidence.operation.id} client={client} active={active} available={reviewAvailable} runHref={runHref} /></div>;
}

export function SuiteManager({ active, incoming, client = api, runHref = simulationLink, modes = ["test", "carla"], initialRunId = null, reviewAvailable = true }: { active: boolean; incoming: SuiteCase | null; client?: LabClient; runHref?: (id: string) => string; modes?: ("test" | "carla")[]; initialRunId?: string | null; reviewAvailable?: boolean }) {
  const [draft, setDraft] = useState(suiteDefaults), [saved, setSaved] = useState<SavedSuite | null>(null);
  const [offset, setOffset] = useState(0), [runId, setRunId] = useState<string | null>(initialRunId);
  const [pastSuite, setPastSuite] = useState<SavedSuite | null>(null), [pastOffset, setPastOffset] = useState(0);
  useEffect(() => { setRunId(initialRunId); }, [initialRunId]);
  const [pendingLaunch, setPendingLaunch] = useState<{ suite: SavedSuite; id: string } | null>(null);
  const [dirty, setDirty] = useState(false), [replacement, setReplacement] = useState<{ suite: SavedSuite | null; draft: SuiteDraft } | null>(null);
  const [deleting, setDeleting] = useState<SavedSuite | null>(null);
  const change = useRef(0), applied = useRef<SuiteCase | null>(null);
  const visible = useRef(active); visible.current = active;
  useEffect(() => { if (!active) change.current++; return () => { change.current++; }; }, [active]);
  const mutation = useLabMutation(active);
  const list = useLabReport(`suites/${offset}`, active, (_, signal) => client.listSuites({ limit: 11, offset }, { signal }), () => true);
  const agents = useLabReport("suite-agents", active, (_, signal) => client.listAgents({ active: true, limit: 100 }, { signal }), () => true);
  const report = useLabReport(runId, active, (id, signal) => client.getSuiteReport(id, { signal }), v => !["queued", "running"].includes(v.evidence.operation.status));
  const past = useLabReport(pastSuite ? `${pastSuite.id}/${pastOffset}` : null, active, (_, signal) => client.listSuiteRuns(pastSuite!.id, { limit: 11, offset: pastOffset }, { signal }), () => true);
  useEffect(() => {
    if (!incoming || applied.current === incoming) return;
    applied.current = incoming; change.current++; setDirty(true); setReplacement(null);
    setDraft(previous => {
      let name = incoming.name, suffix = 2;
      while (previous.cases.some(c => c.name.toLocaleLowerCase() === name.toLocaleLowerCase())) name = `${incoming.name} ${suffix++}`;
      return { ...previous, cases: [...previous.cases, { key: crypto.randomUUID(), name, recipe: JSON.stringify(incoming.scenario, null, 2) }] };
    });
  }, [incoming]);
  function edit(update: Partial<SuiteDraft>) { change.current++; setDirty(true); setReplacement(null); setDraft(d => ({ ...d, ...update })); }
  function replace(value: { suite: SavedSuite | null; draft: SuiteDraft }) {
    change.current++; setSaved(value.suite); setDraft(value.draft); setDirty(value.suite === null); setReplacement(null); setRunId(null); setPendingLaunch(null);
  }
  function load(value: SavedSuite) {
    const next = { suite: value, draft: { name: value.definition.name, description: value.description, mode: value.definition.mode,
      agents: value.definition.agents.map(id => id ?? "baseline"), seeds: value.definition.seeds.join(", "), repeats: String(value.definition.repeats),
      cases: value.definition.cases.map(c => ({ key: crypto.randomUUID(), name: c.name, recipe: JSON.stringify(c.scenario, null, 2) })),
      options: JSON.stringify(value.definition.options, null, 2), evaluation: JSON.stringify(value.definition.evaluation_settings, null, 2), failure: value.definition.failure_kind ?? "" as const, severity: String(value.definition.minimum_severity) } };
    if (dirty) setReplacement(next); else replace(next);
  }
  function save(copy: boolean) {
    try {
      const input = suiteInput(draft), token = change.current;
      void mutation.run(signal => saved && !copy ? client.updateSuite(saved.id, input, saved.revision, { signal }) : client.saveSuite(input, { signal }), value => { setSaved(value); if (token === change.current) setDirty(false); list.refresh(); });
    } catch (error) { mutation.setError(labError(error)); }
  }
  async function importSuite(file: File | undefined) {
    if (!file) return;
    const token = ++change.current;
    try {
      if (file.size > 256 * 1024) throw new Error("Choose a suite JSON file smaller than 256 KB.");
      const value = JSON.parse(await file.text());
      const definition = value.definition ?? value;
      if (!definition || definition.kind !== "suite" || typeof definition.name !== "string" || !["test", "carla"].includes(definition.mode)
        || !Array.isArray(definition.cases) || !Array.isArray(definition.agents) || !Array.isArray(definition.seeds)) throw new Error("Choose an exported Poltergeist suite.");
      const imported: SuiteDraft = { name: definition.name, description: typeof value.description === "string" ? value.description : "", mode: definition.mode,
        agents: definition.agents.map((id: unknown) => id ?? "baseline"), seeds: definition.seeds.join(", "), repeats: String(definition.repeats),
        cases: definition.cases.map((c: SuiteCase) => ({ key: crypto.randomUUID(), name: c.name, recipe: JSON.stringify(c.scenario, null, 2) })),
        options: JSON.stringify(definition.options ?? {}, null, 2), evaluation: JSON.stringify(definition.evaluation_settings ?? {}, null, 2), failure: definition.failure_kind ?? "", severity: String(definition.minimum_severity ?? 0) };
      suiteInput(imported);
      if (token === change.current && visible.current) { const next = { suite: null, draft: imported }; if (dirty) setReplacement(next); else replace(next); }
    } catch (error) { if (token === change.current && visible.current) mutation.setError(labError(error)); }
  }
  function launch(value: SavedSuite, id: string = crypto.randomUUID()) {
    if (!modes.includes(value.definition.mode)) { mutation.setError("This execution mode is outside the computer's current approval."); return; }
    setPendingLaunch({ suite: value, id });
    void mutation.run(signal => client.runSuite(value.id, value.revision, id, { signal }), record => { setRunId(record.id); setPendingLaunch(null); list.refresh(); past.refresh(); window.location.hash = suiteRunLink(record.id); });
  }
  let executions: number | null = null;
  try { const input = suiteInput(draft).definition; executions = input.cases.length * input.agents.length * input.seeds.length * input.repeats; } catch { /* Full validation runs on save. */ }
  const operation = report.value?.evidence.operation;
  const choices = [{ id: "baseline", name: "Built-in baseline" }, ...(agents.value ?? []).map(a => ({ id: a.id, name: `${a.snapshot.manifest.name} · ${a.snapshot.manifest.version}` }))];
  draft.agents.filter(id => !choices.some(a => a.id === id)).forEach(id => choices.push({ id, name: `Unavailable registration ${id}. Remove it or reactivate it locally before launch.` }));
  return <div className="lab-stack"><section className="panel"><div className="panel-header"><h2>Build a reusable experiment suite</h2></div><form className="panel-body lab-form" onSubmit={e => { e.preventDefault(); save(false); }}>
    <p>Save exact recipes, matched seeds and model selections. Each launch freezes its own settings and preserves every run for comparison.</p>
    {replacement && <div className="notice" role="alert"><p>You have unsaved suite changes. Replace this draft with {replacement.suite ? replacement.suite.definition.name : "the imported suite"}?</p><div className="button-row"><button type="button" className="button secondary" onClick={() => setReplacement(null)}>Keep current draft</button><button type="button" className="button primary" onClick={() => replace(replacement)}>Replace unsaved draft</button></div></div>}
    <fieldset disabled={mutation.busy}><div className="lab-fields"><label>Suite name<input value={draft.name} onChange={e => edit({ name: e.target.value })} maxLength={128} required /></label><label>Suite execution mode<select value={draft.mode} onChange={e => edit({ mode: e.target.value as "test" | "carla" })}><option value="test">Synthetic workflow check</option><option value="carla">CARLA driving experiment</option></select></label></div>
      <label>Why keep this suite?<textarea value={draft.description} onChange={e => edit({ description: e.target.value })} maxLength={1000} rows={2} /></label>
      <div className="lab-fields"><label>Matched suite seeds<input value={draft.seeds} onChange={e => edit({ seeds: e.target.value })} /></label><label>Suite repetitions per seed<input inputMode="numeric" value={draft.repeats} onChange={e => edit({ repeats: e.target.value })} /></label></div>
      <div className="lab-fields"><label>Suite failure policy<select value={draft.failure} onChange={e => edit({ failure: e.target.value as SuiteDraft["failure"] })}><option value="">Any measured failure</option><option value="collision">Recorded collision</option><option value="near_miss">Measured near conflict</option><option value="hard_braking">Measured hard braking</option><option value="traffic_violation">Recorded traffic-rule violation</option></select></label><label>Minimum suite heuristic severity<input inputMode="decimal" value={draft.severity} onChange={e => edit({ severity: e.target.value })} /></label></div>
      <fieldset><legend>Controllers in reference order</legend><p className="field-hint">The first selected controller is the comparison reference. Registered versions retain their saved identities.</p>
        {choices.map(a => <label className="lab-check" key={a.id}><input type="checkbox" checked={draft.agents.includes(a.id)} onChange={e => edit({ agents: e.target.checked ? [...draft.agents, a.id] : draft.agents.filter(id => id !== a.id) })} />{a.name}{draft.agents.includes(a.id) && ` · Position ${draft.agents.indexOf(a.id) + 1}${draft.agents[0] === a.id ? " (reference)" : ""}`}</label>)}
        {draft.agents.length > 1 && <ol>{draft.agents.map((id, index) => <li key={id}>{choices.find(a => a.id === id)?.name}{index > 0 && <button type="button" className="button secondary small" onClick={() => { const order = [...draft.agents]; [order[index - 1], order[index]] = [order[index], order[index - 1]]; edit({ agents: order }); }}>Move controller {index + 1} earlier</button>}</li>)}</ol>}
        {agents.error && <p role="alert">{agents.error} <button type="button" onClick={agents.refresh}>Retry agent list</button></p>}
      </fieldset>
      <h3>Suite scenarios ({draft.cases.length} / 20)</h3>{!draft.cases.length && <p className="notice">Add scenes from the gallery or import a saved suite to begin.</p>}
      {draft.cases.map((c, index) => <details className="lab-case-editor" key={c.key}><summary>{index + 1}. {c.name}</summary><label>Scenario {index + 1} name<input value={c.name} onChange={e => edit({ cases: draft.cases.map(item => item.key === c.key ? { ...item, name: e.target.value } : item) })} /></label><label>Scenario {index + 1} recipe JSON<textarea rows={8} value={c.recipe} onChange={e => edit({ cases: draft.cases.map(item => item.key === c.key ? { ...item, recipe: e.target.value } : item) })} spellCheck={false} /></label><button type="button" className="button secondary small" onClick={() => edit({ cases: draft.cases.filter(item => item.key !== c.key) })}>Remove {c.name}</button></details>)}
      <details><summary>Saved simulation and scoring settings</summary><label>Suite simulation options JSON<textarea rows={6} value={draft.options} onChange={e => edit({ options: e.target.value })} spellCheck={false} /></label><label>Suite evaluation settings JSON<textarea rows={6} value={draft.evaluation} onChange={e => edit({ evaluation: e.target.value })} spellCheck={false} /></label></details>
      <p className="notice">{executions === null ? "Complete the suite to calculate its execution count." : `${executions} executions per launch. Each case runs every selected agent on every matched seed and repetition.`}</p>
      <div className="button-row"><button className="button primary" type="submit">{saved ? "Update saved suite" : "Save experiment suite"}</button>{saved && <button type="button" className="button secondary" onClick={() => save(true)}>Save as a new suite</button>}<label className="button secondary lab-file">Import suite JSON<input type="file" accept=".json,application/json" onChange={e => { void importSuite(e.target.files?.[0]); e.target.value = ""; }} /></label></div>
    </fieldset>{saved && <p className="field-hint">Saved revision {saved.revision}{dirty ? " · Unsaved draft changes" : ""} · Launches use the saved revision. Save draft changes before starting a suite.</p>}
  </form></section>
    {mutation.error && <p className="notice error-notice" role="alert">{mutation.error}</p>}
    {pendingLaunch && !mutation.busy && <div className="notice"><p>Launch identity: <code>{pendingLaunch.id}</code>. A retry preserves the original saved revision and request identity.</p><button className="button secondary" onClick={() => launch(pendingLaunch.suite, pendingLaunch.id)}>Retry this suite launch safely</button></div>}
    <section className="panel"><div className="panel-header"><h2>Saved suites</h2><button className="button secondary small" onClick={list.refresh} disabled={list.loading}>Refresh suites</button></div><div className="panel-body">
      {list.loading && <p role="status">Loading saved suites…</p>}{list.error && <p role="alert">{list.error}</p>}{!list.loading && !list.value?.length && !list.error && <p>No saved suites yet. Keep the scenarios you want to revisit.</p>}
      <ul className="lab-saved-list">{(list.value ?? []).slice(0, 10).map(s => <li key={s.id}><div><h3>{s.definition.name}</h3><p>{s.definition.cases.length} scenarios · {s.definition.mode === "test" ? "Synthetic" : "CARLA"} · Revision {s.revision}</p><small>{s.description}</small></div><div className="button-row">
        <button className="button secondary small" disabled={mutation.busy} onClick={() => load(s)}>Edit {s.definition.name}</button>
        <button className="button primary small" disabled={mutation.busy} onClick={() => launch(s)}>Run {s.definition.name} · {s.definition.cases.length * s.definition.agents.length * s.definition.seeds.length * s.definition.repeats} executions</button>
        <button className="button secondary small" onClick={() => { setPastSuite(s); setPastOffset(0); past.refresh(); }}>Past results for {s.definition.name}</button>
        <button className="button secondary small" onClick={() => saveLabJson(s, `poltergeist-suite-${s.id}.json`)}>Export suite</button><button className="button secondary small" disabled={mutation.busy} onClick={() => setDeleting(s)}>Delete {s.definition.name}</button></div>
        {deleting?.id === s.id && <div className="notice" role="alert"><p>Delete saved suite “{s.definition.name}”? Its recorded runs and comparisons remain available.</p><div className="button-row"><button className="button secondary small" disabled={mutation.busy} onClick={() => setDeleting(null)}>Keep saved suite</button><button className="button secondary small" disabled={mutation.busy} onClick={() => void mutation.run(signal => client.deleteSuite(s.id, s.revision, { signal }), () => { if (saved?.id === s.id) setSaved(null); setDeleting(null); list.refresh(); })}>Confirm suite deletion</button></div></div>}
      </li>)}</ul>
      <div className="button-row"><button className="button secondary small" disabled={!offset || list.loading} onClick={() => setOffset(n => Math.max(0, n - 10))}>Previous suites</button><span>Page {offset / 10 + 1}</span><button className="button secondary small" disabled={list.loading || (list.value?.length ?? 0) <= 10} onClick={() => setOffset(n => n + 10)}>Next suites</button></div>
    </div></section>
    {pastSuite && <section className="panel"><div className="panel-header"><h2>Past runs · {pastSuite.definition.name}</h2><button className="button secondary small" onClick={past.refresh}>Refresh past suite runs</button></div><div className="panel-body">
      {past.loading && <p role="status">Loading recorded suite runs…</p>}{past.error && <p role="alert">{past.error}</p>}{!past.loading && !past.value?.length && !past.error && <p>This suite has no recorded launches yet.</p>}
      <ul className="lab-saved-list">{(past.value ?? []).slice(0, 10).map(row => <li key={row.id}><div><p>{row.status} · Saved revision {row.request.kind === "suite" ? row.request.suite_revision : "unknown"}</p><small>{new Date(row.created_at).toLocaleString()} · {row.id}</small></div><a className="button secondary small" href={suiteRunLink(row.id)}>Review suite comparison {row.id.slice(0, 8)}</a></li>)}</ul>
      <div className="button-row"><button className="button secondary small" disabled={!pastOffset || past.loading} onClick={() => setPastOffset(n => Math.max(0, n - 10))}>Previous suite runs</button><span>Page {pastOffset / 10 + 1}</span><button className="button secondary small" disabled={past.loading || (past.value?.length ?? 0) <= 10} onClick={() => setPastOffset(n => n + 10)}>Next suite runs</button></div>
    </div></section>}
    {runId && <section className="panel lab-suite-run"><div className="panel-header"><h2>Suite run · {operation?.status ?? "loading"}</h2><button className="button secondary small" onClick={report.refresh}>Refresh suite result</button></div><div className="panel-body"><a href={suiteRunLink(runId)}>Permanent link to this suite comparison</a>{report.error && <p role="alert">{report.error}</p>}{report.loading && !report.value && <p role="status">Loading suite evidence…</p>}
      {operation && <><p><code>{operation.id}</code> · {report.value!.evidence.trials.length} executions submitted</p>{operation.error && <p role="alert">{operation.error}</p>}<div className="button-row">{["queued", "running"].includes(operation.status) && <button className="button secondary" disabled={mutation.busy || operation.cancel_requested} onClick={() => void mutation.run(signal => client.cancelOptimization(operation.id, { signal }), report.refresh)}>{operation.cancel_requested ? "Suite cancellation requested" : "Cancel suite"}</button>}{operation.status === "interrupted" && <button className="button secondary" disabled={mutation.busy} onClick={() => void mutation.run(signal => client.resumeOptimization(operation.id, operation.revision, { signal }), report.refresh)}>Resume remaining suite runs</button>}</div><SuiteResults report={report.value!} runHref={runHref} client={client} active={active} reviewAvailable={reviewAvailable} /></>}
    </div></section>}
  </div>;
}
