import type { SimulationResult } from "../types/generated";
import { measurement } from "./visual-data";

export function SafetyMetrics({ result }: { result: SimulationResult }) {
  const { metrics, simulation } = result;
  const coverage = metrics.coverage;
  const cards = [
    ["Severity", metrics.severity, "/ 100", 1],
    ["Collisions", metrics.collision_count, "events", 0],
    ["Minimum TTC", metrics.min_ttc_s, "s", 2],
    ["Minimum clearance", metrics.min_clearance_m, "m", 2],
    ["Maximum deceleration", metrics.max_deceleration_mps2, "m/s²", 2],
    ["Hard braking", metrics.hard_braking_count, "episodes", 0],
    ["Traffic violations", metrics.traffic_violation_count, "events", 0],
    ["Maximum stopping time", metrics.max_stopping_time_s, "s", 2],
    ["Maximum stopping distance", metrics.max_stopping_distance_m, "m", 2],
  ] as const;
  return <section className="panel safety-metrics" aria-label="Safety evaluation">
    <div className="panel-header"><div><p className="eyebrow">RECORDED EVALUATION</p><h2>Safety metrics</h2>
      <p>{simulation.mode === "test" ? "Synthetic test" : "CARLA"} · {simulation.status} · {measurement(result.measured_duration_s, 2)} s measured · {result.telemetry_frames} telemetry frames</p></div>
      <span className={`badge ${coverage.has_gaps || simulation.status !== "completed" ? "badge-warning" : "badge-neutral"}`}>
        {coverage.has_gaps || simulation.status !== "completed" ? "Partial run or observation gaps" : "Completed run"}
      </span></div>
    <div className="safety-grid">{cards.map(([label, value, unit, decimals]) => <div className="safety-card" key={label}>
      <span>{label}</span><strong>{measurement(value, decimals)}</strong><small>{value === null ? "Unavailable / no relevant observation" : unit}</small>
    </div>)}</div>
    <div className="form-section">
      <p className="muted">Unknown values are unavailable or have no relevant interaction. A zero is an observed result. Counts and scores describe the recorded trace; inspect coverage before comparing runs.</p>
      <details><summary>Observation coverage</summary>
        <table className="coverage-table"><caption>Frames with observations out of {coverage.total_frames} recorded evaluation frames</caption>
          <thead><tr><th scope="col">Measurement</th><th scope="col">Observed frames</th><th scope="col">Evaluated pairs</th></tr></thead>
          <tbody>{[
            ["Collision", coverage.collision_frames, null], ["Time to collision", coverage.ttc_frames, coverage.ttc_pairs],
            ["Clearance", coverage.clearance_frames, coverage.clearance_pairs], ["Braking", coverage.braking_frames, null],
            ["Traffic rules", coverage.traffic_rule_frames, null],
          ].map(([label, frames, pairs]) => <tr key={String(label)}><th scope="row">{label}</th><td>{frames} / {coverage.total_frames}</td><td>{pairs === null ? "Not applicable" : pairs}</td></tr>)}</tbody>
        </table>
        <p className="muted">{coverage.actor_pairs} actor-pair observations. {coverage.has_gaps ? "The trace contains observation gaps." : "No timing gaps were reported."} Frame coverage alone does not establish that every actor pair was measurable.</p>
      </details>
      <details><summary>Scoring settings and runtime</summary>
        <p>Scoring method: {metrics.evaluation_settings?.method_version ?? "Unknown"}. Adapter: {result.runtime?.adapter ?? "Unknown"}.</p>
        {result.runtime && <p className="muted">Map {result.runtime.map_name} · seed {result.runtime.seed} · step {measurement(result.runtime.fixed_delta_seconds, 3)} s · client {result.runtime.client_version ?? "Unknown"} · server {result.runtime.server_version ?? "Unknown"}</p>}
        {metrics.evaluation_settings ? <pre className="result-json">{JSON.stringify(metrics.evaluation_settings, null, 2)}</pre> : <p className="muted">Evaluation settings were not recorded.</p>}
      </details>
    </div>
  </section>;
}
