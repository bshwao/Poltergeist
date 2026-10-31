import { useEffect, useRef, useState } from "react";
import { api } from "../services/api";
import type { LabClient } from "../services/lab-client";
import type { GalleryScenario, SuiteCase } from "../types/generated";
import { useLabMutation, useLabReport } from "../hooks/useLab";
import { isTerminal, useSimulation } from "../hooks/useSimulation";
import { DEFAULT_EVALUATION, DEFAULT_OPTIONS } from "../components/launch-form";
import { ScenarioGallery, ScenarioIllustration } from "../components/ScenarioGallery";
import { ReplayHighlightsPanel } from "../components/ReplayHighlights";
import { SuiteManager } from "../components/SuiteManager";
import { simulationLink, simulationFrameLink } from "../navigation";
import "../lab.css";

function readGuidedRun(key: string): string | null {
  try { const value = window.localStorage?.getItem(key); return value && /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value) ? value : null; } catch { return null; }
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

export function GuidedLab({ active = true, client = api, contextKey = "local", runHref = (id, frame) => frame === undefined ? simulationLink(id) : simulationFrameLink(id, frame), modes = ["test", "carla"], suiteRunId = null }: { active?: boolean; client?: LabClient; contextKey?: string; runHref?: (id: string, frame?: number) => string; modes?: ("test" | "carla")[]; suiteRunId?: string | null }) {
  const [tab, setTab] = useState<"guide" | "gallery" | "suites">(suiteRunId ? "suites" : "guide");
  const [selected, setSelected] = useState<GalleryScenario | null>(null), [incoming, setIncoming] = useState<SuiteCase | null>(null);
  const [mode, setMode] = useState<"test" | "carla">("test"), [visitedSuites, setVisitedSuites] = useState(Boolean(suiteRunId));
  useEffect(() => { if (suiteRunId) { setTab("suites"); setVisitedSuites(true); } }, [suiteRunId]);
  const key = `poltergeist-guided-v1/${contextKey}`;
  const [runId, setRunId] = useState<string | null>(() => readGuidedRun(key));
  const currentKey = useRef(key);
  useEffect(() => { if (currentKey.current !== key) { currentKey.current = key; setRunId(readGuidedRun(key)); setSelected(null); } }, [key]);
  function remember(id: string | null) { setRunId(id); try { if (id) window.localStorage?.setItem(key, id); else window.localStorage?.removeItem(key); } catch { /* The current run remains available without browser storage. */ } }
  const gallery = useLabReport("gallery", active, (_, signal) => client.getGallery({ signal }), () => true);
  const launch = useLabMutation(active && tab === "guide");
  const state = useSimulation(runId, active && tab === "guide", client);
  const result = state.record?.result, status = state.progress?.status ?? state.record?.simulation.status;
  const ended = status ? isTerminal(status) : false;
  const locked = launch.busy || Boolean(runId);
  const choose = (entry: GalleryScenario) => { setSelected(entry); setTab("guide"); };
  const add = (entry: GalleryScenario) => { setIncoming({ name: entry.title, scenario: entry.scenario }); setVisitedSuites(true); setTab("suites"); };
  const tabs = [["guide", "First experiment"], ["gallery", "Scenario gallery"], ["suites", "Saved suites"]] as const;
  return <div className="lab-surface guided-lab"><header className="page-heading"><div><p className="eyebrow">A QUESTION. A SCENE. RECORDED EVIDENCE.</p><h1>Your experiment lab</h1><p>Start with one scene, understand the outcome, then keep the experiments worth repeating.</p></div><a className="button secondary" href="#discovery">Find a repeatable weakness</a></header>
    <div className="lab-tabs" role="tablist" aria-label="Experiment lab sections">{tabs.map(([value, label], index) => <button key={value} role="tab" tabIndex={tab === value ? 0 : -1} aria-selected={tab === value} aria-controls={`lab-${value}`} id={`lab-tab-${value}`} className={tab === value ? "active" : ""} onClick={() => { setTab(value); if (value === "suites") setVisitedSuites(true); }} onKeyDown={e => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
      e.preventDefault(); const next = e.key === "Home" ? 0 : e.key === "End" ? tabs.length - 1 : (index + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
      setTab(tabs[next][0]); if (tabs[next][0] === "suites") setVisitedSuites(true);
      (e.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>("[role=tab]")[next])?.focus();
    }}>{label}</button>)}</div>
    <div id="lab-guide" role="tabpanel" aria-labelledby="lab-tab-guide" hidden={tab !== "guide"} className="lab-stack">
      <ol className="lab-steps" aria-label="First experiment progress"><li data-complete={Boolean(selected || runId)}><span>1</span>Choose a scene</li><li data-complete={Boolean(result)}><span>2</span>Run one experiment</li><li data-complete={Boolean(result)}><span>3</span>Explain the evidence</li></ol>
      {!runId && <section className="panel"><div className="panel-header"><h2>1. Choose a driving question</h2><button className="button secondary small" onClick={() => setTab("gallery")}>Browse all scenarios</button></div><div className="panel-body"><p>A synthetic check is a quick way to learn the workflow. Choose CARLA when you are ready to measure driving in the simulator.</p>
        {gallery.loading && !gallery.value && <p role="status">Loading illustrated scenarios…</p>}{gallery.error && <p role="alert">{gallery.error} <button onClick={gallery.refresh}>Retry gallery</button></p>}
        {gallery.value && <ScenarioGallery entries={gallery.value} starterOnly choose={choose} disabled={locked} />}
      </div></section>}
      {(selected || runId) && <section className="panel"><div className="panel-header"><h2>2. Run one experiment</h2></div><div className="panel-body lab-form">
        {selected && !runId && <div className="lab-selected-scene"><ScenarioIllustration family={selected.family} /><div><h3>{selected.title}</h3><p>{selected.learning_goal}</p><small>{selected.scenario.map} · {selected.scenario.duration_s} simulated seconds · Seed {selected.scenario.seed}</small></div></div>}
        {!runId && <><label>First experiment mode<select value={mode} disabled={launch.busy} onChange={e => setMode(e.target.value as "test" | "carla")}><option value="test" disabled={!modes.includes("test")}>Synthetic check · learn the workflow</option><option value="carla" disabled={!modes.includes("carla")}>CARLA · measure simulator driving</option></select></label><p className="notice">{mode === "test" ? "This runs synthetic fixtures with the built-in baseline. Use its measurements to learn the review tools." : "This runs the built-in baseline in your local CARLA installation. Keep the scene's map loaded; setup and native cleanup add wall-clock time."}</p>
          <button className="button primary" disabled={launch.busy || !selected || !modes.includes(mode)} onClick={() => selected && void launch.run(signal => client.launchSimulation({ scenario: selected.scenario, scenario_id: null, mode,
            agent_id: null, options: { ...DEFAULT_OPTIONS }, evaluation_settings: { ...DEFAULT_EVALUATION } }, { signal }), record => remember(record.simulation.id))}>{launch.busy ? "Submitting your experiment…" : `Run one ${mode === "test" ? "synthetic" : "CARLA"} experiment`}</button></>}
        {runId && <><p>Guided run: <code>{runId}</code></p><p role="status">{status ? `Experiment ${status}` : "Loading your recorded experiment…"}</p>{!ended && <progress max={1} value={state.progress?.progress ?? state.record?.simulation.progress ?? 0} aria-label="Guided experiment progress" />}
          {state.error && <p role="alert">{state.error} <button onClick={state.refresh}>Refresh guided run</button></p>}{state.record?.simulation.error && <p role="alert">{state.record.simulation.error}</p>}
          {!ended && state.record?.job && <button className="button secondary" disabled={state.cancelling || Boolean(state.progress?.cancel_requested || state.record.job.cancel_requested)} onClick={() => void state.cancel()}>Cancel guided experiment</button>}{state.cancelError && <p role="alert">{state.cancelError}</p>}
          <a className="button secondary" href={runHref(runId)}>Open full run evidence</a>{ended && <button className="button secondary" onClick={() => remember(null)}>Choose another experiment</button>}
        </>}{launch.error && <p role="alert">{launch.error} <a href="#simulations">Review run history</a></p>}
      </div></section>}
      {result && runId && <><section className="panel"><div className="panel-header"><h2>3. Explain what was recorded</h2></div><div className="panel-body"><p>{result.simulation.mode === "test" ? "These measurements come from a synthetic fixture." : "These measurements come from CARLA."} Execution ended as <strong>{result.simulation.status}</strong>.</p>
        <div className="lab-key-figures"><div><span>Recorded contacts</span><strong>{result.metrics.collision_count ?? "Unknown"}</strong></div><div><span>Minimum time to collision</span><strong>{result.metrics.min_ttc_s === null ? "Not measured" : `${result.metrics.min_ttc_s.toFixed(2)} s`}</strong></div><div><span>Contact observations</span><strong>{result.metrics.coverage.total_frames ? `${result.metrics.coverage.collision_frames} / ${result.metrics.coverage.total_frames} frames` : "Not observed"}</strong></div></div>
        <p>Use the highlights below to connect each observation to a frame. Describe contacts, motion and controls using their recorded values; check missing coverage before interpreting an absence of events.</p><p className="field-hint">A completed run and a low heuristic score do not establish real-world driving safety.</p>
        <div className="button-row"><a className="button secondary" href={runHref(runId)}>Review controls, recordings and printable exports</a><button className="button secondary" onClick={() => {
          const scenario = state.record!.simulation.scenario;
          setIncoming({ name: selected?.title ?? scenario.template?.replace(/_/g, " ").replace(/\b\w/g, letter => letter.toUpperCase()) ?? scenario.name, scenario });
          setTab("suites"); setVisitedSuites(true);
        }}>Keep scenes for a comparison</button></div>
      </div></section><ReplayHighlightsPanel simulationId={runId} active={active && tab === "guide"} client={client} frameHref={runHref} /><GuidanceFeedbackForm key={runId} simulationId={runId} active={active && tab === "guide"} client={client} /></>}
    </div>
    <div id="lab-gallery" role="tabpanel" aria-labelledby="lab-tab-gallery" hidden={tab !== "gallery"}><h2 className="lab-section-title">Choose a situation to investigate</h2><p>Each illustrated scene carries a complete recipe, seed and map. Add the scenes you care about to a reusable suite.</p>{gallery.error && <p role="alert">{gallery.error}</p>}{gallery.value && <ScenarioGallery entries={gallery.value} choose={choose} add={add} disabled={locked} />}</div>
    <div id="lab-suites" role="tabpanel" aria-labelledby="lab-tab-suites" hidden={tab !== "suites"}>{visitedSuites && <SuiteManager active={active && tab === "suites"} incoming={incoming} client={client} runHref={runHref} modes={modes} initialRunId={suiteRunId} />}</div>
  </div>;
}
