import { useId, useState } from "react";
import { api } from "../services/api";

export function ReportDownloads({ simulationId }: { simulationId: string }) {
  const id = useId();
  const [telemetry, setTelemetry] = useState(false);
  return <section className="panel report-downloads" aria-labelledby={`${id}-heading`}>
    <div className="panel-header"><div><p className="eyebrow">TAKE THE EVIDENCE WITH YOU</p><h2 id={`${id}-heading`}>Export this run</h2></div></div>
    <div className="form-section"><p>Reports include the recorded recipe, execution mode, metrics, observation coverage, settings, runtime provenance, and failure reports. Unknown observations stay unknown.</p>
      <label className="checkbox-field"><input type="checkbox" checked={telemetry} onChange={event => setTelemetry(event.target.checked)} />Include finalized telemetry in the JSON report</label>
      <p className="field-hint">Full telemetry can make the JSON download large. CSV is a summary; HTML is a readable report. Raw sensor bytes remain separate artifacts.</p>
      <div className="report-download-actions"><a className="button secondary small" href={api.reportUrl(simulationId, "json", telemetry)} download>Download JSON report</a><a className="button secondary small" href={api.reportUrl(simulationId, "csv")} download>Download CSV summary</a><a className="button secondary small" href={api.reportUrl(simulationId, "html")} download>Download HTML report</a></div>
    </div>
  </section>;
}
