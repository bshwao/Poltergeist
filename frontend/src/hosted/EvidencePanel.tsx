import { useEffect, useRef, useState } from "react";
import type { EvidenceReply } from "../types/generated";
import type { createHostedClient } from "./client";
import { metric } from "./workflow";

export function EvidencePanel({ client, device, run, enabled }: {
  client: ReturnType<typeof createHostedClient>; device: string; run: string; enabled: boolean;
}) {
  const [page, setPage] = useState<EvidenceReply | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);
  useEffect(() => { if (!enabled) { active.current?.abort(); setPage(null); } }, [enabled]);
  async function load(next = false) {
    active.current?.abort();
    const controller = new AbortController(); active.current = controller;
    setBusy(true); setError("");
    try {
      const reply = await client.evidence(device, { simulation_id: run, resource: "telemetry",
        offset: next ? page?.next_offset ?? 0 : 0, version: next ? page?.version ?? null : null }, controller.signal);
      if (!controller.signal.aborted) setPage(reply);
    } catch (reason) {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Evidence could not be read.");
    } finally { if (active.current === controller) setBusy(false); }
  }
  const states = { missing: "The local evidence file is unavailable; measurements remain unknown.",
    deleted: "This evidence was explicitly removed on the computer. The saved summary is retained.",
    changed: "The evidence changed. Start review again to use a consistent version.",
    limit_exceeded: "This evidence exceeds the review limits. Use a local export." };
  return <section className="hosted-evidence-review" aria-label="Telemetry evidence">
    <p>Read recorded frames on request. Only one page is kept in this view.</p>
    <div className="hosted-button-row"><button disabled={!enabled || busy} onClick={() => void load()}>Read telemetry</button>
      {page?.state === "available" && page.next_offset !== null && <button disabled={!enabled || busy} onClick={() => void load(true)}>Next frames</button>}
      {busy && <button onClick={() => { active.current?.abort(); setBusy(false); }}>Cancel transfer</button>}</div>
    {!enabled && <p>Connect an updated companion and approve evidence access on that computer to review frames.</p>}
    {error && <p role="alert">{error}</p>}
    {page && page.state !== "available" && <p role="status">{states[page.state]}</p>}
    {page?.state === "available" && <div className="hosted-table-scroll" tabIndex={0} role="region" aria-label="Recorded telemetry table"><table><caption>Recorded telemetry · {page.frames.length} frames</caption>
      <thead><tr><th>Frame</th><th>Time (s)</th><th>Speed (m/s)</th><th>Brake</th><th>Collisions</th><th>Violations</th></tr></thead>
      <tbody>{page.frames.map(row => <tr key={row.frame}><td>{row.frame}</td><td>{row.time_s.toFixed(3)}</td><td>{metric(row.speed_mps)}</td>
        <td>{metric(row.control.brake)}</td><td>{metric(row.collisions)}</td><td>{metric(row.traffic_violations)}</td></tr>)}</tbody></table></div>}
  </section>;
}
