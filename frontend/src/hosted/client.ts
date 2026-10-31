import type {
  BrowserCommandPage, BrowserDeviceView, BrowserScenarioPreset, BrowserSession, CancelCommand,
  ExecuteCommand, PairingStatus, RemoteCommandView, ResultCommand, StatusCommand,
  EvidenceReply, EvidenceRequest, EvidenceSample,
} from "../types/generated";
import type { LabCommand, LabReply } from "../types/generated";

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
  evidence_unavailable: "Evidence review needs a connected, updated companion with local evidence permission.",
  transfer_busy: "Another evidence request is in progress. Try again shortly.",
  evidence_timeout: "The computer did not return evidence in time. Try again when it is connected.",
  command_storage_full: "The service has reached its receipt storage limit. Contact the operator.",
  lab_unavailable: "Guided experiments need a connected, updated companion with approved execution and evidence access.",
  lab_timeout: "The computer did not answer in time. Review saved plans before retrying a submitted action.",
  analysis_unavailable: "Update and reconnect this companion to enable evidence analysis and regression review.",
  queue_full: "The computer's experiment queue is full. Wait for work to finish.",
  result_unavailable: "This evidence is unavailable for remote review. Open its local diagnostics or export it on the computer.",
  run_not_found: "This experiment is unavailable to the current account and computer approval.",
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
const DIGEST = /^[0-9a-f]{64}$/;
export const MAX_SAMPLE_VIEW_BYTES = 4 * 1024 * 1024;

function validSample(value: unknown): value is EvidenceSample {
  if (!object(value) || !integer(value.sample_index, 0, 1000000)
      || (value.index_offset !== null && !integer(value.index_offset)) || !integer(value.frame)
      || typeof value.timestamp !== "number" || !Number.isFinite(value.timestamp) || value.timestamp < 0
      || !integer(value.byte_length, 0, 64 * 1024 * 1024) || typeof value.sha256 !== "string" || !DIGEST.test(value.sha256)
      || !object(value.metadata)) return false;
  const format = value.metadata;
  return value.kind === "camera" ? format.encoding === "bgra8" && integer(format.width, 1, 4096)
      && integer(format.height, 1, 4096) && value.byte_length === Number(format.width) * Number(format.height) * 4
    : value.kind === "lidar" && format.encoding === "float32_xyzi" && integer(format.channels, 1, 4096)
      && Number(value.byte_length) % 16 === 0;
}

function rawChunk(value: EvidenceReply): Uint8Array<ArrayBuffer> {
  if (typeof value.data_base64 !== "string" || value.data_base64.length > 87384) invalid();
  try { return Uint8Array.from(atob(value.data_base64), c => c.charCodeAt(0)); } catch { invalid(); }
}

function sampleIdentity(value: EvidenceSample): string {
  const metadata = value.metadata;
  return JSON.stringify([value.sample_index, value.index_offset, value.kind, value.frame, value.timestamp,
    value.byte_length, value.sha256, metadata.encoding,
    metadata.encoding === "bgra8" ? [metadata.width, metadata.height] : metadata.channels]);
}
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
    if (error instanceof Error && error.name === "AbortError") throw error;
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
            || row.device.policy.allow_map_reload !== false || row.device.policy.allow_graphics_change !== false || typeof row.device.policy.allow_raw_capture !== "boolean"
            || !["offline", "connecting", "connected", "paused", "revoked"].includes(String(row.connection.state))
            || typeof row.connection.remote_requests_enabled !== "boolean"
            || (row.connection.lab_supported !== undefined && typeof row.connection.lab_supported !== "boolean")
            || (row.connection.analysis_supported !== undefined && typeof row.connection.analysis_supported !== "boolean")
            || (row.connection.analysis_supported === true && row.connection.lab_supported !== true)
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
    async evidence(device: string, input: Pick<EvidenceRequest, "simulation_id"> & Partial<EvidenceRequest>, signal?: AbortSignal): Promise<EvidenceReply> {
      const observed = epoch;
      const body = { kind: "evidence", request_id: crypto.randomUUID(), resource: "telemetry", offset: 0, ...input };
      pathId(body.simulation_id);
      const value = await request(`/v1/devices/${pathId(device)}/evidence`, { body, signal });
      if (object(value) && value.kind === "rejected" && value.request_id === body.request_id && typeof value.code === "string") throw new HostedError(value.code);
      if (!object(value) || value.kind !== "evidence" || value.request_id !== body.request_id || value.simulation_id !== body.simulation_id
          || value.resource !== body.resource || value.offset !== body.offset || (value.artifact_index ?? 0) !== (body.artifact_index ?? 0)
          || !["available", "missing", "deleted", "changed", "limit_exceeded"].includes(String(value.state))) invalid();
      if (value.state === "available") {
        if (typeof value.version !== "string" || !DIGEST.test(value.version) || (body.version != null && body.version !== value.version)
            || !integer(value.total_bytes) || (value.next_offset !== null && (!integer(value.next_offset) || Number(value.next_offset) <= body.offset || Number(value.next_offset) > Number(value.total_bytes)))
            || typeof value.data_base64 !== "string" || value.data_base64.length > 87384 || typeof value.sha256 !== "string") invalid();
        const bytes = rawChunk(value as unknown as EvidenceReply);
        if (bytes.byteLength > 65536) invalid();
        const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))).map(b => b.toString(16).padStart(2, "0")).join("");
        if (digest !== value.sha256) invalid();
        if (body.resource !== "sample") {
          let frames: unknown;
          try { frames = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)); } catch { invalid(); }
          if (!Array.isArray(frames) || frames.length > (body.resource === "recordings" ? 64 : body.limit ?? 25)) invalid();
          if (body.resource === "telemetry") for (const row of frames) {
            if (!object(row) || !integer(row.frame) || typeof row.time_s !== "number" || !Number.isFinite(row.time_s) || row.time_s < 0
                || (row.speed_mps !== null && (typeof row.speed_mps !== "number" || !Number.isFinite(row.speed_mps) || row.speed_mps < 0))
                || (row.collisions !== null && !integer(row.collisions)) || (row.traffic_violations !== null && !integer(row.traffic_violations))
                || !object(row.control)) invalid();
          }
          if (body.resource === "telemetry") value.frames = frames;
          else if (body.resource === "recordings") {
            for (const row of frames) {
              if (!object(row) || !integer(row.artifact_index, 0, 64) || !["available", "missing", "deleted", "limit_exceeded"].includes(String(row.state))
                  || (row.state === "available" && (!integer(row.sample_count, 0, 1000001) || !integer(row.total_bytes)))) invalid();
            }
            value.recordings = frames;
          } else {
            if (frames.some(row => !validSample(row))) invalid();
            for (let i = 0; i < frames.length; i++) if (frames[i].sample_index !== (body.sample_index ?? 0) + i) invalid();
            value.samples = frames;
          }
        } else {
          if (bytes.byteLength !== Math.min(65536, Number(value.total_bytes) - body.offset)
              || value.next_offset !== (body.offset + bytes.byteLength < Number(value.total_bytes) ? body.offset + bytes.byteLength : null)) invalid();
          if (value.sample !== undefined && (!validSample(value.sample) || value.sample.sample_index !== (body.sample_index ?? 0)
              || value.sample.byte_length !== value.total_bytes || value.sample.sha256 !== value.sample_sha256
              || value.sample.frame !== value.frame || value.sample.timestamp !== value.timestamp)) invalid();
        }
      }
      if (epoch !== observed) throw new HostedError("stale_response");
      if (signal?.aborted) throw new HostedError("request_cancelled");
      return value as unknown as EvidenceReply;
    },
    async sample(device: string, simulationId: string, artifactIndex: number, selected: EvidenceSample,
                 signal?: AbortSignal, progress?: (received: number, total: number) => void): Promise<ArrayBuffer> {
      if (!validSample(selected) || selected.byte_length > MAX_SAMPLE_VIEW_BYTES) throw new Error("This sample exceeds the 4 MiB browser preview limit. Use a local evidence export.");
      const observed = epoch;
      const output = new Uint8Array(selected.byte_length);
      let offset = 0, version: string | null = null;
      for (;;) {
        if (signal?.aborted) throw new HostedError("request_cancelled");
        const reply = await this.evidence(device, { simulation_id: simulationId, resource: "sample", artifact_index: artifactIndex,
          sample_index: selected.sample_index, sample_cursor: selected.index_offset, offset, version }, signal);
        if (reply.state !== "available") throw new Error(reply.state === "deleted" ? "This recording was removed on the computer."
          : reply.state === "changed" ? "The recording changed. Reload its sample list." : "The recorded sample is unavailable on the computer.");
        if (!reply.sample || sampleIdentity(reply.sample) !== sampleIdentity(selected) || reply.total_bytes !== output.byteLength
            || reply.sample_sha256 !== selected.sha256) invalid();
        const chunk = rawChunk(reply);
        output.set(chunk, offset); offset += chunk.byteLength;
        progress?.(offset, output.byteLength);
        if (reply.next_offset === null) break;
        version = reply.version;
      }
      const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", output))).map(b => b.toString(16).padStart(2, "0")).join("");
      if (digest !== selected.sha256) throw new Error("The recorded sample checksum does not match its saved identity.");
      if (epoch !== observed) throw new HostedError("stale_response");
      if (signal?.aborted) throw new HostedError("request_cancelled");
      return output.buffer;
    },
    async command(device: string, requestId: string, signal?: AbortSignal) {
      return command(await request(`/v1/devices/${pathId(device)}/commands/${pathId(requestId)}`, { signal }), device, requestId);
    },
    async lab(device: string, command: LabCommand, signal?: AbortSignal): Promise<LabReply> {
      pathId(command.request_id);
      const value = await request(`/v1/devices/${pathId(device)}/experimentation`, { body: command, signal });
      if (!object(value) || value.request_id !== command.request_id) invalid();
      if (value.kind === "rejected" && typeof value.code === "string") {
        const status: Record<string, number> = { permission_denied: 403, policy_denied: 403, invalid_request: 400,
          request_conflict: 409, run_not_found: 404, queue_full: 429, device_unavailable: 503, result_unavailable: 503 };
        throw new HostedError(value.code, status[value.code] ?? 503);
      }
      if (value.kind !== "lab" || !object(value.result) || typeof value.result.kind !== "string") invalid();
      return value as unknown as LabReply;
    },
  };
}
