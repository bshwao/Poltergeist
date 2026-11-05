import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../services/api";
import { abortable } from "../services/abortable";

export const labError = (error: unknown) => error instanceof Error ? error.message : "The experiment request could not be completed.";
export function useLabMutation(active: boolean, retryMessage?: string) {
  const visible = useRef(active); visible.current = active;
  const mounted = useRef(true), pending = useRef<AbortController | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; pending.current?.abort(); }; }, []);
  useEffect(() => { if (!active) pending.current?.abort(); }, [active]);
  async function run<T>(action: (signal: AbortSignal) => Promise<T>, accepted: (value: T) => void) {
    if (pending.current || !visible.current) return;
    const controller = new AbortController(); pending.current = controller;
    setBusy(true); setError(null);
    const timer = setTimeout(() => controller.abort(), 15_000);
    try {
      const result = await abortable(action, controller.signal);
      if (mounted.current && visible.current && !controller.signal.aborted) accepted(result);
      else if (mounted.current) setError(`The response could not be confirmed. ${retryMessage ?? "Review saved history before retrying this request."}`);
    } catch (failure) {
      if (mounted.current) {
        const uncertain = controller.signal.aborted || !(failure instanceof ApiError) || failure.status === 0 || failure.status >= 500 || failure.status < 400;
        setError(labError(failure) + (uncertain ? ` The request may have arrived. ${retryMessage ?? "Review saved history before retrying; it is never resubmitted automatically."}` : ""));
      }
    } finally { clearTimeout(timer); if (pending.current === controller) { pending.current = null; if (mounted.current) setBusy(false); } }
  }
  return { busy, error, setError, run };
}

/** Serial polling retains evidence on temporary failures and stops while hidden. */
export function useLabReport<T>(identity: string | null, active: boolean, load: (id: string, signal: AbortSignal) => Promise<T>, terminal: (value: T) => boolean) {
  const reader = useRef(load), finished = useRef(terminal); reader.current = load; finished.current = terminal;
  const [state, setState] = useState<{ id: string; value: T | null; error: string | null; loading: boolean } | null>(null);
  const [attempt, setAttempt] = useState(0);
  const refresh = useCallback(() => setAttempt(n => n + 1), []);
  useEffect(() => {
    if (!identity || !active) return;
    const scope = new AbortController(); let timer: ReturnType<typeof setTimeout> | undefined;
    setState(previous => ({ id: identity, value: previous?.id === identity ? previous.value : null, error: null, loading: true }));
    async function poll() {
      const request = new AbortController(), abort = () => request.abort();
      scope.signal.addEventListener("abort", abort, { once: true });
      const deadline = setTimeout(abort, 12_000);
      try {
        const value = await abortable(signal => reader.current(identity!, signal), request.signal);
        if (scope.signal.aborted || request.signal.aborted) return;
        setState({ id: identity!, value, error: null, loading: false });
        if (!finished.current(value)) timer = setTimeout(poll, 1500);
      } catch (error) {
        if (!scope.signal.aborted) {
          setState(previous => ({ id: identity!, value: previous?.id === identity ? previous.value : null,
            error: request.signal.aborted ? "This read timed out. Refresh to retry." : labError(error), loading: false }));
          if (!(error instanceof ApiError && [404, 409].includes(error.status))) timer = setTimeout(poll, 4000);
        }
      } finally { clearTimeout(deadline); scope.signal.removeEventListener("abort", abort); }
    }
    void poll();
    return () => { scope.abort(); clearTimeout(timer); };
  }, [identity, active, attempt]);
  return { value: state?.id === identity ? state.value : null, error: state?.id === identity ? state.error : null,
    loading: active && Boolean(identity) && (state?.id !== identity || state.loading), refresh };
}
