import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../services/api";
import type { SimulationProgress, SimulationRecord, SimulationStatus } from "../types/generated";

export const isTerminal = (status: SimulationStatus) => ["completed", "failed", "cancelled"].includes(status);
type Snapshot = { id: string; record: SimulationRecord | null; progress: SimulationProgress | null; error: string | null; loading: boolean };
type Session = { id: string; controller: AbortController; cancel: AbortController | null };

/** One serial poll per selected, visible run. Revisions prevent cancellation races. */
export function useSimulation(id: string | null, active = true) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const latest = useRef<Snapshot | null>(null);
  const updateSnapshot = useCallback((update: (previous: Snapshot | null) => Snapshot | null) => {
    const next = update(latest.current);
    latest.current = next;
    setSnapshot(next);
  }, []);
  const [attempt, setAttempt] = useState(0);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const session = useRef<Session | null>(null);
  const refresh = useCallback(() => setAttempt(value => value + 1), []);

  useEffect(() => {
    setCancelling(false);
    setCancelError(null);
    if (!id || !active) return;
    const scope: Session = { id, controller: new AbortController(), cancel: null };
    session.current = scope;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let current: SimulationRecord | null = null;
    let failures = 0;
    const live = () => session.current === scope && !scope.controller.signal.aborted;
    updateSnapshot(previous => ({ id, record: previous?.id === id ? previous.record : null, progress: previous?.id === id ? previous.progress : null, error: null, loading: true }));
    const knownRevision = () => latest.current?.id === id ? Math.max(latest.current.record?.revision ?? 0, latest.current.progress?.revision ?? 0) : 0;
    const schedule = () => { timer = setTimeout(() => void poll(), 1_200); };

    async function poll() {
      const request = new AbortController();
      const abort = () => request.abort();
      scope.controller.signal.addEventListener("abort", abort, { once: true });
      const deadline = setTimeout(abort, 12_000);
      try {
        if (!current) {
          current = await api.getSimulation(id!, { signal: request.signal });
          if (!live()) return;
          const record = current;
          if (record.revision < knownRevision()) { updateSnapshot(previous => previous ? { ...previous, loading: false } : previous); current = null; schedule(); return; }
          updateSnapshot(previous => previous?.id === id && previous.record && previous.record.revision > record.revision
            ? { ...previous, loading: false, error: null } : { id: id!, record, progress: null, error: null, loading: false });
        }
        if (!isTerminal(current.simulation.status)) {
          const progress = await api.getSimulationProgress(id!, { signal: request.signal });
          if (!live()) return;
          if (progress.revision < knownRevision()) { schedule(); return; }
          updateSnapshot(previous => previous?.id === id
            && progress.revision >= (previous.record?.revision ?? 0)
            && progress.revision >= (previous.progress?.revision ?? 0)
            ? { ...previous, progress, loading: false, error: null } : previous);
          if (!isTerminal(progress.status)) {
            failures = 0;
            schedule();
            return;
          }
          current = await api.getSimulation(id!, { signal: request.signal });
          if (!live()) return;
          if (current.revision < knownRevision()) { current = null; schedule(); return; }
        }
        if (!current.result) {
          const result = await api.getSimulationResult(id!, { signal: request.signal });
          if (!live()) return;
          current = { ...current, simulation: result.simulation, result };
        }
        const record = current;
        updateSnapshot(previous => previous?.id === id && previous.record && previous.record.revision > record.revision
          ? previous : { id: id!, record, progress: null, error: null, loading: false });
      } catch (error) {
        if (!live()) return;
        failures += 1;
        updateSnapshot(previous => ({ id: id!, record: previous?.id === id ? previous.record : null, progress: previous?.id === id ? previous.progress : null,
          loading: false, error: request.signal.aborted ? "The run request timed out. Retrying shortly." : error instanceof ApiError ? error.message : "Could not refresh this run. Retrying shortly." }));
        // A missing run needs an explicit retry; temporary failures retain the last observation.
        if (!(error instanceof ApiError && error.status === 404)) timer = setTimeout(() => void poll(), Math.min(10_000, failures * 2_500));
      } finally {
        clearTimeout(deadline);
        scope.controller.signal.removeEventListener("abort", abort);
      }
    }
    void poll();
    return () => {
      scope.controller.abort();
      scope.cancel?.abort();
      clearTimeout(timer);
      if (session.current === scope) session.current = null;
    };
  }, [id, active, attempt, updateSnapshot]);

  const cancel = useCallback(async () => {
    const scope = session.current;
    if (!id || !active || !scope || scope.id !== id || scope.cancel) return;
    const controller = new AbortController();
    scope.cancel = controller;
    setCancelling(true);
    setCancelError(null);
    const deadline = setTimeout(() => controller.abort(), 12_000);
    try {
      const record = await api.cancelSimulation(id, { signal: controller.signal });
      if (session.current !== scope || scope.controller.signal.aborted) return;
      updateSnapshot(previous => previous?.id === id && (!previous.record || record.revision >= previous.record.revision)
        ? { ...previous, record, progress: previous.progress && previous.progress.revision > record.revision ? previous.progress : null } : previous);
      refresh();
    } catch (error) {
      if (session.current !== scope || scope.controller.signal.aborted) return;
      setCancelError(controller.signal.aborted || !(error instanceof ApiError) || error.status === 0 || error.status >= 200 && error.status < 300 || error.status >= 500
        ? "Cancellation could not be confirmed. Refresh the run before retrying; the request may have reached the worker."
        : error.message);
    } finally {
      clearTimeout(deadline);
      if (scope.cancel === controller) scope.cancel = null;
      if (session.current === scope) setCancelling(false);
    }
  }, [id, active, refresh, updateSnapshot]);

  const visible = snapshot?.id === id ? snapshot : null;
  return { record: visible?.record ?? null, progress: visible?.progress ?? null,
    loading: active && Boolean(id) && (!visible || visible.loading), error: visible?.error ?? null,
    refresh, cancel, cancelling, cancelError };
}
