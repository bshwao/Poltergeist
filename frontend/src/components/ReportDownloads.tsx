import { useEffect, useId, useRef, useState } from "react";
import { api, ApiError } from "../services/api";

type Format = "json" | "csv" | "html";

export function saveReportBlob(blob: Blob, simulationId: string, format: Format) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  try {
    anchor.href = url; anchor.download = `poltergeist-${simulationId}.${format}`;
    document.body.appendChild(anchor); anchor.click();
  } finally { anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 0); }
}

export function ReportDownloads({ simulationId, active = true }: { simulationId: string; active?: boolean }) {
  const id = useId();
  const [telemetry, setTelemetry] = useState(false);
  const [behavior, setBehavior] = useState(true);
  const [state, setState] = useState<{ id: string; busy: boolean; error?: string; message?: string } | null>(null);
  const request = useRef<AbortController | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const currentId = useRef(simulationId); currentId.current = simulationId;
  const visible = state?.id === simulationId ? state : null;
  const busy = Boolean(visible?.busy);
  useEffect(() => {
    request.current?.abort(); request.current = null; setState(null);
    return () => { request.current?.abort(); request.current = null; if (timer.current !== null) clearTimeout(timer.current); timer.current = null; };
  }, [simulationId, active]);
  const download = async (format: Format) => {
    if (!active || request.current) return;
    const controller = new AbortController(); request.current = controller;
    const deadline = setTimeout(() => {
      controller.abort();
      if (request.current === controller && currentId.current === simulationId) {
        request.current = null; timer.current = null;
        setState({ id: simulationId, busy: false, error: "Report download timed out. Retry, or use the report CLI with a longer timeout for large recordings." });
      }
    }, 60_000);
    timer.current = deadline;
    setState({ id: simulationId, busy: true });
    try {
      const blob = await api.getReport(simulationId, format, format === "json" && telemetry, behavior, { signal: controller.signal });
      if (request.current !== controller || controller.signal.aborted || currentId.current !== simulationId) return;
      saveReportBlob(blob, simulationId, format);
      setState({ id: simulationId, busy: false, message: `${format.toUpperCase()} report sent to your browser’s downloads.` });
    } catch (error) {
      if (request.current === controller && currentId.current === simulationId) setState({ id: simulationId, busy: false,
        error: controller.signal.aborted ? "Report download timed out. Retry, or use the report CLI with a longer timeout for large recordings."
          : error instanceof ApiError ? error.message : "Could not save this report. Retry the download." });
    } finally { clearTimeout(deadline); if (request.current === controller) { request.current = null; timer.current = null; } }
  };
  const cancel = () => { request.current?.abort(); request.current = null; if (timer.current !== null) clearTimeout(timer.current); timer.current = null; setState({ id: simulationId, busy: false, message: "Report download cancelled." }); };
  return <section className="panel report-downloads" aria-labelledby={`${id}-heading`}>
    <div className="panel-header"><div><p className="eyebrow">TAKE THE EVIDENCE WITH YOU</p><h2 id={`${id}-heading`}>Export this run</h2></div></div>
    <div className="form-section"><p>Reports include the recorded recipe, agent/version identity, input provenance, safety metrics, settings and failure reports. Unknown observations stay unknown.</p>
      <label className="checkbox-field"><input type="checkbox" checked={telemetry} disabled={busy || !active} onChange={event => setTelemetry(event.target.checked)} />Include finalized telemetry in the JSON report</label>
      <label className="checkbox-field"><input type="checkbox" checked={behavior} disabled={busy || !active} onChange={event => setBehavior(event.target.checked)} />Include behavior analysis and recorded agent diagnostics</label>
      <p className="field-hint">Behavior exports use the same default thresholds and findings as the review panel. Agent diagnostics are separately labelled statements; the earliest records are retained up to 200 entries or 1 MiB of compact record JSON, with omitted counts shown.</p>
      <p className="field-hint">CSV preserves every retained field as JSON Pointer rows. HTML is readable offline and printable. Full telemetry can make JSON downloads large; raw sensor captures remain separate artifacts. Disable behavior analysis to download result metadata when its required evidence is unavailable.</p>
      <div className="report-download-actions">{(["json", "csv", "html"] as const).map(format => <a key={format} className="button secondary small" aria-disabled={busy || !active} tabIndex={busy || !active ? -1 : 0} href={api.reportUrl(simulationId, format, format === "json" && telemetry, behavior)} download onClick={event => {
        if (busy || !active) { event.preventDefault(); return; }
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault(); void download(format);
      }}>Download {format.toUpperCase()} {format === "csv" ? "evidence" : "report"}</a>)}</div>
      <div aria-live="polite">{busy && <p role="status">Preparing and downloading saved evidence… <button type="button" className="button secondary small" onClick={cancel}>Cancel download</button></p>}
        {visible?.message && <p role="status">{visible.message}</p>}{visible?.error && <p className="notice error-notice" role="alert">{visible.error} Select a format to retry.</p>}</div>
    </div>
  </section>;
}
