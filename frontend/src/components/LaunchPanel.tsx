import { useEffect, useId, useRef, useState } from "react";
import type { FormEvent } from "react";
import { api, ApiError } from "../services/api";
import type { QueueStatus, Scenario, SimulationRecord } from "../types/generated";
import {
  acceptanceMayBeUncertain, defaultEvaluationDraft, defaultOptionDraft, EVALUATION_FIELDS,
  makeLaunchRequest, OPTION_FIELDS,
} from "./launch-form";
import type { RunMode } from "./launch-form";
import { simulationLink } from "../navigation";

type Receipt = { record: SimulationRecord; recipeJson: string; name: string; mode: RunMode };
type Failure = { message: string; uncertain: boolean; issues: { path: string; message: string }[] };

export function LaunchPanel({ scenario, disabledReason }: { scenario: Scenario | null; disabledReason: string | null }) {
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
  const submitRequest = useRef<AbortController | null>(null);
  const queueRequest = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const failureSummary = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      submitRequest.current?.abort();
      queueRequest.current?.abort();
    };
  }, []);

  const built = makeLaunchRequest(scenario, mode, options, evaluation);
  const errors = built.errors ?? {};
  const errorFor = (key: string) => showErrors || touched[key]
    || (key === "evaluation_settings.ttc_horizon_s" && touched["evaluation_settings.ttc_warning_s"])
    || (key === "evaluation_settings.braking_full_severity_mps2" && touched["evaluation_settings.hard_braking_threshold_mps2"])
    ? errors[key] : undefined;
  const reason = disabledReason || (!scenario ? "Generate or validate the current recipe before launching." : null);
  const priorRecipe = receipt !== null && (!scenario || disabledReason !== null || receipt.recipeJson !== JSON.stringify(scenario));

  function chooseMode(next: RunMode) {
    setMode(next);
    if (next === "test") {
      setCaptureCleared(options.camera || options.lidar);
      setOptions(current => ({ ...current, camera: false, lidar: false }));
    } else setCaptureCleared(false);
  }

  async function checkQueue() {
    if (checkingQueue || queueRequest.current) return;
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
    if (submitting || submitRequest.current || reason) return;
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
    const snapshot = { recipeJson: JSON.stringify(input.scenario), name: input.scenario!.name, mode: input.mode };
    const controller = new AbortController();
    submitRequest.current = controller;
    setSubmitting(true);
    try {
      const record = await api.launchSimulation(input, { signal: controller.signal });
      if (mounted.current && !controller.signal.aborted) setReceipt({ record, ...snapshot });
    } catch (error) {
      if (!mounted.current || controller.signal.aborted) return;
      setFailure(error instanceof ApiError
        ? { message: error.message, uncertain: acceptanceMayBeUncertain(error.status), issues: error.issues }
        : { message: "The launch response could not be confirmed.", uncertain: true, issues: [] });
    } finally {
      if (submitRequest.current === controller) submitRequest.current = null;
      if (mounted.current && !controller.signal.aborted) setSubmitting(false);
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
            <button className="mode-card" type="button" aria-pressed={mode === "carla"} onClick={() => chooseMode("carla")}><strong>CARLA</strong><span>Run against the configured CARLA server. Requires a reachable simulator and the requested map.</span></button>
            <button className="mode-card" type="button" aria-pressed={mode === "test"} onClick={() => chooseMode("test")}><strong>Synthetic test</strong><span>Exercise the pipeline with synthetic telemetry. No CARLA connection or sensor capture.</span></button>
          </div>
          {errorFor("mode") && <p className="field-error launch-field-message" id={`${prefix}-mode-error`}>{errors.mode}</p>}
          {captureCleared && <p className="notice">Camera and LiDAR capture were turned off when you selected synthetic test mode.</p>}

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
        {showErrors && Object.keys(errors).length > 0 && !reason && <div className="notice error-notice" role="alert" tabIndex={-1} ref={failureSummary}>Check the highlighted launch settings before submitting.</div>}
        <div className="form-actions"><button className="button primary" type="submit" disabled={Boolean(reason) || submitting}>{submitting ? "Submitting run…" : "Launch simulation"}<span aria-hidden="true">↗</span></button><span className="muted">Runs use one queue shared by this API.</span></div>
      </form>

      <div className="queue-summary">
        <div className="queue-heading"><strong>Queue availability</strong><button className="button text-button" type="button" disabled={checkingQueue} onClick={checkQueue}>{checkingQueue ? "Checking…" : "Check queue"}</button></div>
        <div aria-live="polite">{queueError ? <p className="field-error" role="alert">{queueError}</p> : queue ? <p>{queue.accepting ? "API accepting submissions" : "API not accepting submissions"} · {queue.pending} waiting · {queue.active_id ? "1 active run" : "No active run"}{queue.error ? " · Worker requires attention" : ""}</p> : <p className="muted">Queue state has not been checked.</p>}</div>
        <p className="field-hint">This is an advisory snapshot. Queue acceptance does not confirm that CARLA is connected or that a run will succeed.</p>
      </div>

      <div aria-live="polite" aria-atomic="true">
        {failure && <div className="notice error-notice" role="alert"><strong>{failure.uncertain ? "Run acceptance is uncertain" : "Run was not accepted"}</strong><p>{failure.message}</p>{failure.issues.length > 0 && <ul>{failure.issues.map((issue, index) => <li key={index}><code>{issue.path || "launch"}</code>: {issue.message}</li>)}</ul>}{failure.uncertain && <p>Check <a href="#simulations">run history</a> before retrying. The server may have accepted this run even though its response was lost.</p>}</div>}
        {receipt && <div className="launch-receipt notice"><strong>Run accepted{priorRecipe ? " for a prior recipe" : ""}</strong><p>{receipt.name} · {receipt.mode === "carla" ? "CARLA" : "Synthetic test"}</p><dl><div><dt>Run ID</dt><dd><code>{receipt.record.simulation.id}</code></dd></div><div><dt>Status at acceptance</dt><dd>{receipt.record.simulation.status}</dd></div><div><dt>Queue position at acceptance</dt><dd>{receipt.record.job?.queue_position ?? "Not available"}</dd></div></dl><p className="field-hint">The API accepted this run. This receipt does not track its subsequent progress or outcome.</p><a className="button secondary" href={simulationLink(receipt.record.simulation.id)}>Track this run</a></div>}
      </div>
    </section>
  );
}
