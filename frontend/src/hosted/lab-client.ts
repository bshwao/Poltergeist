import { api, ApiError } from "../services/api";
import type { RequestOptions, ScenarioPagination } from "../services/api";
import type { LabClient } from "../services/lab-client";
import type { BrowserDeviceView, DiscoveryPlan, DiscoveryProposal, GuidanceFeedback, LabReply, LabCommand, SimulationLaunch, SuiteSave, SavedSuite, SimulationOptions, OptimizationRecord } from "../types/generated";
import { createHostedClient, HostedError } from "./client";
import type { RegressionPolicy } from "../types/generated";
import { isRegressionPolicy } from "../services/analysis-contract";

const stable = (value: unknown): unknown => Array.isArray(value) ? value.map(stable) : value && typeof value === "object" ? Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => [k, stable(v)])) : value;
type LabAction = LabCommand["action"];
type LabResult = LabReply["result"];

function waitForTransfer(delay: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(new DOMException("Request cancelled", "AbortError")); return; }
    const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, delay);
    const abort = () => { clearTimeout(timer); signal.removeEventListener("abort", abort); reject(new DOMException("Request cancelled", "AbortError")); };
    signal.addEventListener("abort", abort, { once: true });
  });
}

/** Serial, fenced RPCs. Ambiguous writes retain their original request identity. */
export function createHostedLabClient(client: ReturnType<typeof createHostedClient>, target: BrowserDeviceView, live: () => boolean, authFailed?: (error: HostedError) => void,
  analysisEnabled: () => boolean = () => target.connection?.analysis_supported === true) {
  let serial: Promise<unknown> = Promise.resolve(), disposed = false;
  const pending = new Map<string, string>(), modes = new Map<string, "test" | "carla">();
  const controllers = new Set<AbortController>();
  const writeKinds = new Set(["suite_save", "suite_update", "suite_delete", "suite_run", "plan_propose", "plan_approve", "operation_cancel", "operation_resume", "run_launch", "run_cancel", "feedback"]);
  const safeOptions = (options: SimulationOptions): SimulationOptions => ({ ...options,
    max_ticks: options.max_ticks === 100000 ? Math.min(10000, target.device.policy.max_ticks) : options.max_ticks });
  const invalid = (): never => { throw new ApiError("The companion returned inconsistent experiment evidence."); };
  const requireAnalysis = () => { if (!analysisEnabled()) throw new ApiError("Update and reconnect this companion to enable evidence analysis and regression review.", 503); };
  function call(action: LabAction, options: RequestOptions = {}, mode: "test" | "carla" | null = null, requestId?: string): Promise<LabResult> {
    let key: string;
    try { key = JSON.stringify(stable([action, mode])); } catch { return Promise.reject(new ApiError("Use a valid JSON experiment request.", 422)); }
    const write = writeKinds.has(action.kind);
    const identity = requestId ?? (write ? pending.get(key) : undefined) ?? crypto.randomUUID();
    const [frozenAction, frozenMode] = JSON.parse(key);
    const command = { kind: "lab", request_id: identity, action: frozenAction, mode: frozenMode } as LabCommand;
    if (write) pending.set(key, identity);
    const perform = async () => {
      if (disposed || options.signal?.aborted) throw new DOMException("Request cancelled", "AbortError");
      if (!live()) throw new ApiError("Connect the selected companion and approve execution and evidence access before continuing.", 503);
      const controller = new AbortController(), abort = () => controller.abort();
      options.signal?.addEventListener("abort", abort, { once: true }); controllers.add(controller);
      const deadline = performance.now() + 12_000, timer = setTimeout(abort, 12_000);
      try {
        for (let attempt = 0; ; attempt++) {
          if (disposed || controller.signal.aborted) throw new DOMException("Request cancelled", "AbortError");
          if (!live()) throw new ApiError("Connect the selected companion and approve execution and evidence access before continuing.", 503);
          if (command.action.kind === "analysis_get" || command.action.kind === "regression_report") requireAnalysis();
          try {
            const reply = await client.lab(target.device.device_id, command, controller.signal);
            if (disposed || controller.signal.aborted) throw new DOMException("Request cancelled", "AbortError");
            if (!api.validateLabResult(reply.result)) invalid();
            if (write) pending.delete(key);
            return reply.result;
          } catch (error) {
            // This refusal occurs before relay admission. Never automatically
            // replay an ambiguous write or replace its frozen request identity.
            if (!(error instanceof HostedError) || error.status !== 429 || error.code !== "transfer_busy" || attempt >= 3
                || !Number.isFinite(error.retryAfter) || error.retryAfter < 0) throw error;
            const delay = Math.max(100, error.retryAfter * 1000);
            if (delay >= deadline - performance.now()) throw error;
            await waitForTransfer(delay, controller.signal);
          }
        }
      } catch (error) {
        if (error instanceof HostedError) {
          if (error.status === 401) authFailed?.(error);
          if (error.status >= 400 && error.status < 500) pending.delete(key);
          throw new ApiError(error.message, error.status);
        }
        throw error;
      } finally { clearTimeout(timer); options.signal?.removeEventListener("abort", abort); controllers.delete(controller); }
    };
    const result = serial.then(perform, perform); serial = result.then(() => undefined, () => undefined);
    return result;
  }
  function rememberSuite(value: SavedSuite): SavedSuite { modes.set(value.id, value.definition.mode); return value; }
  function rememberPlan(value: DiscoveryPlan): DiscoveryPlan { modes.set(value.id, value.proposal.mode); return value; }
  async function page(kind: "suites" | "plans" | "suite_runs", pagination: ScenarioPagination, options: RequestOptions, suiteId?: string) {
    const limit = pagination.limit ?? 11, start = pagination.offset ?? 0;
    if (!Number.isSafeInteger(limit) || limit < 1 || limit > 100 || !Number.isSafeInteger(start) || start < 0) throw new ApiError("Use a page size of 1–100 and a nonnegative offset.", 422);
    const items: (SavedSuite | DiscoveryPlan | OptimizationRecord)[] = [], seen = new Set<string>();
    let offset = start, before: string | null = null;
    while (items.length < limit) {
      const window = { limit: Math.min(20, limit - items.length), offset, before };
      const result = await call(kind === "suite_runs" ? { kind, id: suiteId!, ...window } : { kind, ...window }, options);
      if (result.kind !== kind || (before !== null && result.snapshot_at !== before)) return invalid();
      before = result.snapshot_at;
      if (result.items.some(item => seen.has(item.id))) return invalid();
      result.items.forEach(item => { seen.add(item.id); items.push(item); });
      if (result.next_offset === null) break;
      if (!result.items.length || result.next_offset !== offset + result.items.length) return invalid();
      offset = result.next_offset;
    }
    return items;
  }
  const adapter: LabClient = {
    async getGallery(options = {}) { const result = await call({ kind: "gallery" }, options); if (result.kind !== "gallery") return invalid(); return result.items; },
    async listAgents() { return []; },
    async listSuites(pagination: ScenarioPagination = {}, options = {}) { return (await page("suites", pagination, options) as SavedSuite[]).map(rememberSuite); },
    async listSuiteRuns(id, pagination = {}, options = {}) {
      const values = await page("suite_runs", pagination, options, id) as OptimizationRecord[];
      if (values.some(row => row.request.kind !== "suite" || row.request.suite_id !== id)) return invalid();
      values.forEach(row => modes.set(row.id, row.execution.mode)); return values;
    },
    async getSuite(id, options = {}) { const result = await call({ kind: "suite_get", id, expected_revision: null }, options); if (result.kind !== "suite" || result.suite.id !== id) return invalid(); return rememberSuite(result.suite); },
    async saveSuite(input: SuiteSave, options = {}) { const result = await call({ kind: "suite_save", save: { ...input, definition: { ...input.definition, options: safeOptions(input.definition.options) } } }, options); if (result.kind !== "suite") return invalid(); return rememberSuite(result.suite); },
    async updateSuite(id, input, revision, options = {}) { const result = await call({ kind: "suite_update", id, save: { ...input, expected_revision: revision, definition: { ...input.definition, options: safeOptions(input.definition.options) } } }, options); if (result.kind !== "suite" || result.suite.id !== id) return invalid(); return rememberSuite(result.suite); },
    async deleteSuite(id, revision, options = {}) { const result = await call({ kind: "suite_delete", id, expected_revision: revision }, options); if (result.kind !== "deleted" || result.id !== id) return invalid(); },
    async runSuite(id, revision, requestId, options = {}) {
      const mode = modes.get(id) ?? (await adapter.getSuite(id, options)).definition.mode;
      const result = await call({ kind: "suite_run", id, launch: { expected_revision: revision, request_id: requestId } }, options, mode, requestId);
      if (result.kind !== "operation" || result.operation.id !== requestId || result.operation.request.kind !== "suite" || result.operation.request.suite_id !== id || result.operation.request.suite_revision !== revision) return invalid();
      modes.set(result.operation.id, mode); return result.operation;
    },
    async getSuiteReport(id, options = {}) { const result = await call({ kind: "suite_report", id, expected_revision: null }, options); if (result.kind !== "suite_report" || result.report.evidence.operation.id !== id) return invalid(); modes.set(id, result.report.evidence.operation.execution.mode); return result.report; },
    async proposeDiscovery(input: DiscoveryProposal, options = {}) { const result = await call({ kind: "plan_propose", proposal: { ...input, options: safeOptions(input.options) } }, options); if (result.kind !== "plan") return invalid(); return rememberPlan(result.plan); },
    async listDiscoveryPlans(pagination: ScenarioPagination = {}, options = {}) { return (await page("plans", pagination, options) as DiscoveryPlan[]).map(rememberPlan); },
    async getDiscoveryPlan(id, options = {}) { const result = await call({ kind: "plan_get", id, expected_revision: null }, options); if (result.kind !== "plan" || result.plan.id !== id) return invalid(); return rememberPlan(result.plan); },
    async approveDiscovery(plan, options = {}) { const result = await call({ kind: "plan_approve", id: plan.id, approval: { expected_revision: plan.revision, digest: plan.digest, approve: true } }, options, plan.proposal.mode); if (result.kind !== "operation" || result.operation.request.kind !== "discover" || result.operation.request.plan_id !== plan.id || result.operation.request.plan_digest !== plan.digest) return invalid(); modes.set(result.operation.id, result.operation.execution.mode); return result.operation; },
    async getDiscoveryReport(id, options = {}) { const result = await call({ kind: "discovery_report", id, expected_revision: null }, options); if (result.kind !== "discovery" || result.report.plan.id !== id) return invalid(); return result.report; },
    async getOptimization(id, options = {}) { const result = await call({ kind: "operation_get", id, expected_revision: null }, options); if (result.kind !== "operation" || result.operation.id !== id) return invalid(); modes.set(id, result.operation.execution.mode); return result.operation.request.kind === "discover" ? (await adapter.getDiscoveryReport(result.operation.request.plan_id, options)).evidence : (await adapter.getSuiteReport(id, options)).evidence; },
    async cancelOptimization(id, options = {}) { const result = await call({ kind: "operation_cancel", id, expected_revision: null }, options); if (result.kind !== "operation" || result.operation.id !== id) return invalid(); return result.operation; },
    async resumeOptimization(id, revision, options = {}) { const mode = modes.get(id) ?? (await adapter.getOptimization(id, options)).operation.execution.mode; const result = await call({ kind: "operation_resume", id, expected_revision: revision }, options, mode); if (result.kind !== "operation" || result.operation.id !== id) return invalid(); return result.operation; },
    async launchSimulation(input: SimulationLaunch, options = {}) { const result = await call({ kind: "run_launch", launch: { ...input, options: safeOptions(input.options) } }, options, input.mode); if (result.kind !== "run") return invalid(); return result.record; },
    async getSimulation(id, options = {}) { const result = await call({ kind: "run_get", id, expected_revision: null }, options); if (result.kind !== "run" || result.record.simulation.id !== id) return invalid(); return result.record; },
    async getSimulationProgress(id, options = {}) { const record = await adapter.getSimulation(id, options), s = record.simulation; return { simulation_id: id, status: s.status, progress: s.progress, revision: record.revision, started_at: s.started_at, finished_at: s.finished_at, cancel_requested: record.job?.cancel_requested ?? false, queue_position: record.job?.queue_position ?? null, error: s.error }; },
    async getSimulationResult(id, options = {}) { const record = await adapter.getSimulation(id, options); if (!record.result) throw new ApiError("The finalized run result is not available yet.", 409); return record.result; },
    async cancelSimulation(id, options = {}) { const result = await call({ kind: "run_cancel", id, expected_revision: null }, options); if (result.kind !== "run" || result.record.simulation.id !== id) return invalid(); return result.record; },
    async getReplayHighlights(id, options = {}) { const result = await call({ kind: "highlights", id, expected_revision: null }, options); if (result.kind !== "highlights" || result.highlights.simulation_id !== id) return invalid(); return result.highlights; },
    async getEvidenceAnalysis(id, options = {}) { requireAnalysis(); const result = await call({ kind: "analysis_get", id, expected_revision: null }, options); if (result.kind !== "analysis" || result.analysis.simulation_id !== id) return invalid(); return result.analysis; },
    async getRegressionReport(id, policy: RegressionPolicy = { minimum_pairs: 3, thresholds: { severity: 5, collision_count: 0 } }, options = {}) {
      if (!isRegressionPolicy(policy)) throw new ApiError("Choose valid regression thresholds and at least two matched pairs.", 422);
      requireAnalysis(); const result = await call({ kind: "regression_report", id, policy }, options);
      if (result.kind !== "regression" || result.report.operation_id !== id || JSON.stringify(stable(result.report.policy)) !== JSON.stringify(stable(policy))) return invalid();
      return result.report;
    },
    async saveGuidanceFeedback(input: GuidanceFeedback, options = {}) { const result = await call({ kind: "feedback", feedback: input }, options); if (result.kind !== "feedback" || result.feedback.feedback.simulation_id !== input.simulation_id) return invalid(); return result.feedback; },
  };
  return Object.assign(adapter, {
    async getFrame(id: string, frame: number, options: RequestOptions = {}) { const result = await call({ kind: "frame_get", id, frame }, options); if (result.kind !== "frame" || result.simulation_id !== id || result.frame.frame !== frame) return invalid(); return result.frame; },
    dispose() { disposed = true; controllers.forEach(c => c.abort()); controllers.clear(); pending.clear(); },
  });
}
