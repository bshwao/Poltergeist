export type Page = "overview" | "scenarios" | "agents" | "experiments" | "simulations" | "failures";
export type Route = { page: Page; simulationId: string | null; operationId?: string | null; failureId?: string; experimentKind?: "reproduce" | "minimize" };
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function routeFromHash(hash: string): Route {
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
    const id = hash.slice("#simulations/".length);
    return { page: "simulations", simulationId: uuid.test(id) ? id : null };
  }
  return { page: "scenarios", simulationId: null };
}

export function simulationLink(id: string): string {
  return uuid.test(id) ? `#simulations/${id}` : "#simulations";
}
