import { useEffect, useRef, useState, type RefObject } from "react";
import { api } from "../services/api";
import { GUIDED_SUBMISSION_PREFIX, type LabClient } from "../services/lab-client";
import type { GalleryScenario, SimulationLaunch, SuiteCase } from "../types/generated";
import { useLabMutation, useLabReport } from "../hooks/useLab";
import { isTerminal, useSimulation } from "../hooks/useSimulation";
import { DEFAULT_EVALUATION, DEFAULT_OPTIONS } from "../components/launch-form";
import { ScenarioGallery, ScenarioIllustration } from "../components/ScenarioGallery";
import { ReplayHighlightsPanel } from "../components/ReplayHighlights";
import { EvidenceAnalystPanel } from "../components/EvidenceAnalystPanel";
import { SuiteManager } from "../components/SuiteManager";
import { simulationLink, simulationFrameLink } from "../navigation";
import "../lab.css";

const uuid = /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
type GuidedSubmission = { id: string; input: SimulationLaunch };

function readGuidedRun(key: string): string | null {
  try { const value = window.localStorage?.getItem(key); return value && uuid.test(value) ? value : null; } catch { return null; }
}

function readSubmission(key: string): GuidedSubmission | null {
  try {
    const value = JSON.parse(window.sessionStorage?.getItem(key) ?? "null") as Partial<GuidedSubmission> | null;
    return typeof value?.id === "string" && uuid.test(value.id) && api.validateGuidedLaunch(value.input)
      ? { id: value.id, input: value.input } : null;
  } catch { return null; }
}

function focusAfterRender(target: RefObject<HTMLElement>) {
  window.requestAnimationFrame?.(() => {
    const element = target.current;
    if (element?.getClientRects().length) element.focus();
  });
}

export function GuidanceFeedbackForm({ simulationId, active, client = api }: { simulationId: string; active: boolean; client?: Pick<LabClient, "saveGuidanceFeedback"> }) {
  const [explain, setExplain] = useState(""), [confusion, setConfusion] = useState(""), [returnReason, setReturnReason] = useState("");
  const [consent, setConsent] = useState(false), [saved, setSaved] = useState(false);
  const mutation = useLabMutation(active);
  return <section className="panel"><div className="panel-header"><h2>Help improve the next experiment</h2></div><form className="panel-body lab-form" onSubmit={e => {
    e.preventDefault(); if (!explain || !consent) return;
    void mutation.run(signal => client.saveGuidanceFeedback({ simulation_id: simulationId, could_explain_outcome: explain === "yes", confusing_step: confusion, reason_to_return: returnReason, consent_to_save: true }, { signal }), () => setSaved(true));
  }}>
    <p>Describe the recorded outcome in your own words. Optional feedback is saved with this workspace when you choose to submit it.</p>
    {saved ? <p className="notice" role="status">Feedback saved. Thank you for helping improve this workflow.</p> : <fieldset disabled={mutation.busy}>
      <label>Can you explain the recorded outcome?<select value={explain} onChange={e => setExplain(e.target.value)} required><option value="">Choose an answer</option><option value="yes">Yes, using the measurements and coverage</option><option value="no">Not yet; I need clearer guidance</option></select></label>
      <label>Which step or result was confusing?<textarea rows={3} value={confusion} onChange={e => setConfusion(e.target.value)} maxLength={1000} /></label><label>What would bring you back for another experiment?<textarea rows={2} value={returnReason} onChange={e => setReturnReason(e.target.value)} maxLength={1000} /></label>
      <label className="lab-check"><input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} required />Save this feedback with the experiment</label><button className="button primary" disabled={!consent || !explain} type="submit">Save first-run feedback</button>
    </fieldset>}{mutation.error && <p role="alert">{mutation.error}</p>}
  </form></section>;
}

export function GuidedLab({ active = true, client = api, contextKey = "local", runHref = (id, frame) => frame === undefined ? simulationLink(id) : simulationFrameLink(id, frame), modes = ["test", "carla"], launchMaxTicks = DEFAULT_OPTIONS.max_ticks, historyHref = "#simulations", suiteRunId = null, reviewAvailable = true }: { active?: boolean; client?: LabClient; contextKey?: string; runHref?: (id: string, frame?: number) => string; modes?: ("test" | "carla")[]; launchMaxTicks?: number; historyHref?: string | null; suiteRunId?: string | null; reviewAvailable?: boolean }) {
  const [tab, setTab] = useState<"guide" | "gallery" | "suites">(suiteRunId ? "suites" : "guide");
  const [selected, setSelected] = useState<GalleryScenario | null>(null), [incoming, setIncoming] = useState<SuiteCase | null>(null);
  const [mode, setMode] = useState<"test" | "carla">("test"), [visitedSuites, setVisitedSuites] = useState(Boolean(suiteRunId));
  const galleryTab = useRef<HTMLButtonElement>(null), suitesTab = useRef<HTMLButtonElement>(null);
  const chooseHeading = useRef<HTMLHeadingElement>(null), runHeading = useRef<HTMLHeadingElement>(null), galleryHeading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { if (suiteRunId) { setTab("suites"); setVisitedSuites(true); } }, [suiteRunId]);
  const key = `poltergeist-guided-v1/${contextKey}`;
  const submissionKey = `${GUIDED_SUBMISSION_PREFIX}${contextKey}`;
  const [submission, setSubmission] = useState(() => readSubmission(submissionKey));
  const [submissionStored, setSubmissionStored] = useState(true);
  const [runId, setRunId] = useState<string | null>(() => submission ? null : readGuidedRun(key));
  const currentKey = useRef(key);
  useEffect(() => {
    if (currentKey.current === key) return;
    currentKey.current = key;
    const saved = readSubmission(submissionKey);
    setSubmission(saved);
    setSubmissionStored(true);
    setRunId(saved ? null : readGuidedRun(key));
    setSelected(null);
  }, [key, submissionKey]);
  function remember(id: string | null) {
    setRunId(id);
    try {
      if (id) window.localStorage?.setItem(key, id);
      else if (window.localStorage?.getItem(key) === runId) window.localStorage?.removeItem(key);
    } catch { /* The current run remains available without browser storage. */ }
  }
  const gallery = useLabReport("gallery", active, (_, signal) => client.getGallery({ signal }), () => true);
  const launch = useLabMutation(active && tab === "guide", "Retry the saved submission to recover the original acknowledgement.");
  const state = useSimulation(runId, active && tab === "guide", client);
  const result = state.recordMissing ? null : state.record?.result;
  const status = state.progress?.status ?? state.record?.simulation.status;
  const ended = status ? isTerminal(status) : false;
  const locked = launch.busy || Boolean(runId || submission);
  const currentStep = result ? 3 : selected || runId || submission ? 2 : 1;
  const choose = (entry: GalleryScenario) => { setSelected(entry); setTab("guide"); focusAfterRender(runHeading); };
  function chooseAnother() { setSelected(null); remember(null); focusAfterRender(chooseHeading); }
  function rememberSubmission(value: GuidedSubmission | null) {
    setSubmission(value);
    try {
      if (value) window.sessionStorage.setItem(submissionKey, JSON.stringify(value));
      else window.sessionStorage?.removeItem(submissionKey);
      setSubmissionStored(true);
    } catch { setSubmissionStored(false); }
  }
  function submitGuided() {
    const input = submission?.input ?? (selected ? { scenario: selected.scenario, scenario_id: null, mode,
      agent_id: null, options: { ...DEFAULT_OPTIONS, max_ticks: launchMaxTicks }, evaluation_settings: { ...DEFAULT_EVALUATION } } : null);
    if (!input || !modes.includes(input.mode)) return;
    void launch.run(signal => {
      const saved = submission ?? { id: crypto.randomUUID(), input };
      if (!submission) rememberSubmission(saved);
      focusAfterRender(runHeading);
      return client.launchGuidedSimulation(saved.input, saved.id, { signal });
    }, record => {
      remember(record.simulation.id);
      rememberSubmission(null);
      focusAfterRender(runHeading);
    });
  }
  function retryGallery() { gallery.refresh(); focusAfterRender(tab === "gallery" ? galleryHeading : chooseHeading); }
  const galleryStatus = <>
    {gallery.loading && !gallery.value && <p role="status">Loading illustrated scenarios…</p>}
    {gallery.error && <p role="alert">{gallery.error} <button type="button" onClick={retryGallery}>Retry gallery</button></p>}
  </>;
  function openSuite(draft: SuiteCase) {
    setIncoming(draft);
    setVisitedSuites(true);
    setTab("suites");
    focusAfterRender(suitesTab);
  }
  const add = (entry: GalleryScenario) => openSuite({ name: entry.title, scenario: entry.scenario });
  const tabs = [["guide", "First experiment"], ["gallery", "Scenario gallery"], ["suites", "Saved suites"]] as const;
  return <div className="lab-surface guided-lab"><header className="page-heading"><div><p className="eyebrow">A QUESTION. A SCENE. RECORDED EVIDENCE.</p><h1>Your experiment lab</h1><p>Start with one scene, understand the outcome, then keep the experiments worth repeating.</p></div><a className="button secondary" href="#discovery">Find a repeatable weakness</a></header>
    <div className="lab-tabs" role="tablist" aria-label="Experiment lab sections">{tabs.map(([value, label], index) => <button key={value} ref={value === "gallery" ? galleryTab : value === "suites" ? suitesTab : undefined} role="tab" tabIndex={tab === value ? 0 : -1} aria-selected={tab === value} aria-controls={`lab-${value}`} id={`lab-tab-${value}`} className={tab === value ? "active" : ""} onClick={() => { setTab(value); if (value === "suites") setVisitedSuites(true); }} onKeyDown={e => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
      e.preventDefault(); const next = e.key === "Home" ? 0 : e.key === "End" ? tabs.length - 1 : (index + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
      setTab(tabs[next][0]); if (tabs[next][0] === "suites") setVisitedSuites(true);
      (e.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>("[role=tab]")[next])?.focus();
    }}>{label}</button>)}</div>
    <div id="lab-guide" role="tabpanel" aria-labelledby="lab-tab-guide" hidden={tab !== "guide"} className="lab-stack">
      <ol className="lab-steps" aria-label="First experiment progress">
        {["Choose a scene", "Run one experiment", "Explain the evidence"].map((label, index) => <li key={label} aria-current={currentStep === index + 1 ? "step" : undefined} data-complete={currentStep > index + 1}><span>{index + 1}</span>{label}</li>)}
      </ol>
      {!runId && !submission && <section className="panel"><div className="panel-header"><h2 ref={chooseHeading} tabIndex={-1}>1. Choose a driving question</h2><button className="button secondary small" onClick={() => { setTab("gallery"); focusAfterRender(galleryTab); }}>Browse all scenarios</button></div><div className="panel-body"><p>A synthetic check is a quick way to learn the workflow. Choose CARLA when you are ready to measure driving in the simulator.</p>
        {galleryStatus}
        {gallery.value && <ScenarioGallery entries={gallery.value} starterOnly choose={choose} disabled={locked} />}
      </div></section>}
      {(selected || runId || submission) && <section className="panel"><div className="panel-header"><h2 ref={runHeading} tabIndex={-1}>2. Run one experiment</h2></div><div className="panel-body lab-form">
        {selected && !runId && !submission && <div className="lab-selected-scene"><ScenarioIllustration family={selected.family} /><div><h3>{selected.title}</h3><p>{selected.learning_goal}</p><small>{selected.scenario.map} · {selected.scenario.duration_s} simulated seconds · Seed {selected.scenario.seed}</small></div></div>}
        {!runId && !submission && <><label>First experiment mode<select value={mode} disabled={launch.busy} onChange={e => setMode(e.target.value as "test" | "carla")}><option value="test" disabled={!modes.includes("test")}>Synthetic check · learn the workflow</option><option value="carla" disabled={!modes.includes("carla")}>CARLA · measure simulator driving</option></select></label><p className="notice">{mode === "test" ? "This runs synthetic fixtures with the built-in baseline. Use its measurements to learn the review tools." : "This runs the built-in baseline in your local CARLA installation. Keep the scene's map loaded; setup and native cleanup add wall-clock time."}</p>
          <button className="button primary" disabled={launch.busy || !selected || !modes.includes(mode)} onClick={submitGuided}>{`Run one ${mode === "test" ? "synthetic" : "CARLA"} experiment`}</button></>}
        {!runId && submission && <>
          <p>Saved {submission.input.mode === "test" ? "synthetic" : "CARLA"} submission for <strong>{submission.input.scenario?.name}</strong>.</p>
          {!submissionStored && <p role="status">Keep this tab open to retry this request.</p>}
          {launch.busy ? <p role="status">Submitting your experiment…</p> : <>
            <p>Retry uses the saved recipe and request. If the worker already accepted it, the original run opens.</p>
            {!modes.includes(submission.input.mode) && <p role="status">The saved mode needs approval on this computer.</p>}
            <button className="button primary" disabled={!modes.includes(submission.input.mode)} onClick={submitGuided}>Retry saved submission</button>
            <p>Discard clears the saved request from this tab. Any accepted run stays on the worker.</p>
            <button className="button secondary" onClick={() => { rememberSubmission(null); launch.setError(null); chooseAnother(); }}>Discard saved submission</button>
          </>}
        </>}
        {runId && <><p>Guided run: <code>{runId}</code></p>
          {!state.recordMissing && <>
            <p role="status">{status ? `Experiment ${status}` : state.loading ? "Loading your recorded experiment…" : "Guided run status unavailable."}</p>
            {!ended && (status || state.loading) && <progress max={1} value={state.progress?.progress ?? state.record?.simulation.progress} aria-label="Guided experiment progress" />}
            {state.record?.simulation.error && <p role="alert">{state.record.simulation.error}</p>}
            {!ended && state.record?.job && <button className="button secondary" disabled={state.cancelling || Boolean(state.progress?.cancel_requested || state.record.job.cancel_requested)} onClick={() => void state.cancel()}>Cancel guided experiment</button>}
            {state.cancelError && <p role="alert">{state.cancelError}</p>}
            <a className="button secondary" href={runHref(runId)}>Open full run evidence</a>
          </>}
          {state.error && <p role="alert">{state.recordMissing ? "This saved guided run is unavailable in this workspace." : state.error} <button onClick={state.refresh}>Refresh guided run</button></p>}
          {(ended || state.recordMissing) && <button className="button secondary" onClick={chooseAnother}>Choose another experiment</button>}
        </>}{launch.error && <p role="alert">{launch.error}{historyHref && <> <a href={historyHref}>Review run history</a></>}</p>}
      </div></section>}
      {result && runId && <><section className="panel"><div className="panel-header"><h2>3. Explain what was recorded</h2></div><div className="panel-body"><p>{result.simulation.mode === "test" ? "These measurements come from a synthetic fixture." : "These measurements come from CARLA."} Execution ended as <strong>{result.simulation.status}</strong>.</p>
        <div className="lab-key-figures"><div><span>Recorded contacts</span><strong>{result.metrics.collision_count ?? "Unknown"}</strong></div><div><span>Minimum time to collision</span><strong>{result.metrics.min_ttc_s === null ? "Not measured" : `${result.metrics.min_ttc_s.toFixed(2)} s`}</strong></div><div><span>Contact observations</span><strong>{result.metrics.coverage.total_frames ? `${result.metrics.coverage.collision_frames} / ${result.metrics.coverage.total_frames} frames` : "Not observed"}</strong></div></div>
        <p>Use the highlights below to connect each observation to a frame. Describe contacts, motion and controls using their recorded values; check missing coverage before interpreting an absence of events.</p><p className="field-hint">A completed run and a low heuristic score do not establish real-world driving safety.</p>
        <div className="button-row"><a className="button secondary" href={runHref(runId)}>Review controls, recordings and printable exports</a><button className="button secondary" onClick={() => {
          const scenario = state.record!.simulation.scenario;
          openSuite({ name: selected?.title ?? scenario.template?.replace(/_/g, " ").replace(/\b\w/g, letter => letter.toUpperCase()) ?? scenario.name, scenario });
        }}>Keep scenes for a comparison</button></div>
      </div></section><ReplayHighlightsPanel simulationId={runId} active={active && tab === "guide"} client={client} frameHref={runHref} /><EvidenceAnalystPanel key={`analyst-${runId}`} simulationId={runId} active={active && tab === "guide"} available={reviewAvailable} client={client} frameHref={(id, frame) => runHref(id, frame)} /><GuidanceFeedbackForm key={runId} simulationId={runId} active={active && tab === "guide"} client={client} /></>}
    </div>
    <div id="lab-gallery" role="tabpanel" aria-labelledby="lab-tab-gallery" hidden={tab !== "gallery"}><h2 className="lab-section-title" ref={galleryHeading} tabIndex={-1}>Choose a situation to investigate</h2><p>Each illustrated scene carries a complete recipe, seed and map. Add the scenes you care about to a reusable suite.</p>{galleryStatus}{gallery.value && <ScenarioGallery entries={gallery.value} choose={choose} add={add} disabled={locked} />}</div>
    <div id="lab-suites" role="tabpanel" aria-labelledby="lab-tab-suites" hidden={tab !== "suites"}>{visitedSuites && <SuiteManager active={active && tab === "suites"} incoming={incoming} client={client} runHref={runHref} modes={modes} initialRunId={suiteRunId} reviewAvailable={reviewAvailable} />}</div>
  </div>;
}
