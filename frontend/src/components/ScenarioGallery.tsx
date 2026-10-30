import { useState } from "react";
import type { GalleryScenario } from "../types/generated";

export function ScenarioIllustration({ family }: { family: GalleryScenario["family"] }) {
  const intersection = family === "intersection", pedestrian = family.includes("pedestrian");
  return <svg className="scenario-illustration" viewBox="0 0 320 150" role="img" aria-label={intersection ? "Ego vehicle approaching a crossing vehicle at a junction" : pedestrian ? "Ego vehicle approaching a pedestrian crossing" : "Ego vehicle following a braking lead vehicle"}>
    <rect width="320" height="150" rx="12" fill="#e9efe6" /><rect y="53" width="320" height="54" fill="#50645a" />
    {intersection && <rect x="165" width="54" height="150" fill="#50645a" />}
    <path d="M0 80h320" stroke="#f1f4e9" strokeWidth="2" strokeDasharray="12 11" />
    {intersection && <path d="M192 0v150" stroke="#f1f4e9" strokeWidth="2" strokeDasharray="12 11" />}
    <rect x="52" y="85" width="42" height="18" rx="5" fill="#f7d9a4" stroke="#253f30" strokeWidth="2" />
    <text x="72" y="129" textAnchor="middle" fontSize="12" fill="#253f30">Ego</text><path d="m106 94 20 0-5-4m5 4-5 4" stroke="#fff" fill="none" strokeWidth="2" />
    {intersection ? <><rect x="196" y="15" width="18" height="34" rx="4" fill="#bce0ef" stroke="#253f30" strokeWidth="2" /><text x="224" y="25" fontSize="11" fill="#253f30">Cross traffic</text></>
      : pedestrian ? <><path d="M188 53v54m8-54v54m8-54v54m8-54v54m8-54v54" stroke="#f1f4e9" strokeWidth="4" />
        <circle cx="204" cy="32" r="5" fill="#b74035" /><path d="M204 38v12m-7-7 7-3 7 3m-7 7-6 8m6-8 6 8" stroke="#b74035" strokeWidth="3" fill="none" />
        {family === "occluded_pedestrian" && <rect x="137" y="41" width="34" height="22" rx="3" fill="#d7dce0" stroke="#253f30" strokeWidth="2" />}<text x="233" y="31" fontSize="11" fill="#253f30">Pedestrian</text></>
      : <><rect x="196" y="85" width="42" height="18" rx="5" fill="#bce0ef" stroke="#253f30" strokeWidth="2" /><path d="M195 88v12" stroke="#b74035" strokeWidth="4" /><text x="217" y="129" textAnchor="middle" fontSize="12" fill="#253f30">Braking lead</text><path d="M104 116h78m-78-4v8m78-8v8" stroke="#50645a" strokeWidth="1.5" /></>}
    <text x="14" y="22" fontSize="10" letterSpacing="1.5" fill="#50645a">SCENARIO SKETCH · NOT TO SCALE</text>
  </svg>;
}

export function ScenarioGallery({ entries, choose, add, starterOnly = false, disabled = false }: {
  entries: GalleryScenario[]; choose: (entry: GalleryScenario) => void; add?: (entry: GalleryScenario) => void; starterOnly?: boolean; disabled?: boolean;
}) {
  const [family, setFamily] = useState("all"), [level, setLevel] = useState("all");
  const visible = entries.filter(e => (starterOnly ? e.level === "starter" : (family === "all" || e.family === family) && (level === "all" || e.level === level)));
  return <div>{!starterOnly && <div className="lab-filter-row"><label>Scenario family<select value={family} onChange={e => setFamily(e.target.value)}><option value="all">All families</option>{[...new Set(entries.map(e => e.family))].map(value => <option key={value} value={value}>{value.replace(/_/g, " ")}</option>)}</select></label><label>Gallery level<select value={level} onChange={e => setLevel(e.target.value)}><option value="all">All levels</option>{["starter", "low", "medium", "high"].map(v => <option key={v} value={v}>{v}</option>)}</select></label></div>}
    <div className="lab-gallery">{visible.map(entry => <article className="lab-gallery-card" key={entry.id}>
      <ScenarioIllustration family={entry.family} /><div className="lab-gallery-copy"><span className="lab-tag">{entry.level === "starter" ? "First experiment" : `${entry.level} stress`} · {entry.scenario.duration_s} simulated seconds</span><h3>{entry.title}</h3><p>{entry.description}</p><p className="lab-learning">{entry.learning_goal}</p><small>{entry.scenario.map} · Seed {entry.scenario.seed}</small>
        <div className="button-row"><button className="button primary" type="button" disabled={disabled} onClick={() => choose(entry)}>Choose {entry.title}</button>{add && <button type="button" className="button secondary" disabled={disabled} onClick={() => add(entry)}>Add to suite</button>}</div>
      </div></article>)}</div>{!visible.length && <p>No gallery scenes match these filters.</p>}
    {!starterOnly && <p className="field-hint">Stress labels describe the recorded calibration conditions. A selected scene's outcome depends on its map, agent, simulator and observations.</p>}
  </div>;
}
