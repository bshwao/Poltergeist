import { useCallback, useEffect, useState } from "react";
import { health, listTemplates } from "../services/api";
import type { TemplateInfo } from "../types";

export type Connection = "checking" | "online" | "offline";

export function useWorkspace() {
  const [attempt, setAttempt] = useState(0);
  const [connection, setConnection] = useState<Connection>("checking");
  const [templates, setTemplates] = useState<TemplateInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    const timeout = window.setTimeout(() => controller.abort(), 10_000);
    setConnection("checking");
    setLoading(true);
    setError(null);
    void health({ signal: controller.signal }).then(
      (result) => { if (current) setConnection(result.status === "ok" ? "online" : "offline"); },
      () => { if (current) setConnection("offline"); },
    );
    void listTemplates({ signal: controller.signal }).then(
      (result) => {
        if (!current) return;
        if (result.length > 0) setTemplates(result);
        else setError("The API returned no scenario templates. Check the backend and retry.");
      },
      (failure: unknown) => {
        if (current) setError(controller.signal.aborted
          ? "The API did not respond in time. Check that the backend is running and retry."
          : failure instanceof Error ? failure.message : "Could not load scenario templates.");
      },
    ).finally(() => { if (current) setLoading(false); });
    return () => {
      current = false;
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [attempt]);

  const refresh = useCallback(() => setAttempt((value) => value + 1), []);
  return { connection, templates, loading, error, refresh };
}
