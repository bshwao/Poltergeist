import { useState } from "react";
import { api } from "../services/api";
import type { LabClient } from "../services/lab-client";
import type { AnalysisReference } from "../types/generated";
import { useLabReport } from "../hooks/useLab";
import { simulationFrameLink } from "../navigation";
import { saveLabJson } from "./lab-download";
import "../lab.css";

export function EvidenceAnalystPanel({ simulationId, active = true, available = true, client = api, inspect,
  frameHref = simulationFrameLink }: { simulationId: string; active?: boolean; available?: boolean;
  client?: Pick<LabClient, "getEvidenceAnalysis">; inspect?: (frame: number, source: AnalysisReference["source"]) => void;
  frameHref?: (id: string, frame: number) => string }) {
  const [opened, setOpened] = useState(false);
  const supported = available && typeof client.getEvidenceAnalysis === "function";
  const state = useLabReport(opened ? simulationId : null, active && supported,
    (id, signal) => client.getEvidenceAnalysis(id, { signal }), value => value.state !== "pending");
  const report = state.value;
  function refs(items: AnalysisReference[]) {
    return <ul className="review-references">{items.map((ref, index) => <li key={index}>
      {ref.frame === null ? <span>Saved result: <code>{ref.field}</code></span> : inspect
        ? <button className="button secondary small" disabled={!active} onClick={() => inspect(ref.frame!, ref.source)}>Inspect frame {ref.frame}</button>
        : <a className="button secondary small" href={frameHref(simulationId, ref.frame)}>Inspect frame {ref.frame}</a>}
      {ref.time_s !== null && <small>{ref.time_s.toFixed(3)} s · {ref.source} · {ref.field}</small>}
    </li>)}</ul>;
  }
  return <section className="lab-surface panel evidence-analyst" aria-label="Evidence Analyst">
    <div className="panel-header"><h2>Evidence Analyst</h2><span className="lab-tag">Recorded evidence · deterministic review</span></div>
    <div className="panel-body">
      <p>Review contacts, braking, scripted interventions and missing observations, with links to the saved frames.</p>
      {!supported ? <p role="status">Update and reconnect the companion to enable evidence analysis.</p>
        : <button type="button" className="button secondary" disabled={!active || state.loading}
          onClick={() => { if (opened) state.refresh(); else setOpened(true); }}>{state.loading ? "Reviewing saved evidence…" : opened ? "Refresh evidence analysis" : "Analyze recorded evidence"}</button>}
      {state.error && <p className="notice error-notice" role="alert">{state.error}{report && " Showing the previous review; the latest source could not be confirmed."}</p>}
      {report && <div className="review-stack">
        <p className="notice" role="status">{report.summary}</p>
        <dl className="review-facts">
          <div><dt>Recorded frames reviewed</dt><dd>{report.coverage.telemetry_frames ?? "Unknown"}</dd></div>
          <div><dt>Safety observation coverage</dt><dd>{report.coverage.safety_complete === null ? "Unknown" : report.coverage.safety_complete ? "Complete for the saved trace" : "Incomplete"}</dd></div>
          <div><dt>Decision traces</dt><dd>{report.coverage.decision_trace === "not_reviewed" ? "Not reviewed" : "Unavailable"}</dd></div>
        </dl>
        <div className="review-observations">{report.observations.map((item, index) => <article className="review-finding" key={index}>
          <h3>{item.kind.replace(/_/g, " ")} observation</h3><p>{item.statement}</p><small>Coverage: {item.coverage}</small>{refs(item.references)}
        </article>)}</div>
        {report.hypotheses.length > 0 && <section className="review-hypotheses"><h3>Unverified hypotheses</h3><p>These are questions for review, separate from the recorded observations.</p>
          {report.hypotheses.map((item, index) => <article className="review-finding" key={index}><p>{item.statement}</p><p>{item.review}</p>{refs(item.references)}</article>)}
        </section>}
        <details><summary>Unknowns, source identity and limits</summary><ul>{report.unresolved_questions.map(item => <li key={item}>{item}</li>)}</ul>
          <ul>{report.limitations.map(item => <li key={item}>{item}</li>)}</ul>
          <p>Result fingerprint: <code>{report.source_digest ?? "Unavailable"}</code></p><p>Reviewed telemetry fingerprint: <code>{report.telemetry_digest ?? "Unavailable"}</code></p>
        </details>
        <button type="button" className="button secondary" onClick={() => saveLabJson(report, `poltergeist-analysis-${simulationId}.json`)}>Download evidence analysis</button>
      </div>}
    </div>
  </section>;
}
