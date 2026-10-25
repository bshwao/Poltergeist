import type {
  BrowserCommandPage, BrowserDeviceView, BrowserScenarioPreset, BrowserSession, CancelCommand,
  ExecuteCommand, PairingStatus, RemoteCommandView, ResultCommand, StatusCommand,
} from "../types/generated";

export type HostedCommand = Pick<ExecuteCommand, "kind" | "request_id" | "scenario" | "mode" | "agent_id" | "options">
  | CancelCommand | ResultCommand | StatusCommand;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SCOPES = new Set(["status:read", "result:read", "run:execute:test", "run:execute:carla", "run:cancel"]);
const messages: Record<string, string> = {
  authentication_required: "Sign in to continue.", session_expired: "Your session ended. Sign in again.",
  invalid_credentials: "The username or password was not accepted.", account_unavailable: "That username is unavailable.",
  invalid_request: "Check the submitted details and the approved experiment limits.",
  permission_denied: "This operation is outside this computer's approved permissions.",
  policy_denied: "This experiment exceeds the computer's approved limits.",
  resource_not_found: "This item is unavailable to your account. Refresh the computer list.",
  device_unavailable: "The computer is not accepting remote requests. Review Connection in the companion.",
  request_conflict: "This request already exists with different details. Its original identity was preserved.",
  pairing_unavailable: "This code could not be claimed. Check it in the companion and try again.",
  pairing_changed: "Pairing changed. Review the companion and create a fresh code if needed.",
  rate_limited: "Too many requests. Wait before trying again.", device_queue_full: "The computer's request queue is full.",
  invalid_response: "The service returned an unexpected response. Refresh before continuing.",
  network_error: "The connection was interrupted. A submitted request may have arrived; retry its original request.",
  request_cancelled: "The request was cancelled.", stale_response: "The account context changed.",
};

export class HostedError extends Error {
  constructor(readonly code: string, readonly status = 0, readonly retryAfter = 0) {
    super(messages[code] ?? (status === 401 ? messages.session_expired : "The request could not be completed."));
    this.name = "HostedError";
  }
}

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function id(value: unknown): value is string { return typeof value === "string" && UUID.test(value); }
function date(value: unknown): boolean { return typeof value === "string" && Number.isFinite(Date.parse(value)); }
function integer(value: unknown, minimum = 0, maximum = Number.MAX_SAFE_INTEGER): boolean { return Number.isSafeInteger(value) && Number(value) >= minimum && Number(value) <= maximum; }
function scopes(value: unknown): boolean { return Array.isArray(value) && value.length > 0 && value.length <= 5 && new Set(value).size === value.length && value.every(scope => SCOPES.has(String(scope))); }
function invalid(): never { throw new HostedError("invalid_response"); }
function pathId(value: string): string { if (!id(value)) throw new HostedError("invalid_request", 400); return value; }

function session(value: unknown): BrowserSession {
  if (!object(value) || !object(value.account) || !id(value.account.account_id)
      || typeof value.account.username !== "string" || !/^[a-z0-9][a-z0-9_-]{2,31}$/.test(value.account.username)
      || typeof value.csrf_token !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(value.csrf_token)
      || !date(value.expires_at)) invalid();
  return value as unknown as BrowserSession;
}

function command(value: unknown, device: string, request?: string): RemoteCommandView {
  if (!object(value) || !id(value.request_id) || (request && value.request_id !== request)
      || !["execute", "cancel", "status", "result"].includes(String(value.operation))
      || !["queued", "accepted", "completed", "rejected"].includes(String(value.status))
      || !date(value.created_at) || !date(value.updated_at)
      || (value.simulation_id !== null && !id(value.simulation_id))) invalid();
  if (value.reply !== null) {
    if (!object(value.reply) || value.reply.request_id !== value.request_id) invalid();
    const kinds: Record<string, string[]> = { execute: ["accepted", "rejected"], cancel: ["cancel_requested", "rejected"], status: ["status", "rejected"], result: ["result", "rejected"] };
    if (!kinds[String(value.operation)].includes(String(value.reply.kind))) invalid();
    if (value.reply.kind === "accepted" && (!id(value.reply.simulation_id) || !date(value.reply.accepted_at))) invalid();
    if (value.reply.kind === "cancel_requested" && (!id(value.reply.simulation_id) || typeof value.reply.terminal !== "boolean" || !date(value.reply.accepted_at))) invalid();
    if (value.reply.kind === "rejected" && typeof value.reply.code !== "string") invalid();
    if (value.reply.kind === "result") {
      const summary = value.reply.summary;
      if (!object(summary) || summary.device_id !== device || !id(summary.simulation_id)
          || !id(summary.execute_request_id) || summary.protocol_version !== "poltergeist-hosted-run-v1"
          || !Number.isSafeInteger(summary.ownership_generation) || Number(summary.ownership_generation) < 1
          || !Number.isSafeInteger(summary.receipt_revision) || Number(summary.receipt_revision) < 1
          || !["queued", "running", "completed", "failed", "cancelled"].includes(String(summary.status))
          || !object(summary.scenario) || typeof summary.scenario.name !== "string"
          || summary.scenario.name.length > 128 || !object(summary.evidence)
          || !["local_only", "missing"].includes(String(summary.evidence.state))
          || (summary.evidence.artifact_reference_count !== null && !integer(summary.evidence.artifact_reference_count))
          || (summary.evidence.telemetry_frames !== null && !integer(summary.evidence.telemetry_frames))
          || !["test", "carla"].includes(String(summary.mode)) || !date(summary.created_at)
          || (summary.cancel_requested !== null && typeof summary.cancel_requested !== "boolean")
          || (summary.measured_duration_s !== null && (typeof summary.measured_duration_s !== "number" || !Number.isFinite(summary.measured_duration_s) || summary.measured_duration_s < 0))
          || (summary.error !== null && (!object(summary.error) || !["The experiment failed on this computer. Review its local diagnostics.", "The finalized result is unavailable on this computer."].includes(String(summary.error.message))))
          || typeof summary.progress !== "number" || !Number.isFinite(summary.progress)
          || summary.progress < 0 || summary.progress > 1) invalid();
      if (summary.metrics !== null) {
        if (!object(summary.metrics) || !object(summary.metrics.coverage) || !integer(summary.metrics.coverage.total_frames)) invalid();
        for (const field of ["collision_count", "min_ttc_s"]) {
          const measurement = summary.metrics[field];
          if (measurement !== null && (typeof measurement !== "number" || !Number.isFinite(measurement) || measurement < 0)) invalid();
        }
      }
    } else if (!["accepted", "cancel_requested", "status", "rejected"].includes(String(value.reply.kind))) invalid();
  }
  return value as unknown as RemoteCommandView;
}

function pairing(value: unknown, account: string | null): PairingStatus {
  if (!object(value) || !id(value.attempt_id) || !id(value.device_id) || !integer(value.revision, 1)
      || !["pending", "claimed", "approved", "denied", "expired", "cancelled"].includes(String(value.state))
      || !date(value.expires_at) || !scopes(value.requested_scopes) || !object(value.claimed_account)
      || value.claimed_account.account_id !== account || typeof value.claimed_account.username !== "string") invalid();
  return value as unknown as PairingStatus;
}

async function json(response: Response): Promise<unknown> {
  if (!response.headers.get("content-type")?.toLowerCase().startsWith("application/json")) invalid();
  const reader = response.body?.getReader();
  if (!reader) invalid();
  const chunks: Uint8Array[] = [];
  let length = 0;
  try {
    for (;;) {
      const next = await reader.read();
      if (next.done) break;
      length += next.value.byteLength;
      if (length > 2 * 1024 * 1024) { await reader.cancel(); invalid(); }
      chunks.push(next.value);
    }
    const bytes = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) as unknown;
  } catch (error) {
    if (error instanceof HostedError) throw error;
    invalid();
  } finally { reader.releaseLock(); }
}

export function createHostedClient(fetcher: typeof fetch = (...args) => fetch(...args)) {
  let csrf: string | null = null, account: string | null = null, epoch = 0;
  function clear() { csrf = account = null; epoch += 1; }
  async function request(path: string, options: { body?: unknown; signal?: AbortSignal; anonymous?: boolean } = {}) {
    if (!path.startsWith("/v1/") || path.includes("\\") || path.includes("..") || path.includes("#")) throw new HostedError("invalid_request", 400);
    const observed = epoch;
    const controller = new AbortController();
    const cancel = () => controller.abort();
    options.signal?.addEventListener("abort", cancel, { once: true });
    if (options.signal?.aborted) controller.abort();
    const timer = setTimeout(cancel, 15_000);
    try {
      const write = options.body !== undefined;
      if (write && !options.anonymous && !csrf) throw new HostedError("session_expired", 401);
      const body = write ? JSON.stringify(options.body) : undefined;
      if (body && new TextEncoder().encode(body).byteLength > 32768) throw new HostedError("invalid_request", 400);
      const response = await fetcher(path, { method: write ? "POST" : "GET", body,
        mode: "same-origin", credentials: "same-origin", redirect: "error", cache: "no-store", signal: controller.signal,
        headers: { Accept: "application/json", "X-Poltergeist-Client": "browser",
          ...(write ? { "Content-Type": "application/json" } : {}),
          ...(write && csrf && !options.anonymous ? { "X-CSRF-Token": csrf } : {}) } });
      if (observed !== epoch) throw new HostedError("stale_response");
      if (response.status === 204) return null;
      // The stream can finish after another login or logout has changed context.
      const value = await json(response);
      if (observed !== epoch) throw new HostedError("stale_response");
      if (!response.ok) {
        const code = object(value) && object(value.error) && typeof value.error.code === "string" ? value.error.code : "invalid_response";
        if (response.status === 401) clear();
        const delay = Number(response.headers.get("retry-after"));
        throw new HostedError(code, response.status, Number.isFinite(delay) ? Math.max(0, Math.min(delay, 86400)) : 0);
      }
      return value;
    } catch (error) {
      if (error instanceof HostedError) throw error;
      throw new HostedError(options.signal?.aborted ? "request_cancelled" : "network_error");
    } finally {
      clearTimeout(timer);
      options.signal?.removeEventListener("abort", cancel);
    }
  }
  function remember(value: unknown, observed: number) {
    if (observed !== epoch) throw new HostedError("stale_response");
    const parsed = session(value); csrf = parsed.csrf_token; account = parsed.account.account_id; return parsed;
  }
  return {
    clear,
    async session(signal?: AbortSignal) { const observed = epoch; return remember(await request("/v1/auth/session", { signal }), observed); },
    async login(username: string, password: string) {
      clear();
      const observed = epoch;
      return remember(await request("/v1/auth/login", { body: { username, password }, anonymous: true }), observed);
    },
    async register(username: string, password: string) {
      await request("/v1/auth/register", { body: { username, password }, anonymous: true });
    },
    async logout() { await request("/v1/auth/logout", { body: {} }); clear(); },
    async devices(signal?: AbortSignal): Promise<BrowserDeviceView[]> {
      const value = await request("/v1/browser/devices", { signal });
      if (!Array.isArray(value) || value.length > 100) invalid();
      for (const row of value) {
        if (!object(row) || !object(row.device) || !object(row.connection) || !id(row.device.device_id)
            || !object(row.device.owner) || row.device.owner.account_id !== account
            || typeof row.device.display_name !== "string" || row.device.display_name.length > 64
            || !integer(row.device.ownership_generation, 1) || !integer(row.device.policy_revision, 1)
            || !date(row.device.created_at) || (row.device.revoked_at !== null && !date(row.device.revoked_at))
            || !object(row.device.policy) || !scopes(row.device.policy.scopes)
            || typeof row.device.policy.max_duration_s !== "number" || !Number.isFinite(row.device.policy.max_duration_s) || row.device.policy.max_duration_s <= 0 || row.device.policy.max_duration_s > 120
            || !integer(row.device.policy.max_ticks, 1, 10000) || !integer(row.device.policy.max_waiting_runs, 0, 10)
            || row.device.policy.allow_map_reload !== false || row.device.policy.allow_graphics_change !== false || row.device.policy.allow_raw_capture !== false
            || !["offline", "connecting", "connected", "paused", "revoked"].includes(String(row.connection.state))
            || typeof row.connection.remote_requests_enabled !== "boolean"
            || (row.connection.remote_requests_enabled && (row.connection.state !== "connected" || row.device.revoked_at !== null || row.connection.protocol_compatible !== true))
            || (row.connection.last_seen_at !== null && !date(row.connection.last_seen_at))
            || row.connection.carla_readiness !== "unknown" || row.connection.baseline_only !== true) invalid();
      }
      return value as BrowserDeviceView[];
    },
    async scenarios(signal?: AbortSignal): Promise<BrowserScenarioPreset[]> {
      const value = await request("/v1/browser/scenarios", { signal });
      if (!Array.isArray(value) || value.length > 13 || value.some(row => !object(row) || typeof row.id !== "string" || typeof row.label !== "string" || typeof row.description !== "string" || !object(row.recipe) || typeof row.recipe.name !== "string" || typeof row.recipe.map !== "string" || !Array.isArray(row.recipe.actors) || typeof row.recipe.duration_s !== "number" || typeof row.recipe.seed !== "number")) invalid();
      return value as BrowserScenarioPreset[];
    },
    async claim(code: string) { return pairing(await request("/v1/pairing/claim", { body: { code } }), account); },
    async claimStatus(attempt: string, signal?: AbortSignal) { return pairing(await request(`/v1/browser/claims/${pathId(attempt)}`, { signal }), account); },
    async revoke(device: string) { return await request(`/v1/devices/${pathId(device)}/revoke`, { body: {} }); },
    async history(device: string, signal?: AbortSignal, before?: string): Promise<BrowserCommandPage> {
      const value = await request(`/v1/devices/${pathId(device)}/commands?limit=20${before ? `&before=${pathId(before)}` : ""}`, { signal });
      if (!object(value) || !Array.isArray(value.items) || value.items.length > 50
          || (value.next_before !== null && !id(value.next_before))) invalid();
      return { items: value.items.map(row => command(row, device)), next_before: value.next_before as string | null };
    },
    async send(device: string, value: HostedCommand, signal?: AbortSignal) {
      pathId(value.request_id);
      return command(await request(`/v1/devices/${pathId(device)}/commands`, { body: value, signal }), device, value.request_id);
    },
    async command(device: string, requestId: string, signal?: AbortSignal) {
      return command(await request(`/v1/devices/${pathId(device)}/commands/${pathId(requestId)}`, { signal }), device, requestId);
    },
  };
}
