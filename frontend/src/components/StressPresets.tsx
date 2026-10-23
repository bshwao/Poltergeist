import { useEffect, useState } from "react";
import { api } from "../services/api";
import type { Scenario } from "../types/generated";
import { readableName } from "./scenario-form";

export function StressPresets({ active, disabled, onLoad }: {
  active: boolean; disabled: boolean; onLoad: (recipe: Scenario) => void;
}) {
  const [recipes, setRecipes] = useState<Scenario[]>([]);
  const [selectedName, setSelectedName] = useState("");
  const [loading, setLoading] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    let disposed = false;
    setLoading(true);
    setFailure(null);
    const timer = globalThis.setTimeout(() => {
      if (disposed) return;
      controller.abort();
      setFailure("Loading stress presets timed out. Retry the catalogue request.");
      setLoading(false);
    }, 10_000);
    void api.listStressPresets({ signal: controller.signal }).then(items => {
      if (disposed || controller.signal.aborted) return;
      setRecipes(items);
      setSelectedName(current => items.some(item => item.name === current) ? current : "");
    }).catch(error => {
      if (disposed || controller.signal.aborted) return;
      setFailure(error instanceof Error ? error.message : "The stress catalogue is unavailable.");
    }).finally(() => {
      globalThis.clearTimeout(timer);
      if (!disposed && !controller.signal.aborted) setLoading(false);
    });
    return () => { disposed = true; globalThis.clearTimeout(timer); controller.abort(); };
  }, [active, revision]);
  const selected = recipes.find(recipe => recipe.name === selectedName);
  return <section className="panel stress-presets" aria-labelledby="stress-presets-heading">
    <div className="panel-header"><h2 id="stress-presets-heading">Collision stress presets</h2><span className="muted">{loading ? "Loading…" : `${recipes.length} presets`}</span></div>
    <div className="form-section">
      <p className="muted">Three stress levels per family, calibrated with the unchanged baseline on Town10HD_Opt. Medium cases can collide.</p>
      {failure ? <div className="notice error-notice" role="alert"><p>{failure}</p><button className="button secondary small" type="button" onClick={() => setRevision(value => value + 1)} disabled={!active || loading}>Retry stress presets</button></div> : <>
        <div className="stress-preset-controls"><div className="field"><label htmlFor="stress-preset">Stress preset</label>
          <select id="stress-preset" value={selectedName} disabled={!active || disabled || loading || !recipes.length} onChange={event => setSelectedName(event.target.value)}>
            <option value="">Choose a preset</option>
            {recipes.map(recipe => <option key={recipe.name} value={recipe.name}>{readableName(recipe.name.replace(/-/g, " "))}</option>)}
          </select></div>
          <button className="button secondary" type="button" disabled={!active || disabled || loading || !selected} onClick={() => { if (selected) onLoad(structuredClone(selected)); }}>Load stress preset</button>
        </div>
        {!loading && !recipes.length && <p role="status">This backend supplied no stress presets.</p>}
        {selected && <p className="field-hint">{selected.map} · {selected.duration_s} s · seed {selected.seed}. Loading replaces the workspace recipe after you confirm any unsaved edits.</p>}
      </>}
      <details><summary>Calibration conditions</summary><p className="muted">The measured runs used CARLA 0.9.16, eight-second recipes and fixed green signals. Ordinary launches keep the server’s traffic signals. Changing the recipe, controller or conditions changes comparability. The baseline uses ground-truth actors, including occluded pedestrians.</p></details>
    </div>
  </section>;
}
