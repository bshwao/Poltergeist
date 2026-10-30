import { useEffect, useState } from "react";
import { api } from "../services/api";
import type { LabClient } from "../services/lab-client";
import type { DiscoveryPlan, DiscoveryReport } from "../types/generated";
import { labError, useLabMutation, useLabReport } from "../hooks/useLab";
import { discoveryDefaults, discoveryProposal } from "../components/lab-form";
import type { DiscoveryDraft } from "../components/lab-form";
import { saveLabJson } from "../components/SuiteManager";
import { simulationLink } from "../navigation";
import "../lab.css";

export function DiscoveryFindings({ report, runHref = simulationLink, client = api, active = true }: { report: DiscoveryReport; runHref?: (id: string) => string; client?: LabClient; active?: boolean }) {
  const [page, setPage] = useState(0);
  const [saved, setSaved] = useState<Record<number, string>>({});
  const mutation = useLabMutation(active);
  const operation = report.evidence.operation, budget = report.plan.proposal.budget;
  const ongoing = ["queued", "running"].includes(operation.status);
  return <section className="panel"><div className="panel-header"><h2>{report.confirmed_failures ? `${report.confirmed_failures} confirmed ${report.confirmed_failures === 1 ? "weakness" : "weaknesses"}` : ongoing ? "Search and confirmation evidence" : "No failure confirmed"}</h2></div><div className="panel-body lab-stack">
    <p role="status">{report.summary}</p><div className="lab-key-figures"><div><span>Executions used</span><strong>{report.executions_used} / {budget.max_executions}</strong></div><div><span>Simulated time reserved</span><strong>{Number(report.simulated_time_reserved_s.toFixed(2))} / {budget.max_simulated_time_s} s</strong></div><div><span>Unknown / awaiting assessment</span><strong>{report.unknown_trials} / {report.unassessed_trials}</strong></div></div>
    <p>{report.negative_trials} negative trials are retained alongside matching and unsuccessful executions. A candidate observation becomes confirmed only after the approved repetition policy is satisfied.</p>
    {report.candidates.map(c => <article className="lab-finding" key={c.candidate_index} data-confirmed={c.status === "confirmed"}><div><span className="lab-tag">{c.status.replace(/_/g, " ")}</span><h3>{c.scenario.name} · Seed {c.scenario.seed}</h3><p>{c.matching_confirmations} matching, {c.negative_confirmations} negative, {c.unknown_confirmations} unknown confirmations; {c.pending_confirmations} still required to finish assessment. Approval requires at least {c.required_successes} matching repetitions.</p></div>
      <div className="button-row"><a className="button secondary small" href={runHref(c.search_simulation_id)}>Review candidate run</a>{c.confirmation_simulation_ids.map((id, i) => <a className="button secondary small" key={id} href={runHref(id)}>Confirmation {i + 1}</a>)}
        {c.status === "confirmed" && <button className="button primary small" onClick={() => saveLabJson({ schema_version: "poltergeist-confirmed-failure-v1", scenario: c.scenario, agent_snapshot: report.plan.agent_snapshot,
          execution: operation.execution, reproduction: c.reproduction, discovery_plan_id: report.plan.id, discovery_plan_digest: report.plan.digest,
          search_simulation_id: c.search_simulation_id, confirmation_simulation_ids: c.confirmation_simulation_ids }, `poltergeist-confirmed-${c.search_simulation_id}.json`)}>Download reproducible failure</button>}
        {c.status === "confirmed" && <button className="button secondary small" disabled={mutation.busy || Boolean(saved[c.candidate_index])} onClick={() => void mutation.run(signal => client.saveSuite({ description: `Confirmed by discovery plan ${report.plan.id}; ${c.matching_confirmations} matching repetitions.`, definition: {
          kind: "suite", name: `Confirmed: ${c.scenario.name}`.slice(0, 128), suite_id: null, suite_revision: null,
          cases: [{ name: c.scenario.name.slice(0, 128), scenario: c.scenario }], agents: [report.plan.proposal.goal.agent_id], seeds: [c.scenario.seed], repeats: budget.confirmation_repeats,
          mode: operation.execution.mode, options: operation.execution.options, evaluation_settings: operation.execution.evaluation_settings,
          failure_kind: report.plan.proposal.goal.failure_kind, minimum_severity: report.plan.proposal.goal.minimum_severity,
        } }, { signal }), value => setSaved(previous => ({ ...previous, [c.candidate_index]: value.id })))}>{saved[c.candidate_index] ? "Confirmed scenario saved as a suite" : "Save confirmed scenario as a suite"}</button>}
      </div>{saved[c.candidate_index] && <p role="status"><a href="#lab">Open saved suites in the guided lab</a> to repeat this exact recipe.</p>}</article>)}
    {mutation.error && <p role="alert">{mutation.error}</p>}
    <h3>Complete trial record</h3><div className="table-scroll" tabIndex={0} role="region" aria-label="Discovery trial evidence"><table className="data-table"><thead><tr><th>Execution</th><th>Purpose / seed</th><th>Measured outcome</th><th>Evidence</th></tr></thead><tbody>{report.evidence.trials.slice(page * 20, (page + 1) * 20).map(t => <tr key={t.number}><td>{t.number + 1}</td><td>{t.role === "discovery_search" ? "Search" : `Confirmation ${t.repeat_index + 1}`} · {t.scenario.seed}</td><td>{!t.assessed ? "Awaiting assessment" : t.matches === null ? "Unknown" : t.matches ? "Matching observation" : "Negative observation"}<small>{t.reason}</small></td><td><a href={runHref(t.simulation_id)}>Inspect recorded run</a></td></tr>)}</tbody></table></div>
    {!report.evidence.trials.length && <p>No execution has been submitted. Queued time still counts toward the approved wall-clock budget.</p>}
    <div className="button-row"><button className="button secondary small" disabled={!page} onClick={() => setPage(p => p - 1)}>Previous discovery trials</button><span>Page {page + 1}</span><button className="button secondary small" disabled={(page + 1) * 20 >= report.evidence.trials.length} onClick={() => setPage(p => p + 1)}>Next discovery trials</button><button className="button secondary" onClick={() => saveLabJson(report, `poltergeist-discovery-${report.plan.id}.json`)}>Download complete discovery report</button></div>
    <details><summary>Interpretation, coverage and reproducibility</summary>{report.limitations.map(note => <p key={note}>{note}</p>)}</details>
  </div></section>;
}

export function Discovery({ active = true, planId = null, client = api, runHref = simulationLink, navigatePlan, modes = ["test", "carla"], maxExecutions = 200 }: {
  active?: boolean; planId?: string | null; client?: LabClient; runHref?: (id: string) => string; navigatePlan?: (id: string) => void; modes?: ("test" | "carla")[]; maxExecutions?: number;
}) {
  const [draft, setDraft] = useState(discoveryDefaults), [selectedId, setSelectedId] = useState<string | null>(planId);
  const [approval, setApproval] = useState(false), [edited, setEdited] = useState(false), [offset, setOffset] = useState(0);
  const mutation = useLabMutation(active);
  useEffect(() => { setSelectedId(planId); setEdited(false); setApproval(false); }, [planId]);
  const selected = useLabReport(edited ? null : selectedId, active, (id, signal) => client.getDiscoveryPlan(id, { signal }), () => true);
  const history = useLabReport(`discovery-history/${offset}`, active, (_, signal) => client.listDiscoveryPlans({ limit: 11, offset }, { signal }), () => true);
  const agents = useLabReport("discovery-agents", active, (_, signal) => client.listAgents({ active: true, limit: 100 }, { signal }), () => true);
  const plan = selected.value;
  const report = useLabReport(plan?.operation_id ? plan.id : null, active, (id, signal) => client.getDiscoveryReport(id, { signal }), v => !["queued", "running"].includes(v.evidence.operation.status));
  useEffect(() => { setApproval(false); }, [plan?.id, plan?.revision]);
  function field<K extends keyof DiscoveryDraft>(key: K, value: DiscoveryDraft[K]) { setDraft(d => ({ ...d, [key]: value })); setEdited(true); setApproval(false); }
  function open(id: string) {
    setSelectedId(id); setEdited(false); setApproval(false);
    if (navigatePlan) navigatePlan(id); else window.location.hash = `discovery/${id}`;
  }
  function propose() {
    try { const proposal = discoveryProposal(draft); if (proposal.budget.max_executions > maxExecutions || proposal.budget.max_search_trials > Math.min(100, maxExecutions)) throw new Error(`This computer allows at most ${maxExecutions} executions and ${Math.min(100, maxExecutions)} search candidates per discovery plan.`); void mutation.run(signal => client.proposeDiscovery(proposal, { signal }), value => { open(value.id); history.refresh(); }); }
    catch (error) { mutation.setError(labError(error)); }
  }
  function approve(value: DiscoveryPlan) {
    if (!approval || !modes.includes(value.proposal.mode)) return;
    void mutation.run(signal => client.approveDiscovery(value, { signal }), () => { selected.refresh(); history.refresh(); });
  }
  const operation = report.value?.evidence.operation;
  const expired = plan && Date.parse(plan.expires_at) <= Date.now();
  const stoppedBudget = plan?.deadline_at ? Date.parse(plan.deadline_at) <= Date.now() : false;
  return <div className="lab-surface discovery-page"><header className="page-heading"><div><p className="eyebrow">SEARCH. CONFIRM. KEEP THE EVIDENCE.</p><h1>Failure Discovery Agent</h1><p>Turn a driving question into a bounded plan, then approve the work you want your computer to perform.</p></div><a className="button secondary" href="#lab">Open the guided lab</a></header>
    <section className="panel"><div className="panel-header"><h2>Define an evaluation goal</h2><span className="lab-tag">Plan first · explicit approval</span></div><form className="panel-body lab-form" onSubmit={e => { e.preventDefault(); propose(); }}><fieldset disabled={mutation.busy}>
      <label>Evaluation goal<textarea value={draft.description} onChange={e => field("description", e.target.value)} rows={2} maxLength={500} required /></label>
      <p className="field-hint">The selected failure policy and parameter ranges define the search. Your description records the question alongside the evidence.</p>
      <div className="lab-fields"><label>Failure to investigate<select value={draft.failure} onChange={e => field("failure", e.target.value as DiscoveryDraft["failure"])}><option value="collision">Recorded collision</option><option value="near_miss">Measured near conflict</option><option value="hard_braking">Measured hard braking</option><option value="traffic_violation">Recorded traffic-rule violation</option></select></label><label>Discovery scenario family<select value={draft.template} onChange={e => { setDraft(d => ({ ...d, template: e.target.value as DiscoveryDraft["template"], parameters: "{}", ranges: "{}" })); setEdited(true); setApproval(false); }}>{["sudden_braking", "intersection", "pedestrian_crossing", "occluded_pedestrian"].map(t => <option key={t} value={t}>{t.replace(/_/g, " ")}</option>)}</select></label></div>
      <div className="lab-fields"><label>Discovery execution mode<select value={draft.mode} onChange={e => field("mode", e.target.value as "test" | "carla")}><option value="test">Synthetic workflow check</option><option value="carla">CARLA driving experiments</option></select></label><label>Agent to investigate<select value={draft.agent} onChange={e => field("agent", e.target.value)}><option value="baseline">Built-in baseline</option>{(agents.value ?? []).map(a => <option key={a.id} value={a.id}>{a.snapshot.manifest.name} · {a.snapshot.manifest.version}</option>)}</select></label></div>
      {agents.error && <p role="alert">{agents.error} <button type="button" onClick={agents.refresh}>Refresh registered agents</button></p>}
      <div className="lab-fields"><label>Candidate duration (simulated seconds)<input value={draft.duration} onChange={e => field("duration", e.target.value)} inputMode="decimal" /></label><label>Discovery seed<input value={draft.seed} onChange={e => field("seed", e.target.value)} inputMode="numeric" /></label><label>Minimum heuristic severity<input value={draft.severity} onChange={e => field("severity", e.target.value)} inputMode="decimal" /></label></div>
      <fieldset><legend>Your experiment budget</legend><div className="lab-fields">{([
        ["executions", "Maximum total executions"], ["search", "Maximum search candidates"], ["candidates", "Maximum candidates to confirm"],
        ["confirmations", "Confirmation repetitions per candidate"], ["required", "Required matching confirmations"], ["seconds", "Wall-clock budget (seconds)"], ["simulated", "Simulated-time budget (seconds)"],
      ] as const).map(([key, label]) => <label key={key}>{label}<input inputMode={key === "seconds" || key === "simulated" ? "decimal" : "numeric"} value={draft[key]} onChange={e => field(key, e.target.value)} /></label>)}</div>
      <p className="field-hint">Every submitted execution counts, including failures and unknown results. Confirmation repeats use the exact candidate recipe and seed. Queue time consumes the wall-clock budget after approval.</p></fieldset>
      <details><summary>Parameter ranges and driving task</summary><p>Use the scenario's named parameters. Empty ranges use the template's documented search space. Invalid names, bounds and event timing are rejected before approval.</p><label>Discovery fixed parameters JSON<textarea rows={3} value={draft.parameters} onChange={e => field("parameters", e.target.value)} spellCheck={false} /></label><label>Discovery parameter ranges JSON<textarea rows={3} value={draft.ranges} onChange={e => field("ranges", e.target.value)} spellCheck={false} /></label><label>Discovery driving task JSON (optional)<textarea rows={3} value={draft.task} onChange={e => field("task", e.target.value)} spellCheck={false} /></label></details>
      <p className="notice">{draft.mode === "test" ? "Synthetic fixtures exercise discovery and confirmation plumbing. They do not demonstrate CARLA driving or model perception." : "CARLA trials run serially on this computer. Plans retain each agent's registered identity and the exact simulation and scoring settings."}</p>
      <button className="button primary" type="submit">{mutation.busy ? "Working…" : "Build a plan for review"}</button>
    </fieldset></form></section>
    {mutation.error && <p className="notice error-notice" role="alert">{mutation.error} <button onClick={() => { history.refresh(); selected.refresh(); report.refresh(); }}>Check saved plan status</button></p>}
    {edited && selectedId && <p className="notice">The draft changed. Build a new plan to review these settings; earlier plans remain in history.</p>}
    {selected.loading && !plan && !edited && <p role="status">Loading the saved discovery plan…</p>}{selected.error && <p role="alert">{selected.error} <button onClick={selected.refresh}>Retry saved plan</button></p>}
    {plan && !edited && <section className="panel lab-plan"><div className="panel-header"><h2>Review the experiment plan</h2><span className="lab-tag">{plan.operation_id ? "Approved" : "Awaiting your approval"}</span></div><div className="panel-body lab-stack">
      <h3>{plan.proposal.goal.description}</h3><p>{plan.proposal.mode === "test" ? "Synthetic" : "CARLA"} · {plan.agent_snapshot ? `${plan.agent_snapshot.manifest.name} ${plan.agent_snapshot.manifest.version}` : "Built-in baseline"} · {plan.per_run_duration_s} simulated seconds per execution · Seed {plan.proposal.goal.seed}</p>
      <ol className="lab-plan-steps">{plan.steps.map(step => <li key={step}>{step}</li>)}</ol><div className="lab-key-figures"><div><span>At most</span><strong>{plan.maximum_executions} executions</strong></div><div><span>Wall-clock budget</span><strong>{plan.proposal.budget.max_wall_time_s} s</strong></div><div><span>Simulated-time budget</span><strong>{plan.proposal.budget.max_simulated_time_s} s</strong></div></div>
      <details><summary>Frozen settings, identity and limitations</summary><p>Plan {plan.id} · Revision {plan.revision} · Fingerprint <code>{plan.digest}</code></p><pre>{JSON.stringify({ goal: plan.proposal.goal, search: plan.search, scenarios: plan.scenarios, agent_snapshot: plan.agent_snapshot }, null, 2)}</pre>{plan.limitations.map(note => <p key={note}>{note}</p>)}</details>
      {!plan.operation_id ? <><p className="field-hint">Review expires {new Date(plan.expires_at).toLocaleString()}.</p>{expired && <p role="alert">This review expired. Build a fresh plan to approve work.</p>}<label className="lab-check"><input type="checkbox" checked={approval} disabled={mutation.busy || Boolean(expired)} onChange={e => setApproval(e.target.checked)} />I approve this plan and its execution and time budgets</label><button className="button primary" disabled={!approval || mutation.busy || Boolean(expired) || !modes.includes(plan.proposal.mode)} onClick={() => approve(plan)}>Approve and start discovery</button></>
        : <><p>Approved {new Date(plan.approved_at!).toLocaleString()} · Absolute deadline {new Date(plan.deadline_at!).toLocaleString()}</p><p role="status">Discovery {operation?.status ?? "loading recorded progress"}{operation?.cancel_requested ? " · Cancellation requested" : ""}</p>{operation?.error && <p role="alert">{operation.error}</p>}
          <div className="button-row"><button className="button secondary" onClick={report.refresh}>Refresh discovery evidence</button>{operation && ["queued", "running"].includes(operation.status) && <button className="button secondary" disabled={mutation.busy || operation.cancel_requested} onClick={() => void mutation.run(signal => client.cancelOptimization(operation.id, { signal }), report.refresh)}>Cancel discovery</button>}{operation?.status === "interrupted" && <button className="button secondary" disabled={mutation.busy || stoppedBudget} onClick={() => void mutation.run(signal => client.resumeOptimization(operation.id, operation.revision, { signal }), report.refresh)}>Resume within remaining budget</button>}</div>{stoppedBudget && operation?.status === "interrupted" && <p>The original deadline has passed. Create and review a new plan for further work.</p>}
        </>}
    </div></section>}
    {report.error && !edited && <p className="notice error-notice" role="alert">{report.error} <button onClick={report.refresh}>Retry discovery report</button></p>}
    {report.value && !edited && <DiscoveryFindings key={report.value.plan.id} report={report.value} runHref={runHref} client={client} active={active} />}
    <section className="panel"><div className="panel-header"><h2>Saved discovery plans</h2><button className="button secondary small" onClick={history.refresh}>Refresh plan history</button></div><div className="panel-body">{history.loading && <p role="status">Loading reviewed plans…</p>}{history.error && <p role="alert">{history.error}</p>}{!history.loading && !history.value?.length && !history.error && <p>No discovery plan has been saved yet.</p>}<ul className="lab-saved-list">{(history.value ?? []).slice(0, 10).map(p => <li key={p.id}><div><h3>{p.proposal.goal.description}</h3><p>{p.proposal.mode} · {p.operation_id ? "approved" : "awaiting approval"} · {p.maximum_executions} execution ceiling</p></div><button className="button secondary small" disabled={mutation.busy} onClick={() => open(p.id)}>Review plan {p.id.slice(0, 8)}</button></li>)}</ul><div className="button-row"><button className="button secondary small" disabled={!offset || history.loading} onClick={() => setOffset(n => Math.max(0, n - 10))}>Previous plans</button><span>Page {offset / 10 + 1}</span><button className="button secondary small" disabled={history.loading || (history.value?.length ?? 0) <= 10} onClick={() => setOffset(n => n + 10)}>Next plans</button></div></div></section>
  </div>;
}
