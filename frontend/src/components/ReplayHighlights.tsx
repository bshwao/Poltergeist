import { api } from "../services/api";
import type { LabClient } from "../services/lab-client";
import { simulationFrameLink } from "../navigation";
import { useLabReport } from "../hooks/useLab";

export function ReplayHighlightsPanel({ simulationId, active = true, client = api, inspect, frameHref = simulationFrameLink }: {
  simulationId: string; active?: boolean; client?: Pick<LabClient, "getReplayHighlights">; inspect?: (frame: number) => void; frameHref?: (id: string, frame: number) => string;
}) {
  const state = useLabReport(simulationId, active, (id, signal) => client.getReplayHighlights(id, { signal }), () => true);
  return <section className="panel replay-highlights" aria-label="Replay highlights"><div className="panel-header"><h2>Replay highlights</h2><button type="button" className="button secondary small" onClick={state.refresh} disabled={state.loading}>Refresh highlights</button></div>
    <div className="panel-body">{state.loading && !state.value && <p role="status">Finding recorded moments…</p>}{state.error && <p role="alert">{state.error}</p>}
      {state.value && <><p>{state.value.explanation}</p><p className="field-hint">{state.value.total_frames} recorded frames · {state.value.coverage_complete ? "Complete recorded safety coverage" : "Partial or unavailable safety coverage"}</p>
        {state.value.highlights.length ? <ol className="highlight-list">{state.value.highlights.map((h, index) => <li key={`${h.kind}/${h.frame}/${index}`}><div><strong>{h.title}</strong><span className="field-hint">Frame {h.frame} · {h.time_s.toFixed(2)} s</span><p>{h.description}</p></div>
          {inspect ? <button className="button secondary small" onClick={() => inspect(h.frame)}>Inspect frame {h.frame}</button> : <a className="button secondary small" href={frameHref(simulationId, h.frame)}>Inspect frame {h.frame}</a>}</li>)}</ol> : <p>No frame highlights are available. Review the run's coverage and execution status.</p>}
        <details><summary>How to interpret these moments</summary>{state.value.limitations.map(note => <p key={note}>{note}</p>)}</details></>}
    </div></section>;
}
