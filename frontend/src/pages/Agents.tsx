import { useEffect, useRef, useState } from "react";
import { emptyRegistrationDraft, registrationInput } from "../components/agent-registration-form";
import type { RegistrationDraft } from "../components/agent-registration-form";
import { api, ApiError } from "../services/api";
import type { AgentExecutionOptions, AgentReadiness, RegisteredAgent } from "../types/generated";

interface AgentsProps { active?: boolean; onRegistryChange?: () => void }
interface Mutation { controller: AbortController; kind: "register" | "deactivate"; notified: boolean }
const PAGE_SIZE = 8;
const message = (error: unknown) => error instanceof Error ? error.message : "The agent request could not be completed.";
const deadlineLabels: Record<keyof AgentExecutionOptions, string> = {
  load_timeout_s: "Load deadline (s)", reset_timeout_s: "Reset deadline (s)", step_timeout_s: "Decision deadline (s)",
  close_timeout_s: "Close deadline (s)", terminate_timeout_s: "Termination deadline (s)", max_message_bytes: "Maximum message size (bytes)",
};

function AgentDetails({ agent }: { agent: RegisteredAgent }) {
  const snapshot = agent.snapshot;
  const manifest = snapshot.manifest;
  return <>
    <dl className="detail-facts"><div><dt>Registration ID</dt><dd><code>{agent.id}</code></dd></div><div><dt>Agent</dt><dd>{manifest.name} · {manifest.version}</dd></div><div><dt>Status</dt><dd>{agent.active ? "Active" : "Inactive"}</dd></div>
      <div><dt>Observation mode</dt><dd>{snapshot.observation_mode === "sensors" ? "Camera / LiDAR sensors" : "Structured simulator state"}</dd></div><div><dt>SDK protocol</dt><dd>{manifest.protocol_version}</dd></div><div><dt>Navigation</dt><dd>{manifest.navigation_mode === "provided_route" ? "Framework route" : "Agent planner"}</dd></div>
      <div><dt>Control authority</dt><dd>{snapshot.agent_control_mode}</dd></div><div><dt>Package directory</dt><dd><code>{snapshot.package_path}</code></dd></div><div><dt>Worker Python</dt><dd><code>{snapshot.python_executable}</code></dd></div>
      <div><dt>Entry point</dt><dd><code>{manifest.entry_point}</code></dd></div><div><dt>Registered</dt><dd>{new Date(agent.created_at).toLocaleString()}</dd></div><div><dt>Package SHA-256</dt><dd><code>{snapshot.package_sha256}</code></dd></div></dl>
    <p className="notice">{snapshot.observation_mode === "sensors" ? "Sensor observations exclude other-actor ground truth. Ideal ego localization is still provided; the rig and task determine other assistance." : "Structured state includes simulator ground truth. Results do not demonstrate perception from camera or LiDAR inputs."}</p>
    <details><summary>Effective configuration</summary><pre>{JSON.stringify(snapshot.configuration, null, 2)}</pre></details>
    <details><summary>Declared sensor rig</summary>{manifest.sensors.length ? <pre>{JSON.stringify(manifest.sensors, null, 2)}</pre> : <p>No sensor streams declared.</p>}<p>The run resolves sensor defaults and validates compatibility with the simulation timestep.</p></details>
    <details><summary>Worker deadlines and control settings</summary><pre>{JSON.stringify({ agent_control_mode: snapshot.agent_control_mode, process_options: snapshot.process_options }, null, 2)}</pre></details>
    <details><summary>Saved manifest</summary><pre>{JSON.stringify(manifest, null, 2)}</pre></details>
    <details><summary>Package, model, runtime, and interpreter fingerprints</summary><p>Registrations keep file hashes. Changed files require a new registration. Dependency availability and environment contents remain unverified.</p><pre>{JSON.stringify({ package_sha256: snapshot.package_sha256, python_executable: snapshot.python_executable, python_sha256: snapshot.python_sha256, python_size_bytes: snapshot.python_size_bytes, files: snapshot.files, model_files: snapshot.model_files, runtime_files: snapshot.runtime_files }, null, 2)}</pre></details>
  </>;
}

export function Agents({ active = true, onRegistryChange }: AgentsProps) {
  const [draft, setDraft] = useState(emptyRegistrationDraft);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [mutating, setMutating] = useState(false);
  const [filter, setFilter] = useState("active");
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [records, setRecords] = useState<RegisteredAgent[]>([]);
  const [hasNext, setHasNext] = useState(false);
  const [loading, setLoading] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [detail, setDetail] = useState<RegisteredAgent | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [readiness, setReadiness] = useState<AgentReadiness | null>(null);
  const [readinessError, setReadinessError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const mutation = useRef<Mutation | null>(null);
  const readinessRequest = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const activeRef = useRef(active); activeRef.current = active;
  const selectedRef = useRef(detailId); selectedRef.current = detailId;
  const changedRef = useRef(onRegistryChange); changedRef.current = onRegistryChange;

  function uncertain(pending: Mutation) {
    if (pending.notified) return;
    pending.notified = true;
    setMutationError(pending.kind === "register"
      ? "Registration may have reached the API. Refresh the registration list and inspect it before submitting again. Your draft is preserved."
      : "Deactivation may have reached the API. Refresh the registration detail before retrying.");
    changedRef.current?.();
  }
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; mutation.current?.controller.abort(); readinessRequest.current?.abort(); }; }, []);
  useEffect(() => {
    if (active) return;
    if (mutation.current) { uncertain(mutation.current); mutation.current.controller.abort(); }
    readinessRequest.current?.abort(); setChecking(false);
  }, [active]);
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController(); let current = true;
    setLoading(true); setListError(null); setRecords([]); setHasNext(false);
    void api.listAgents({ limit: PAGE_SIZE + 1, offset, ...(filter !== "all" ? { active: filter === "active" } : {}) }, { signal: controller.signal }).then(items => {
      if (current) { setRecords(items.slice(0, PAGE_SIZE)); setHasNext(items.length > PAGE_SIZE); }
    }, error => { if (current) setListError(message(error)); }).finally(() => { if (current) setLoading(false); });
    return () => { current = false; controller.abort(); };
  }, [active, offset, filter, refresh]);
  useEffect(() => {
    readinessRequest.current?.abort(); readinessRequest.current = null;
    setReadiness(null); setReadinessError(null); setChecking(false);
    if (!active || !detailId) return;
    const controller = new AbortController(); let current = true;
    setDetail(null); setDetailError(null); setDetailLoading(true);
    void api.getAgent(detailId, { signal: controller.signal }).then(value => { if (current) setDetail(value); }, error => { if (current) setDetailError(message(error)); }).finally(() => { if (current) setDetailLoading(false); });
    return () => { current = false; controller.abort(); };
  }, [active, detailId, refresh]);

  function update<K extends keyof RegistrationDraft>(key: K, value: RegistrationDraft[K]) {
    setDraft(previous => ({ ...previous, [key]: value }));
    setFieldErrors(previous => { const next = { ...previous }; delete next[key]; return next; });
  }
  async function register(event: React.FormEvent) {
    event.preventDefault();
    if (!activeRef.current || mutation.current) return;
    const parsed = registrationInput(draft); setFieldErrors(parsed.errors);
    setMutationError(null); setNotice(null);
    if (!parsed.input) return;
    const pending: Mutation = { controller: new AbortController(), kind: "register", notified: false };
    mutation.current = pending; setMutating(true);
    try {
      const saved = await api.registerAgent(parsed.input, { signal: pending.controller.signal });
      if (!mounted.current || !activeRef.current || pending.controller.signal.aborted) return;
      setNotice(`Registered ${saved.snapshot.manifest.name} ${saved.snapshot.manifest.version}. Static inspection does not load the model or test driving.`);
      setDraft(previous => ({ ...previous, trusted: false }));
      setDetailId(saved.id); setFilter("active"); setOffset(0); setRefresh(value => value + 1); changedRef.current?.();
    } catch (error) {
      if (!mounted.current || !activeRef.current || pending.controller.signal.aborted) return;
      if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
        setMutationError(`${message(error)} No registration was accepted.${error.issues.length ? ` ${error.issues.map(issue => `${issue.path}: ${issue.message}`).join("; ")}` : ""}`);
      } else { uncertain(pending); setMutationError(`${message(error)} Registration may have reached the API. Refresh the registration list before submitting again. Your draft is preserved.`); }
    } finally { if (mutation.current === pending) mutation.current = null; if (mounted.current) setMutating(false); }
  }
  async function deactivate() {
    if (!activeRef.current || mutation.current || !detail?.active) return;
    const id = detail.id;
    const pending: Mutation = { controller: new AbortController(), kind: "deactivate", notified: false };
    mutation.current = pending; setMutating(true); setMutationError(null); setNotice(null);
    readinessRequest.current?.abort(); setReadiness(null); setChecking(false);
    try {
      const saved = await api.deactivateAgent(id, { signal: pending.controller.signal });
      if (!mounted.current || !activeRef.current || pending.controller.signal.aborted) return;
      if (selectedRef.current === id) setDetail(saved);
      setNotice("Registration deactivated for future launches. Saved and queued runs retain their original agent snapshot.");
      setRefresh(value => value + 1); changedRef.current?.();
    } catch (error) {
      if (!mounted.current || !activeRef.current || pending.controller.signal.aborted) return;
      if (error instanceof ApiError && error.status >= 400 && error.status < 500) setMutationError(message(error));
      else { uncertain(pending); setMutationError(`${message(error)} Deactivation may have reached the API. Refresh the registration detail before retrying.`); }
    } finally { if (mutation.current === pending) mutation.current = null; if (mounted.current) setMutating(false); }
  }
  async function checkReadiness() {
    if (!activeRef.current || !detail || mutation.current || readinessRequest.current) return;
    const id = detail.id; const controller = new AbortController(); readinessRequest.current = controller;
    setChecking(true); setReadiness(null); setReadinessError(null);
    try {
      const result = await api.getAgentReadiness(id, { signal: controller.signal });
      if (mounted.current && activeRef.current && !controller.signal.aborted && selectedRef.current === id) setReadiness(result);
    } catch (error) { if (mounted.current && activeRef.current && !controller.signal.aborted && selectedRef.current === id) setReadinessError(message(error)); }
    finally { if (readinessRequest.current === controller) { readinessRequest.current = null; if (mounted.current) setChecking(false); } }
  }
  const fieldError = (key: string) => fieldErrors[key] && <p className="field-error" id={`agent-error-${key}`} role="alert">{fieldErrors[key]}</p>;
  const inputProps = (key: string) => ({ "aria-invalid": Boolean(fieldErrors[key]), "aria-describedby": fieldErrors[key] ? `agent-error-${key}` : undefined });
  const selected = detail?.id === detailId ? detail : null;
  return <div className="agents-page">
    <header className="page-heading"><div><p className="eyebrow">AUTONOMOUS AGENTS</p><h1>Agent workspace<span className="title-dot">.</span></h1><p className="page-description">Register trusted local agents, inspect their inputs, and choose what drives your experiments.</p></div></header>
    <p className="notice">Packages and model files must already exist on the computer running the backend. Registration reads declarations and fingerprints files. Model loading and inference happen only when you explicitly execute a scenario.</p>
    <section className="panel agent-registration" aria-labelledby="agent-register-heading"><div className="panel-header"><div><p className="eyebrow">LOCAL PACKAGE</p><h2 id="agent-register-heading">Register an agent</h2></div><span>Immutable registration</span></div>
      <form onSubmit={event => { void register(event); }} noValidate>
        <fieldset disabled={!active || mutating} className="agent-panel-body"><div className="agent-form-grid">
          <div className="field"><label htmlFor="agent-package-path">Package directory</label><input id="agent-package-path" value={draft.packagePath} onChange={event => update("packagePath", event.target.value)} placeholder="C:\\agents\\my-pilot" {...inputProps("packagePath")} /><small>Absolute directory containing agent.json on the backend computer. No file upload is performed.</small>{fieldError("packagePath")}</div>
          <div className="field"><label htmlFor="agent-python">Worker Python (optional)</label><input id="agent-python" value={draft.pythonExecutable} onChange={event => update("pythonExecutable", event.target.value)} placeholder="Use backend Python" {...inputProps("pythonExecutable")} /><small>Absolute interpreter path with the agent’s dependencies installed.</small>{fieldError("pythonExecutable")}</div>
          <div className="field"><label htmlFor="agent-configuration">Configuration overrides (JSON object)</label><textarea id="agent-configuration" rows={7} spellCheck={false} value={draft.configuration} onChange={event => update("configuration", event.target.value)} {...inputProps("configuration")} /><small>Merged over manifest defaults and saved with this registration. Do not enter secrets.</small>{fieldError("configuration")}</div>
          <div className="field"><label htmlFor="agent-model-files">External model files (optional)</label><textarea id="agent-model-files" rows={7} spellCheck={false} value={draft.modelFiles} onChange={event => update("modelFiles", event.target.value)} {...inputProps("modelFiles")} /><small>One absolute file path per line. Files are fingerprinted; weights are not uploaded or downloaded.</small>{fieldError("modelFiles")}</div>
        </div><details><summary>Advanced execution settings</summary><div className="agent-form-grid">
          <div className="field"><label htmlFor="agent-control-mode">Control authority</label><select id="agent-control-mode" value={draft.controlMode} onChange={event => update("controlMode", event.target.value as RegistrationDraft["controlMode"])}><option value="authoritative">Authoritative agent control</option><option value="scripted">Scripted scenario assistance</option></select><small>Authoritative control uses the agent’s commands. Scripted assistance can override its speed or braking and is recorded as an intervention. Sensor agents require authoritative control.</small></div>
          {(Object.keys(deadlineLabels) as (keyof AgentExecutionOptions)[]).map(key => <div className="field" key={key}><label htmlFor={`agent-${key}`}>{deadlineLabels[key]}</label><input id={`agent-${key}`} type="number" min={key === "max_message_bytes" ? 1 : undefined} step={key === "max_message_bytes" ? 1 : "any"} value={draft.processOptions[key]} onChange={event => { setDraft(previous => ({ ...previous, processOptions: { ...previous.processOptions, [key]: event.target.value } })); setFieldErrors(previous => { const next = { ...previous }; delete next[key]; return next; }); }} {...inputProps(key)} />{fieldError(key)}</div>)}
        </div><p className="muted">Deadlines use wall time. The decision deadline includes communication and inference; a missed deadline fails the run. These settings do not install dependencies.</p></details>
        <label className="checkbox-field agent-trust"><input id="agent-trusted-code" type="checkbox" checked={draft.trusted} onChange={event => update("trusted", event.target.checked)} {...inputProps("trusted")} /><span>I trust this local agent code and allow it to execute with the backend user’s permissions when I launch a run.</span></label>{fieldError("trusted")}
        </fieldset><div className="form-actions"><button type="submit" className="button primary" disabled={!active || mutating}>{mutating && mutation.current?.kind === "register" ? "Registering agent…" : "Register local agent"}</button><span className="muted">Registration never starts CARLA or executes agent code.</span></div>
      </form>
    </section>
    {mutationError && <p className="notice error-notice" role="alert">{mutationError}</p>}{notice && <p className="notice" role="status">{notice}</p>}
    <section className="panel" aria-labelledby="agent-library-heading"><div className="panel-header"><h2 id="agent-library-heading">Registered agents</h2><button type="button" className="button secondary small" disabled={!active || loading || mutating} onClick={() => setRefresh(value => value + 1)}>Refresh registrations</button></div>
      <div className="agent-panel-body"><div className="filter-bar"><div className="field"><label htmlFor="agent-active-filter">Registration status</label><select id="agent-active-filter" disabled={!active || loading || mutating} value={filter} onChange={event => { setFilter(event.target.value); setOffset(0); }}><option value="active">Active registrations</option><option value="inactive">Inactive registrations</option><option value="all">All registrations</option></select></div></div>
        {listError && <p className="notice error-notice" role="alert">{listError}</p>}{loading && <p role="status">Loading registrations…</p>}{!loading && !listError && !records.length && <p>No {filter === "all" ? "" : `${filter} `}registrations on this page. Register a local SDK package to make it available when launching a scenario.</p>}
        {!!records.length && <div className="table-scroll"><table className="data-table"><caption>Saved agent registrations. Active status does not imply successful model loading.</caption><thead><tr><th scope="col">Agent</th><th scope="col">Inputs</th><th scope="col">Status</th><th scope="col">Inspect</th></tr></thead><tbody>{records.map(agent => <tr key={agent.id}><th scope="row">{agent.snapshot.manifest.name}<br /><small>{agent.snapshot.manifest.version} · SDK {agent.snapshot.manifest.protocol_version}</small><br /><small><code>{agent.id.slice(0, 8)}…{agent.id.slice(-6)}</code> · {new Date(agent.created_at).toLocaleString()}</small></th><td>{agent.snapshot.observation_mode === "sensors" ? "Sensors" : "Structured state"}</td><td>{agent.active ? "Active" : "Inactive"}</td><td><button type="button" className="button secondary small" disabled={!active || mutating} aria-label={`Inspect ${agent.snapshot.manifest.name}, registration ${agent.id}`} aria-pressed={detailId === agent.id} onClick={() => setDetailId(agent.id)}>Inspect {agent.snapshot.manifest.name}</button></td></tr>)}</tbody></table></div>}
        <div className="pagination"><button type="button" className="button secondary small" disabled={!active || loading || mutating || !offset} onClick={() => setOffset(value => Math.max(0, value - PAGE_SIZE))}>Previous agent page</button><span>Page {Math.floor(offset / PAGE_SIZE) + 1}</span><button type="button" className="button secondary small" disabled={!active || loading || mutating || !hasNext} onClick={() => setOffset(value => value + PAGE_SIZE)}>Next agent page</button></div>
      </div>
    </section>
    <section className="panel" aria-labelledby="agent-detail-heading"><div className="panel-header"><h2 id="agent-detail-heading">Registration detail</h2>{detailId && <button type="button" className="button secondary small" disabled={mutating} onClick={() => { setDetailId(null); setDetail(null); }}>Close registration</button>}</div><div className="agent-panel-body">
      {!detailId ? <p>Inspect a registration to view its configuration, input contract, file fingerprints, and static readiness.</p> : detailError ? <div className="notice error-notice" role="alert"><p>{detailError}</p><button type="button" className="button secondary small" disabled={!active || mutating} onClick={() => setRefresh(value => value + 1)}>Retry registration detail</button></div> : detailLoading || !selected ? <p role="status">Loading registration detail…</p> : <>
        <AgentDetails agent={selected} />
        <div className="agent-readiness"><h3>Static readiness</h3><p>Checks saved files and declarations against this registration. Dependencies, inference, and driving behavior are unverified. This check does not start CARLA or execute the agent.</p><button type="button" className="button secondary small" disabled={!active || checking || mutating} onClick={() => { void checkReadiness(); }}>{checking ? "Checking files…" : "Check static readiness"}</button>
          {readinessError && <p className="notice error-notice" role="alert">{readinessError}</p>}{readiness && <div role="status"><p><strong>{readiness.ready ? "Static checks passed" : "Static checks found issues"}</strong> · {new Date(readiness.checked_at).toLocaleString()}</p>{!!readiness.issues.length && <ul>{readiness.issues.map((issue, index) => <li key={index}>{issue}</li>)}</ul>}<ul>{readiness.limitations.map((limitation, index) => <li key={index}>{limitation}</li>)}</ul></div>}
        </div>
        <div className="agent-readiness"><h3>Registration lifecycle</h3><p>To change code, model files, or configuration, register a new version. Deactivation blocks future launches. Saved and queued runs preserve their original snapshots.</p>{selected.active ? <button type="button" className="button secondary small" disabled={!active || mutating} onClick={() => { void deactivate(); }}>{mutating && mutation.current?.kind === "deactivate" ? "Deactivating…" : "Deactivate registration"}</button> : <p>This registration is inactive. Its history remains available.</p>}</div>
      </>}
    </div></section>
  </div>;
}
