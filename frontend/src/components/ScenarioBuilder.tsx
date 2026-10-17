import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { api, ApiError } from "../services/api";
import type { Scenario, ScenarioRecord, TemplateInfo } from "../types/generated";
import { makeGenerateRequest, parameterDefaults, parseRecipeJson, readableName } from "./scenario-form";
import { bindScenarioForm, reconcileSave, sameRecipe } from "./scenario-workspace";
import { SavedScenarios } from "./SavedScenarios";
import { LaunchPanel } from "./LaunchPanel";

type RequestFailure = { message: string; issues: { path: string; message: string }[] };
type RecipeStatus = "empty" | "generated" | "edited" | "validated";

function describeFailure(error: unknown): RequestFailure {
  if (error instanceof ApiError) return { message: error.message, issues: error.issues };
  return { message: error instanceof Error ? error.message : "The request failed. Please try again.", issues: [] };
}

function TemplateGlyph({ template }: { template: string }) {
  return (
    <svg className="template-icon" viewBox="0 0 40 40" width="40" height="40" aria-hidden="true">
      <path d="M0 20h40M20 0v40" stroke="currentColor" strokeWidth="10" opacity=".12" />
      <path d="M0 20h40M20 0v40" stroke="currentColor" strokeWidth="1" strokeDasharray="3 3" opacity=".45" />
      {template === "intersection" ? <><rect x="10" y="17" width="10" height="6" rx="2" fill="currentColor" /><rect x="17" y="3" width="6" height="10" rx="2" fill="currentColor" opacity=".55" /></> :
        template === "sudden_braking" ? <><rect x="17" y="21" width="6" height="11" rx="2" fill="currentColor" /><rect x="17" y="6" width="6" height="10" rx="2" fill="currentColor" opacity=".55" /><path d="M26 8l4-3m-4 10 4 3" stroke="currentColor" strokeWidth="2" /></> :
          <><rect x="17" y="23" width="6" height="10" rx="2" fill="currentColor" /><circle cx="30" cy="14" r="3" fill="currentColor" /><path d="m30 18-2 6m2-6 5 3m-7 3-4 3" stroke="currentColor" strokeWidth="2" fill="none" />{template === "occluded_pedestrian" && <rect x="24" y="4" width="5" height="9" rx="1" fill="currentColor" opacity=".4" />}</>}
    </svg>
  );
}

export function ScenarioBuilder({ templates }: { templates: TemplateInfo[] }) {
  const [selectedId, setSelectedId] = useState<string | null>(templates[0]?.template ?? null);
  const selected = templates.find((template) => template.template === selectedId);
  const [seed, setSeed] = useState("0");
  const [values, setValues] = useState<Record<string, string>>(() => parameterDefaults(templates[0]?.parameters ?? []));
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [editor, setEditor] = useState("");
  const [editorError, setEditorError] = useState<string | null>(null);
  const [recipe, setRecipe] = useState<Scenario | null>(null);
  const [recipeStatus, setRecipeStatus] = useState<RecipeStatus>("empty");
  const [formChanged, setFormChanged] = useState(false);
  const [busy, setBusy] = useState<"generate" | "validate" | "load" | null>(null);
  const [failure, setFailure] = useState<RequestFailure | null>(null);
  const [savedRecord, setSavedRecord] = useState<ScenarioRecord | null>(null);
  const [libraryVersion, setLibraryVersion] = useState(0);
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const [pendingLoad, setPendingLoad] = useState<ScenarioRecord | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveNotice, setSaveNotice] = useState<{ error: boolean; message: string; conflictId?: string } | null>(null);
  const serial = useRef(0);
  const activeRequest = useRef<AbortController | null>(null);
  const firstInvalid = useRef<HTMLInputElement | null>(null);
  const savedRef = useRef<ScenarioRecord | null>(null);
  const writing = useRef(false);
  const mounted = useRef(true);
  const loadPrompt = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; serial.current += 1; activeRequest.current?.abort(); };
  }, []);
  useEffect(() => { if (pendingLoad) loadPrompt.current?.focus(); }, [pendingLoad]);

  function associate(record: ScenarioRecord | null) {
    savedRef.current = record;
    setSavedRecord(record);
  }

  function invalidate() {
    serial.current += 1;
    activeRequest.current?.abort();
    activeRequest.current = null;
    setBusy(null);
    setLoadingId(null);
    setPendingLoad(null);
    setFailure(null);
  }

  function editForm() {
    invalidate();
    setFieldErrors({});
    if (recipeStatus !== "empty") setFormChanged(true);
  }

  function selectTemplate(template: TemplateInfo) {
    if (template.template === selectedId) return;
    invalidate();
    setSelectedId(template.template);
    setValues(parameterDefaults(template.parameters));
    setSeed("0");
    setFieldErrors({});
    setEditor("");
    setEditorError(null);
    setRecipe(null);
    setRecipeStatus("empty");
    setFormChanged(false);
    associate(null);
    setSaveNotice(null);
  }

  function startRequest(kind: "generate" | "validate" | "load") {
    invalidate();
    const controller = new AbortController();
    activeRequest.current = controller;
    setBusy(kind);
    if (kind !== "load" && editor.trim()) setRecipeStatus("edited");
    return { controller, token: serial.current };
  }

  function acceptRecipe(result: Scenario, status: RecipeStatus) {
    setRecipe(result);
    setEditor(JSON.stringify(result, null, 2));
    setEditorError(null);
    setRecipeStatus(status);
  }

  function syncForm(result: Scenario) {
    const binding = bindScenarioForm(result, templates);
    setSelectedId(binding.selectedId);
    setSeed(binding.seed);
    setValues(binding.values);
    setFieldErrors({});
    setFormChanged(false);
  }

  function applyRecord(record: ScenarioRecord) {
    invalidate();
    associate(record);
    acceptRecipe(record.scenario, "validated");
    syncForm(record.scenario);
    setSaveNotice(null);
  }

  const parsedDraft = parseRecipeJson(editor);
  const hasFormDraft = seed !== "0" || selected && selected.parameters.some((parameter) => values[parameter.name] !== String(parameter.default));
  const dirty = formChanged || (savedRecord ? Boolean(parsedDraft.error) || !sameRecipe(parsedDraft.recipe, savedRecord.scenario) : Boolean(editor.trim()) || Boolean(hasFormDraft));
  const ready = recipe !== null && recipeStatus === "validated" && !formChanged && busy === null && !editorError;

  async function loadSaved(id: string) {
    const preserveDraft = dirty;
    const { controller, token } = startRequest("load");
    setLoadingId(id);
    try {
      const record = await api.getScenario(id, { signal: controller.signal });
      if (token !== serial.current || controller.signal.aborted) return;
      if (preserveDraft) setPendingLoad(record);
      else applyRecord(record);
    } catch (error) {
      if (token === serial.current && !controller.signal.aborted) setFailure(describeFailure(error));
    } finally {
      if (token === serial.current) { setBusy(null); setLoadingId(null); activeRequest.current = null; }
    }
  }

  async function save(kind: "create" | "update") {
    if (!ready || !recipe || writing.current || kind === "update" && !savedRef.current) return;
    const snapshot = structuredClone(recipe);
    const source = savedRef.current;
    const token = serial.current;
    writing.current = true;
    setSaving(true);
    setSaveNotice(null);
    try {
      const persisted = kind === "update" && source
        ? await api.updateScenario(source.id, snapshot, source.revision)
        : await api.createScenario(snapshot);
      if (!mounted.current) return;
      const result = reconcileSave(savedRef.current, source, persisted, kind, token === serial.current);
      associate(result.record);
      if (result.replaceDraft) { acceptRecipe(persisted.scenario, "validated"); syncForm(persisted.scenario); }
      setLibraryVersion((value) => value + 1);
      setSaveNotice({ error: false, message: `Saved “${persisted.scenario.name}” at revision ${persisted.revision}.${result.replaceDraft ? "" : " Your newer workspace edits are preserved."}` });
    } catch (error) {
      if (!mounted.current) return;
      const failure = describeFailure(error);
      const conflict = error instanceof ApiError && error.status === 409 && source;
      const uncertain = error instanceof ApiError && (error.status === 0 || error.status >= 200 && error.status < 300);
      setSaveNotice({ error: true, message: conflict
        ? "This saved recipe changed elsewhere. Your draft is preserved. Load the latest revision, or save your draft as a separate copy."
        : `${failure.message}${uncertain ? " The save may have completed. Refresh the saved library before trying again." : ""}`,
        ...(conflict ? { conflictId: source.id } : {}),
      });
    } finally {
      writing.current = false;
      if (mounted.current) setSaving(false);
    }
  }

  async function generate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    invalidate();
    const result = makeGenerateRequest(selected, seed, values);
    if (result.errors) {
      setFieldErrors(result.errors);
      requestAnimationFrame(() => firstInvalid.current?.focus());
      return;
    }
    setFieldErrors({});
    const { controller, token } = startRequest("generate");
    const currentName = parsedDraft.recipe?.name;
    const keepName = recipe?.template === selected.template && typeof currentName === "string" && currentName.trim();
    try {
      const generated = await api.generateScenario(result.request, { signal: controller.signal });
      if (token !== serial.current || controller.signal.aborted) return;
      if (keepName) generated.name = currentName as string;
      acceptRecipe(generated, "generated");
      syncForm(generated);
    } catch (error) {
      if (token !== serial.current || controller.signal.aborted) return;
      const failure = describeFailure(error);
      setFailure(failure);
      const errors: Record<string, string> = {};
      for (const issue of failure.issues) {
        const name = issue.path.replace(/^parameters\./, "");
        if (name === "seed" || selected.parameters.some((parameter) => parameter.name === name)) errors[name] = issue.message;
      }
      setFieldErrors(errors);
    } finally {
      if (token === serial.current) {
        setBusy(null);
        activeRequest.current = null;
      }
    }
  }

  async function validate() {
    invalidate();
    const result = parseRecipeJson(editor);
    if (result.error) {
      setEditorError(result.error);
      return;
    }
    setEditorError(null);
    const { controller, token } = startRequest("validate");
    try {
      const normalized = await api.validateScenario(result.recipe, { signal: controller.signal });
      if (token !== serial.current || controller.signal.aborted) return;
      acceptRecipe(normalized, "validated");
      syncForm(normalized);
    } catch (error) {
      if (token !== serial.current || controller.signal.aborted) return;
      setFailure(describeFailure(error));
      if (error instanceof ApiError && error.status === 422) setEditorError("The server found recipe errors. Review the details below and edit the JSON.");
    } finally {
      if (token === serial.current) {
        setBusy(null);
        activeRequest.current = null;
      }
    }
  }

  const summary = recipe ?? selected?.scenario;
  if (!summary) return <section className="panel"><p className="muted">Choose a template to begin.</p></section>;
  const isCurrent = !formChanged && recipeStatus !== "edited";
  const statusLabel = busy === "generate" ? "Generating…" : busy === "validate" ? "Validating…" : formChanged ? "Form changed" : recipeStatus === "validated" ? "Recipe validated" : recipeStatus === "generated" ? "Generated" : recipeStatus === "edited" ? "Draft · needs validation" : "Template preview";
  let invalidAssigned = false;

  return (
    <div className="scenario-builder">
      <SavedScenarios currentId={savedRecord?.id ?? null} refreshKey={libraryVersion} loadingId={loadingId} writing={saving} onLoad={(id) => void loadSaved(id)} onDeleted={(id) => { if (loadingId === id || pendingLoad?.id === id) invalidate(); if (savedRef.current?.id === id) associate(null); }} />
      {pendingLoad && <div className="notice warning-notice load-confirmation" role="region" aria-label="Replace workspace draft" tabIndex={-1} ref={loadPrompt}>
        <strong>Replace the current draft?</strong><p>Load “{pendingLoad.scenario.name}”, revision {pendingLoad.revision}. Unsaved workspace edits will be replaced.</p><div className="saved-item-actions"><button className="button primary small" type="button" onClick={() => applyRecord(pendingLoad)}>Load saved recipe</button><button className="button secondary small" type="button" onClick={() => setPendingLoad(null)}>Keep current draft</button></div>
      </div>}
      <section className="template-section" aria-labelledby="template-heading">
        <div className="section-heading"><div><p className="eyebrow">01 / Choose a starting point</p><h2 id="template-heading">Scenario templates</h2></div><span className="muted">{templates.length} available templates</span></div>
        <div className="template-grid" role="group" aria-label="Scenario template">
          {templates.map((template) => (
            <button className={`template-card${selectedId === template.template ? " selected" : ""}`} type="button" key={template.template} aria-pressed={selectedId === template.template} onClick={() => selectTemplate(template)}>
              <TemplateGlyph template={template.template} />
              <span className="template-card-body"><strong>{readableName(template.template)}</strong><span>{template.scenario.description}</span></span>
              <span className="template-selected" aria-hidden="true">{selectedId === template.template ? "✓" : ""}</span>
            </button>
          ))}
        </div>
      </section>

      <div className="builder-grid">
        {selected ? <form className="panel builder-form" onSubmit={generate} noValidate aria-busy={busy === "generate"}>
          <div className="panel-header"><div><p className="eyebrow">02 / Configure the recipe</p><h2>Parameters</h2></div><button className="button text-button" type="button" onClick={() => { editForm(); setValues(parameterDefaults(selected.parameters)); setSeed("0"); }}>Reset defaults</button></div>
          <p className="muted">Use fixed values within the template bounds. The same template, seed, and values produce the same recipe.</p>
          <div className="field seed-field">
            <label htmlFor="scenario-seed" className="field-label">Random seed</label>
            <input id="scenario-seed" type="number" inputMode="numeric" min={0} max={2 ** 31 - 1} step={1} value={seed} aria-invalid={Boolean(fieldErrors.seed)} aria-describedby={`seed-help${fieldErrors.seed ? " seed-error" : ""}`} ref={fieldErrors.seed ? firstInvalid : undefined} onChange={(event) => { editForm(); setSeed(event.target.value); }} />
            <span id="seed-help" className="field-hint">Whole number from 0 to 2,147,483,647.</span>
            {fieldErrors.seed && <span id="seed-error" className="field-error">{fieldErrors.seed}</span>}
          </div>
          <div className="parameter-grid">
            {selected.parameters.map((parameter) => {
              const error = fieldErrors[parameter.name];
              const ref = !fieldErrors.seed && error && !invalidAssigned ? firstInvalid : undefined;
              if (ref) invalidAssigned = true;
              const id = `parameter-${parameter.name}`;
              return (
                <div className="field" key={parameter.name}>
                  <label className="field-label" htmlFor={id}>{readableName(parameter.name.replace(/_(mps2|mps|pct|deg|m|s)$/, ""))}{" "}<span className="field-unit">{parameter.unit}</span></label>
                  <input id={id} type="number" inputMode={parameter.integer ? "numeric" : "decimal"} min={parameter.minimum} max={parameter.maximum} step={parameter.integer ? 1 : "any"} value={values[parameter.name] ?? ""} aria-invalid={Boolean(error)} aria-describedby={`${id}-help${error ? ` ${id}-error` : ""}`} ref={ref} onChange={(event) => { editForm(); setValues((current) => ({ ...current, [parameter.name]: event.target.value })); }} />
                  <span className="field-hint" id={`${id}-help`}>{parameter.description} Range {parameter.minimum}–{parameter.maximum}{parameter.unit ? ` ${parameter.unit}` : ""}.</span>
                  {error && <span className="field-error" id={`${id}-error`}>{error}</span>}
                </div>
              );
            })}
          </div>
          {Object.keys(fieldErrors).length > 0 && <p className="field-error" role="alert">Check the highlighted fields before generating.</p>}
          <div className="form-actions"><button className="button primary" type="submit" disabled={busy === "generate"}>{busy === "generate" ? "Generating…" : recipe ? "Regenerate recipe" : "Generate recipe"}<span aria-hidden="true">↗</span></button><span className="muted">Recipe generation does not run a simulation.</span></div>
        </form> : <section className="panel custom-recipe-panel"><div className="panel-header"><div><p className="eyebrow">CUSTOM RECIPE</p><h2>Edit the complete scenario</h2></div></div><p>This recipe uses its own actors and events. Edit its name or full JSON, then validate before saving or launching. Choosing a template starts a new draft.</p></section>}

        <section className="panel recipe-panel" aria-labelledby="recipe-heading">
          <div className="panel-header"><div><p className="eyebrow">03 / Inspect & validate</p><h2 id="recipe-heading">Recipe preview</h2></div><span className={`badge ${busy ? "badge-neutral" : recipeStatus === "validated" && !formChanged ? "badge-success" : formChanged || recipeStatus === "edited" ? "badge-warning" : "badge-neutral"}`} role="status">{statusLabel}</span></div>
          <div className="recipe-title"><strong>{summary.name}</strong><span className="muted">{recipe ? !isCurrent ? "Last normalized recipe" : "Normalized recipe" : "Bundled template"}</span></div>
          <div className="field recipe-name-field"><label htmlFor="recipe-name">Recipe name</label><input id="recipe-name" type="text" value={typeof parsedDraft.recipe?.name === "string" ? parsedDraft.recipe.name : ""} disabled={!parsedDraft.recipe} placeholder="Generate or load a recipe" onChange={(event) => { if (!parsedDraft.recipe) return; invalidate(); setEditor(JSON.stringify({ ...parsedDraft.recipe, name: event.target.value }, null, 2)); setRecipeStatus("edited"); setEditorError(null); }} /><span className="field-hint">Changing the name requires validation again.</span></div>
          <dl className="recipe-facts"><div><dt>Map</dt><dd>{summary.map}</dd></div><div><dt>Duration</dt><dd>{summary.duration_s} s</dd></div><div><dt>Time step</dt><dd>{summary.fixed_delta_seconds} s</dd></div><div><dt>Actors / events</dt><dd>{summary.actors.length} / {summary.events.length}</dd></div></dl>
          <div className="actor-list" aria-label="Actors in the last normalized recipe">{summary.actors.map((actor, index) => <div className="actor-row" key={actor.id ?? index}><span className={`actor-dot ${actor.type === "ego_vehicle" ? "ego" : ""}`} aria-hidden="true" /><span>{actor.id ?? actor.type}<small>{readableName(actor.type)}</small></span><strong>{actor.speed_mps} <small>m/s</small></strong></div>)}</div>
          {formChanged && <p className="notice warning-notice">Parameters changed. Regenerate to apply them, or validate the JSON to keep that recipe and restore its parameter values.</p>}
          <div className="editor-heading"><label htmlFor="scenario-json" className="field-label">Full recipe JSON</label><span className="muted">Editable</span></div>
          <textarea id="scenario-json" className="editor json-editor" value={editor} spellCheck={false} aria-invalid={Boolean(editorError)} aria-describedby={`recipe-json-help${editorError ? " recipe-json-error" : ""}`} placeholder="Generate a recipe or paste a complete scenario JSON object." onChange={(event) => { invalidate(); setEditor(event.target.value); setRecipeStatus("edited"); setEditorError(null); }} />
          <p id="recipe-json-help" className="field-hint">Validation checks recipe structure and normalizes actor IDs. Map placement is checked by the simulation adapter.</p>
          {editorError && <p id="recipe-json-error" className="field-error" role="alert">{editorError}</p>}
          <div className="form-actions"><button className="button secondary" type="button" onClick={validate} disabled={!editor.trim() || busy === "validate"}>{busy === "validate" ? "Validating…" : "Validate JSON"}</button><span className="muted" aria-live="polite">{recipeStatus === "validated" ? "The current JSON passed recipe validation." : "JSON edits require validation."}</span></div>
        </section>
      </div>
      <div aria-live="polite" aria-atomic="true">{failure && <section className="notice error-notice" role="alert"><strong>Recipe request failed</strong><p>{failure.message}</p>{failure.issues.length > 0 && <ul>{failure.issues.map((issue, index) => <li key={`${issue.path}-${index}`}><code>{issue.path || "recipe"}</code>: {issue.message}</li>)}</ul>}<p>Review the fields or JSON, then try the request again.</p></section>}</div>
      <section className="panel save-panel" aria-labelledby="save-heading"><div className="panel-header"><div><p className="eyebrow">KEEP YOUR RECIPE</p><h2 id="save-heading">{savedRecord ? "Update saved scenario" : "Save to your library"}</h2></div><span className="badge badge-neutral">{savedRecord ? `Revision ${savedRecord.revision}${dirty ? " · Unsaved edits" : " · Saved"}` : "New draft"}</span></div>
        <div className="save-content"><p>{savedRecord ? `Editing “${savedRecord.scenario.name}”. Updates check its saved revision.` : "Save a validated recipe so you can load it again later."}</p>{savedRecord && <code>{savedRecord.id}</code>}<p className="muted">{ready ? "The current recipe is validated and ready to save." : "Validate the current JSON before saving or launching."}</p></div>
        {saveNotice && <div className={`notice ${saveNotice.error ? "error-notice" : "success-notice"}`} role={saveNotice.error ? "alert" : "status"}><p>{saveNotice.message}</p>{saveNotice.conflictId && <button className="button secondary small" type="button" onClick={() => void loadSaved(saveNotice.conflictId!)} disabled={saving}>Load latest revision</button>}</div>}
        <div className="form-actions"><button className="button primary" type="button" disabled={!ready || saving || Boolean(savedRecord) && !dirty} onClick={() => void save(savedRecord ? "update" : "create")}>{saving ? "Saving…" : savedRecord ? "Update saved recipe" : "Save new recipe"}</button>{savedRecord && <button className="button secondary" type="button" disabled={!ready || saving} onClick={() => void save("create")}>Save a copy</button>}</div>
      </section>
      <LaunchPanel scenario={ready ? recipe : null} disabledReason={ready ? null : busy === "load" ? "Loading a saved recipe…" : formChanged ? "Regenerate the changed parameters or validate the JSON recipe first." : "Generate or load a recipe, then validate its current JSON before launching."} />
    </div>
  );
}
