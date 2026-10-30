import { useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { Icon } from "../components/Icon";
import { SkipLink } from "../components/SkipLink";
import { readNumericField } from "../components/scenario-form";
import type { BrowserDeviceView, BrowserScenarioPreset, BrowserSession, PairingStatus, PermissionScope, RemoteCommandView, Scenario } from "../types/generated";
import { createHostedClient, HostedError } from "./client";
import type { HostedCommand } from "./client";
import { mergeRuns, metric, terminal } from "./workflow";
import type { HostedRun } from "./workflow";
import { EvidencePanel } from "./EvidencePanel";
import { RecordingPanel } from "./RecordingPanel";
import { HostedLabWorkspace, hostedLabRoute } from "./LabWorkspace";

type Client = ReturnType<typeof createHostedClient>;
type Pending = { device: string; command: HostedCommand; uncertain: boolean };
const short = (value: string) => value.slice(0, 8);
const when = (value: string | null) => value ? new Date(value).toLocaleString() : "Not yet connected";
const readable = (value: string) => value.replace(/_/g, " ");

function AuthForm({ loading, message, logoutUncertain, onLogin, onRetryLogout, onRetrySession }: {
  loading: boolean; message: string; logoutUncertain: boolean;
  onLogin: (username: string, password: string, register: boolean) => Promise<void>;
  onRetryLogout: () => void; onRetrySession: () => void;
}) {
  const [register, setRegister] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (submitting.current) return;
    submitting.current = true; setBusy(true);
    const secret = password; setPassword("");
    try { await onLogin(username, secret, register); }
    finally { submitting.current = false; setBusy(false); }
  }
  return <div className="hosted-auth">
    <SkipLink target="sign-in" />
    <section className="hosted-welcome" aria-label="About Poltergeist">
      <a className="hosted-brand" href="/app/"><Icon name="ghost" size={34} /> Poltergeist<span>EXPERIMENT LAB</span></a>
      <div className="hosted-welcome-copy"><p className="hosted-eyebrow">YOUR COMPUTER. YOUR EXPERIMENTS.</p>
        <h1>A clearer view<br />of every drive.</h1>
        <p>Design a scenario, connect your companion, and review measured results from your own computer.</p>
        <ol className="hosted-flow"><li><Icon name="scenarios" /> Choose a scenario</li><li><Icon name="simulations" /> Run locally</li><li><Icon name="activity" /> Review the evidence</li></ol>
      </div><p className="hosted-welcome-foot">Simulation and recordings stay on your computer. You choose which account can request work.</p>
    </section>
    <main className="hosted-auth-main" id="sign-in" tabIndex={-1}><div className="hosted-auth-card">
      <p className="hosted-eyebrow">CONNECTED EXPERIMENTS</p><h2>{logoutUncertain ? "Confirm sign out" : register ? "Create your account" : "Welcome back"}</h2>
      <p className="hosted-muted">{logoutUncertain ? "Your private workspace is hidden while sign out is unconfirmed." : "Sign in to connect a computer and manage your experiments."}</p>
      {message && <p className="hosted-alert" role="alert">{message}</p>}
      {logoutUncertain ? <button className="hosted-primary" onClick={onRetryLogout} disabled={busy}>Retry sign out</button>
        : loading ? <p role="status">Checking your session…</p> : <>
          <form onSubmit={submit} method="post" action={register ? "/v1/auth/register" : "/v1/auth/login"}>
            <label>Username<input name="username" autoComplete="username" pattern="[A-Za-z0-9][A-Za-z0-9_-]{2,31}" minLength={3} maxLength={32} required value={username} onChange={event => setUsername(event.target.value)} disabled={busy} /></label>
            <label>Password<input name="password" type="password" autoComplete={register ? "new-password" : "current-password"} minLength={register ? 15 : 1} maxLength={128} required value={password} onChange={event => setPassword(event.target.value)} disabled={busy} /></label>
            {register && <small className="hosted-muted">Use a passphrase of at least 15 characters.</small>}
            <button className="hosted-primary" disabled={busy}>{busy ? "Please wait…" : register ? "Create account" : "Sign in"}<Icon name="arrow" size={17} /></button>
          </form>
          <button className="hosted-text-button" onClick={() => { setRegister(!register); setPassword(""); }} disabled={busy}>{register ? "Already have an account? Sign in" : "New here? Create an account"}</button>
          {message && <button className="hosted-text-button" onClick={onRetrySession} disabled={busy}>Check existing session</button>}
        </>}
    </div></main>
  </div>;
}

function ConnectComputer({ claim, busy, onClaim }: { claim: PairingStatus | null; busy: boolean; onClaim: (code: string) => Promise<void> }) {
  const [code, setCode] = useState("");
  const sending = useRef(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (sending.current) return;
    sending.current = true;
    const value = code; setCode("");
    try { await onClaim(value); } finally { sending.current = false; }
  }
  return <section className="hosted-card hosted-connect" id="connect" aria-labelledby="connect-title">
    <div><p className="hosted-eyebrow">BRING YOUR COMPUTER ONLINE</p><h2 id="connect-title">Connect your companion</h2>
      <ol className="hosted-steps"><li>Open Poltergeist Companion on the computer you want to use.</li><li>In Connection, create a pairing code and enter it here.</li><li>Review this account on that computer, approve its permissions, then allow requests and choose Connect.</li></ol>
    </div><div className="hosted-claim-box">
      <form onSubmit={submit} method="post" action="/v1/pairing/claim">
        <label>Pairing code<input aria-label="Pairing code" name="code" value={code} onChange={event => setCode(event.target.value.toUpperCase())} className="hosted-code-input" autoComplete="off" spellCheck={false} pattern="[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{10}" minLength={10} maxLength={10} placeholder="10 characters" required disabled={busy} /></label>
        <button className="hosted-primary" disabled={busy}>{busy ? "Claiming…" : "Connect computer"}<Icon name="arrow" size={17} /></button>
      </form>
      {claim && <div className="hosted-claim-state" role="status"><strong>{claim.state === "claimed" ? "Waiting for local approval" : claim.state === "approved" ? "Approved on your computer" : `Pairing ${claim.state}`}</strong>
        <p>{claim.state === "claimed" ? "Return to the companion to review your account and permissions. Claiming this code alone does not allow experiments." : claim.state === "approved" ? "Your computer is paired. Start its local runtime and explicitly connect with consent." : "Review the code in the companion before trying again."}</p>
      </div>}
    </div>
  </section>;
}

function Composer({ target, presets, blocked, onExecute }: { target: BrowserDeviceView; presets: BrowserScenarioPreset[]; blocked: boolean; onExecute: (recipe: Scenario, mode: "test" | "carla", capture: { camera: boolean; lidar: boolean }) => void }) {
  const [presetId, setPresetId] = useState("");
  const [source, setSource] = useState("");
  const [name, setName] = useState("");
  const [map, setMap] = useState("");
  const [duration, setDuration] = useState("0.5");
  const [seed, setSeed] = useState("7");
  const [mode, setMode] = useState<"test" | "carla">("test");
  const [error, setError] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [camera, setCamera] = useState(false);
  const [lidar, setLidar] = useState(false);
  const captureAllowed = mode === "carla" && target.device.policy.allow_raw_capture && target.connection.recordings_supported;
  const scopes = target.device.policy.scopes;
  function load(recipe: Scenario, identifier = "") {
    setPresetId(identifier); setSource(JSON.stringify(recipe, null, 2)); setName(recipe.name);
    setMap(recipe.map); setDuration(String(recipe.duration_s)); setSeed(String(recipe.seed)); setError("");
  }
  useEffect(() => { if (!source && presets[0]) load(presets[0].recipe, presets[0].id); }, [presets]);
  useEffect(() => { if (!scopes.includes(`run:execute:${mode}`)) setMode(scopes.includes("run:execute:test") ? "test" : "carla"); }, [scopes.join(",")]);
  const preset = presets.find(value => value.id === presetId);
  async function importRecipe(file: File | undefined) {
    if (!file) return;
    try {
      if (file.size > 30000) throw new Error();
      const value = JSON.parse(await file.text()) as Scenario;
      if (typeof value.name !== "string" || typeof value.map !== "string" || !Array.isArray(value.actors) || typeof value.duration_s !== "number") throw new Error();
      load(value); setAdvanced(true);
    } catch { setError("Choose a scenario JSON file smaller than 30 KB with a name, map, duration and actors."); }
  }
  function submit(event: FormEvent) {
    event.preventDefault(); setError("");
    try {
      const recipe = JSON.parse(source) as Scenario;
      const limit = target.device.policy;
      const seconds = readNumericField(duration, { minimum: .005, maximum: Math.min(120, limit.max_duration_s), integer: false });
      const seedValue = readNumericField(seed, { minimum: 0, maximum: 2 ** 31 - 1, integer: true });
      if (seconds.error || seedValue.error) { setError(seconds.error ?? seedValue.error ?? "Check the numeric fields."); return; }
      if (!name.trim() || name.trim().length > 128 || !map.trim() || map.trim().length > 64 || !Array.isArray(recipe.actors)
          || !Number.isFinite(recipe.fixed_delta_seconds) || recipe.fixed_delta_seconds <= 0
          || Math.ceil(seconds.value! / recipe.fixed_delta_seconds - 1e-9) > Math.min(10000, limit.max_ticks)) throw new Error();
      onExecute({ ...recipe, name: name.trim(), map: map.trim(), duration_s: seconds.value!, seed: seedValue.value! }, mode,
        { camera: captureAllowed && camera, lidar: captureAllowed && lidar });
    } catch { setError("Check the scenario JSON, name, map and tick budget. The companion accepts only a bounded baseline experiment."); }
  }
  const allowed = scopes.includes(`run:execute:${mode}`);
  return <section className="hosted-card hosted-composer" aria-labelledby="compose-title">
    <div className="hosted-section-heading"><div><p className="hosted-eyebrow">DESIGN YOUR NEXT RUN</p><h2 id="compose-title">New experiment</h2></div><Icon name="scenarios" /></div>
    <form onSubmit={submit}>
      <fieldset disabled={blocked}><label>Starting recipe<select value={presetId} onChange={event => { const value = presets.find(item => item.id === event.target.value); if (value) load(value.recipe, value.id); }}><option value="">Custom recipe</option>{presets.map(value => <option key={value.id} value={value.id}>{value.label}</option>)}</select></label>
        {preset && <p className="hosted-muted hosted-preset-description">{preset.description}</p>}
        <div className="hosted-fields"><label>Experiment name<input value={name} onChange={event => setName(event.target.value)} maxLength={128} required /></label><label>Map already loaded<input value={map} onChange={event => setMap(event.target.value)} maxLength={64} required /></label></div>
        <div className="hosted-fields three"><label>Duration (simulated seconds)<input value={duration} onChange={event => setDuration(event.target.value)} inputMode="decimal" required /></label><label>Seed<input value={seed} onChange={event => setSeed(event.target.value)} inputMode="numeric" required /></label><label>Controller<select value="baseline" onChange={() => {}}><option value="baseline">Baseline controller</option></select></label></div>
        <div className="hosted-mode-options"><label><input type="radio" name="mode" value="test" checked={mode === "test"} onChange={() => setMode("test")} disabled={!scopes.includes("run:execute:test")} /> Synthetic check</label><label><input type="radio" name="mode" value="carla" checked={mode === "carla"} onChange={() => setMode("carla")} disabled={!scopes.includes("run:execute:carla")} /> CARLA experiment</label></div>
        <p className="hosted-muted">Synthetic runs check the workflow. CARLA setup is checked on your computer when a live run starts. Remote runs do not reload maps or change graphics.</p>
        <div className="hosted-mode-options"><label><input type="checkbox" checked={camera} disabled={!captureAllowed} onChange={event => setCamera(event.target.checked)} /> Save camera recording</label>
          <label><input type="checkbox" checked={lidar} disabled={!captureAllowed} onChange={event => setLidar(event.target.checked)} /> Save LiDAR recording</label></div>
        <p className="hosted-muted">Camera capture is 320 × 180 pixels. CARLA recordings use the computer's storage budget. Enable capture in its local pairing approval to save them. Sensor data stays local during execution.</p>
        <div className="hosted-advanced-actions"><button type="button" className="hosted-text-button" onClick={() => setAdvanced(!advanced)}>{advanced ? "Hide recipe JSON" : "Edit recipe JSON"}</button><label className="hosted-file-label">Import JSON<input type="file" accept=".json,application/json" onChange={event => { void importRecipe(event.target.files?.[0]); event.target.value = ""; }} /></label></div>
        {advanced && <label>Scenario JSON<small className="hosted-muted">The name, map, duration and seed fields above take precedence when submitting.</small><textarea className="hosted-json" aria-label="Scenario JSON" value={source} onChange={event => {
          setSource(event.target.value); setPresetId("");
          try { const value = JSON.parse(event.target.value); if (typeof value.name === "string") setName(value.name); if (typeof value.map === "string") setMap(value.map); if (typeof value.duration_s === "number") setDuration(String(value.duration_s)); if (typeof value.seed === "number") setSeed(String(value.seed)); } catch { /* Keep incomplete JSON editable. */ }
        }} spellCheck={false} maxLength={30000} rows={10} /></label>}
      </fieldset>
      {error && <p role="alert" className="hosted-alert">{error}</p>}
      <div className="hosted-run-footer"><small>Approved limit: {target.device.policy.max_duration_s} s · {target.device.policy.max_ticks.toLocaleString()} ticks · {target.device.policy.max_waiting_runs} waiting</small><button className="hosted-primary" disabled={blocked || !allowed || !target.connection.remote_requests_enabled || !source}>Run experiment<Icon name="arrow" size={17} /></button></div>
    </form>
  </section>;
}

function RunDetail({ run, canRead, canCancel, onRead, onCancel }: { run: HostedRun; canRead: boolean; canCancel: boolean; onRead: () => void; onCancel: () => void }) {
  const summary = run.summary;
  return <section className="hosted-card hosted-result" aria-labelledby="result-title"><div className="hosted-section-heading"><div><p className="hosted-eyebrow">MEASURED RESULTS</p><h2 id="result-title">{summary?.scenario.name ?? `Experiment ${short(run.id)}`}</h2></div><span className="hosted-status" data-state={summary?.status ?? "queued"}>{summary ? readable(summary.status) : "Accepted locally"}</span></div>
    {!summary ? <p className="hosted-muted">The companion accepted this run. Its completion and metrics have not been received yet.</p> : <>
      <div className="hosted-run-meta"><span>{summary.mode === "carla" ? "CARLA experiment" : "Synthetic check"}</span><span>Baseline controller</span><span>{Math.round(summary.progress * 100)}%</span></div>
      <progress value={summary.progress} max={1} aria-label="Experiment progress" />
      {summary.cancel_requested && !terminal(run) && <p role="status">Cancellation requested; waiting for the computer to finish cleanup.</p>}
      {summary.error && <p className="hosted-alert" role="status">{summary.error.message}</p>}
      <div className="hosted-metrics"><div><span>Collisions</span><strong>{metric(summary.metrics?.collision_count)}</strong></div><div><span>Minimum time to collision</span><strong>{metric(summary.metrics?.min_ttc_s, " s")}</strong></div><div><span>Measured duration</span><strong>{metric(summary.measured_duration_s, " s")}</strong></div></div>
      <p className="hosted-muted">Coverage: {metric(summary.metrics?.coverage.total_frames)} frames. Unavailable measurements remain marked as unmeasured.</p>
      <div className="hosted-evidence"><Icon name="simulations" /><div><strong>{summary.evidence.state === "local_only" ? "Evidence stays on your computer" : "Evidence not available"}</strong><p>{summary.evidence.telemetry_frames ?? "Unknown"} recorded frames · {summary.evidence.artifact_reference_count ?? "Unknown"} artifact references. Open the companion for local exports.</p></div></div>
    </>}
    <div className="hosted-result-footer"><small>Run {run.id}<br />Accepted {when(run.acceptedAt)}</small><div className="hosted-button-row"><button onClick={onRead} disabled={!canRead}>Refresh result</button>{!terminal(run) && <button onClick={onCancel} disabled={!canCancel}>Request cancellation</button>}</div></div>
  </section>;
}

function RevokeDialog({ target, onClose, onRevoke }: { target: BrowserDeviceView; onClose: () => void; onRevoke: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { dialog.current?.showModal(); }, []);
  return <dialog ref={dialog} className="hosted-modal" aria-labelledby="revoke-title" onCancel={event => { event.preventDefault(); onClose(); }}>
    <h2 id="revoke-title">Revoke {target.device.display_name}?</h2><p>This account will lose device control and result access. Already accepted work may finish locally. Pair again on that computer to restore access.</p>
    <div className="hosted-button-row"><button onClick={onClose} autoFocus>Keep access</button><button className="hosted-danger" onClick={onRevoke}>Revoke access</button></div>
  </dialog>;
}

export function HostedApp({ client: provided }: { client?: Client } = {}) {
  const client = useMemo(() => provided ?? createHostedClient(), [provided]);
  const [session, setSession] = useState<BrowserSession | null>(null);
  const [checking, setChecking] = useState(true);
  const [bootstrap, setBootstrap] = useState(0);
  const [notice, setNotice] = useState("");
  const [logoutUncertain, setLogoutUncertain] = useState(false);
  const [devices, setDevices] = useState<BrowserDeviceView[]>([]);
  const [devicesLoading, setDevicesLoading] = useState(true);
  const [presets, setPresets] = useState<BrowserScenarioPreset[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [availabilityUnknown, setAvailabilityUnknown] = useState(false);
  const [claim, setClaim] = useState<PairingStatus | null>(null);
  const [claimBusy, setClaimBusy] = useState(false);
  const [runs, setRuns] = useState<HostedRun[]>([]);
  const [selectedRun, setSelectedRun] = useState("");
  const [pending, setPending] = useState<Pending | null>(null);
  const [older, setOlder] = useState<string | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<BrowserDeviceView | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [labRoute, setLabRoute] = useState(() => hostedLabRoute(typeof window === "undefined" ? "" : window.location.hash));
  const [labVisited, setLabVisited] = useState(labRoute.section !== "legacy");
  useEffect(() => {
    const navigate = () => { const next = hostedLabRoute(window.location.hash); setLabRoute(next); if (next.section !== "legacy") setLabVisited(true); };
    window.addEventListener("hashchange", navigate);
    return () => window.removeEventListener("hashchange", navigate);
  }, []);
  const pendingRef = useRef<Pending | null>(null);
  const runsRef = useRef<HostedRun[]>([]);
  const resultRequests = useRef(new Map<string, string>());
  const writing = useRef(false);
  const accountEpoch = useRef(0);
  const paginated = useRef(false);
  const selected = devices.find(value => value.device.device_id === selectedId) ?? null;
  const key = session && selected ? JSON.stringify([session.account.account_id, selected.device.device_id,
    selected.device.ownership_generation, selected.device.policy_revision, selected.device.policy, selected.device.revoked_at]) : "";
  const scope = useRef(key); scope.current = key;
  const currentDevice = useRef(selected); currentDevice.current = selected;

  function clearPrivate() {
    accountEpoch.current += 1;
    setDevices([]); setDevicesLoading(true); setPresets([]); setSelectedId(""); setRuns([]); runsRef.current = [];
    setClaim(null); setRevokeTarget(null); setPending(null); pendingRef.current = null; resultRequests.current.clear();
    setLabRoute({ section: "legacy" }); setLabVisited(false);
    if (typeof window !== "undefined") window.history.replaceState(null, "", window.location.pathname + window.location.search);
  }
  function failed(error: unknown, context?: string) {
    if (context !== undefined && context !== scope.current) return;
    if (error instanceof HostedError && ["request_cancelled", "stale_response"].includes(error.code)) return;
    if (error instanceof HostedError && error.status === 401) { clearPrivate(); setSession(null); setChecking(false); }
    setNotice(error instanceof Error ? error.message : "The operation could not be completed.");
  }
  useEffect(() => {
    const controller = new AbortController(); setChecking(true);
    client.session(controller.signal).then(value => { if (!controller.signal.aborted) { setSession(value); setLogoutUncertain(false); } })
      .catch(error => { if (!controller.signal.aborted && (!(error instanceof HostedError) || error.status !== 401)) failed(error); })
      .finally(() => { if (!controller.signal.aborted) setChecking(false); });
    return () => controller.abort();
  }, [client, bootstrap]);

  useEffect(() => {
    if (!session) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    client.scenarios(controller.signal).then(value => { if (!controller.signal.aborted) setPresets(value); }).catch(error => { if (!controller.signal.aborted) failed(error); });
    async function poll() {
      try {
        const list = await client.devices(controller.signal);
        if (controller.signal.aborted) return;
        setDevices(list); setAvailabilityUnknown(false);
        setSelectedId(old => old && list.some(value => value.device.device_id === old) ? old
          : list.find(value => value.device.revoked_at === null)?.device.device_id ?? "");
      } catch (error) { if (!controller.signal.aborted) { setAvailabilityUnknown(true); failed(error); } }
      finally { if (!controller.signal.aborted) { setDevicesLoading(false); timer = setTimeout(() => { void poll(); }, 4000); } }
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [session?.account.account_id, client, refresh]);

  useEffect(() => {
    if (!session || claim || typeof window === "undefined") return;
    const match = /^#claim=([0-9a-f-]{36})$/i.exec(window.location.hash);
    if (!match) return;
    const controller = new AbortController();
    client.claimStatus(match[1], controller.signal).then(value => { if (!controller.signal.aborted) setClaim(value); }).catch(error => { if (!controller.signal.aborted) failed(error); });
    return () => controller.abort();
  }, [session?.account.account_id, claim, client]);

  useEffect(() => {
    if (!claim || claim.state !== "claimed") return;
    const controller = new AbortController();
    const timer = setInterval(() => {
      client.claimStatus(claim.attempt_id, controller.signal).then(value => {
        if (!controller.signal.aborted) { setClaim(value); if (value.state === "approved") setRefresh(value => value + 1); }
      }).catch(error => { if (!controller.signal.aborted) failed(error); });
    }, 3000);
    return () => { controller.abort(); clearInterval(timer); };
  }, [claim?.attempt_id, claim?.state, client]);

  function ingest(rows: RemoteCommandView[], context: string, target: BrowserDeviceView) {
    if (context !== scope.current) return;
    const value = mergeRuns(runsRef.current, rows, target.device.device_id, target.device.ownership_generation);
    runsRef.current = value; setRuns(value);
    setSelectedRun(old => value.some(run => run.id === old) ? old : value[0]?.id ?? "");
    const pending = pendingRef.current;
    const resolved = pending && rows.find(row => row.request_id === pending.command.request_id && row.status !== "queued");
    if (resolved) {
      pendingRef.current = null; setPending(null);
      if (resolved.reply?.kind === "rejected") setNotice(`Request declined: ${readable(resolved.reply.code)}.`);
      else if (resolved.operation === "cancel") setNotice("Cancellation requested. The final experiment result confirms its outcome.");
      else {
        if (resolved.reply?.kind === "accepted") setSelectedRun(resolved.reply.simulation_id);
        setNotice("Your computer accepted the experiment.");
      }
    }
  }

  useEffect(() => {
    runsRef.current = []; setRuns([]); setSelectedRun(""); setOlder(null);
    paginated.current = false;
    pendingRef.current = null; setPending(null); resultRequests.current.clear();
    if (!selected || !key || selected.device.revoked_at) return;
    const target = selected, context = key, controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const page = await client.history(target.device.device_id, controller.signal);
        if (controller.signal.aborted || context !== scope.current) return;
        ingest(page.items, context, target); if (!paginated.current) setOlder(page.next_before);
        const pending = pendingRef.current;
        if (pending) {
          try { ingest([await client.command(pending.device, pending.command.request_id, controller.signal)], context, target); }
          catch (error) { if (!(error instanceof HostedError && error.status === 404)) throw error; }
        }
        const live = currentDevice.current;
        if (live?.connection.remote_requests_enabled && live.device.policy.scopes.includes("result:read")) {
          const candidates = runsRef.current.filter(run => !terminal(run)).slice(0, 5);
          for (const run of candidates) {
            if (controller.signal.aborted || context !== scope.current) return;
            let request = resultRequests.current.get(run.id);
            if (!request) { request = crypto.randomUUID(); resultRequests.current.set(run.id, request); }
            const answer = await client.send(target.device.device_id, { kind: "result", request_id: request, simulation_id: run.id }, controller.signal);
            ingest([answer], context, target);
            if (answer.status !== "queued") resultRequests.current.delete(run.id);
          }
        }
      } catch (error) { if (!controller.signal.aborted) failed(error, context); }
      finally { if (!controller.signal.aborted) timer = setTimeout(() => { void poll(); }, 3000); }
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [key, client]);

  async function authenticate(username: string, password: string, register: boolean) {
    setNotice("");
    try {
      if (register) await client.register(username, password);
      const value = await client.login(username, password);
      clearPrivate(); setSession(value); setChecking(false); setLogoutUncertain(false);
    } catch (error) { failed(error); }
  }
  async function signOut() {
    clearPrivate(); setSession(null); setChecking(false); setLogoutUncertain(true); setNotice("");
    try { await client.logout(); setLogoutUncertain(false); }
    catch (error) { if (error instanceof HostedError && error.status === 401) setLogoutUncertain(false); else failed(error); }
  }
  async function claimCode(code: string) {
    const observed = accountEpoch.current;
    setClaimBusy(true); setNotice("");
    try {
      const value = await client.claim(code);
      if (observed !== accountEpoch.current) return;
      setClaim(value);
      if (typeof window !== "undefined") window.history.replaceState(null, "", `#claim=${value.attempt_id}`);
    } catch (error) { if (observed === accountEpoch.current) failed(error); }
    finally { setClaimBusy(false); }
  }
  async function send(value: HostedCommand) {
    if (!selected || writing.current || pendingRef.current) return;
    const context = key, target = selected;
    writing.current = true;
    const waiting = { device: target.device.device_id, command: value, uncertain: false };
    pendingRef.current = waiting; setPending(waiting); setNotice("");
    try {
      const answer = await client.send(waiting.device, value);
      ingest([answer], context, target);
    } catch (error) {
      if (context !== scope.current) return;
      if (error instanceof HostedError && error.status >= 400 && error.status < 500 && error.status !== 429) {
        pendingRef.current = null; setPending(null);
      } else { waiting.uncertain = true; pendingRef.current = waiting; setPending({ ...waiting }); }
      failed(error, context);
    } finally { writing.current = false; }
  }
  async function retryPending() {
    const value = pendingRef.current;
    if (!value || !selected || writing.current) return;
    writing.current = true; const context = key, target = selected;
    try { ingest([await client.send(value.device, value.command)], context, target); }
    catch (error) { failed(error, context); }
    finally { writing.current = false; }
  }
  async function refreshResult(run: HostedRun) {
    if (!selected || !selected.connection.remote_requests_enabled) return;
    const context = key, target = selected;
    let request = resultRequests.current.get(run.id);
    if (!request) { request = crypto.randomUUID(); resultRequests.current.set(run.id, request); }
    try {
      const answer = await client.send(target.device.device_id, { kind: "result", request_id: request, simulation_id: run.id });
      ingest([answer], context, target);
      if (answer.status !== "queued") resultRequests.current.delete(run.id);
    } catch (error) { failed(error, context); }
  }
  async function revoke() {
    const target = revokeTarget;
    if (!target || writing.current) return;
    const observed = accountEpoch.current;
    writing.current = true;
    try {
      await client.revoke(target.device.device_id);
      if (observed !== accountEpoch.current) return;
      setRevokeTarget(null);
      setRuns([]); runsRef.current = []; setRefresh(value => value + 1); setNotice("Computer access revoked. Accepted local work may still finish on that computer.");
    } catch (error) { if (observed === accountEpoch.current) failed(error); }
    finally { writing.current = false; }
  }

  if (!session) return <AuthForm loading={checking} message={notice} logoutUncertain={logoutUncertain}
    onLogin={authenticate} onRetryLogout={() => { void signOut(); }} onRetrySession={() => setBootstrap(value => value + 1)} />;
  const scopes: PermissionScope[] = selected?.device.policy.scopes ?? [];
  const activeRun = runs.find(value => value.id === selectedRun) ?? runs[0];
  const live = selected?.connection.remote_requests_enabled === true && !availabilityUnknown;
  return <div className="hosted-shell">
    <SkipLink target="experiments" />
    <aside className="hosted-sidebar"><a className="hosted-brand" href="/app/"><Icon name="ghost" size={30} />Poltergeist<span>EXPERIMENT LAB</span></a>
      <p className="hosted-nav-heading">YOUR WORKSPACE</p><nav aria-label="Workspace"><a href="#experiments" aria-current={labRoute.section === "legacy" && window.location.hash !== "#connect" ? "page" : undefined}><Icon name="experiments" />Experiments</a><a href="#lab" aria-current={labRoute.section === "guide" || labRoute.section === "run" ? "page" : undefined}><Icon name="lab" />Guided lab</a><a href="#discovery" aria-current={labRoute.section === "discovery" ? "page" : undefined}><Icon name="discovery" />Discovery</a><a href="#connect" aria-current={window.location.hash === "#connect" ? "page" : undefined}><Icon name="simulations" />Connect a computer</a></nav>
      <div className="hosted-sidebar-note"><Icon name="activity" /><strong>Local execution.<br />Connected insight.</strong><p>Your companion runs the simulation. This workspace receives approved summaries.</p></div><small className="hosted-sidebar-footer">Private connected workspace</small>
    </aside>
    <div className="hosted-workspace"><header className="hosted-topbar"><span>Workspace <span className="hosted-slash">/</span> Experiments</span><div><span className="hosted-account">{session.account.username}</span><button onClick={() => { void signOut(); }}>Sign out</button></div></header>
      <main className="hosted-main" id="experiments" tabIndex={-1}><div className="hosted-page-heading"><div><p className="hosted-eyebrow">CONNECTED EXPERIMENTS</p>{labRoute.section === "legacy" && <h1>Your lab, connected<span>.</span></h1>}<p className="hosted-muted">Create repeatable scenarios and review results from the computer you control.</p></div><button onClick={() => setRefresh(value => value + 1)}><Icon name="refresh" size={16} />Refresh computers</button></div>
        {notice && <p className="hosted-alert" role="alert">{notice}</p>}
        <div className="hosted-stats"><div><Icon name="simulations" /><span>Paired computers</span><strong>{devicesLoading ? "Loading…" : availabilityUnknown ? "Unknown" : devices.filter(value => !value.device.revoked_at).length}</strong></div><div><Icon name="activity" /><span>Accepting requests</span><strong>{devicesLoading ? "Loading…" : availabilityUnknown ? "Unknown" : devices.filter(value => value.connection.remote_requests_enabled).length}</strong></div><div><Icon name="experiments" /><span>Completed in this view</span><strong>{runs.filter(run => run.summary?.status === "completed").length}</strong></div></div>
        {devicesLoading ? <p className="hosted-state-note" role="status">Loading your computers…</p> : availabilityUnknown ? <section className="hosted-state-note" role="status"><strong>Computer availability could not be refreshed</strong><p>Check your connection and choose Refresh computers to try again. Sending new experiments is paused until availability is confirmed.</p></section> : !devices.some(value => !value.device.revoked_at) && <section className="hosted-state-note" role="status"><strong>No computers connected yet</strong><p>Pair Poltergeist Companion to run experiments and keep your results on that computer.</p><a href="#connect">Connect your first computer →</a></section>}
        <section className="hosted-card hosted-device" aria-labelledby="computer-title"><div><p className="hosted-eyebrow">EXECUTION COMPUTER</p><h2 id="computer-title">Choose your companion</h2></div><label className="hosted-device-picker">Computer<select aria-label="Execution computer" value={selectedId} onChange={event => setSelectedId(event.target.value)} disabled={pending !== null}><option value="">Select a computer</option>{devices.map(value => <option key={value.device.device_id} value={value.device.device_id}>{value.device.display_name}{value.device.revoked_at ? " (revoked)" : ""}</option>)}</select></label>
          {selected && <><span className="hosted-status" data-state={availabilityUnknown ? "connecting" : selected.connection.state}>{availabilityUnknown ? "Availability unknown" : readable(selected.connection.state)}</span><p className="hosted-device-detail">Last seen: {when(selected.connection.last_seen_at)}{selected.connection.protocol_compatible ? " · Compatible connection protocol" : " · Compatibility not yet confirmed"}</p>
            <p className="hosted-muted">{selected.connection.state === "paused" ? "Connected, with remote requests paused. In the companion, disconnect, check consent and reconnect to allow approved requests." : !live ? "Open the companion, start its local runtime and connect with consent to accept requests." : "Approved requests are delivered to this computer. CARLA readiness is checked when a live experiment starts."}</p>
            {!selected.device.revoked_at && <button className="hosted-danger-link" onClick={() => setRevokeTarget(selected)}>Revoke computer access</button>}</>}
        </section>
        {pending && <section className="hosted-card hosted-pending" role="status"><strong>{pending.uncertain ? "Delivery is unconfirmed" : "Waiting for your computer"}</strong><p>The original request identity is preserved. An acceptance acknowledgement confirms queueing, not completion.</p><code>{pending.command.request_id}</code>{pending.uncertain && <button onClick={() => { void retryPending(); }}>Retry original request</button>}</section>}
        {labRoute.section === "legacy" && selected && !selected.device.revoked_at && <div className="hosted-experiment-grid"><Composer key={key} target={selected} presets={presets} blocked={pending !== null || availabilityUnknown} onExecute={(recipe, mode, capture) => { void send({ kind: "execute", request_id: crypto.randomUUID(), scenario: recipe, mode, agent_id: null, options: { max_ticks: Math.min(10000, selected.device.policy.max_ticks), ...(capture.camera ? { camera: true } : {}), ...(capture.lidar ? { lidar: true } : {}) } }); }} />
          <section className="hosted-card hosted-run-list" aria-labelledby="runs-title"><div className="hosted-section-heading"><div><p className="hosted-eyebrow">DURABLE READBACK</p><h2 id="runs-title">Recent experiments</h2></div><span className="hosted-count">{runs.length}</span></div>
            {!runs.length ? <div className="hosted-empty"><Icon name="experiments" size={35} /><h3>Your next experiment starts here</h3><p>Choose a recipe and send an approved request. Existing receipts reappear when this page reloads.</p></div> : <ul>{runs.map(run => <li key={run.id}><button aria-pressed={run.id === activeRun?.id} onClick={() => setSelectedRun(run.id)}><span><strong>{run.summary?.scenario.name ?? `Experiment ${short(run.id)}`}</strong><small>{when(run.acceptedAt)}</small></span><span className="hosted-status" data-state={run.summary?.status ?? "queued"}>{run.summary ? readable(run.summary.status) : "Accepted"}</span></button></li>)}</ul>}
            {older && <button className="hosted-text-button" onClick={() => { const context = key, target = selected; void client.history(target.device.device_id, undefined, older).then(page => { if (context === scope.current) { paginated.current = true; ingest(page.items, context, target); setOlder(page.next_before); } }).catch(error => failed(error, context)); }}>Load earlier receipts</button>}
          </section></div>}
        {labRoute.section === "legacy" && activeRun && selected && <RunDetail run={activeRun} canRead={live && scopes.includes("result:read")} canCancel={live && scopes.includes("run:cancel") && pending === null}
          onRead={() => { void refreshResult(activeRun); }} onCancel={() => { void send({ kind: "cancel", request_id: crypto.randomUUID(), simulation_id: activeRun.id }); }} />}
        {labRoute.section === "legacy" && activeRun && selected && terminal(activeRun) && <EvidencePanel key={`${key}:${activeRun.id}`} client={client} device={selected.device.device_id}
          run={activeRun.id} enabled={live && scopes.includes("result:read") && selected.connection.evidence_supported === true && selected.device.policy.allow_evidence_read === true} />}
        {labRoute.section === "legacy" && activeRun && selected && terminal(activeRun) && <RecordingPanel key={`recordings:${key}:${activeRun.id}`} client={client} device={selected.device.device_id}
          run={activeRun.id} synthetic={activeRun.summary?.mode === "test"} enabled={live && scopes.includes("result:read") && selected.connection.recordings_supported === true && selected.device.policy.allow_evidence_read === true} />}
        {labRoute.section !== "legacy" && !selected && <section className="hosted-state-note"><h1>Select a computer to open your experiment lab</h1><p>Pair or choose an approved companion, then return to the guided lab or discovery.</p><a href="#connect">Connect a computer</a></section>}
        <div hidden={labRoute.section === "legacy"}>{labVisited && selected && !selected.device.revoked_at && <HostedLabWorkspace key={key} client={client} target={selected} contextKey={key} enabled={live} active={labRoute.section !== "legacy"} route={labRoute} authFailed={error => failed(error, key)} />}</div>
        <ConnectComputer claim={claim} busy={claimBusy} onClaim={claimCode} />
        <footer className="hosted-footer">A completed experiment is an observation, not a driving-safety guarantee. Evidence stays on your computer until requested.</footer>
      </main>
    </div>
    {revokeTarget && <RevokeDialog target={revokeTarget} onClose={() => setRevokeTarget(null)} onRevoke={() => { void revoke(); }} />}
  </div>;
}
