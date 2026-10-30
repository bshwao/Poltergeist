import { useCallback, useEffect, useRef, useState } from "react";
import { Icon } from "./components/Icon";
import { SkipLink } from "./components/SkipLink";
import { useWorkspace } from "./hooks/useWorkspace";
import { Dashboard } from "./pages/Dashboard";
import { Scenarios } from "./pages/Scenarios";
import { Simulations } from "./pages/Simulations";
import { Failures } from "./pages/Failures";
import { Agents } from "./pages/Agents";
import { Experiments } from "./pages/Experiments";
import { GuidedLab } from "./pages/GuidedLab";
import { Discovery } from "./pages/Discovery";
import { routeFromHash } from "./navigation";
import type { Page } from "./navigation";
import { API_BASE_URL } from "./services/api";
import "./styles.css";

const currentRoute = () => routeFromHash(window.location.hash);
const pageLabels: Record<Page, string> = { overview: "Overview", lab: "Guided lab", discovery: "Discovery", scenarios: "Scenarios", agents: "Agents", experiments: "Experiments", simulations: "Simulations", failures: "Failures" };

export default function App() {
  const [route, setRoute] = useState(currentRoute);
  const [visited, setVisited] = useState<Set<Page>>(() => new Set([route.page]));
  const page = route.page;
  const main = useRef<HTMLElement>(null);
  const workspace = useWorkspace();
  const [registryRevision, setRegistryRevision] = useState(0);
  const registryChanged = useCallback(() => setRegistryRevision(value => value + 1), []);

  useEffect(() => { document.title = `Poltergeist · ${pageLabels[page]}`; }, [page]);

  useEffect(() => {
    function navigate() {
      const next = currentRoute();
      setRoute(next);
      setVisited(previous => new Set([...previous, next.page]));
      main.current?.focus();
      window.scrollTo(0, 0);
    }
    window.addEventListener("hashchange", navigate);
    return () => window.removeEventListener("hashchange", navigate);
  }, []);

  return <div className="app-shell">
    <SkipLink target="main-content" />
    <aside className="sidebar">
      <a href="#overview" className="brand" aria-label="Poltergeist overview"><span className="brand-mark"><Icon name="ghost" size={24} /></span><span>poltergeist<span className="brand-caption">DRIVING SAFETY LAB</span></span></a>
      <div className="sidebar-label">WORKSPACE</div>
      <nav className="main-nav" aria-label="Main navigation">{(["overview", "lab", "scenarios", "agents", "experiments", "discovery", "simulations", "failures"] as Page[]).map(item => <a key={item} href={`#${item}`} className={page === item ? "active" : ""} aria-current={page === item ? "page" : undefined}><Icon name={item} /><span>{pageLabels[item]}</span>{item === "scenarios" && <span className="nav-count">{workspace.templates.length || "—"}</span>}</a>)}</nav>
      <div className="sidebar-note"><span className="sidebar-note-icon"><Icon name="activity" size={19} /></span><strong>Make the unexpected<br />repeatable.</strong><p>Build precise scenarios.<br />Ask better safety questions.</p></div>
      <div className="sidebar-footer"><span className="local-dot" /> Local development<span className="version-label">v0.1</span></div>
    </aside>
    <div className="workspace-content">
      <div className="topbar"><div className="breadcrumb">Workspace<span>/</span><strong>{pageLabels[page]}</strong></div><div className="topbar-right"><span className={`connection-pill connection-${workspace.connection}`} title="API liveness; does not indicate CARLA readiness" role="status"><span className="status-dot" />{workspace.connection === "online" ? "API connected" : workspace.connection === "checking" ? "Checking API" : "API unavailable"}</span><button type="button" className="icon-button" onClick={workspace.refresh} disabled={workspace.loading} aria-label="Refresh API connection"><Icon name="refresh" size={17} /></button></div></div>
      <main id="main-content" className="main-content" ref={main} tabIndex={-1}>
        {workspace.connection === "offline" && <section className="notice error-notice connection-notice" aria-label="API connection" role="status"><div><strong>The local API is unavailable</strong><p>Start the local backend, then retry the connection. Saved work appears when it reconnects.</p></div><button className="button secondary" onClick={workspace.refresh} disabled={workspace.loading}>Retry connection</button></section>}
        <div hidden={page !== "overview"}><Dashboard active={page === "overview"} /></div>
        {visited.has("lab") && <div hidden={page !== "lab"}><GuidedLab active={page === "lab"} contextKey={API_BASE_URL} suiteRunId={route.suiteRunId ?? null} /></div>}
        {visited.has("discovery") && <div hidden={page !== "discovery"}><Discovery active={page === "discovery"} planId={route.planId ?? null} /></div>}
        <div hidden={page !== "scenarios"}><Scenarios templates={workspace.templates} loading={workspace.loading} error={workspace.error} onRetry={workspace.refresh} active={page === "scenarios"} registryRevision={registryRevision} /></div>
        {visited.has("agents") && <div hidden={page !== "agents"}><Agents active={page === "agents"} onRegistryChange={registryChanged} /></div>}
        {visited.has("simulations") && <div hidden={page !== "simulations"}><Simulations active={page === "simulations"} selectedId={route.simulationId} focusFrame={route.focusFrame} /></div>}
        {visited.has("experiments") && <div hidden={page !== "experiments"}><Experiments active={page === "experiments"} selectedId={route.operationId ?? null} sourceFailureId={route.failureId} initialKind={route.experimentKind} templates={workspace.templates} registryRevision={registryRevision} /></div>}
        {visited.has("failures") && <div hidden={page !== "failures"}><Failures active={page === "failures"} /></div>}
        <footer className="workspace-footer"><span>POLTERGEIST <span className="footer-divider">/</span> Driving safety workspace</span><span className="api-address">API <code>{API_BASE_URL}</code></span></footer>
      </main>
    </div>
  </div>;
}
