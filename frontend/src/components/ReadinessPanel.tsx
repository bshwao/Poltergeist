import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../services/api";
import type { ReadinessReport, ReadinessStatus, WorkflowReadiness } from "../types/generated";

const labels: Record<ReadinessStatus, string> = {
  ready: "Ready", missing: "Missing", unsupported: "Unsupported", not_running: "Not running",
  not_checked: "Not checked", unknown: "Unknown", error: "Needs attention",
};
function Status({ status }: { status: ReadinessStatus }) {
  return <span className={`status-badge readiness-${status}`}>{labels[status]}</span>;
}
function Workflow({ title, workflow }: { title: string; workflow: WorkflowReadiness }) {
  return <article className="readiness-workflow">
    <div className="readiness-title"><h3>{title}</h3><Status status={workflow.status} /></div>
    <p className="readiness-summary">{workflow.summary}</p>
    <ul className="readiness-checks">{workflow.checks.map(check => <li key={check.id}>
      <div className="readiness-title"><h4>{check.label}</h4><Status status={check.status} /></div>
      <p>{check.detail}</p>{check.next_step && <p className="readiness-next"><strong>Next:</strong> {check.next_step}</p>}
    </li>)}</ul>
  </article>;
}

export function ReadinessPanel({ active }: { active: boolean }) {
  const [report, setReport] = useState<ReadinessReport | null>(null);
  const [pending, setPending] = useState<"static" | "carla" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const request = useRef<AbortController | null>(null);
  const visible = useRef(active);
  visible.current = active;
  const load = useCallback(async (checkCarla = false) => {
    if (!visible.current || request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setPending(checkCarla ? "carla" : "static"); setError(null);
    const deadline = setTimeout(() => {
      if (request.current !== controller || !visible.current) return;
      controller.abort(); request.current = null; setPending(null);
      setError("Setup check timed out. Retry when the backend is available.");
    }, 16_000);
    try {
      const result = await (checkCarla ? api.checkCarlaConnection : api.getReadiness)({ signal: controller.signal });
      if (request.current !== controller || !visible.current || controller.signal.aborted) return;
      setReport(result);
    } catch (failure) {
      if (request.current === controller && visible.current) setError(failure instanceof ApiError ? failure.message : "Could not load setup readiness. Check the backend and retry.");
    } finally {
      clearTimeout(deadline);
      if (request.current === controller) { request.current = null; setPending(null); }
    }
  }, []);
  useEffect(() => {
    if (active) void load(); else setPending(null);
    return () => { request.current?.abort(); request.current = null; };
  }, [active, load]);

  return <section className="panel readiness-panel" aria-labelledby="readiness-heading" aria-busy={pending !== null}>
    <div className="panel-header"><div><p className="eyebrow">LOCAL SETUP</p><h2 id="readiness-heading">Setup readiness</h2></div>
      <div className="readiness-actions">
        <button type="button" className="button secondary small" disabled={!active || pending !== null} onClick={() => void load()}>{pending === "static" ? "Checking setup…" : error ? "Retry setup check" : "Refresh setup"}</button>
        <button type="button" className="button secondary small" disabled={!active || pending !== null} onClick={() => void load(true)}>{pending === "carla" ? "Checking CARLA…" : "Check CARLA connection"}</button>
      </div>
    </div>
    <div className="detail-content">
      <p className="field-hint">Checks the computer running this backend. Refresh reads local prerequisites. Check CARLA connection contacts an already running server; neither action starts CARLA, runs an agent, or changes a world.</p>
      <p className="field-hint">Need help with a missing prerequisite? Follow the <a href="https://github.com/bshwao/Poltergeist/blob/main/docs/first-run.md" target="_blank" rel="noreferrer">First run guide</a>.</p>
      {pending && <p role="status">{pending === "carla" ? "Checking the CARLA connection and reported versions…" : "Checking local setup…"}{report ? " Previous snapshot shown while checking." : ""}</p>}
      {error && <p className="notice error-notice" role="alert">{error}{report ? " Previous snapshot shown below; it may be out of date." : " Readiness is unknown until a check succeeds."}</p>}
      {report && <><p className="field-hint">Snapshot from <time dateTime={report.checked_at}>{new Date(report.checked_at).toLocaleString()}</time>. {report.carla_connection_requested ? "Includes an explicit connection check." : "Static checks only; CARLA connection is not checked."}</p>
        <div className="readiness-grid"><Workflow title="Synthetic test" workflow={report.synthetic} /><Workflow title="CARLA live simulation" workflow={report.carla} /></div>
        <p className="field-hint">Ready means the listed prerequisites passed at that time. A real run must still open storage and execute successfully. Synthetic results do not establish real driving, perception or model quality.</p></>}
    </div>
  </section>;
}
