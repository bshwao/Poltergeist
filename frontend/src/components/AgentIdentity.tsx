import type { AgentInputInfo, Simulation } from "../types/generated";

type Props = {
  simulation: Pick<Simulation, "agent" | "agent_snapshot">;
  detailed?: boolean;
  inputs?: AgentInputInfo | null;
};

export function AgentIdentity({ simulation, detailed = false, inputs }: Props) {
  const snapshot = simulation.agent_snapshot;
  const name = snapshot?.manifest.name ?? (simulation.agent === "baseline" ? "Built-in baseline" : "Custom agent");
  const summary = <div className="agent-identity">
    <strong>{name}</strong>
    {snapshot ? <><span className="agent-version">v{snapshot.manifest.version}</span>
      <span className="agent-mode">{snapshot.observation_mode === "sensors" ? "Camera / LiDAR inputs" : "Simulator-state inputs"}</span>
      <small title={snapshot.registration_id}>Registration {snapshot.registration_id.slice(0, 8)}</small></>
      : simulation.agent === "custom" && <small>Version and input mode not recorded</small>}
  </div>;
  if (!detailed) return summary;

  return <section className="recorded-agent" aria-label="Recorded agent">
    <h3>Recorded agent</h3>{summary}
    {snapshot ? <>
      <p className="field-hint">This selection was saved with the run. Later registry changes do not rewrite its identity.</p>
      <dl className="detail-facts">
        <div><dt>Registration ID</dt><dd><code>{snapshot.registration_id}</code></dd></div>
        <div><dt>SDK / controls</dt><dd>{snapshot.manifest.protocol_version} · {snapshot.agent_control_mode === "authoritative" ? "Agent controls the ego vehicle" : "Scripted ego interventions allowed"}</dd></div>
        <div><dt>Navigation</dt><dd>{snapshot.manifest.navigation_mode === "agent_planner" ? "Agent planner" : "Provided route"}</dd></div>
      </dl>
      <p className="muted">{snapshot.observation_mode === "sensors"
        ? "Sensor inputs include ideal simulated ego localization and any configured route assistance. The declared rig does not establish that perception is learned."
        : "Simulator-state inputs can include privileged information about surrounding actors. They do not represent camera-based perception."}</p>
      {inputs && <p className="agent-input-source"><strong>Recorded input source: </strong>{({
        carla_ground_truth: "CARLA ground-truth state", synthetic_state: "Synthetic state",
        carla_sensors: "CARLA camera / LiDAR", deterministic_fixture: "Deterministic sensor fixtures",
      } as const)[inputs.source]}{inputs.assistance.length > 0 && <span> · Assistance: {inputs.assistance.map(value => value.replace(/_/g, " ")).join(", ")}</span>}</p>}
      <details className="agent-provenance"><summary>Saved configuration and fingerprints</summary>
        <dl className="detail-facts"><div><dt>Agent package</dt><dd><code>{snapshot.package_path}</code></dd></div><div><dt>Worker Python</dt><dd><code>{snapshot.python_executable}</code></dd></div><div><dt>Package SHA-256</dt><dd><code>{snapshot.package_sha256}</code></dd></div></dl>
        <h4>Effective configuration</h4><pre>{JSON.stringify(snapshot.configuration, null, 2)}</pre>
        <h4>Process deadlines and limits</h4><pre>{JSON.stringify(snapshot.process_options, null, 2)}</pre>
        {snapshot.manifest.sensors.length > 0 && <><h4>Declared sensor rig</h4><pre>{JSON.stringify(snapshot.manifest.sensors, null, 2)}</pre></>}
        <p className="field-hint">{snapshot.files.length} package files · {snapshot.model_files.length} external model files · {snapshot.runtime_files.length} runtime source files. Fingerprints record inspected files; third-party environments and hardware are outside their scope.</p>
        <h4>Model file fingerprints</h4><pre>{JSON.stringify([...snapshot.files.filter(file => snapshot.manifest.assets.includes(file.path)), ...snapshot.model_files], null, 2)}</pre>
      </details>
    </> : <p className="field-hint">{simulation.agent === "baseline" ? "This run used the built-in baseline controller." : "This older or manually stored run has no registered-agent snapshot. Its current package or version cannot be inferred."}</p>}
  </section>;
}
