import type { ReadinessReport } from "../types/generated";

const statuses = new Set(["ready", "missing", "unsupported", "not_running", "not_checked", "unknown", "error"]);
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const nonempty = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;

/** Fail closed on incompatible snapshots rather than displaying false readiness. */
export function isReadinessReport(value: unknown): value is ReadinessReport {
  if (!object(value) || value.schema_version !== "poltergeist-readiness-v1" || value.scope !== "local_core"
      || typeof value.carla_connection_requested !== "boolean" || typeof value.checked_at !== "string"
      || !/(?:Z|[+-]\d{2}:\d{2})$/.test(value.checked_at) || !Number.isFinite(Date.parse(value.checked_at))) return false;
  for (const [mode, ids] of [["synthetic", ["runtime", "packages", "configuration", "storage", "execution"]],
    ["carla", ["client", "installation", "connection", "server_version", "execution"]]] as const) {
    const workflow = value[mode];
    if (!object(workflow) || typeof workflow.status !== "string" || !statuses.has(workflow.status) || !nonempty(workflow.summary) || !Array.isArray(workflow.checks)
        || workflow.checks.length !== ids.length) return false;
    const seen = new Set<string>();
    for (const check of workflow.checks) {
      if (!object(check) || !nonempty(check.id) || !(ids as readonly string[]).includes(check.id) || seen.has(check.id)
          || !nonempty(check.label) || typeof check.status !== "string" || !statuses.has(check.status) || !nonempty(check.detail)
          || !(check.next_step === null || nonempty(check.next_step))) return false;
      seen.add(check.id);
      if (check.id === "execution" && check.status !== "not_checked") return false;
      if (mode === "carla" && !value.carla_connection_requested && ["connection", "server_version"].includes(check.id)
          && check.status !== "not_checked") return false;
    }
    if (workflow.status === "ready") {
      const required = mode === "synthetic" ? ["runtime", "packages", "configuration", "storage"] : ["client", "connection", "server_version"];
      if (workflow.checks.some(check => required.includes(check.id) && check.status !== "ready")) return false;
      if (mode === "carla" && (!object(value.synthetic) || value.synthetic.status !== "ready")) return false;
    }
    if (mode === "carla" && workflow.status === "ready" && !value.carla_connection_requested) return false;
  }
  return true;
}
