/** Settle cancellation even when a transport ignores its signal. */
export function abortable<T>(action: (signal: AbortSignal) => Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    const abort = () => reject(new DOMException("Request cancelled", "AbortError"));
    if (signal.aborted) { abort(); return; }
    signal.addEventListener("abort", abort, { once: true });
    Promise.resolve().then(() => {
      if (signal.aborted) throw new DOMException("Request cancelled", "AbortError");
      return action(signal);
    }).then(resolve, reject).finally(() => signal.removeEventListener("abort", abort));
  });
}
