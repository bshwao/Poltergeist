import { useEffect, useId, useRef, useState } from "react";
import { api, ApiError } from "../services/api";
import type { RawCaptureManifest } from "../services/api";
import type { SimulationResult } from "../types/generated";
import { decodeBgra, inspectLidar, measurement, verifySample } from "./visual-data";

export type LoadedSample = { index: number } & (
  { kind: "camera"; width: number; height: number; rgba: Uint8ClampedArray }
  | { kind: "lidar"; statistics: ReturnType<typeof inspectLidar> }
);

export function CameraFrame({ frame }: { frame: Extract<LoadedSample, { kind: "camera" }> }) {
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    setError(false);
    try {
      const context = canvas.current?.getContext("2d");
      if (!context) { setError(true); return; }
      const image = context.createImageData(frame.width, frame.height);
      image.data.set(frame.rgba); context.putImageData(image, 0, 0);
    } catch { setError(true); }
  }, [frame]);
  return <>{error && <p role="alert">This browser could not display the decoded camera pixels. The raw sample is available to download.</p>}
    <canvas ref={canvas} className="capture-canvas" width={frame.width} height={frame.height} role="img" aria-label={`Recorded camera pixels, ${frame.width} by ${frame.height}`} />
    <p className="muted">Raw BGRA8 camera sample decoded to RGBA pixels. This is one recorded frame, not encoded video.</p>
  </>;
}

function CaptureInspector({ simulationId, artifactIndex, active }: { simulationId: string; artifactIndex: number; active: boolean }) {
  const prefix = useId();
  const [manifest, setManifest] = useState<RawCaptureManifest | null>(null);
  const [sampleIndex, setSampleIndex] = useState(0);
  const [loaded, setLoaded] = useState<LoadedSample | null>(null);
  const [loading, setLoading] = useState<"manifest" | "sample" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const request = useRef<AbortController | null>(null);
  const sample = manifest?.samples[sampleIndex];

  useEffect(() => {
    if (!active) { request.current?.abort(); setLoading(null); }
    return () => request.current?.abort();
  }, [active]);

  async function loadManifest() {
    request.current?.abort();
    const current = new AbortController(); request.current = current;
    setLoading("manifest"); setError(null); setLoaded(null);
    try {
      const data = await api.getCaptureManifest(simulationId, artifactIndex, { signal: current.signal });
      if (current.signal.aborted) return;
      setManifest(data); setSampleIndex(0);
    } catch (cause) {
      if (current.signal.aborted) return;
      setError(cause instanceof ApiError ? cause.message : "Could not load the raw capture manifest.");
    } finally { if (!current.signal.aborted) setLoading(null); }
  }

  function chooseSample(index: number) {
    if (!manifest || !Number.isInteger(index) || index < 0 || index >= manifest.samples.length) return;
    request.current?.abort(); setLoading(null); setSampleIndex(index); setLoaded(null); setError(null);
  }

  async function loadSample() {
    if (!sample) return;
    request.current?.abort();
    const current = new AbortController(); request.current = current;
    setLoading("sample"); setError(null); setLoaded(null);
    try {
      const data = await api.getCaptureSample(simulationId, artifactIndex, sampleIndex, { signal: current.signal });
      if (current.signal.aborted) return;
      await verifySample(data, sample.byte_length, sample.sha256);
      if (current.signal.aborted) return;
      if (sample.metadata.encoding === "bgra8") {
        setLoaded({ kind: "camera", index: sampleIndex, width: sample.metadata.width, height: sample.metadata.height, rgba: decodeBgra(data, sample.metadata.width, sample.metadata.height) });
      } else {
        setLoaded({ kind: "lidar", index: sampleIndex, statistics: inspectLidar(data) });
      }
    } catch (cause) {
      if (current.signal.aborted) return;
      // Decoder errors are local fixed messages. API diagnostics remain sanitized.
      setError(cause instanceof ApiError ? cause.message : cause instanceof Error && /^(The raw sample|Sample integrity|The camera sample|The LiDAR sample)/.test(cause.message) ? cause.message : "Could not inspect the raw sensor sample.");
    } finally { if (!current.signal.aborted) setLoading(null); }
  }

  return <div className="capture-inspector" aria-busy={loading !== null}>
    <p className="muted">Inspect one raw sensor sample at a time. Loading a manifest reads capture metadata; sensor bytes are fetched only when you choose “Inspect sample”.</p>
    <button className="button secondary small" disabled={!active || loading !== null} onClick={loadManifest}>{manifest ? "Reload capture manifest" : "Load capture manifest"}</button>
    {loading === "manifest" && <p role="status">Loading raw capture manifest…</p>}
    {error && <p role="alert">{error}</p>}
    {manifest && <>
      <p>{manifest.sample_count} raw samples · {manifest.total_bytes.toLocaleString()} bytes · raw-sensor-v1</p>
      {manifest.sample_count === 0 && <p>The recording contains no sensor samples.</p>}
      {sample && <div className="capture-sample">
        <div className="capture-controls"><button className="button secondary small" disabled={!active || sampleIndex === 0} onClick={() => chooseSample(sampleIndex - 1)}>Previous sample</button>
          <div className="field"><label htmlFor={`${prefix}-sample`}>Sample number</label><input id={`${prefix}-sample`} type="number" min="1" max={manifest.sample_count} step="1" value={sampleIndex + 1} disabled={!active} onChange={event => chooseSample(Number(event.target.value) - 1)} /></div>
          <button className="button secondary small" disabled={!active || sampleIndex + 1 >= manifest.sample_count} onClick={() => chooseSample(sampleIndex + 1)}>Next sample</button></div>
        <dl className="frame-values"><div><dt>Sensor</dt><dd>{sample.kind === "camera" ? "Camera" : "LiDAR"}{typeof sample.metadata.agent_sensor_id === "string" && ` · ${sample.metadata.agent_sensor_id}`}</dd></div><div><dt>Frame / timestamp</dt><dd>{sample.frame} / {measurement(sample.timestamp, 3)} s</dd></div><div><dt>Raw encoding / size</dt><dd>{sample.metadata.encoding} / {sample.byte_length.toLocaleString()} bytes</dd></div></dl>
        <div className="capture-controls"><button className="button primary small" disabled={!active || loading !== null} onClick={loadSample}>Inspect sample</button><a className="button secondary small" href={api.sampleUrl(simulationId, artifactIndex, sampleIndex)} download>Download raw sample</a></div>
        {loading === "sample" && <p role="status">Loading and verifying sample…</p>}
        {loaded?.index === sampleIndex && <div className="verified-sample"><p className="badge badge-success">SHA-256 and byte length verified</p>
          {loaded.kind === "camera" ? <CameraFrame frame={loaded} /> : <>
            <h3>LiDAR sample inspection</h3><p>{loaded.statistics.points} XYZI points · {loaded.statistics.finitePoints} finite points · {loaded.statistics.points - loaded.statistics.finitePoints} nonfinite points excluded from bounds</p>
            <p className="muted">Float32 values are decoded as little endian. Bounds are in the sensor coordinate frame; no world registration or vehicle replay is inferred.</p>
            {loaded.statistics.bounds ? <table className="coverage-table"><caption>Finite point bounds (m)</caption><thead><tr><th>Axis</th><th>Minimum</th><th>Maximum</th></tr></thead><tbody>{loaded.statistics.bounds.map((bounds, index) => <tr key={index}><th scope="row">{["X", "Y", "Z"][index]}</th><td>{measurement(bounds.min, 3)}</td><td>{measurement(bounds.max, 3)}</td></tr>)}</tbody></table> : <p>No finite XYZ bounds are available.</p>}
          </>}
        </div>}
      </div>}
    </>}
  </div>;
}

export function SimulationViewer({ result, active = true }: { result: SimulationResult; active?: boolean }) {
  const prefix = useId();
  const recordings = result.artifacts.flatMap((artifact, index) => artifact.kind === "recording" ? [{ artifact, index }] : []);
  const [selection, setSelection] = useState<{ id: string; index: number }>({ id: result.simulation.id, index: recordings[0]?.index ?? -1 });
  const selected = selection.id === result.simulation.id && recordings.some(item => item.index === selection.index) ? selection.index : recordings[0]?.index ?? -1;
  return <section className="panel simulation-viewer" aria-label="Run artifacts and raw capture">
    <div className="panel-header"><div><p className="eyebrow">RECORDED OUTPUT</p><h2>Artifacts &amp; sensor frames</h2><p>Downloads use this run’s indexed artifact references.</p></div></div>
    <div className="form-section">
      {result.agent_inputs?.source === "deterministic_fixture" && <p className="notice">Recorded sensor inputs use deterministic fixtures. These are synthetic test data, not rendered CARLA frames.</p>}
      {result.artifacts.length ? <ul className="artifact-list">{result.artifacts.map((artifact, index) => <li key={index}>
        <span>{artifact.kind === "recording" ? "Raw capture manifest" : artifact.kind === "telemetry" ? "Telemetry trace" : artifact.kind === "decisions" ? "Decision timeline" : "Report"} <small className="muted">{artifact.media_type}</small></span>
        <a className="button secondary small" href={api.artifactUrl(result.simulation.id, index)} download>Download {artifact.kind === "recording" ? "manifest" : artifact.kind} {index + 1}</a>
      </li>)}</ul> : <p>No artifacts were recorded for this run.</p>}
      {selected >= 0 ? <>
        {recordings.length > 1 && <div className="field"><label htmlFor={`${prefix}-capture`}>Raw recording</label><select id={`${prefix}-capture`} value={selected} onChange={event => setSelection({ id: result.simulation.id, index: Number(event.target.value) })}>{recordings.map(({ index }) => <option key={index} value={index}>Recording artifact {index + 1}</option>)}</select></div>}
        <CaptureInspector key={`${result.simulation.id}:${selected}`} simulationId={result.simulation.id} artifactIndex={selected} active={active} />
      </> : <p className="muted">{result.simulation.mode === "test" ? "Synthetic test runs do not produce CARLA camera or LiDAR captures." : "No raw camera or LiDAR recording is available. Capture must have been enabled and sensor samples successfully recorded."}</p>}
    </div>
  </section>;
}
