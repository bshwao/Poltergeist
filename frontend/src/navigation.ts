export type Page = "overview" | "scenarios" | "agents" | "simulations" | "failures";
export type Route = { page: Page; simulationId: string | null };
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function routeFromHash(hash: string): Route {
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
