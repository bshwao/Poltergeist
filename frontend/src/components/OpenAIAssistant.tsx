import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../services/api";
import type { AnalysisReference, AssistantRecord, AssistantRequest, AssistantStatus, DiscoveryProposal, EvidenceAnalysis } from "../types/generated";
import { simulationFrameLink } from "../navigation";
import { saveLabJson } from "./lab-download";

export function OpenAIAssistant({ active, proposal, evidence, inspect }: { active: boolean; proposal?: () => DiscoveryProposal; evidence?: EvidenceAnalysis; inspect?: (frame: number, source: AnalysisReference["source"]) => void }) {
  const [opened, setOpened] = useState(false), [status, setStatus] = useState<AssistantStatus | null>(null);
  const [question, setQuestion] = useState(""), [tokens, setTokens] = useState("1024"), [selection, setSelection] = useState<number[]>([]);
  const [approved, setApproved] = useState(false), [record, setRecord] = useState<AssistantRecord | null>(null);
  const [history, setHistory] = useState<AssistantRecord[]>([]), [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const pending = useRef<AbortController | null>(null), mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; pending.current?.abort(); }; }, []);
  useEffect(() => { if (!active) pending.current?.abort(); }, [active]);
  useEffect(() => { pending.current?.abort(); setRecord(null); setApproved(false); setSelection([]); }, [evidence?.simulation_id, evidence?.source_digest, evidence?.telemetry_digest]);
  async function action(work: (signal: AbortSignal) => Promise<void>) {
    if (!active || pending.current) return;
    const controller = new AbortController(); pending.current = controller; setBusy(true); setError(null);
    const timer = setTimeout(() => controller.abort(), 65000);
    try { await work(controller.signal); }
    catch (failure) { if (mounted.current) setError(failure instanceof ApiError && failure.status >= 400 && failure.status < 500 ? failure.message : "This request could not be confirmed. Refresh saved assistant history before retrying; it is never sent again automatically."); }
    finally { clearTimeout(timer); pending.current = null; if (mounted.current) setBusy(false); }
  }
  function preview() {
    void action(async signal => {
      if (!question.trim() || !/^\d+$/.test(tokens) || Number(tokens) < 128 || Number(tokens) > 2048) { setError("Enter a question and a whole-number output limit from 128 to 2048 tokens."); return; }
      const value = await api.previewAssistant({ request_id: crypto.randomUUID(), kind: evidence ? "analysis" : "plan", prompt: question,
        budget: { max_provider_calls: 1, max_input_bytes: 16384, max_output_tokens: Number(tokens) },
        proposal: proposal ? proposal() : null, simulation_id: evidence?.simulation_id ?? null,
        observation_ids: (evidence ? selection : []) as AssistantRequest["observation_ids"] }, { signal });
      if (!signal.aborted && mounted.current) { setRecord(value); setApproved(false); }
    });
  }
  function invalidate() { setRecord(null); setApproved(false); }
  const ready = status?.enabled && status.configured;
  return <section className="lab-stack assistant-panel" aria-label="Optional OpenAI assistance">
    <h3>Optional OpenAI assistance</h3><p>Ask a question, review what would leave this computer, then approve one provider call. Experiment execution needs its own approval.</p>
    {!opened ? <button className="button secondary" disabled={!active || busy} onClick={() => { setOpened(true); void action(async signal => { const value = await api.getAssistantStatus({ signal }); if (!signal.aborted && mounted.current) setStatus(value); }); }}>Open language assistance</button> : <>
      {!status ? <p role="status">{busy ? "Checking local provider configuration…" : "Provider configuration is unavailable."}</p> : !ready
        ? <p className="notice" role="status">OpenAI assistance is off. Configure OPENAI_ENABLED, OPENAI_API_KEY and OPENAI_MODEL on the local API, then reopen this page. Credentials stay on the API server.</p>
        : <p className="notice">Model: {status.model}. Daily limits: {status.daily_request_limit} calls and {status.daily_output_token_limit} reserved output tokens. Failed or uncertain calls keep their reservation. Input tokens may also be billed.</p>}
      <button className="button secondary" disabled={!active || busy} onClick={() => void action(async signal => { const value = await api.getAssistantStatus({ signal }); if (!signal.aborted && mounted.current) setStatus(value); })}>Refresh OpenAI configuration</button>
      <fieldset disabled={!active || busy || !ready} className="lab-stack"><legend>{evidence ? "Ask about selected recorded observations" : "Draft from your current discovery settings"}</legend>
        <label>Question for OpenAI<textarea rows={2} maxLength={evidence ? 1000 : 500} value={question} onChange={e => { setQuestion(e.target.value); invalidate(); }} /></label>
        <label>Maximum OpenAI output tokens<input inputMode="numeric" value={tokens} onChange={e => { setTokens(e.target.value); invalidate(); }} /></label>
        {evidence && <fieldset><legend>Observations to disclose (choose up to 16)</legend>{evidence.observations.map((item, index) => <label className="lab-check" key={index}>
          <input type="checkbox" checked={selection.includes(index)} onChange={e => { setSelection(previous => e.target.checked ? [...previous, index] : previous.filter(id => id !== index)); invalidate(); }} disabled={!selection.includes(index) && selection.length >= 16} />{item.statement} <small>Coverage: {item.coverage}</small></label>)}</fieldset>}
        <p className="field-hint">Analysis discloses checked observations, values, frame references and coverage. Planning discloses your question and the current scenario family/failure policy. Raw traces, images, local paths, credentials and agent packages are excluded. Automatic redaction is limited; inspect the preview.</p>
        <button type="button" className="button secondary" disabled={!question.trim() || Boolean(evidence && !selection.length)} onClick={preview}>Preview OpenAI disclosure</button>
      </fieldset>
      {record && <article className="lab-stack" aria-label="OpenAI request review"><h4>Review disclosure and usage</h4>
        <p>{record.preview.input_bytes} input bytes · at most {record.preview.budget.max_output_tokens} output tokens · one call · {record.preview.model}</p>
        <details><summary>Exact provider request (no API key)</summary><pre tabIndex={0} role="region" aria-label="Exact OpenAI disclosure">{JSON.stringify(JSON.parse(record.preview.payload), null, 2)}</pre></details><p role="status">{record.message}</p>
        {record.reserved_output_tokens > 0 && <p>Reported usage: {record.input_tokens ?? "Unknown"} input tokens · {record.output_tokens ?? "Unknown"} output tokens. The {record.reserved_output_tokens}-token reservation remains charged to the local daily budget.</p>}
        {record.state === "preview" && <><label className="lab-check"><input type="checkbox" checked={approved} disabled={busy || !active} onChange={e => setApproved(e.target.checked)} />I approve sending this exact disclosure to OpenAI within these usage limits</label>
          <button className="button primary" disabled={!approved || busy || !active || !ready} onClick={() => void action(async signal => { const value = await api.sendAssistant(record.id, { digest: record.preview.digest, approve: true }, { signal }); if (!signal.aborted && mounted.current) { setRecord(value); setApproved(false); } })}>Send approved OpenAI request</button></>}
        {record.state === "running" && <p className="notice">The provider outcome is unconfirmed. Refresh this saved request; it will never be resent. A new request spends a new budget reservation.</p>}
        {record.plan && <a className="button secondary" href={`#discovery/${record.plan.id}`}>Review the drafted experiment plan</a>}
        {record.analysis && <><p>{record.analysis.mode === "test" ? "Synthetic evidence" : "CARLA evidence"} · coverage {record.analysis.state}. Missing evidence stays unknown.</p>
          {record.analysis.observations.map((item, i) => <article className="review-finding" key={i}><p>{item.statement}</p><small>Coverage: {item.coverage}</small><ul>{item.references.map((ref, j) => <li key={j}>{ref.frame === null ? `Saved result: ${ref.field}` : inspect && record.analysis!.simulation_id === evidence?.simulation_id ? <button className="button secondary small" disabled={!active} onClick={() => inspect(ref.frame!, ref.source)}>Inspect frame {ref.frame} · {ref.time_s?.toFixed(3)} s · {ref.source}</button> : <a href={simulationFrameLink(record.analysis!.simulation_id, ref.frame)}>Inspect frame {ref.frame} · {ref.time_s?.toFixed(3)} s · {ref.source}</a>}</li>)}</ul></article>)}
          <details><summary>Coverage, unknowns and limits</summary><pre tabIndex={0} role="region" aria-label="Recorded evidence coverage">{JSON.stringify(record.analysis.coverage, null, 2)}</pre><ul>{[...record.analysis.unresolved_questions, ...record.analysis.limitations].map((note, i) => <li key={i}>{note}</li>)}</ul></details></>}
        <div className="button-row"><button className="button secondary" disabled={busy || !active} onClick={() => void action(async signal => { const value = await api.getAssistantRequest(record.id, { signal }); if (!signal.aborted && mounted.current) setRecord(value); })}>Refresh saved assistant request</button><button className="button secondary" onClick={() => saveLabJson(record, `poltergeist-assistant-${record.id}.json`)}>Download assistant record</button></div>
      </article>}
      <button className="button secondary" disabled={busy || !active} onClick={() => void action(async signal => { const values = await api.listAssistantRequests({ signal }); if (!signal.aborted && mounted.current) setHistory(values); })}>Refresh assistant history</button>
      <ul className="lab-saved-list">{history.map(item => <li key={item.id}><span>{item.preview.kind} · {item.state} · {new Date(item.preview.created_at).toLocaleString()}</span><button className="button secondary small" disabled={busy || !active} onClick={() => { setRecord(item); setApproved(false); }}>Review assistant request {item.id.slice(0, 8)}</button></li>)}</ul>
    </>}{busy && <p role="status">Working on one bounded assistant request…</p>}{error && <p role="alert">{error}</p>}
  </section>;
}
