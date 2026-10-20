import type { AgentExecutionOptions, AgentRegistration } from "../types/generated";

export const DEFAULT_AGENT_PROCESS_OPTIONS: AgentExecutionOptions = {
  load_timeout_s: 30, reset_timeout_s: 10, step_timeout_s: 2,
  close_timeout_s: 2, terminate_timeout_s: 1, max_message_bytes: 4194304,
};
export interface RegistrationDraft {
  packagePath: string;
  pythonExecutable: string;
  configuration: string;
  modelFiles: string;
  controlMode: "authoritative" | "scripted";
  processOptions: Record<keyof AgentExecutionOptions, string>;
  trusted: boolean;
}
export function emptyRegistrationDraft(): RegistrationDraft {
  return { packagePath: "", pythonExecutable: "", configuration: "{}", modelFiles: "", controlMode: "authoritative",
    processOptions: Object.fromEntries(Object.entries(DEFAULT_AGENT_PROCESS_OPTIONS).map(([key, value]) => [key, String(value)])) as RegistrationDraft["processOptions"], trusted: false };
}
const absoluteLocalPath = (path: string) => !path.startsWith("\\\\") && !path.startsWith("//")
  && (/^[A-Za-z]:[\\/]/.test(path) || path.startsWith("/"));
const finiteJson = (value: unknown): boolean => typeof value === "number" ? Number.isFinite(value)
  : Array.isArray(value) ? value.every(finiteJson)
  : value !== null && typeof value === "object" ? Object.values(value).every(finiteJson) : true;

export function registrationInput(draft: RegistrationDraft): { input?: AgentRegistration; errors: Record<string, string> } {
  const errors: Record<string, string> = {};
  const package_path = draft.packagePath.trim();
  const python_executable = draft.pythonExecutable.trim();
  if (!absoluteLocalPath(package_path)) errors.packagePath = "Enter an absolute local package directory on the backend computer.";
  if (python_executable && !absoluteLocalPath(python_executable)) errors.pythonExecutable = "Use an absolute local Python executable path, or leave it empty for the backend interpreter.";
  let configuration: AgentRegistration["configuration"] = {};
  try {
    const parsed: unknown = JSON.parse(draft.configuration);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed) || !finiteJson(parsed)) throw new Error();
    configuration = parsed as AgentRegistration["configuration"];
  } catch { errors.configuration = "Configuration must be a JSON object with finite numbers."; }
  const model_files = draft.modelFiles.split(/\r?\n/).map(path => path.trim()).filter(Boolean);
  if (model_files.length > 256 || model_files.some(path => !absoluteLocalPath(path)) || new Set(model_files).size !== model_files.length) {
    errors.modelFiles = "Enter up to 256 unique absolute local model file paths, one per line.";
  }
  const process_options = { ...DEFAULT_AGENT_PROCESS_OPTIONS };
  for (const key of Object.keys(process_options) as (keyof AgentExecutionOptions)[]) {
    const value = Number(draft.processOptions[key]);
    if (!draft.processOptions[key].trim() || !Number.isFinite(value) || value <= 0
      || (key === "max_message_bytes" && (!Number.isSafeInteger(value) || value > 67108864))) {
      errors[key] = key === "max_message_bytes" ? "Message size must be a whole number from 1 to 67,108,864 bytes." : "Enter a positive deadline in seconds.";
    } else process_options[key] = value;
  }
  if (!draft.trusted) errors.trusted = "Acknowledge that you trust this local executable code before registering.";
  return Object.keys(errors).length ? { errors } : { errors, input: {
    package_path, python_executable: python_executable || null, configuration, model_files,
    agent_control_mode: draft.controlMode, process_options, trusted_local_code: true,
  } };
}
