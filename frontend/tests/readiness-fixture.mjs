export function readinessFixture() {
  const check = (id, label, status = "ready") => ({ id, label, status, detail: `${label} detail.`, next_step: status === "not_checked" ? "Check this explicitly." : null });
  return {
    schema_version: "poltergeist-readiness-v1", scope: "local_core", checked_at: "2026-10-20T12:00:00Z", carla_connection_requested: false,
    synthetic: { status: "ready", summary: "Ready to try synthetic mode. Execution is not tested here.", checks: [
      check("runtime", "Core runtime"), check("packages", "Core dependencies"), check("configuration", "Local configuration"),
      check("storage", "Storage access estimate"), check("execution", "Synthetic execution", "not_checked"),
    ] },
    carla: { status: "missing", summary: "CARLA prerequisites are incomplete or unchecked. Synthetic mode is separate.", checks: [
      check("client", "CARLA Python client", "missing"), check("installation", "Simulator installation", "ready"),
      check("connection", "CARLA connection", "not_checked"), check("server_version", "Server version", "not_checked"),
      check("execution", "Live execution", "not_checked"),
    ] },
  };
}
