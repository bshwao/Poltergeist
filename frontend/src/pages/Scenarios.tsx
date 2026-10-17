import { ScenarioBuilder } from "../components/ScenarioBuilder";
import { Icon } from "../components/Icon";
import type { TemplateInfo } from "../types";

interface ScenariosProps {
  templates: TemplateInfo[];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}

export function Scenarios({ templates, loading, error, onRetry }: ScenariosProps) {
  return <>
    <header className="page-heading">
      <div><p className="eyebrow">SCENARIO DESIGN</p><h1>Scenario workspace<span className="title-dot">.</span></h1>
        <p className="page-description">Set the conditions. Build a repeatable recipe. Know exactly what you’ll test.</p></div>
      <span className="workspace-tag"><span className="status-dot" /> Local workspace</span>
    </header>
    {error && <div className="notice error-notice connection-notice" role="alert">
      <div><strong>Couldn’t load scenario templates</strong><p>{error}</p><p className="muted">Start the backend, then check the API address and its allowed frontend origins.</p></div>
      <button type="button" className="button secondary" onClick={onRetry} disabled={loading}><Icon name="refresh" size={16} />Retry connection</button>
    </div>}
    {templates.length > 0 ? <ScenarioBuilder templates={templates} /> : loading ? <section className="panel loading-panel" role="status" aria-live="polite">
      <span className="spinner" /><h2>Loading the scenario library</h2><p className="muted">Reading templates and parameter bounds from your API.</p>
    </section> : <section className="panel empty-panel"><Icon name="scenarios" size={32} /><h2>Your scenario library is unavailable</h2><p>Connect to the backend to choose a template and build a recipe.</p></section>}
  </>;
}
