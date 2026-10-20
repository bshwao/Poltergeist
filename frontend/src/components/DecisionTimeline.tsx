import { useEffect, useId, useState } from "react";
import { api, ApiError } from "../services/api";
import type { ControlInput, DecisionPage, DecisionRecord, DecisionSensor, TelemetryFrame } from "../types/generated";
import { CameraFrame } from "./SimulationViewer";
import type { LoadedSample } from "./SimulationViewer";
import { decodeBgra, inspectLidar, verifySample } from "./visual-data";
import { locateFrame } from "./evidence-navigation";
import type { EvidenceFocus } from "./evidence-navigation";

const PAGE_SIZE = 10;
const number = (value: unknown, digits = 3) => typeof value === "number" ? value.toFixed(digits) : "Unavailable";
const speed = (frame?: TelemetryFrame) => frame?.ego.velocity_mps ? Math.hypot(...Object.values(frame.ego.velocity_mps)) : null;
const controlText = (control: ControlInput | null) => control
  ? `Throttle ${number(control.throttle)} · brake ${number(control.brake)} · steer ${number(control.steer)} · reverse ${control.reverse ? "on" : "off"} · hand brake ${control.hand_brake ? "on" : "off"}`
  : "No recorded control";

function InputSample({ simulationId, artifactIndex, sensor, active }: { simulationId: string; artifactIndex: number; sensor: DecisionSensor; active: boolean }) {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{ loading: boolean; error?: string; sample?: LoadedSample }>({ loading: false });
  useEffect(() => {
    if (!active || attempt === 0) return;
    const controller = new AbortController();
    let current = true;
    const deadline = setTimeout(() => controller.abort(), 12_000);
    setState({ loading: true });
    void (async () => {
      const bytes = await api.getCaptureSample(simulationId, artifactIndex, sensor.sample_index, { signal: controller.signal });
      await verifySample(bytes, sensor.byte_length, String(sensor.header.sha256));
      const sample: LoadedSample = sensor.header.encoding === "bgra8"
        ? { index: sensor.sample_index, kind: "camera", width: Number(sensor.header.width), height: Number(sensor.header.height),
          rgba: decodeBgra(bytes, Number(sensor.header.width), Number(sensor.header.height)) }
        : { index: sensor.sample_index, kind: "lidar", statistics: inspectLidar(bytes) };
      if (current && !controller.signal.aborted) setState({ loading: false, sample });
    })().catch(error => {
      if (current) setState({ loading: false, error: controller.signal.aborted ? "Input sample timed out. Retry when the API is available."
        : error instanceof ApiError ? error.message : "Could not verify or decode this recorded input." });
    }).finally(() => clearTimeout(deadline));
    return () => { current = false; controller.abort(); clearTimeout(deadline); };
  }, [active, attempt, simulationId, artifactIndex, sensor]);
  return <div className="decision-sensor">
    <h4>{String(sensor.header.sensor_id)} · {sensor.header.encoding === "bgra8" ? "RGB camera" : "LiDAR"}</h4>
    <p>Input frame {String(sensor.header.frame)} · episode time {number(sensor.header.timestamp_s)} s · {sensor.byte_length.toLocaleString()} bytes</p>
    <div className="capture-controls"><button className="button secondary small" disabled={!active || state.loading} onClick={() => setAttempt(value => value + 1)}>Inspect recorded input</button>
      <a className="button secondary small" href={api.sampleUrl(simulationId, artifactIndex, sensor.sample_index)} download>Download input bytes</a></div>
    {state.loading && <p role="status">Loading and verifying recorded input…</p>}
    {state.error && <p className="notice error-notice" role="alert">{state.error}</p>}
    {state.sample && <><p className="badge badge-success">Input SHA-256 and byte length verified</p>{state.sample.kind === "camera"
      ? <CameraFrame frame={state.sample} /> : <p>{state.sample.statistics.points} XYZI points · {state.sample.statistics.finitePoints} finite points. Coordinates are relative to the sensor.</p>}</>}
    <details><summary>Exact sensor header and raw reference</summary><pre>{JSON.stringify(sensor, null, 2)}</pre></details>
  </div>;
}

function DecisionDetail({ record, before, after, page, active }: { record: DecisionRecord; before?: TelemetryFrame; after?: TelemetryFrame; page: DecisionPage; active: boolean }) {
  const diagnostics = record.agent_result?.diagnostics;
  return <div className="decision-detail" aria-label={`Decision at frame ${record.frame}`}>
    <h3>Input {record.frame} → {record.status === "applied" ? `result ${record.resulting_frame}` : "unresolved action"}</h3>
    <p className="field-hint">Input time {number(record.time_s)} s. {record.control_mode === "authoritative" ? "The agent owns vehicle control." : "Scenario scripts may influence vehicle control."}</p>
    {record.status === "unresolved" && <p className="notice">This input was recorded, but the run ended before a complete decision and transition record was saved. No action or reasoning is inferred. Consult the run’s execution error and measured telemetry.</p>}
    <div className="decision-columns">
      <div><h4>Preceding input</h4><dl className="frame-values"><div><dt>Input source</dt><dd>{String(record.observation.sensor_source ?? (record.observation.scene as Record<string, unknown> | null)?.source ?? "Simulator state")}</dd></div>
        <div><dt>Ego speed</dt><dd>{number(speed(before))} m/s</dd></div><div><dt>Requested speed guidance</dt><dd>{number(record.observation.target_speed_mps)} m/s</dd></div>
        <div><dt>Actor inputs</dt><dd>{record.observation.actors === null ? "Unavailable / withheld" : Array.isArray(record.observation.actors) ? record.observation.actors.length : "Unavailable"}</dd></div></dl></div>
      <div><h4>Action and measured result</h4><dl className="frame-values"><div><dt>Agent proposal</dt><dd>{controlText(record.proposed_control)}</dd></div><div><dt>Applied control</dt><dd>{controlText(record.applied_control)}</dd></div>
        <div><dt>Script interventions</dt><dd>{record.interventions.length ? record.interventions.map(kind => kind.replace(/_/g, " ")).join(", ") : "None recorded"}</dd></div>
        <div><dt>Following measured speed</dt><dd>{number(speed(after))} m/s</dd></div><div><dt>Following collision / rule events</dt><dd>{after ? `${after.collisions === null ? "Unavailable" : after.collisions.length} / ${after.traffic_violations === null ? "Unavailable" : after.traffic_violations.length}` : "Unavailable"}</dd></div></dl></div>
    </div>
    <div className="decision-diagnostics"><h4>Agent-reported diagnostics</h4><p className="field-hint">These are statements supplied by the agent. They do not establish its internal reasoning or explain causality.</p>
      {diagnostics ? <><dl className="frame-values"><div><dt>Maneuver</dt><dd>{diagnostics.maneuver ?? "Not reported"}</dd></div><div><dt>Reason codes</dt><dd>{diagnostics.reason_codes.join(", ") || "Not reported"}</dd></div>
        <div><dt>Hazard actor IDs</dt><dd>{diagnostics.hazard_actor_ids.join(", ") || "Not reported"}</dd></div><div><dt>Reported target speed</dt><dd>{number(diagnostics.target_speed_mps)} m/s</dd></div></dl>
        <details><summary>Full diagnostics, trajectory and metadata</summary><pre>{JSON.stringify(diagnostics, null, 2)}</pre></details></> : <p>No diagnostics were reported for this input.</p>}
    </div>
    <dl className="frame-values"><div><dt>Worker inference</dt><dd>{number(record.worker_step_wall_time_s === null ? null : record.worker_step_wall_time_s * 1000)} ms</dd></div>
      <div><dt>Worker round trip</dt><dd>{number(record.round_trip_wall_time_s === null ? null : record.round_trip_wall_time_s * 1000)} ms</dd></div><div><dt>Recorded step wall time</dt><dd>{number(record.full_step_wall_time_s === null ? null : record.full_step_wall_time_s * 1000)} ms</dd></div></dl>
    {record.sensors.length > 0 && <div><h4>Exact sensor inputs for frame {record.frame}</h4>{record.observation.sensor_source === "deterministic_fixture" && <p className="notice">Deterministic fixture inputs are synthetic test data.</p>}
      {page.recording_artifact_index !== null && record.sensors.map(sensor => <InputSample key={String(sensor.header.sensor_id)} simulationId={page.simulation_id} artifactIndex={page.recording_artifact_index!} sensor={sensor} active={active} />)}</div>}
    <details><summary>Exact observation fields and input references</summary><p>Sensor bytes are stored separately. Combine these observation fields with each sensor’s header and downloaded bytes to reconstruct the delivered sensor bundle.</p><pre>{JSON.stringify({ observation: record.observation, sensors: record.sensors }, null, 2)}</pre></details>
    <details><summary>Synchronized measured telemetry</summary><p>The input precedes the applied action. The following frame measures the resulting state; observations alone do not prove why the agent acted.</p><pre>{JSON.stringify({ before: before ?? null, after: after ?? null }, null, 2)}</pre></details>
  </div>;
}

export function DecisionTimeline({ simulationId, active, focus }: { simulationId: string; active: boolean; focus?: EvidenceFocus }) {
  const prefix = useId();
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const [dismissedFocus, setDismissedFocus] = useState<number | null>(null);
  const target = focus && focus.token !== dismissedFocus ? focus : undefined;
  const [state, setState] = useState<{ key: string; data?: DecisionPage; error?: string; loading: boolean } | null>(null);
  const key = `${simulationId}/${offset}/${attempt}/${target?.token ?? ""}`;
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    let current = true;
    const deadline = setTimeout(() => controller.abort(), target ? 30_000 : 12_000);
    setState({ key, loading: true });
    void (async () => {
      let start = offset;
      if (target) {
        const index = await locateFrame(target.frame, async at => {
          const data = await api.getDecisions(simulationId, { offset: at, limit: 1 }, { signal: controller.signal });
          return { total: data.total, items: data.records };
        }, controller.signal);
        if (index === null) throw new ApiError(`No recorded decision input exists at frame ${target.frame}. Inspect its telemetry evidence instead.`);
        start = index;
      }
      if (controller.signal.aborted) return;
      const data = await api.getDecisions(simulationId, { offset: start, limit: PAGE_SIZE }, { signal: controller.signal });
      if (current && !controller.signal.aborted) { setSelected(0); setState({ key, loading: false, data }); }
    })().catch(error => {
      if (current) setState({ key, loading: false, error: controller.signal.aborted ? "Decision timeline timed out. Retry when the API is available."
        : error instanceof ApiError ? error.message : "Could not load decision evidence." });
    }).finally(() => clearTimeout(deadline));
    return () => { current = false; controller.abort(); clearTimeout(deadline); };
  }, [active, simulationId, offset, attempt, key, target?.frame, target?.token]);
  const visible = state?.key === key ? state : null;
  const page = visible?.data;
  const record = page?.records[selected];
  const move = (next: number) => { setDismissedFocus(focus?.token ?? null); setOffset(next); setSelected(0); };
  return <section className="panel decision-timeline" aria-labelledby={`${prefix}-title`}>
    <div className="panel-header"><div><p className="eyebrow">DECISION EVIDENCE</p><h2 id={`${prefix}-title`}>Inputs, decisions &amp; outcomes</h2><p>Review the input before an action alongside the following measured frame.</p></div>
      <button className="button secondary small" disabled={!active || visible?.loading} onClick={() => { setSelected(0); setAttempt(value => value + 1); }}>Refresh timeline</button></div>
    <div className="form-section">
      {(!visible || visible.loading) && <p role="status">Loading decision timeline…</p>}
      {visible?.error && <p className="notice error-notice" role="alert">{visible.error}</p>}
      {target && page && <p className="notice" role="status">Opened supporting decision input at native frame {target.frame}. The following frame is its measured result.</p>}
      {page && (!page.available ? <p className="notice">Decision evidence is unavailable for this run. Older results and failed input-storage transactions can lack a timeline. Agent inputs and diagnostics cannot be reconstructed retroactively from telemetry. Execute a new run to record a timeline.</p> : <>
        <p className={page.coverage === "partial" ? "notice" : "muted"}>Evidence coverage: <strong>{page.coverage}</strong> · {page.applied} recorded actions / {page.transitions} measured transitions · {page.total - page.applied} unresolved inputs.</p>
        <p className="field-hint">Coverage describes the saved decision chain. It does not measure safety or explain the agent’s internal reasoning.</p>
        {page.reset && <details><summary>Delivered episode reset, route and sensor calibration</summary><pre>{JSON.stringify(page.reset, null, 2)}</pre></details>}
        {page.total === 0 && <p>No decision inputs were recorded before this run ended.</p>}
        {page.records.length > 0 && <><div className="table-scroll"><table className="data-table"><caption className="sr-only">Decision inputs {page.offset + 1}–{page.offset + page.records.length}</caption><thead><tr><th>Input frame</th><th>Time</th><th>State</th><th>Agent maneuver</th><th>Interventions</th></tr></thead>
          <tbody>{page.records.map((item, index) => <tr key={item.frame} className={selected === index ? "selected-row" : undefined}><th scope="row"><button className="button secondary small" aria-pressed={selected === index} onClick={() => setSelected(index)}>Frame {item.frame}</button></th><td>{number(item.time_s)} s</td><td>{item.status}</td><td>{item.agent_result?.diagnostics?.maneuver ?? "Not reported"}</td><td>{item.interventions.length}</td></tr>)}</tbody></table></div>
          <div className="pagination"><button className="button secondary small" disabled={!active || page.offset === 0} onClick={() => move(Math.max(0, page.offset - PAGE_SIZE))}>Previous decisions</button><span>{page.offset + 1}–{page.offset + page.records.length} of {page.total} inputs</span><button className="button secondary small" disabled={!active || page.offset + page.records.length >= page.total} onClick={() => move(page.offset + PAGE_SIZE)}>Next decisions</button></div>
        </>}
        {record && <DecisionDetail key={`${simulationId}/${record.frame}`} record={record} before={page.telemetry[selected]} after={page.telemetry[selected + 1]} page={page} active={active} />}
      </>)}
    </div>
  </section>;
}
