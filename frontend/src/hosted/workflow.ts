import type { HostedRunSummaryV1, RemoteCommandView } from "../types/generated";

export interface HostedRun {
  id: string;
  executeRequestId: string;
  acceptedAt: string;
  summary: HostedRunSummaryV1 | null;
}

export function mergeRuns(previous: HostedRun[], rows: RemoteCommandView[], device: string, generation: number): HostedRun[] {
  const runs = new Map(previous.map(run => [run.id, run]));
  for (const row of rows) {
    if (row.operation === "execute" && row.reply?.kind === "accepted") {
      const reply = row.reply;
      if (!runs.has(reply.simulation_id)) runs.set(reply.simulation_id, { id: reply.simulation_id,
        executeRequestId: row.request_id, acceptedAt: reply.accepted_at, summary: null });
    }
    if (row.operation === "result" && row.reply?.kind === "result") {
      const summary = row.reply.summary;
      if (summary.device_id !== device || summary.ownership_generation !== generation) continue;
      const old = runs.get(summary.simulation_id);
      if (old?.summary && old.summary.receipt_revision >= summary.receipt_revision) continue;
      runs.set(summary.simulation_id, { id: summary.simulation_id, executeRequestId: summary.execute_request_id,
        acceptedAt: old?.acceptedAt ?? summary.created_at, summary });
    }
  }
  return [...runs.values()].sort((a, b) => b.acceptedAt.localeCompare(a.acceptedAt) || b.id.localeCompare(a.id)).slice(0, 50);
}

export function terminal(run: HostedRun): boolean {
  return run.summary !== null && ["completed", "failed", "cancelled"].includes(run.summary.status);
}

export function metric(value: number | null | undefined, suffix = ""): string {
  return typeof value === "number" && Number.isFinite(value) ? `${Number(value.toFixed(2))}${suffix}` : "Not measured";
}
