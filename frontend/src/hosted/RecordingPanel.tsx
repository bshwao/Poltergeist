import { useEffect, useRef, useState } from "react";
import type { EvidenceRecording, EvidenceReply, EvidenceSample } from "../types/generated";
import { decodeBgra, inspectLidar } from "../components/visual-data";
import { MAX_SAMPLE_VIEW_BYTES, type createHostedClient } from "./client";

type Client = ReturnType<typeof createHostedClient>;
const messages = {
  missing: "The recording is unavailable on the computer.",
  deleted: "This recording was explicitly removed on the computer. Its saved summary remains.",
  changed: "The recording changed. Reload its sample list before continuing.",
  limit_exceeded: "This recording exceeds the review limits. Use a local evidence export.",
};

function SamplePreview({ sample, bytes }: { sample: EvidenceSample; bytes: ArrayBuffer }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [error, setError] = useState("");
  const lidar = sample.kind === "lidar" ? inspectLidar(bytes) : null;
  useEffect(() => {
    setError("");
    try {
      const context = canvas.current?.getContext("2d");
      if (!context) throw new Error();
      if (sample.metadata.encoding === "bgra8") {
        const pixels = context.createImageData(sample.metadata.width, sample.metadata.height);
        pixels.data.set(decodeBgra(bytes, sample.metadata.width, sample.metadata.height));
        context.putImageData(pixels, 0, 0);
      } else {
        const width = 480, height = 300;
        context.fillStyle = "#10251e"; context.fillRect(0, 0, width, height);
        const summary = inspectLidar(bytes);
        if (summary.bounds) {
          const [x, y] = summary.bounds;
          const data = new DataView(bytes);
          const stride = Math.max(1, Math.ceil(summary.points / 30000));
          context.fillStyle = "#a8ddb1";
          for (let i = 0; i < summary.points; i += stride) {
            const point = [0, 4, 8, 12].map(k => data.getFloat32(i * 16 + k, true));
            if (!point.every(Number.isFinite)) continue;
            context.fillRect(16 + (point[0] - x.min) / (x.max - x.min || 1) * (width - 32),
              height - 16 - (point[1] - y.min) / (y.max - y.min || 1) * (height - 32), 2, 2);
          }
        }
      }
    } catch { setError("The verified sample could not be rendered in this browser."); }
  }, [sample, bytes]);
  return <figure className="hosted-sample-preview">
    {error && <p role="alert">{error}</p>}
    <canvas ref={canvas} width={sample.metadata.encoding === "bgra8" ? sample.metadata.width : 480}
      height={sample.metadata.encoding === "bgra8" ? sample.metadata.height : 300} role="img"
      aria-label={`Recorded ${sample.kind === "camera" ? "camera pixels" : "LiDAR points"} at frame ${sample.frame}`} />
    <figcaption>Recorded frame {sample.frame} · timestamp {sample.timestamp.toFixed(3)} s · SHA-256 verified</figcaption>
    {lidar && <p>{lidar.points.toLocaleString()} recorded points · {lidar.finitePoints.toLocaleString()} finite points.
      {lidar.bounds ? " Top view in sensor coordinates; at most 30,000 points drawn." : " No finite point bounds are available."}</p>}
  </figure>;
}

export function RecordingPanel({ client, device, run, enabled, synthetic }: {
  client: Client; device: string; run: string; enabled: boolean; synthetic: boolean;
}) {
  const [recordings, setRecordings] = useState<EvidenceRecording[] | null>(null);
  const [recordingIndex, setRecordingIndex] = useState<number | "">("");
  const [page, setPage] = useState<EvidenceReply | null>(null);
  const [sampleIndex, setSampleIndex] = useState<number | "">("");
  const [loaded, setLoaded] = useState<ArrayBuffer | null>(null);
  const [busy, setBusy] = useState<"recordings" | "samples" | "sample" | null>(null);
  const [error, setError] = useState("");
  const [received, setReceived] = useState(0);
  const active = useRef<AbortController | null>(null);
  const samples = page?.samples ?? [];
  const selected = samples.find(item => item.sample_index === sampleIndex);
  const recording = recordings?.find(item => item.artifact_index === recordingIndex);
  useEffect(() => () => active.current?.abort(), []);
  useEffect(() => {
    if (!enabled) { active.current?.abort(); setBusy(null); setLoaded(null); setRecordings(null); setPage(null); }
  }, [enabled]);

  async function action(kind: "recordings" | "samples" | "sample", operation: (signal: AbortSignal) => Promise<void>) {
    active.current?.abort();
    const controller = new AbortController(); active.current = controller;
    setBusy(kind); setError(""); setLoaded(null); setReceived(0);
    try { await operation(controller.signal); }
    catch (reason) { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "The recording could not be read."); }
    finally { if (active.current === controller) setBusy(null); }
  }
  async function discover() {
    await action("recordings", async signal => {
      setPage(null); setRecordings(null);
      const reply = await client.evidence(device, { simulation_id: run, resource: "recordings" }, signal);
      if (signal.aborted) return;
      if (reply.state !== "available") throw new Error(messages[reply.state]);
      const items = reply.recordings ?? [];
      setRecordings(items); setRecordingIndex(items.find(item => item.state === "available")?.artifact_index ?? "");
    });
  }
  async function list(next = false) {
    if (recordingIndex === "") return;
    await action("samples", async signal => {
      const reply = await client.evidence(device, { simulation_id: run, resource: "samples", artifact_index: recordingIndex,
        offset: next ? page?.next_offset ?? 0 : 0, version: next ? page?.version ?? null : null,
        sample_index: next ? (samples[samples.length - 1]?.sample_index ?? -1) + 1 : 0 }, signal);
      if (signal.aborted) return;
      setPage(reply); setSampleIndex(reply.samples?.[0]?.sample_index ?? "");
    });
  }
  async function inspect() {
    if (!selected || recordingIndex === "") return;
    await action("sample", async signal => {
      const bytes = await client.sample(device, run, recordingIndex, selected, signal, count => { if (!signal.aborted) setReceived(count); });
      if (!signal.aborted) setLoaded(bytes);
    });
  }
  return <section className="hosted-evidence-review" aria-label="Recording evidence" aria-busy={busy !== null}>
    <h2>Sensor recordings</h2>
    <p>{synthetic ? "Synthetic sensor fixtures" : "Saved camera and LiDAR samples"} stay on the computer until requested. One sample is kept in this view, up to 4 MiB.</p>
    <div className="hosted-button-row"><button disabled={!enabled || busy !== null} onClick={() => void discover()}>Read recordings</button>
      {busy && <button onClick={() => { active.current?.abort(); setBusy(null); setLoaded(null); }}>Cancel recording transfer</button>}</div>
    {!enabled && <p>Connect a companion that supports recording review and approve local evidence access.</p>}
    {error && <p role="alert">{error}</p>}
    {recordings?.length === 0 && <p>No sensor recording was saved for this run.</p>}
    {recordings && recordings.length > 0 && <>
      <label>Recording<select value={recordingIndex} disabled={!enabled || busy !== null} onChange={event => {
        active.current?.abort(); setRecordingIndex(Number(event.target.value)); setPage(null); setSampleIndex(""); setLoaded(null); setError("");
      }}><option value="" disabled>Select a recording</option>{recordings.map((value, index) => <option key={value.artifact_index} value={value.artifact_index}>
        Recording {index + 1} · {value.state === "available" ? `${value.sample_count?.toLocaleString()} saved samples` : value.state.replace(/_/g, " ")}
      </option>)}</select></label>
      {recording?.state !== "available" && recording && <p role="status">{messages[recording.state]}</p>}
      <div className="hosted-button-row"><button disabled={!enabled || busy !== null || recording?.state !== "available"} onClick={() => void list()}>Read sample list</button>
        {page?.state === "available" && page.next_offset !== null && <button disabled={!enabled || busy !== null} onClick={() => void list(true)}>Next saved samples</button>}</div>
    </>}
    {page && page.state !== "available" && <p role="status">{messages[page.state]}</p>}
    {page?.state === "available" && samples.length === 0 && <p>This recording has no saved samples.</p>}
    {samples.length > 0 && <>
      <label>Saved sample<select disabled={!enabled || busy !== null} value={sampleIndex} onChange={event => { setSampleIndex(Number(event.target.value)); setLoaded(null); setError(""); }}>
        {samples.map(value => <option key={value.sample_index} value={value.sample_index}>{value.kind === "camera" ? "Camera" : "LiDAR"} · frame {value.frame} · {value.timestamp.toFixed(3)} s</option>)}
      </select></label>
      {selected && <p>Recorded frame {selected.frame} · {selected.byte_length.toLocaleString()} bytes · {selected.metadata.encoding}</p>}
      <button disabled={!enabled || busy !== null || !selected || selected.byte_length > MAX_SAMPLE_VIEW_BYTES} onClick={() => void inspect()}>Inspect saved sample</button>
      {selected && selected.byte_length > MAX_SAMPLE_VIEW_BYTES && <p>This sample exceeds the browser preview limit. Use a local evidence export.</p>}
      {busy === "sample" && <progress aria-label="Recording sample transfer" value={received} max={selected?.byte_length || 1} />}
      {loaded !== null && selected && <SamplePreview key={`${recordingIndex}:${selected.sample_index}`} sample={selected} bytes={loaded} />}
    </>}
  </section>;
}
