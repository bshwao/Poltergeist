import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../services/api";
import type { ScenarioRecord } from "../types/generated";

interface Props {
  currentId: string | null;
  refreshKey: number;
  loadingId: string | null;
  writing: boolean;
  onLoad: (id: string) => void;
  onDeleted: (id: string) => void;
}

const PAGE_SIZE = 8;

export function SavedScenarios({ currentId, refreshKey, loadingId, writing, onLoad, onDeleted }: Props) {
  const [open, setOpen] = useState(false);
  const [offset, setOffset] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const [items, setItems] = useState<ScenarioRecord[]>([]);
  const [hasNext, setHasNext] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<ScenarioRecord | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const mounted = useRef(true);
  const onDeletedRef = useRef(onDeleted);
  onDeletedRef.current = onDeleted;

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    let active = true;
    setLoading(true);
    setError(null);
    setItems([]);
    setHasNext(false);
    void api.listScenarios({ limit: PAGE_SIZE + 1, offset }, { signal: controller.signal }).then(
      (records) => {
        if (!active) return;
        setItems(records.slice(0, PAGE_SIZE));
        setHasNext(records.length > PAGE_SIZE);
      },
      (failure: unknown) => { if (active) setError(failure instanceof Error ? failure.message : "Could not load saved scenarios."); },
    ).finally(() => { if (active) setLoading(false); });
    return () => { active = false; controller.abort(); };
  }, [open, offset, attempt, refreshKey]);

  async function remove(record: ScenarioRecord) {
    if (deleting || writing || loadingId) return;
    setDeleting(record.id);
    setError(null);
    setMessage(null);
    try {
      await api.deleteScenario(record.id, record.revision);
      onDeletedRef.current(record.id);
      if (!mounted.current) return;
      setConfirm(null);
      setMessage(`Deleted “${record.scenario.name}”. The workspace draft is unchanged.`);
      if (items.length === 1 && offset > 0) setOffset(Math.max(0, offset - PAGE_SIZE));
      else setAttempt((value) => value + 1);
    } catch (failure) {
      if (!mounted.current) return;
      const detail = failure instanceof Error ? failure.message : "Delete failed.";
      setError(failure instanceof ApiError && failure.status === 409
        ? `This recipe changed or is referenced by a run. Nothing was deleted. Refresh the library before trying again. ${detail}`
        : `${detail}${failure instanceof ApiError && (failure.status === 0 || failure.status >= 200 && failure.status < 300) ? " The deletion may have completed; refresh the library before retrying." : ""}`);
      setConfirm(null);
    } finally { if (mounted.current) setDeleting(null); }
  }

  return <section className="panel saved-library" aria-labelledby="saved-library-heading">
    <div className="panel-header"><div><p className="eyebrow">YOUR LIBRARY</p><h2 id="saved-library-heading">Saved scenarios</h2></div><button className="button secondary small" type="button" onClick={() => setOpen((value) => !value)} aria-expanded={open} aria-controls="saved-library-content">{open ? "Hide library" : "Open library"}</button></div>
    <div id="saved-library-content" hidden={!open}>
      <div className="library-toolbar"><p>Load a saved recipe or manage your local library.</p><button className="button secondary small" type="button" disabled={loading || Boolean(deleting)} onClick={() => { setConfirm(null); setAttempt((value) => value + 1); }}>Refresh saved scenarios</button></div>
      {error && <div className="notice error-notice" role="alert">{error}</div>}
      {message && <p className="notice success-notice" role="status">{message}</p>}
      {loading && <p className="library-status" role="status">Loading saved scenarios…</p>}
      {!loading && !error && items.length === 0 && <p className="library-status">No saved scenarios on this page. Validate a recipe below, then save it to your library.</p>}
      <ul className="saved-scenario-list" aria-label="Saved scenarios">
        {items.map((record) => <li key={record.id} className={currentId === record.id ? "current" : ""}>
          <div className="saved-item-heading"><div><strong>{record.scenario.name}</strong><span>{record.scenario.map} · {record.scenario.actors.length} actors · Revision {record.revision}{currentId === record.id ? " · In workspace" : ""}</span><code>{record.id}</code></div><div className="saved-item-actions"><button type="button" className="button secondary small" onClick={() => onLoad(record.id)} disabled={Boolean(deleting) || writing || loadingId === record.id} aria-label={`Load ${record.scenario.name}`}>{loadingId === record.id ? "Loading…" : "Load"}</button><button type="button" className="button small danger-button" onClick={() => setConfirm(record)} disabled={Boolean(deleting) || writing || loadingId !== null} aria-label={`Delete ${record.scenario.name}`}>Delete</button></div></div>
          {confirm?.id === record.id && <div className="delete-confirm" role="group" aria-label={`Confirm deletion of ${confirm.scenario.name}`}><p>Delete “{confirm.scenario.name}”, revision {confirm.revision}, from the saved library? Your workspace draft will remain available.</p><div><button className="button small danger-button" type="button" disabled={Boolean(deleting) || writing || loadingId !== null} onClick={() => { if (confirm) void remove(confirm); }}>{deleting === record.id ? "Deleting…" : "Confirm delete"}</button><button className="button secondary small" type="button" disabled={Boolean(deleting)} onClick={() => setConfirm(null)}>Keep recipe</button></div></div>}
        </li>)}
      </ul>
      <div className="library-pagination"><button type="button" className="button secondary small" disabled={offset === 0 || loading || Boolean(deleting)} onClick={() => { setConfirm(null); setOffset((value) => Math.max(0, value - PAGE_SIZE)); }}>Previous page</button><span>Page {Math.floor(offset / PAGE_SIZE) + 1}</span><button type="button" className="button secondary small" disabled={!hasNext || loading || Boolean(deleting)} onClick={() => { setConfirm(null); setOffset((value) => value + PAGE_SIZE); }}>Next page</button></div>
    </div>
  </section>;
}
