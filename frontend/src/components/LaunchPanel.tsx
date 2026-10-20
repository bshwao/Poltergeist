import { useEffect, useId, useRef, useState } from "react";
import type { FormEvent } from "react";
import { api, ApiError } from "../services/api";
import type { AgentReadiness, QueueStatus, RegisteredAgent, Scenario, SimulationRecord } from "../types/generated";
import {
  acceptanceMayBeUncertain, defaultEvaluationDraft, defaultOptionDraft, EVALUATION_FIELDS,
  makeLaunchRequest, OPTION_FIELDS,
} from "./launch-form";
import type { RunMode } from "./launch-form";
import { simulationLink } from "../navigation";
import { AgentIdentity } from "./AgentIdentity";

type Receipt = { record: SimulationRecord; recipeJson: string; name: string; mode: RunMode; agentId: string | null };
type Failure = { message: string; uncertain: boolean; issues: { path: string; message: string }[] };
const canonicalSnapshot = (value: unknown) => JSON.stringify(value, (_key, item) => item && typeof item === "object" && !Array.isArray(item)
  ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item);

export function LaunchPanel({ scenario, disabledReason, active = true, registryRevision = 0 }: { scenario: Scenario | null; disabledReason: string | null; active?: boolean; registryRevision?: number }) {
  const prefix = useId();
  const [mode, setMode] = useState<RunMode | null>(null);
  const [options, setOptions] = useState(defaultOptionDraft);
  const [evaluation, setEvaluation] = useState(defaultEvaluationDraft);
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const [showErrors, setShowErrors] = useState(false);
  const [optionsOpen, setOptionsOpen] = useState(false);
  const [evaluationOpen, setEvaluationOpen] = useState(false);
  const [captureCleared, setCaptureCleared] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [queue, setQueue] = useState<QueueStatus | null>(null);
  const [checkingQueue, setCheckingQueue] = useState(false);
  const [queueError, setQueueError] = useState<string | null>(null);
  const [agents, setAgents] = useState<RegisteredAgent[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [selectedAgent, setSelectedAgent] = useState<RegisteredAgent | null>(null);
  const [readiness, setReadiness] = useState<AgentReadiness | null>(null);
  const [agentsLoading, setAgentsLoading] = useState(false);
  const [agentLoading, setAgentLoading] = useState(false);
  const [agentsError, setAgentsError] = useState<string | null>(null);
  const [agentError, setAgentError] = useState<string | null>(null);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const submitRequest = useRef<AbortController | null>(null);
  const queueRequest = useRef<AbortController | null>(null);
  const listRequest = useRef<AbortController | null>(null);
  const agentRequest = useRef<AbortController | null>(null);
  const launchPostStarted = useRef(false);
  const mounted = useRef(true);
  const failureSummary = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      submitRequest.current?.abort();
      queueRequest.current?.abort();
      listRequest.current?.abort();
      agentRequest.current?.abort();
    };
  }, []);

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    listRequest.current = controller;
    setAgentsLoading(true);
    setAgentsError(null);
    void (async () => {
      try {
        const records: RegisteredAgent[] = [];
        // The registry can exceed one API page; never hide later registrations.
        for (let offset = 0; ; offset += 100) {
          const page = await api.listAgents({ limit: 100, offset, active: true }, { signal: controller.signal });
          if (controller.signal.aborted) return;
          records.push(...page);
          if (page.length < 100) break;
        }
        if (!controller.signal.aborted) setAgents(Array.from(new Map(records.filter(record => record.active).map(record => [record.id, record])).values()));
      } catch (error) {
        if (!controller.signal.aborted) setAgentsError(error instanceof ApiError ? error.message : "Could not load registered agents. Baseline controller remains available.");
      } finally {
        if (listRequest.current === controller) listRequest.current = null;
        if (!controller.signal.aborted) setAgentsLoading(false);
      }
    })();
    return () => controller.abort();
  }, [active, registryRevision, refreshVersion]);

  useEffect(() => {
    setReadiness(null);
    setSelectedAgent(null);
    setAgentError(null);
    setAgentLoading(false);
    if (!active || !selectedAgentId) return;
    const controller = new AbortController();
    agentRequest.current = controller;
    setAgentLoading(true);
    void Promise.all([
      api.getAgent(selectedAgentId, { signal: controller.signal }),
      api.getAgentReadiness(selectedAgentId, { signal: controller.signal }),
    ]).then(([record, checked]) => {
      if (controller.signal.aborted) return;
      setSelectedAgent(record);
      setReadiness(checked);
    }).catch(error => {
      if (!controller.signal.aborted) setAgentError(error instanceof ApiError ? error.message : "Could not inspect the selected agent. Refresh agents to try again.");
    }).finally(() => {
      if (agentRequest.current === controller) agentRequest.current = null;
      if (!controller.signal.aborted) setAgentLoading(false);
    });
    return () => controller.abort();
  }, [active, selectedAgentId, registryRevision, refreshVersion]);

  useEffect(() => {
    if (active) return;
    queueRequest.current?.abort();
    queueRequest.current = null;
    setCheckingQueue(false);
    if (submitRequest.current) {
      submitRequest.current.abort();
      if (launchPostStarted.current) setFailure({ message: "The workspace was hidden before the launch response was confirmed.", uncertain: true, issues: [] });
    }
  }, [active]);

  const selection = selectedAgentId ? { id: selectedAgentId, record: selectedAgent } : null;
  const built = makeLaunchRequest(scenario, mode, options, evaluation, selection);
  const errors = built.errors ?? {};
  const errorFor = (key: string) => showErrors || touched[key]
    || (key === "evaluation_settings.ttc_horizon_s" && touched["evaluation_settings.ttc_warning_s"])
    || (key === "evaluation_settings.braking_full_severity_mps2" && touched["evaluation_settings.hard_braking_threshold_mps2"])
    ? errors[key] : undefined;
  const reason = disabledReason || (!scenario ? "Generate or validate the current recipe before launching." : null);
  const agentBlocked = Boolean(selectedAgentId && (agentLoading || agentError || !selectedAgent?.active || !readiness?.ready));
  const priorRecipe = receipt !== null && (!scenario || disabledReason !== null || receipt.recipeJson !== JSON.stringify(scenario));

  function chooseMode(next: RunMode) {
    setMode(next);
    if (next === "test") {
      setCaptureCleared(options.camera || options.lidar);
      setOptions(current => ({ ...current, camera: false, lidar: false }));
    } else setCaptureCleared(false);
  }

  async function checkQueue() {
    if (!active || checkingQueue || queueRequest.current) return;
    const controller = new AbortController();
    queueRequest.current = controller;
    setCheckingQueue(true);
    setQueueError(null);
    try {
      const result = await api.getSimulationQueue({ signal: controller.signal });
      if (mounted.current && !controller.signal.aborted) setQueue(result);
    } catch (error) {
      if (mounted.current && !controller.signal.aborted) setQueueError(error instanceof ApiError ? error.message : "Could not check the simulation queue.");
    } finally {
      if (queueRequest.current === controller) queueRequest.current = null;
      if (mounted.current && !controller.signal.aborted) setCheckingQueue(false);
    }
  }

  async function launch(event: FormEvent) {
    event.preventDefault();
    if (!active || submitting || submitRequest.current || reason || agentBlocked) return;
    setShowErrors(true);
    setFailure(null);
    if (!built.request) {
      if (Object.keys(errors).some(key => key.startsWith("options."))) setOptionsOpen(true);
      if (Object.keys(errors).some(key => key.startsWith("evaluation_settings.") || key === "weights")) setEvaluationOpen(true);
      requestAnimationFrame(() => failureSummary.current?.focus());
      return;
    }
    // All inputs are copied before the asynchronous request. A newly loaded or
    // edited recipe cannot change the run already being submitted.
    const input = built.request;
    const snapshot = { recipeJson: JSON.stringify(input.scenario), name: input.scenario!.name, mode: input.mode, agentId: input.agent_id };
    const controller = new AbortController();
    submitRequest.current = controller;
    launchPostStarted.current = false;
    setSubmitting(true);
    try {
      let expectedAgentSnapshot = selectedAgent?.snapshot ?? null;
      if (input.agent_id) {
        // Registration contents are immutable, but files and activation can change.
        // Repeat static checks for this exact ID immediately before the POST.
        const [current, checked] = await Promise.all([
          api.getAgent(input.agent_id, { signal: controller.signal }),
          api.getAgentReadiness(input.agent_id, { signal: controller.signal }),
        ]);
        if (!mounted.current || controller.signal.aborted) return;
        setSelectedAgent(current);
        setReadiness(checked);
        expectedAgentSnapshot = current.snapshot;
        const fresh = makeLaunchRequest(input.scenario, input.mode, options, evaluation, { id: input.agent_id, record: current });
        if (!checked.ready || !fresh.request) {
          setFailure({ message: checked.ready ? "The selected agent is incompatible with these launch settings." : "The selected agent failed its current static checks. Resolve the listed issues and refresh agents.", uncertain: false,
            issues: [...checked.issues.map(message => ({ path: "agent", message })), ...Object.entries(fresh.errors ?? {}).map(([path, message]) => ({ path, message }))] });
          return;
        }
      }
      if (controller.signal.aborted) return;
      launchPostStarted.current = true;
      const record = await api.launchSimulation(input, { signal: controller.signal });
      if (mounted.current && !controller.signal.aborted) {
        const acceptedId = record.simulation.agent_snapshot?.registration_id ?? null;
        const acceptedSnapshot = record.simulation.agent_snapshot;
        if (acceptedId !== snapshot.agentId || canonicalSnapshot(expectedAgentSnapshot) !== canonicalSnapshot(acceptedSnapshot ?? null)) throw new ApiError("The acceptance response did not match the selected agent and version. Check run history before retrying.", 202);
        setReceipt({ record, ...snapshot });
      }
    } catch (error) {
      if (!mounted.current || controller.signal.aborted) return;
      setFailure(error instanceof ApiError
        ? { message: error.message, uncertain: launchPostStarted.current && acceptanceMayBeUncertain(error.status), issues: error.issues }
        : { message: launchPostStarted.current ? "The launch response could not be confirmed." : "The selected agent could not be checked. No launch was submitted.", uncertain: launchPostStarted.current, issues: [] });
    } finally {
      if (submitRequest.current === controller) submitRequest.current = null;
      if (mounted.current) setSubmitting(false);
    }
  }

  return (
    <section className="launch-panel panel" aria-labelledby={`${prefix}-heading`}>
      <div className="panel-header"><div><p className="eyebrow">05 / Execute the recipe</p><h2 id={`${prefix}-heading`}>Launch a simulation</h2></div><span className="badge badge-neutral">Single worker</span></div>
      <form onSubmit={launch} noValidate aria-busy={submitting}>
        <p className="launch-intro muted">Submit the exact current recipe and the settings below. Select an execution mode for this workspace.</p>
        {reason && <p className="notice warning-notice">{reason}</p>}
        <fieldset className="launch-fields" disabled={submitting}>
          <legend className="sr-only">Simulation execution settings</legend>
          <div className="mode-grid" role="group" aria-label="Execution mode" aria-describedby={errorFor("mode") ? `${prefix}-mode-error` : undefined}>
            <button className="mode-card" type="button" aria-pressed={mode === "carla"} onClick={() => chooseMode("carla")}><strong>CARLA</strong><span>Run against the configured CARLA server. Requires a reachable simulator and the requested map. Its spectator camera moves above the scenario when the run starts.</span></button>
            <button className="mode-card" type="button" aria-pressed={mode === "test"} onClick={() => chooseMode("test")}><strong>Synthetic test</strong><span>Exercise the pipeline with synthetic telemetry. Sensor agents receive deterministic fixtures. No CARLA connection or rendered perception.</span></button>
          </div>
          {errorFor("mode") && <p className="field-error launch-field-message" id={`${prefix}-mode-error`}>{errors.mode}</p>}
          {captureCleared && <p className="notice">Camera and LiDAR capture were turned off when you selected synthetic test mode.</p>}

          <div className="agent-launch-section">
            <div className="queue-heading"><label className="field-label" htmlFor={`${prefix}-agent`}>Ego controller</label><button className="button text-button" type="button" disabled={agentsLoading || agentLoading || !active} onClick={() => setRefreshVersion(value => value + 1)}>{agentsLoading || agentLoading ? "Loading agents…" : "Refresh agents"}</button></div>
            <select id={`${prefix}-agent`} value={selectedAgentId ?? ""} onChange={event => { setSelectedAgentId(event.target.value || null); setFailure(null); }} aria-describedby={`${prefix}-agent-help`}>
              <option value="">Baseline controller</option>
              {selectedAgentId && !agents.some(agent => agent.id === selectedAgentId) && <option value={selectedAgentId}>{selectedAgent?.snapshot.manifest.name ?? "Selected registration"} · {selectedAgentId}{selectedAgent?.active ? "" : " · unavailable"}</option>}
              {agents.map(agent => <option value={agent.id} key={agent.id}>{agent.snapshot.manifest.name} · v{agent.snapshot.manifest.version} · {agent.id}</option>)}
            </select>
            <p id={`${prefix}-agent-help`} className="field-hint">Choose the built-in baseline or an active local registration. <a href="#agents">Manage agents</a>. The selected registration is kept when the recipe changes.</p>
            {agentsError && <p className="field-error" role="alert">{agentsError}</p>}
            {!selectedAgentId ? <p className="muted">The built-in baseline uses simulator state and permits scripted ego interventions.</p> : <div aria-live="polite">
              {agentLoading && <p role="status">Checking the selected registration…</p>}
              {agentError && <p className="field-error" role="alert">{agentError} The selection is preserved; refresh or choose another controller.</p>}
              {selectedAgent && <>
                <AgentIdentity simulation={{ agent: "custom", agent_snapshot: selectedAgent.snapshot }} />
                <p className="field-hint">SDK {selectedAgent.snapshot.manifest.protocol_version} · {selectedAgent.snapshot.agent_control_mode === "authoritative" ? "Authoritative ego control" : "Scripted ego interventions allowed"} · {selectedAgent.snapshot.manifest.navigation_mode === "agent_planner" ? "Agent owns navigation" : "Provided-route navigation"}{!selectedAgent.active ? " · Deactivated" : ""}</p>
                <p className="muted">{selectedAgent.snapshot.observation_mode === "sensors" ? `Declared sensor rig: ${selectedAgent.snapshot.manifest.sensors.map(sensor => `${sensor.id} (${sensor.type})`).join(", ")}. ${mode === "test" ? "Synthetic test delivers deterministic fixtures, not CARLA observations." : "CARLA delivers synchronized sensor inputs."} Ideal simulated ego localization and configured route assistance remain available.` : "Simulator-state inputs can expose privileged surrounding-actor information; this mode does not test camera-based perception."}</p>
              </>}
              {readiness && <div className={`notice ${readiness.ready ? "success-notice" : "warning-notice"}`}><strong>{readiness.ready ? "Static checks passed" : "Static checks failed"}</strong><p>Checked {new Date(readiness.checked_at).toLocaleString()}. Inference and simulator connectivity were not tested. Static checks are repeated before submission.</p>{readiness.issues.length > 0 && <ul>{readiness.issues.map((issue, index) => <li key={index}>{issue}</li>)}</ul>}{readiness.limitations.length > 0 && <ul>{readiness.limitations.map((limitation, index) => <li key={index}>{limitation}</li>)}</ul>}</div>}
              {Object.entries(errors).filter(([key]) => (key === "agent_id" ? !agentLoading && selectedAgent !== null : key.startsWith("agent.") || key === "options.rendering")).map(([key, message]) => <p className="field-error" key={key}>{message}</p>)}
            </div>}
          </div>

          <details className="settings-section" open={optionsOpen} onToggle={event => setOptionsOpen(event.currentTarget.open)}>
            <summary>Execution settings</summary>
            <div className="settings-grid">
              {OPTION_FIELDS.map(field => {
                const key = `options.${field.key}`;
                const error = errorFor(key);
                const id = `${prefix}-${field.key}`;
                return <div className="field" key={field.key}><label className="field-label" htmlFor={id}>{field.label}</label><input id={id} type="text" inputMode={field.integer ? "numeric" : "decimal"} value={options[field.key]} aria-invalid={Boolean(error)} aria-describedby={error ? `${id}-error` : undefined} onChange={event => { setOptions(current => ({ ...current, [field.key]: event.target.value })); setTouched(current => ({ ...current, [key]: true })); }} onBlur={() => setTouched(current => ({ ...current, [key]: true }))} />{error && <span className="field-error" id={`${id}-error`}>{error}</span>}</div>;
              })}
            </div>
            <div className="field rendering-field"><label className="field-label" htmlFor={`${prefix}-rendering`}>CARLA rendering</label><select id={`${prefix}-rendering`} value={options.rendering} onChange={event => { setOptions(current => ({ ...current, rendering: event.target.value as typeof options.rendering })); setTouched(current => ({ ...current, "options.rendering": true })); }} aria-invalid={Boolean(errorFor("options.rendering"))}>
              <option value="inherit">Keep current simulator setting</option><option value="enabled">Enabled for this run</option><option value="disabled">Disabled for this run</option>
            </select><p className="field-hint">RGB agents require Enabled in CARLA. Rendering changes are scoped to this run and restored afterward. Optional recordings below are separate from an agent’s declared sensor rig.</p>{errorFor("options.rendering") && <span className="field-error">{errors["options.rendering"]}</span>}</div>
            <div className="capture-options">
              <label className="checkbox-field"><input type="checkbox" checked={options.allow_map_reload} onChange={event => setOptions(current => ({ ...current, allow_map_reload: event.target.checked }))} />Allow map reload if the CARLA map differs</label>
              <label className="checkbox-field"><input type="checkbox" checked={options.camera} disabled={mode !== "carla"} onChange={event => setOptions(current => ({ ...current, camera: event.target.checked }))} />Capture camera frames (CARLA)</label>
              <label className="checkbox-field"><input type="checkbox" checked={options.lidar} disabled={mode !== "carla"} onChange={event => setOptions(current => ({ ...current, lidar: event.target.checked }))} />Capture LiDAR frames (CARLA)</label>
              <p className="field-hint">Camera dimensions are validated even when camera capture is off. Sensor timing applies to CARLA execution.</p>
              {errors.capture && <p className="field-error">{errors.capture}</p>}
            </div>
          </details>

          <details className="settings-section" open={evaluationOpen} onToggle={event => setEvaluationOpen(event.currentTarget.open)}>
            <summary>Evaluation settings <span className="muted">method 1.0</span></summary>
            <p className="settings-help muted">These thresholds and weights are saved with the run. Severity weights must total 100%.</p>
            <div className="settings-grid">
              {EVALUATION_FIELDS.map(field => {
                const key = `evaluation_settings.${field.key}`;
                const error = errorFor(key);
                const id = `${prefix}-${field.key}`;
                return <div className="field" key={field.key}><label className="field-label" htmlFor={id}>{field.label}</label><input id={id} type="text" inputMode={field.integer ? "numeric" : "decimal"} value={evaluation[field.key]} aria-invalid={Boolean(error)} aria-describedby={error ? `${id}-error` : undefined} onChange={event => { setEvaluation(current => ({ ...current, [field.key]: event.target.value })); setTouched(current => ({ ...current, [key]: true })); }} onBlur={() => setTouched(current => ({ ...current, [key]: true }))} />{error && <span className="field-error" id={`${id}-error`}>{error}</span>}</div>;
              })}
            </div>
            {errors.weights && (showErrors || Object.keys(touched).some(key => key.endsWith("_weight"))) && <p className="field-error launch-field-message" role="alert">{errors.weights}</p>}
          </details>
        </fieldset>
        {showErrors && Object.keys(errors).length > 0 && !reason && !agentBlocked && <div className="notice error-notice" role="alert" tabIndex={-1} ref={failureSummary}>Check the highlighted launch settings before submitting.</div>}
        <div className="form-actions"><button className="button primary" type="submit" disabled={!active || Boolean(reason) || agentBlocked || submitting}>{submitting ? "Submitting run…" : "Launch simulation"}<span aria-hidden="true">↗</span></button><span className="muted">Runs use one queue shared by this API.</span></div>
      </form>

      <div className="queue-summary">
        <div className="queue-heading"><strong>Queue availability</strong><button className="button text-button" type="button" disabled={checkingQueue} onClick={checkQueue}>{checkingQueue ? "Checking…" : "Check queue"}</button></div>
        <div aria-live="polite">{queueError ? <p className="field-error" role="alert">{queueError}</p> : queue ? <p>{queue.accepting ? "API accepting submissions" : "API not accepting submissions"} · {queue.pending} waiting · {queue.active_id ? "1 active run" : "No active run"}{queue.error ? " · Worker requires attention" : ""}</p> : <p className="muted">Queue state has not been checked.</p>}</div>
        <p className="field-hint">This is an advisory snapshot. Queue acceptance does not confirm that CARLA is connected or that a run will succeed.</p>
      </div>

      <div aria-live="polite" aria-atomic="true">
        {failure && <div className="notice error-notice" role="alert"><strong>{failure.uncertain ? "Run acceptance is uncertain" : "Run was not accepted"}</strong><p>{failure.message}</p>{failure.issues.length > 0 && <ul>{failure.issues.map((issue, index) => <li key={index}><code>{issue.path || "launch"}</code>: {issue.message}</li>)}</ul>}{failure.uncertain && <p>Check <a href="#simulations">run history</a> before retrying. The server may have accepted this run even though its response was lost.</p>}</div>}
        {receipt && <div className="launch-receipt notice"><strong>Run accepted{priorRecipe ? " for a prior recipe" : ""}</strong><p>{receipt.name} · {receipt.mode === "carla" ? "CARLA" : "Synthetic test"}</p><AgentIdentity simulation={receipt.record.simulation} /><dl><div><dt>Run ID</dt><dd><code>{receipt.record.simulation.id}</code></dd></div><div><dt>Controller registration</dt><dd>{receipt.agentId ? <code>{receipt.agentId}</code> : "Baseline controller"}</dd></div><div><dt>Status at acceptance</dt><dd>{receipt.record.simulation.status}</dd></div><div><dt>Queue position at acceptance</dt><dd>{receipt.record.job?.queue_position ?? "Not available"}</dd></div></dl><p className="field-hint">The API accepted this run. This receipt does not track its subsequent progress or outcome.</p><a className="button secondary" href={simulationLink(receipt.record.simulation.id)}>Track this run</a></div>}
      </div>
    </section>
  );
}
