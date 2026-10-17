import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../services/api";
import type { AnalyticsSummary } from "../types/generated";

export function AnalyticsPanel({ active }: { active: boolean }) {
  const [summary, setSummary] = useState<AnalyticsSummary | null>(null);
  const [at, setAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const request = useRef<AbortController | null>(null);
  const visible = useRef(active);
  visible.current = active;
  useEffect(() => {
    if (!active) { request.current?.abort(); request.current = null; setLoading(false); }
    return () => { request.current?.abort(); request.current = null; };
  }, [active]);
  async function load() {
    if (!active || request.current) return;
    const controller = new AbortController();
    request.current = controller;
    const deadline = setTimeout(() => controller.abort(), 12_000);
    setLoading(true);
    setError(null);
    try {
      const result = await api.getAnalytics(undefined, { signal: controller.signal });
      if (request.current !== controller || !visible.current || controller.signal.aborted) return;
      setSummary(result);
      setAt(new Date().toLocaleString());
    } catch (failure) {
      if (request.current === controller && visible.current) setError(controller.signal.aborted ? "Analytics request timed out. Retry when the API is available." : failure instanceof ApiError ? failure.message : "Could not load analytics.");
    } finally {
      clearTimeout(deadline);
      if (request.current === controller) { request.current = null; setLoading(false); }
    }
  }
  return <section className="panel analytics-panel" aria-labelledby="analytics-heading">
    <div className="panel-header"><div><p className="eyebrow">RECORDED HISTORY</p><h2 id="analytics-heading">Results by execution mode</h2></div><button className="button secondary small" type="button" disabled={loading} onClick={() => void load()}>{loading ? "Loading analytics…" : summary ? "Refresh analytics" : "Load analytics"}</button></div>
    <div className="detail-content">
      <p className="field-hint">{at ? `Snapshot from ${at}.` : "Load persisted run counts on demand."} CARLA and synthetic test observations are reported separately. These totals cover all history, including runs outside the current history page.</p>
      {error && <p className="notice error-notice" role="alert">{error}{summary ? " Previous snapshot shown below." : ""}</p>}
      {summary && <div className="analytics-grid">{summary.groups.map(group => <article className="analytics-mode" key={group.mode}>
        <h3>{group.mode === "carla" ? "CARLA" : "Synthetic test"}</h3>
        <dl className="detail-facts"><div><dt>Runs</dt><dd>{group.total_runs}</dd></div><div><dt>Final results</dt><dd>{group.result_count}</dd></div><div><dt>Scored results</dt><dd>{group.scored_runs}</dd></div><div><dt>Unscored results</dt><dd>{group.unscored_results}</dd></div><div><dt>Mean severity</dt><dd>{group.mean_severity === null ? "Unknown" : `${group.mean_severity.toFixed(1)} / 100`}</dd></div><div><dt>Failure reports</dt><dd>{group.failure_reports}</dd></div><div><dt>Runs with collisions</dt><dd>{group.collision_runs} / {group.collision_observed_runs} observed</dd></div><div><dt>Runs with traffic violations</dt><dd>{group.traffic_violation_runs} / {group.traffic_observed_runs} observed</dd></div></dl>
        <ul className="analytics-counts">{Object.entries(group.status_counts).map(([status, count]) => <li key={status}><span>{status}</span><strong>{count}</strong></li>)}</ul>
        {Object.keys(group.failure_kinds).length > 0 && <p className="field-hint">Report kinds: {Object.entries(group.failure_kinds).map(([kind, count]) => `${kind.replace(/_/g, " ")}: ${count}`).join(" · ")}</p>}
      </article>)}</div>}
      {summary?.groups.length === 0 && <p>No recorded history is available.</p>}
      {summary && <p className="field-hint">Mean severity includes all scored terminal results under their recorded settings. Partial positive observations can be scored; observed counts alone do not establish complete coverage. Review individual results before comparing safety.</p>}
    </div>
  </section>;
}
