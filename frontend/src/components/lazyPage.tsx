import { Component, Suspense, lazy, type ComponentProps, type ComponentType, type ReactNode } from "react";

class PageErrorBoundary extends Component<{ label: string; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (!this.state.failed) return this.props.children;
    return <section className="panel notice error-notice" role="alert">
      <h1>Unable to open {this.props.label}</h1>
      <p>The page could not load. You can still use the other workspace pages. Check your connection before reloading the dashboard.</p>
      <p>Copy any unsaved edits first; reloading clears the current drafts.</p>
      <button className="button secondary" onClick={() => window.location.reload()}>Reload dashboard</button>
    </section>;
  }
}

/** Load a page on first visit and isolate failures from the remaining workspace. */
export function lazyPage<Page extends ComponentType<any>>(load: () => Promise<{ default: Page }>, label: string) {
  const LoadedPage = lazy(load);
  return function DeferredPage(props: ComponentProps<Page>) {
    return <PageErrorBoundary label={label}>
      <Suspense fallback={<p role="status" className="notice">Loading {label}…</p>}><LoadedPage {...props} /></Suspense>
    </PageErrorBoundary>;
  };
}
