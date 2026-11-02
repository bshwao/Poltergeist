import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../services/api";
import type { GitHubInputs, GitHubPreviewRequest, GitHubPublication, GitHubStatus, RegressionReport } from "../types/generated";
import { saveLabJson } from "./lab-download";

const choices = [
  ["metrics", "Measurement policy, counts and native-unit changes"],
  ["fingerprints", "Report, suite, controller and verified commit fingerprints"],
  ["run_ids", "Suite and matched run identifiers"],
  ["scenarios", "Scenario recipes, names, map coordinates and execution settings"],
] as const;
type Disclosure = typeof choices[number][0];

export function GitHubPublisher({ report, active }: { report: RegressionReport; active: boolean }) {
  const [opened, setOpened] = useState(false), [status, setStatus] = useState<GitHubStatus | null>(null), [inputs, setInputs] = useState<GitHubInputs | null>(null);
  const [candidateIndex, setCandidateIndex] = useState("1"), [refSHA, setRefSHA] = useState(""), [candidateSHA, setCandidateSHA] = useState("");
  const [refPrefix, setRefPrefix] = useState(""), [candidatePrefix, setCandidatePrefix] = useState(""), [refModels, setRefModels] = useState("[]"), [candidateModels, setCandidateModels] = useState("[]");
  const [disclose, setDisclose] = useState<Disclosure[]>([]), [details, setDetails] = useState("");
  const [record, setRecord] = useState<GitHubPublication | null>(null), [history, setHistory] = useState<GitHubPublication[]>([]), [approved, setApproved] = useState<string | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const pending = useRef<AbortController | null>(null), mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; pending.current?.abort(); }; }, []);
  useEffect(() => { if (!active) pending.current?.abort(); }, [active]);
  useEffect(() => { pending.current?.abort(); setRecord(null); setApproved(null); setHistory([]); }, [report.operation_id, report.source_digest, JSON.stringify(report.policy)]);
  async function action(work: (signal: AbortSignal) => Promise<void>) {
    if (!active || pending.current) return;
    const controller = new AbortController(); pending.current = controller; setBusy(true); setError(null);
    const timer = setTimeout(() => controller.abort(), 75000);
    try { await work(controller.signal); }
    catch (failure) { if (mounted.current) setError(failure instanceof ApiError && failure.status >= 400 && failure.status < 500 ? failure.message : "The outcome could not be confirmed. Refresh saved publication history or reconcile; publication is never repeated automatically."); }
    finally { clearTimeout(timer); pending.current = null; if (mounted.current) setBusy(false); }
  }
  function invalidate() { setRecord(null); setApproved(null); }
  async function configuration(signal: AbortSignal) {
    const value = await api.getGitHubStatus({ signal });
    if (signal.aborted || !mounted.current) return;
    setStatus(value);
    if (value.enabled && value.configured) {
      const mapping = await api.getGitHubInputs(report.operation_id, { signal });
      if (!signal.aborted && mounted.current) setInputs(mapping);
    }
  }
  function preview() {
    const modelPaths = (raw: string) => { const value: unknown = JSON.parse(raw); if (!Array.isArray(value) || value.some(item => typeof item !== "string")) throw new Error(); return value as string[]; };
    let request: GitHubPreviewRequest;
    try {
      if (![refSHA, candidateSHA].every(value => /^[0-9a-f]{40}$/.test(value))) throw new Error();
      request = { request_id: crypto.randomUUID(), operation_id: report.operation_id, candidate_index: Number(candidateIndex), policy: report.policy,
        reference: { commit: refSHA, package_prefix: refPrefix, model_paths: modelPaths(refModels) as GitHubPreviewRequest["reference"]["model_paths"] },
        candidate: { commit: candidateSHA, package_prefix: candidatePrefix, model_paths: modelPaths(candidateModels) as GitHubPreviewRequest["candidate"]["model_paths"] },
        disclose: disclose as GitHubPreviewRequest["disclose"], details_url: details.trim() || null };
    } catch { setError("Use full lowercase 40-character commit SHAs and JSON arrays of committed model-pin paths."); return; }
    void action(async signal => { const value = await api.previewGitHubCheck(request, { signal }); if (value.preview.source_digest !== report.source_digest) throw new ApiError("Regression evidence changed. Refresh the suite review before publishing.", 409); if (!signal.aborted && mounted.current) { setRecord(value); setApproved(null); } });
  }
  const ready = Boolean(status?.enabled && status.configured), index = Number(candidateIndex);
  const supported = Boolean(inputs?.controllers[0]?.supported && inputs?.controllers[index]?.supported);
  return <section className="lab-stack assistant-panel" aria-label="Optional GitHub reporting">
    <h3>Publish a commit-bound regression check</h3><p>Verify the frozen model inputs against exact GitHub commits, then review and approve the outgoing check. No repository code, issue or pull-request comment is changed.</p>
    {!opened ? <button className="button secondary" disabled={!active || busy} onClick={() => { setOpened(true); void action(configuration); }}>Open GitHub reporting</button> : <>
      {!status ? <p role="status">{busy ? "Reading local GitHub configuration…" : "GitHub configuration is unavailable."}</p> : !ready
        ? <p className="notice">GitHub reporting is off. Configure GITHUB_ENABLED, GITHUB_CHECKS_TOKEN, GITHUB_APP_ID and GITHUB_REPOSITORY on the local API. Use an App installation with Contents read and Checks write.</p>
        : <p className="notice">Target repository: {status.repository} · App {status.app_id} · at most {status.daily_publish_limit} reserved publication attempts per UTC day. Check names bind the measurement policy and candidate index.</p>}
      <button className="button secondary" disabled={!active || busy} onClick={() => void action(configuration)}>Refresh GitHub configuration</button>
      <fieldset className="lab-stack" disabled={!active || busy || !ready}><legend>Bind the recorded versions</legend>
        <label>Candidate controller for GitHub<select value={candidateIndex} onChange={e => { setCandidateIndex(e.target.value); invalidate(); }}>{report.controllers.slice(1).map(item => <option key={item.agent_index} value={item.agent_index}>Candidate {item.agent_index}: {item.name}</option>)}</select></label>
        {!supported && ready && inputs && <p role="status">Commit checks require two registered frozen controller versions. Built-in baseline comparisons remain local reviews.</p>}
        {([ ["Reference", refSHA, setRefSHA, refPrefix, setRefPrefix, refModels, setRefModels, 0],
            ["Candidate", candidateSHA, setCandidateSHA, candidatePrefix, setCandidatePrefix, candidateModels, setCandidateModels, index] ] as const).map(([label, sha, setSHA, prefix, setPrefix, models, setModels, inputIndex]) => <fieldset className="lab-stack" key={label}><legend>{label} commit inputs</legend>
          <label>{label} full GitHub commit SHA<input value={sha} maxLength={40} onChange={e => { setSHA(e.target.value.trim()); invalidate(); }} spellCheck={false} /></label>
          <label>{label} package directory in the repository<input value={prefix} placeholder="Empty for repository root" onChange={e => { setPrefix(e.target.value); invalidate(); }} spellCheck={false} /></label>
          {inputs?.controllers[inputIndex] && <details><summary>{label} frozen local inputs</summary><p>{inputs.controllers[inputIndex].package_files} package files. Local package: <code>{inputs.controllers[inputIndex].package_path ?? "Not recorded"}</code></p><ul>{inputs.controllers[inputIndex].models.map(model => <li key={model.index}>Model {model.index + 1}: {model.size_bytes} bytes · SHA-256 <code>{model.sha256}</code></li>)}</ul></details>}
          <label>{label} committed external model-pin paths JSON<textarea rows={2} value={models} onChange={e => { setModels(e.target.value); invalidate(); }} spellCheck={false} /></label>
        </fieldset>)}
        <p className="field-hint">Package inventories must match. Large weights use committed LFS pointers or JSON pins containing exactly sha256 and size_bytes. Provide one model path per displayed external model, in order. No branch names or short revisions are accepted.</p>
        <fieldset><legend>Private evidence to disclose (nothing selected by default)</legend>{choices.map(([key, label]) => <label className="lab-check" key={key}><input type="checkbox" checked={disclose.includes(key)} onChange={e => { setDisclose(old => e.target.checked ? [...old, key] : old.filter(item => item !== key)); invalidate(); }} />{label}</label>)}</fieldset>
        <label>Optional shared report HTTPS link<input value={details} onChange={e => { setDetails(e.target.value); invalidate(); }} placeholder="No link is shared by default" /></label>
        <p className="field-hint">Verification reads the selected repository's immutable Git objects. Publishing shares the commits, verdict, opaque check identity and only the selected evidence. Raw traces, recordings, credentials and configuration values are excluded. Inspect full scenario metadata before choosing to disclose it.</p>
        <button type="button" className="button secondary" disabled={!supported || report.state === "pending"} onClick={preview}>Verify commits and preview GitHub check</button>
      </fieldset>
      {record && <article className="lab-stack" aria-label="GitHub publication review"><h4>Review the exact GitHub publication</h4>
        <p>{record.preview.repository} · candidate <code>{record.preview.candidate.commit}</code> · App {record.preview.app_id} · conclusion <strong>{record.preview.conclusion.replace(/_/g, " ")}</strong></p>
        <details><summary>Bound policy and source identity (local)</summary><pre tabIndex={0} role="region" aria-label="GitHub bound policy">{JSON.stringify({ operation: record.preview.operation_id, execution: record.preview.execution_status, policy: record.preview.policy, source: record.preview.source_digest, reference: record.preview.reference, candidate: record.preview.candidate }, null, 2)}</pre></details>
        <details><summary>Exact outgoing GitHub check JSON (no token)</summary><pre tabIndex={0} role="region" aria-label="Exact GitHub disclosure">{JSON.stringify(JSON.parse(record.preview.payload), null, 2)}</pre></details><p role="status">{record.message}</p>
        {record.state === "preview" && <><label className="lab-check"><input type="checkbox" checked={approved === record.preview.digest} disabled={busy || !active} onChange={e => setApproved(e.target.checked ? record.preview.digest : null)} />I approve publishing this exact check and selected evidence to this repository and commit</label><button className="button primary" disabled={approved !== record.preview.digest || busy || !active || !ready} onClick={() => { if (approved !== record.preview.digest) return; void action(async signal => { const value = await api.publishGitHubCheck(record.id, { digest: record.preview.digest, approve: true }, { signal }); if (!signal.aborted && mounted.current) { setRecord(value); setApproved(null); } }); }}>Publish approved GitHub check</button></>}
        {record.check_url && <a className="button secondary" href={record.check_url} target="_blank" rel="noreferrer">Open verified GitHub check</a>}
        {["uncertain", "publishing"].includes(record.state) && <p className="notice">The remote outcome is unconfirmed. Refresh or reconcile this request; it will never automatically publish again.</p>}
        <div className="button-row"><button className="button secondary" disabled={busy || !active} onClick={() => void action(async signal => { const value = await api.getGitHubPublication(record.id, { signal }); if (!signal.aborted && mounted.current) setRecord(value); })}>Refresh saved GitHub publication</button>
          {record.state !== "preview" && <button className="button secondary" disabled={busy || !active || !ready} onClick={() => void action(async signal => { const value = await api.reconcileGitHubCheck(record.id, { signal }); if (!signal.aborted && mounted.current) setRecord(value); })}>Reconcile GitHub check (read only)</button>}
          <button className="button secondary" onClick={() => saveLabJson(record, `poltergeist-github-${record.id}.json`)}>Download GitHub publication record</button></div>
      </article>}
      <button className="button secondary" disabled={!active || busy} onClick={() => void action(async signal => { const values = await api.listGitHubPublications({ signal }); if (!signal.aborted && mounted.current) setHistory(values.filter(item => item.preview.operation_id === report.operation_id)); })}>Refresh GitHub publication history</button>
      <ul className="lab-saved-list">{history.map(item => <li key={item.id}><span>{item.preview.repository} · {item.state} · {item.preview.conclusion.replace(/_/g, " ")}</span><button className="button secondary small" disabled={busy || !active} onClick={() => { setRecord(item); setApproved(null); }}>Review publication {item.id.slice(0, 8)}</button></li>)}</ul>
    </>}{busy && <p role="status">Working on one bounded GitHub reporting request…</p>}{error && <p role="alert">{error}</p>}
  </section>;
}
