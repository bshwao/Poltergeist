import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../services/api";
import { failureKindLabel, observation } from "../services/failure-view";
import type { FailureKind, FailureReport, SimulationResult } from "../types/generated";
import { DEFAULT_EVALUATION } from "./launch-form";

/** Match the API's positive evidence rule; missing observations do not become zero. */
export function eligibleFailureKinds(result: SimulationResult): FailureKind[] {
  const metrics = result.metrics;
  if (!["completed", "failed", "cancelled"].includes(result.simulation.status) || metrics.severity === null || !Number.isFinite(metrics.severity) || metrics.severity < 0 || metrics.severity > 100) return [];
  const settings = metrics.evaluation_settings ?? DEFAULT_EVALUATION;
  const kinds: FailureKind[] = [];
  if (metrics.collision_count !== null && metrics.collision_count > 0) kinds.push("collision");
  if ((metrics.min_ttc_s !== null && metrics.min_ttc_s < settings.ttc_warning_s) || (metrics.min_clearance_m !== null && metrics.min_clearance_m < settings.clearance_warning_m)) kinds.push("near_miss");
  if (metrics.hard_braking_count !== null && metrics.hard_braking_count > 0) kinds.push("hard_braking");
  if (metrics.traffic_violation_count !== null && metrics.traffic_violation_count > 0) kinds.push("traffic_violation");
  return kinds;
}

export function FailureReportForm({ result, active = true }: { result: SimulationResult; active?: boolean }) {
  const eligible = eligibleFailureKinds(result);
  const [kind, setKind] = useState<FailureKind | "">(eligible[0] ?? "");
  const [summary, setSummary] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<FailureReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const request = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const currentId = useRef(result.simulation.id);
  const isActive = useRef(active);
  currentId.current = result.simulation.id;
  isActive.current = active;
  const fieldPrefix = `failure-create-${result.simulation.id}`;

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; request.current?.abort(); }; }, []);
  useEffect(() => {
    request.current?.abort(); request.current = null;
    setKind(eligibleFailureKinds(result)[0] ?? ""); setSummary(""); setSaving(false); setSaved(null); setError(null);
  // The immutable run ID owns this form. Background refreshes of that run retain edits.
  }, [result.simulation.id]);
  useEffect(() => {
    if (!active && request.current) {
      request.current.abort(); request.current = null; setSaving(false);
      setError("The save response was interrupted. Check Failure reports before creating another report; the API may have saved it.");
    }
  }, [active]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (request.current || !isActive.current || saved) return;
    if (!kind || !eligibleFailureKinds(result).includes(kind) || result.metrics.severity === null) { setError("Choose a failure kind supported by this scored result."); return; }
    if (!summary.trim()) { setError("Describe the observed failure before saving the report."); return; }
    const controller = new AbortController();
    request.current = controller;
    const sourceId = result.simulation.id;
    setSaving(true); setError(null);
    try {
      const report: FailureReport = {
        id: globalThis.crypto.randomUUID(), simulation_id: sourceId,
        scenario: JSON.parse(JSON.stringify(result.simulation.scenario)) as FailureReport["scenario"],
        kind, summary: summary.trim(), severity: result.metrics.severity,
        reproduced: null, created_at: new Date().toISOString(),
      };
      const accepted = await api.createFailure(report, { signal: controller.signal });
      if (!mounted.current || !isActive.current || controller.signal.aborted || currentId.current !== sourceId || request.current !== controller) return;
      setSaved(accepted);
    } catch (failure) {
      if (!mounted.current || !isActive.current || currentId.current !== sourceId || controller.signal.aborted || request.current !== controller) return;
      const uncertain = !(failure instanceof ApiError) || failure.status === 0 || failure.status >= 200 && failure.status < 300 || failure.status >= 500;
      const detail = failure instanceof ApiError ? failure.message : "The failure report could not be saved.";
      setError(`${detail}${uncertain ? " Check Failure reports before retrying; the API may have saved this report." : " No report was accepted."}`);
    } finally {
      if (request.current === controller) { request.current = null; if (mounted.current) setSaving(false); }
    }
  }

  return <section className="failure-report-form" aria-labelledby={`${fieldPrefix}-heading`}>
    <h3 id={`${fieldPrefix}-heading`}>Save a failure report</h3>
    <p>Record positive evidence from this run using its exact recipe and measured severity. Reproduction starts as Unknown. This does not launch another simulation.</p>
    {eligible.length === 0 ? <p className="notice">This result has no scored positive failure evidence eligible for a report. Unknown observations are not evidence that a failure occurred.</p> : saved ? <div className="notice" role="status"><strong>Failure report saved</strong><p><code>{saved.id}</code> · {failureKindLabel(saved.kind)} · Severity {observation(saved.severity)} · Reproduction Unknown</p><a href="#failures">Open Failure reports</a></div> : <form onSubmit={event => { void submit(event); }}>
      <div className="field"><label htmlFor={`${fieldPrefix}-kind`}>Observed failure kind</label><select id={`${fieldPrefix}-kind`} value={kind} disabled={saving || !active} onChange={event => setKind(event.target.value as FailureKind)}>{eligible.map(value => <option key={value} value={value}>{failureKindLabel(value)}</option>)}</select></div>
      <div className="field"><label htmlFor={`${fieldPrefix}-summary`}>Failure summary</label><textarea id={`${fieldPrefix}-summary`} value={summary} disabled={saving || !active} rows={3} onChange={event => setSummary(event.target.value)} placeholder="Describe the observed behavior and its context." /></div>
      <p className="muted">Measured severity: {observation(result.metrics.severity)} / 100. {result.metrics.evaluation_settings ? "Near-miss thresholds use this run’s recorded settings." : "Recorded evaluation settings are unavailable; near-miss eligibility uses the API’s default thresholds."}</p>
      {error && <p className="notice error-notice" role="alert">{error}</p>}
      <button type="submit" className="button secondary small" disabled={saving || !active}>{saving ? "Saving failure report…" : "Save failure report"}</button>
    </form>}
  </section>;
}
