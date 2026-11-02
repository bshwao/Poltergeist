import type { GitHubInputs, GitHubPublication, GitHubStatus } from "../types/generated";
import { isRegressionPolicy } from "./analysis-contract";

const object = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === "object" && !Array.isArray(v);
const count = (v: unknown): v is number => typeof v === "number" && Number.isSafeInteger(v) && v >= 0;
const uuid = (v: unknown) => typeof v === "string" && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(v);
const hash = (v: unknown, length = 64) => typeof v === "string" && new RegExp(`^[0-9a-f]{${length}}$`).test(v);
const repo = (v: unknown) => typeof v === "string" && /^[A-Za-z0-9][A-Za-z0-9_.-]*\/[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(v);
const date = (v: unknown) => typeof v === "string" && /(?:Z|[+-]\d{2}:\d{2})$/.test(v) && Number.isFinite(Date.parse(v));
const conclusions = ["success", "failure", "action_required", "cancelled"];
const disclosures = ["metrics", "fingerprints", "run_ids", "scenarios"];

export function githubUrl(value: unknown, repository: string): value is string {
  try {
    if (typeof value !== "string") return false;
    const url = new URL(value);
    return url.protocol === "https:" && url.hostname === "github.com" && !url.port && !url.username && !url.password
      && !url.search && !url.hash && url.pathname.toLowerCase().startsWith(`/${repository.toLowerCase()}/`);
  } catch { return false; }
}

export function isGitHubStatus(v: unknown): v is GitHubStatus {
  return object(v) && typeof v.enabled === "boolean" && typeof v.configured === "boolean" && (v.repository === "" || repo(v.repository))
    && (v.app_id === null || count(v.app_id) && v.app_id > 0) && typeof v.check_name === "string" && v.check_name.length > 0
    && count(v.daily_publish_limit) && v.daily_publish_limit >= 1 && v.daily_publish_limit <= 1000 && v.scope === "local_api_only";
}

export function isGitHubInputs(v: unknown): v is GitHubInputs {
  return object(v) && uuid(v.operation_id) && Array.isArray(v.controllers) && v.controllers.length <= 8
    && v.controllers.every((item, index) => object(item) && item.agent_index === index && typeof item.supported === "boolean"
      && (item.package_path === null || typeof item.package_path === "string") && count(item.package_files)
      && Array.isArray(item.models) && item.models.length <= 256 && item.models.every((model, i) => object(model)
        && model.index === i && hash(model.sha256) && count(model.size_bytes)));
}

function proof(v: unknown): boolean {
  return object(v) && hash(v.commit, 40) && hash(v.tree, 40) && typeof v.package_prefix === "string" && hash(v.inventory_digest)
    && ["package_files", "external_models", "raw_files", "crlf_files", "lfs_pins", "manifest_pins"].every(key => count(v[key]))
    && Number(v.package_files) > 0 && Number(v.package_files) + Number(v.external_models) <= 256
    && Number(v.package_files) + Number(v.external_models) === Number(v.raw_files) + Number(v.crlf_files) + Number(v.lfs_pins) + Number(v.manifest_pins);
}

export function isGitHubPublication(v: unknown): v is GitHubPublication {
  if (!object(v) || v.schema_version !== "poltergeist-github-check-v1" || !uuid(v.id)
      || !["preview", "publishing", "published", "failed", "uncertain"].includes(String(v.state)) || typeof v.message !== "string"
      || !object(v.preview) || v.preview.id !== v.id || !uuid(v.preview.operation_id) || !repo(v.preview.repository)
      || !count(v.preview.app_id) || v.preview.app_id < 1 || !count(v.preview.candidate_index) || v.preview.candidate_index < 1 || v.preview.candidate_index > 7
      || !isRegressionPolicy(v.preview.policy) || typeof v.preview.execution_status !== "string" || ["queued", "running"].includes(v.preview.execution_status)
      || !conclusions.includes(String(v.preview.conclusion)) || !hash(v.preview.digest) || !hash(v.preview.source_digest) || !hash(v.preview.context_digest)
      || !date(v.preview.created_at) || !date(v.preview.expires_at) || Date.parse(String(v.preview.expires_at)) <= Date.parse(String(v.preview.created_at))
      || !proof(v.preview.reference) || !proof(v.preview.candidate) || !Array.isArray(v.preview.disclose)
      || v.preview.disclose.length > 4 || new Set(v.preview.disclose).size !== v.preview.disclose.length
      || !v.preview.disclose.every(item => disclosures.includes(String(item))) || typeof v.preview.payload !== "string"
      || new TextEncoder().encode(v.preview.payload).length > 60000) return false;
  const p = v.preview;
  try {
    const body = JSON.parse(p.payload as string);
    if (!object(body) || !object(p.candidate) || body.head_sha !== p.candidate.commit || body.status !== "completed"
        || body.conclusion !== p.conclusion || typeof body.name !== "string" || body.name.length > 100
        || typeof body.external_id !== "string" || !/^poltergeist:[0-9a-f]{64}$/.test(body.external_id)
        || !object(body.output) || typeof body.output.title !== "string" || typeof body.output.summary !== "string") return false;
    if (p.execution_status !== "completed" && body.conclusion === "success") return false;
  } catch { return false; }
  if (v.check_run_id !== null && (!count(v.check_run_id) || v.check_run_id < 1)) return false;
  if (v.check_url !== null && !githubUrl(v.check_url, String(p.repository))) return false;
  return v.state !== "published" || v.check_run_id !== null && v.check_url !== null;
}
