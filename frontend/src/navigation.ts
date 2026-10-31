export type Page = "overview" | "lab" | "discovery" | "scenarios" | "agents" | "experiments" | "simulations" | "failures";
export type Route = { page: Page; simulationId: string | null; operationId?: string | null; planId?: string | null; suiteRunId?: string; focusFrame?: number; failureId?: string; experimentKind?: "reproduce" | "minimize" };
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function routeFromHash(hash: string): Route {
  const suite = /^#lab\/suite-run\/([0-9a-f-]+)$/i.exec(hash);
  if (suite && uuid.test(suite[1])) return { page: "lab", simulationId: null, suiteRunId: suite[1] };
  if (hash === "#lab") return { page: "lab", simulationId: null };
  if (hash === "#discovery" || hash.startsWith("#discovery/")) {
    const id = hash.slice("#discovery/".length);
    return { page: "discovery", simulationId: null, planId: uuid.test(id) ? id : null };
  }
  if (hash === "#experiments" || hash.startsWith("#experiments/")) {
    const parts = hash.slice(1).split("/");
    if ((parts[1] === "reproduce" || parts[1] === "minimize") && uuid.test(parts[2] ?? "") && parts.length === 3)
      return { page: "experiments", simulationId: null, failureId: parts[2], experimentKind: parts[1] };
    return { page: "experiments", simulationId: null, operationId: parts.length === 2 && uuid.test(parts[1]) ? parts[1] : null };
  }
  if (hash === "#overview") return { page: "overview", simulationId: null };
  if (hash === "#failures") return { page: "failures", simulationId: null };
  if (hash === "#agents") return { page: "agents", simulationId: null };
  if (hash === "#simulations" || hash.startsWith("#simulations/")) {
    const pieces = hash.slice(1).split("/");
    if (pieces.length === 4 && uuid.test(pieces[1]) && pieces[2] === "frame" && /^\d+$/.test(pieces[3]) && Number.isSafeInteger(Number(pieces[3])))
      return { page: "simulations", simulationId: pieces[1], focusFrame: Number(pieces[3]) };
    const id = hash.slice("#simulations/".length);
    return { page: "simulations", simulationId: uuid.test(id) ? id : null };
  }
  return { page: "scenarios", simulationId: null };
}

export function simulationFrameLink(id: string, frame: number): string {
  return uuid.test(id) && Number.isSafeInteger(frame) && frame >= 0 ? `#simulations/${id}/frame/${frame}` : simulationLink(id);
}

export function simulationLink(id: string): string {
  return uuid.test(id) ? `#simulations/${id}` : "#simulations";
}

export function suiteRunLink(id: string): string { return uuid.test(id) ? `#lab/suite-run/${id}` : "#lab"; }
