import { useEffect, useMemo, useRef, useState } from "react";
import type { BrowserDeviceView } from "../types/generated";
import { GuidedLab } from "../pages/GuidedLab";
import { Discovery } from "../pages/Discovery";
import { SafetyMetrics } from "../components/SafetyMetrics";
import { ReplayHighlightsPanel } from "../components/ReplayHighlights";
import { useSimulation } from "../hooks/useSimulation";
import { useLabReport } from "../hooks/useLab";
import { createHostedClient, HostedError } from "./client";
import { createHostedLabClient } from "./lab-client";
import { EvidencePanel } from "./EvidencePanel";
import { RecordingPanel } from "./RecordingPanel";

export type HostedLabRoute = { section: "legacy" | "guide" | "discovery" | "run"; planId?: string | null; runId?: string; frame?: number; suiteRunId?: string };
export function hostedLabRoute(hash: string): HostedLabRoute {
  if (hash === "#lab") return { section: "guide" };
  const suite = /^#lab\/suite-run\/([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$/i.exec(hash);
  if (suite) return { section: "guide", suiteRunId: suite[1] };
  const plan = /^#discovery(?:\/([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}))?$/i.exec(hash);
  if (plan) return { section: "discovery", planId: plan[1] ?? null };
  const run = /^#lab-run=([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})(?:&frame=(\d+))?$/i.exec(hash);
  if (run && (run[2] === undefined || Number.isSafeInteger(Number(run[2])))) return { section: "run", runId: run[1], frame: run[2] === undefined ? undefined : Number(run[2]) };
  return { section: "legacy" };
}
const runHref = (id: string, frame?: number) => `#lab-run=${id}${frame === undefined ? "" : `&frame=${frame}`}`;

function HostedLabRun({ adapter, client, device, run, frame, active }: { adapter: ReturnType<typeof createHostedLabClient>; client: ReturnType<typeof createHostedClient>; device: string; run: string; frame?: number; active: boolean }) {
  const state = useSimulation(run, active, adapter);
  const [selectedFrame, setSelectedFrame] = useState<number | null>(frame ?? null);
  useEffect(() => { setSelectedFrame(frame ?? null); }, [run, frame]);
  const detail = useLabReport(selectedFrame === null ? null : `${run}/${selectedFrame}`, active,
    (_, signal) => adapter.getFrame(run, selectedFrame!, { signal }), () => true);
  const record = state.record, result = record?.result;
  return <div className="lab-surface lab-stack"><header className="page-heading"><div><p className="eyebrow">APPROVED LOCAL EVIDENCE</p><h1>{record?.simulation.scenario.name ?? "Recorded experiment"}</h1><p>{run}</p></div><a href="#lab" className="button secondary">Return to guided lab</a></header>
    {state.loading && <p role="status">Loading experiment evidence…</p>}{state.error && <p role="alert">{state.error} <button onClick={state.refresh}>Retry run</button></p>}
    {record && <section className="panel"><div className="panel-body"><h2>{state.progress?.status ?? record.simulation.status} · {record.simulation.mode === "test" ? "Synthetic check" : "CARLA"}</h2><p>The recorded recipe and execution settings belong to this computer's approved experiment.</p><details><summary>Frozen recipe and settings</summary><pre>{JSON.stringify({ scenario: record.simulation.scenario, options: record.job?.options, evaluation_settings: record.job?.evaluation_settings }, null, 2)}</pre></details></div></section>}
    {result && <><SafetyMetrics result={result} /><ReplayHighlightsPanel simulationId={run} active={active} client={adapter} inspect={value => { setSelectedFrame(value); window.location.hash = runHref(run, value); }} />
      {selectedFrame !== null && <section className="panel" aria-label="Selected recorded frame"><div className="panel-header"><h2>Recorded frame {selectedFrame}</h2><button className="button secondary small" onClick={detail.refresh}>Refresh selected frame</button></div><div className="panel-body">{detail.loading && <p role="status">Reading the exact recorded frame…</p>}{detail.error && <p role="alert">{detail.error}</p>}{detail.value && <dl className="lab-frame-facts">{Object.entries({ "Frame": detail.value.frame, "Time (s)": detail.value.time_s, "Speed (m/s)": detail.value.speed_mps,
        "Throttle": detail.value.control.throttle, "Brake": detail.value.control.brake, "Steer": detail.value.control.steer, "Contacts": detail.value.collisions, "Traffic violations": detail.value.traffic_violations }).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value ?? "Not observed"}</dd></div>)}</dl>}</div></section>}
      <EvidencePanel client={client} device={device} run={run} enabled={active} /><RecordingPanel client={client} device={device} run={run} enabled={active} synthetic={record.simulation.mode === "test"} />
    </>}
  </div>;
}

export function HostedLabWorkspace({ client, target, contextKey, enabled, active, route, authFailed }: {
  client: ReturnType<typeof createHostedClient>; target: BrowserDeviceView; contextKey: string; enabled: boolean; active: boolean; route: HostedLabRoute; authFailed: (error: HostedError) => void;
}) {
  const live = useRef(enabled); live.current = enabled;
  const failure = useRef(authFailed); failure.current = authFailed;
  const adapter = useMemo(() => createHostedLabClient(client, target, () => live.current, error => failure.current(error)), [client, contextKey]);
  useEffect(() => () => adapter.dispose(), [adapter]);
  const [visited, setVisited] = useState<Set<string>>(() => new Set([route.section]));
  useEffect(() => { setVisited(previous => new Set([...previous, route.section])); }, [route.section]);
  const ready = target.connection.lab_supported === true && target.device.policy.allow_evidence_read === true && target.device.policy.scopes.includes("result:read");
  const modes = (["test", "carla"] as const).filter(mode => target.device.policy.scopes.includes(`run:execute:${mode}`));
  return <div className="hosted-guided-workspace">{!ready ? <section className="hosted-state-note" role="status"><h1>Prepare this computer for guided experiments</h1><p>Update and connect its companion, and approve result reading, experiment execution and evidence access in its local connection settings.</p><a href="#connect">Review computer connection</a></section> : <>
    <div className="hosted-state-note"><strong>{target.device.display_name} · Built-in baseline</strong><p>Paired-computer suites and discovery plans support up to 12 executions, within this computer's approved per-run limits. Registered custom agents are available in the local workspace.</p>{!enabled && <p role="status">Computer availability is unconfirmed. Connect it to continue reading or submitting work.</p>}</div>
    <div hidden={route.section !== "guide"}>{visited.has("guide") && <GuidedLab client={adapter} active={active && route.section === "guide" && enabled} contextKey={contextKey} runHref={runHref} modes={modes} suiteRunId={route.suiteRunId ?? null} />}</div>
    <div hidden={route.section !== "discovery"}>{visited.has("discovery") && <Discovery client={adapter} active={active && route.section === "discovery" && enabled} planId={route.planId ?? null} runHref={runHref} navigatePlan={id => { window.location.hash = `discovery/${id}`; }} modes={modes} maxExecutions={12} />}</div>
    {route.section === "run" && route.runId && <HostedLabRun key={route.runId} adapter={adapter} client={client} device={target.device.device_id} run={route.runId} frame={route.frame} active={active && enabled} />}
  </>}</div>;
}
