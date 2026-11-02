import type { AssistantRecord, AssistantStatus } from "../types/generated";
import { isEvidenceAnalysis } from "./analysis-contract";

const object = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === "object" && !Array.isArray(v);
const count = (v: unknown): v is number => typeof v === "number" && Number.isSafeInteger(v) && v >= 0;
const uuid = (v: unknown) => typeof v === "string" && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(v);
const hash = (v: unknown) => typeof v === "string" && /^[0-9a-f]{64}$/.test(v);
const date = (v: unknown) => typeof v === "string" && /(?:Z|[+-]\d{2}:\d{2})$/.test(v) && Number.isFinite(Date.parse(v));

export function isAssistantStatus(v: unknown): v is AssistantStatus {
  return object(v) && typeof v.enabled === "boolean" && typeof v.configured === "boolean" && typeof v.model === "string"
    && count(v.daily_request_limit) && v.daily_request_limit >= 1 && v.daily_request_limit <= 1000
    && count(v.daily_output_token_limit) && v.daily_output_token_limit >= 16 && v.daily_output_token_limit <= 1000000
    && v.scope === "local_api_only";
}

export function isAssistantRecord(v: unknown, plan: (p: unknown, id: string) => boolean): v is AssistantRecord {
  if (!object(v) || v.schema_version !== "poltergeist-openai-assistance-v1" || !uuid(v.id)
      || !["preview", "running", "completed", "declined", "failed"].includes(String(v.state)) || typeof v.message !== "string"
      || !object(v.preview) || v.preview.id !== v.id || !["plan", "analysis"].includes(String(v.preview.kind))
      || !hash(v.preview.digest) || !date(v.preview.created_at) || !date(v.preview.expires_at)
      || Date.parse(String(v.preview.created_at)) >= Date.parse(String(v.preview.expires_at)) || typeof v.preview.model !== "string"
      || typeof v.preview.redactions_applied !== "boolean" || !object(v.preview.budget)) return false;
  const p = v.preview, b = v.preview.budget;
  if (b.max_provider_calls !== 1 || !count(b.max_input_bytes) || b.max_input_bytes < 1024 || b.max_input_bytes > 32768
      || !count(b.max_output_tokens) || b.max_output_tokens < 128 || b.max_output_tokens > 2048
      || !count(p.input_bytes) || p.input_bytes > b.max_input_bytes || typeof p.payload !== "string"
      || new TextEncoder().encode(p.payload).length !== p.input_bytes || !count(v.reserved_output_tokens)
      || ![0, b.max_output_tokens].includes(v.reserved_output_tokens)
      || ![v.input_tokens, v.output_tokens].every(item => item === null || count(item))) return false;
  try {
    const body = JSON.parse(p.payload);
    if (!object(body) || body.model !== p.model || body.store !== false || body.parallel_tool_calls !== false
        || body.tool_choice !== "required" || body.max_output_tokens !== b.max_output_tokens || typeof body.input !== "string"
        || !Array.isArray(body.tools) || body.tools.length !== 2 || !body.tools.every(tool => object(tool)
          && tool.type === "function" && tool.strict === true && [p.kind === "plan" ? "draft_discovery" : "report_evidence", "decline_request"].includes(String(tool.name)))) return false;
  } catch { return false; }
  if (v.plan !== null && (p.kind !== "plan" || !plan(v.plan, String(v.id)) || !object(v.plan) || v.plan.operation_id !== null)) return false;
  if (v.analysis !== null) {
    const a = v.analysis;
    if (p.kind !== "analysis" || !object(a) || !Array.isArray(a.observation_ids) || a.observation_ids.length < 1
        || a.observation_ids.length > 16 || new Set(a.observation_ids).size !== a.observation_ids.length
        || !a.observation_ids.every(id => count(id) && id <= 31) || !Array.isArray(a.observations)
        || a.observations.length !== a.observation_ids.length || !["complete", "partial"].includes(String(a.state))
        || !isEvidenceAnalysis({ ...a, schema_version: "poltergeist-evidence-analysis-v1", method: "recorded-evidence-rules-v1",
          execution_status: "completed", summary: "Verified selected evidence", hypotheses: [] })) return false;
  }
  return v.state === "completed" ? (p.kind === "plan" ? v.plan !== null && v.analysis === null : v.analysis !== null && v.plan === null)
    : v.plan === null && v.analysis === null;
}
