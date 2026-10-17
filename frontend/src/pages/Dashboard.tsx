import { Icon } from "../components/Icon";
import { AnalyticsPanel } from "../components/AnalyticsPanel";
import type { Connection } from "../hooks/useWorkspace";
import type { TemplateInfo } from "../types";

export function Dashboard({ templates, connection, active = true }: { templates: TemplateInfo[]; connection: Connection; active?: boolean }) {
  return <>
    <header className="page-heading"><div><p className="eyebrow">WORKSPACE OVERVIEW</p><h1>Start with a scenario<span className="title-dot">.</span></h1><p className="page-description">A focused workspace for driving-safety experiments.</p></div></header>
    <section className="overview-hero panel">
      <div className="hero-copy"><span className="badge badge-neutral">REPEATABLE BY DESIGN</span><h2>Small changes.<br />Better questions.</h2><p>Explore a driving situation, adjust its conditions, and validate the recipe before taking it to the simulator.</p><a href="#scenarios" className="button primary">Open scenario workspace<Icon name="arrow" size={18} /></a></div>
      <div className="road-illustration" aria-hidden="true"><div className="road horizontal" /><div className="road vertical" /><span className="car car-ego" /><span className="car car-crossing" /><span className="road-marker">SCENARIO → RECIPE</span></div>
    </section>
    <div className="overview-cards">
      <section className="panel overview-card"><span className="card-icon"><Icon name="scenarios" /></span><p className="eyebrow">SCENARIO LIBRARY</p><strong className="stat-value">{templates.length > 0 ? String(templates.length).padStart(2, "0") : "—"}</strong><p>{templates.length > 0 ? "Templates with API-defined parameter bounds." : "Connect to the API to load available templates."}</p></section>
      <section className="panel overview-card"><span className="card-icon"><Icon name="activity" /></span><p className="eyebrow">API CONNECTION</p><strong className="stat-label">{connection === "online" ? "Connected" : connection === "checking" ? "Checking…" : "Unavailable"}</strong><p>API liveness only. Simulator readiness is checked when a run starts.</p></section>
      <section className="panel overview-card"><span className="card-icon"><Icon name="overview" /></span><p className="eyebrow">RECIPE VALIDATION</p><strong className="stat-label">Before you run</strong><p>Check structure, actor references, and template bindings with the backend.</p></section>
    </div>
    <AnalyticsPanel active={active} />
    <section className="panel workflow-panel"><div className="panel-header"><div><p className="eyebrow">THE WORKFLOW</p><h2>From an idea to a recipe</h2></div></div><ol className="workflow-list"><li><span>01</span><div><h3>Choose a situation</h3><p>Start with a bundled driving scenario.</p></div></li><li><span>02</span><div><h3>Adjust the conditions</h3><p>Set speeds, timing, weather, and a reproducible seed.</p></div></li><li><span>03</span><div><h3>Validate the recipe</h3><p>Inspect the full JSON and resolve validation errors.</p></div></li></ol></section>
  </>;
}
