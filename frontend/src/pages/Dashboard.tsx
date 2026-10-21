import { Icon } from "../components/Icon";
import { AnalyticsPanel } from "../components/AnalyticsPanel";
import { ReadinessPanel } from "../components/ReadinessPanel";

export function Dashboard({ active = true }: { active?: boolean }) {
  return <div className="overview-page">
    <header className="page-heading"><div><p className="eyebrow">WORKSPACE OVERVIEW</p><h1>Start with a scenario<span className="title-dot">.</span></h1><p className="page-description">A focused workspace for driving-safety experiments.</p></div></header>
    <ReadinessPanel active={active} />
    <section className="overview-hero panel">
      <div className="hero-copy"><span className="badge badge-neutral">BUILD → RUN → REVIEW</span><h2>Build a scenario.<br />Run it in CARLA.</h2><p>Choose an intersection, pedestrian crossing, or sudden braking event. Adjust the conditions and inspect the recorded outcome.</p><a href="#scenarios" className="button primary">Open scenario workspace<Icon name="arrow" size={18} /></a></div>
      <div className="road-illustration" aria-hidden="true"><div className="road horizontal"><span className="car car-crossing" /></div><div className="road vertical"><span className="car car-ego" /></div><span className="road-marker">SCENARIO → RECIPE</span></div>
    </section>
    <section className="panel workflow-panel" aria-labelledby="workflow-heading">
      <div className="panel-header"><div><p className="eyebrow">GET STARTED</p><h2 id="workflow-heading">From setup to results</h2></div></div>
      <ol className="workflow-list">
        <li><span>01</span><div><h3>Define the situation</h3><p>Choose a template and set speeds, crossing delays, and weather.</p><a className="workflow-link" href="#scenarios">Create a scenario<Icon name="arrow" size={14} /></a></div></li>
        <li><span>02</span><div><h3>Run and observe</h3><p>Launch in CARLA, then follow progress and inspect telemetry.</p><a className="workflow-link" href="#simulations">View runs<Icon name="arrow" size={14} /></a></div></li>
        <li><span>03</span><div><h3>Investigate the result</h3><p>Review collisions, rule violations, and recorded failure evidence.</p><a className="workflow-link" href="#failures">Explore failures<Icon name="arrow" size={14} /></a></div></li>
      </ol>
    </section>
    <AnalyticsPanel active={active} />
  </div>;
}
