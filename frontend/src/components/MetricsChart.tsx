import { useEffect, useId, useMemo, useState } from "react";
import type { KeyboardEvent, PointerEvent } from "react";
import { api, ApiError } from "../services/api";
import type { TelemetryPage } from "../types/generated";
import { measurement, seriesSegments, telemetryPoints } from "./visual-data";
import type { TelemetryPoint } from "./visual-data";
import { locateFrame } from "./evidence-navigation";
import type { EvidenceFocus } from "./evidence-navigation";

const PAGE_SIZE = 200;

function SeriesChart({ points, metric, label, unit, maxSampleGapS }: { points: TelemetryPoint[]; metric: "speed" | "deceleration"; label: string; unit: string; maxSampleGapS?: number }) {
  const title = useId();
  const description = useId();
  const tooltip = useId();
  const [inspected, setInspected] = useState<TelemetryPoint | null>(null);
  const [tooltipPosition, setTooltipPosition] = useState<{ left: number; top: number; width: number } | null>(null);
  // A point belongs to one loaded page; never reuse it after paging or changing runs.
  const point = inspected && points.includes(inspected) ? inspected : null;
  const segments = seriesSegments(points, metric, maxSampleGapS);
  const values = points.flatMap(point => point[metric] === null ? [] : [point[metric]!]);
  if (!values.length) return <div className="chart-empty"><h3>{label}</h3><p className="muted">No directly measured {label.toLowerCase()} is available on this page.</p></div>;
  const width = 640, height = 210, left = 52, top = 18, right = 18, bottom = 40;
  const earliest = points[0].time;
  const latest = points[points.length - 1].time;
  const duration = Math.max(latest - earliest, 0.001);
  const peak = Math.max(...values, 1);
  const x = (time: number) => left + (time - earliest) / duration * (width - left - right);
  const y = (value: number) => height - bottom - value / peak * (height - top - bottom);
  const color = metric === "speed" ? "#7053dc" : "#29847b";
  function showPoint(sample: TelemetryPoint, svg: SVGSVGElement, pointer?: { x: number; y: number }) {
    setInspected(sample);
    const matrix = svg?.getScreenCTM(), bounds = svg?.getBoundingClientRect?.();
    if (!matrix || !bounds) { setTooltipPosition(null); return; }
    const anchor = svg.createSVGPoint(); anchor.x = x(sample.time); anchor.y = sample[metric] === null ? top : y(sample[metric]!);
    const screen = anchor.matrixTransform(matrix);
    const ax = screen.x - bounds.left, ay = screen.y - bounds.top;
    // Leave room beside the marker even in narrow, letterboxed mobile plots.
    const boxWidth = Math.min(190, Math.max(1, bounds.width / 2 - 16)), boxHeight = 76, gap = 16;
    const candidates = [[ax + gap, ay - boxHeight / 2], [ax - boxWidth - gap, ay - boxHeight / 2],
      [ax - boxWidth / 2, ay - boxHeight - gap], [ax - boxWidth / 2, ay + gap]].map(([left, top]) => ({
        left: Math.max(0, Math.min(left, bounds.width - boxWidth)), top: Math.max(0, Math.min(top, bounds.height - boxHeight)),
      }));
    const proximity = (box: { left: number; top: number }, px: number, py: number) => Math.max(0, 12 - Math.hypot(
      Math.max(box.left - px, 0, px - box.left - boxWidth), Math.max(box.top - py, 0, py - box.top - boxHeight)));
    const score = (box: { left: number; top: number }) => 4 * proximity(box, ax, ay)
      + (pointer ? proximity(box, pointer.x - bounds.left, pointer.y - bounds.top) : 0);
    setTooltipPosition({ ...candidates.reduce((best, candidate) => score(candidate) < score(best) ? candidate : best), width: boxWidth });
  }
  function inspectPointer(event: PointerEvent<SVGSVGElement>) {
    const matrix = event.currentTarget.getScreenCTM();
    if (!matrix) return;
    // SVG transforms include responsive scaling and any viewBox letterboxing.
    const cursor = event.currentTarget.createSVGPoint();
    cursor.x = event.clientX; cursor.y = event.clientY;
    const local = cursor.matrixTransform(matrix.inverse());
    if (local.x < left || local.x > width - right || local.y < top || local.y > height - bottom) {
      setInspected(null); return;
    }
    const time = earliest + (local.x - left) / (width - left - right) * duration;
    // Snap to a real frame, including unknown measurements, rather than interpolate.
    showPoint(points.reduce((nearest, candidate) => Math.abs(candidate.time - time) < Math.abs(nearest.time - time) ? candidate : nearest),
      event.currentTarget, { x: event.clientX, y: event.clientY });
  }
  function inspectKey(event: KeyboardEvent<SVGSVGElement>) {
    const index = point ? points.indexOf(point) : 0;
    let next: number;
    switch (event.key) {
      case "ArrowLeft": next = Math.max(0, index - 1); break;
      case "ArrowRight": next = Math.min(points.length - 1, index + 1); break;
      case "Home": next = 0; break;
      case "End": next = points.length - 1; break;
      case "Escape": event.preventDefault(); setInspected(null); return;
      default: return;
    }
    event.preventDefault(); showPoint(points[next], event.currentTarget);
  }
  return <div className="chart-series"><h3>{label} <span className="muted">({unit})</span></h3>
    <div className="chart-plot">
    <svg className="chart-svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby={`${title} ${description}`} aria-describedby={point ? tooltip : undefined} tabIndex={0}
      onPointerMove={inspectPointer} onPointerUp={inspectPointer} onPointerLeave={() => setInspected(null)} onPointerCancel={() => setInspected(null)}
      onFocus={event => showPoint(points[0], event.currentTarget)} onBlur={() => setInspected(null)} onKeyDown={inspectKey}>
      <title id={title}>{label} over simulation time</title><desc id={description}>Only the current telemetry page is shown. Missing measurements, skipped frames, and time gaps above the supplied threshold break the line. Hover or use Left and Right Arrow keys to inspect recorded coordinates; Home and End select the endpoints, and Escape hides the tooltip.</desc>
      {[0, 0.5, 1].map(ratio => <g key={ratio}><line x1={left} x2={width - right} y1={y(peak * ratio)} y2={y(peak * ratio)} stroke="#e5e7ee" /><text x={left - 8} y={y(peak * ratio) + 4} textAnchor="end" fill="#687085" fontSize="11">{measurement(peak * ratio, 1)}</text></g>)}
      <line x1={left} x2={left} y1={top} y2={height - bottom} stroke="#bec1d0" />
      {segments.map((segment, index) => segment.length > 1
        ? <polyline key={index} points={segment.map(point => `${x(point.time)},${y(point.value)}`).join(" ")} stroke={color} strokeWidth="2" fill="none" />
        : <circle key={index} cx={x(segment[0].time)} cy={y(segment[0].value)} r="3" fill={color} />)}
      {point && <g className="chart-inspection" aria-hidden="true">
        <line x1={x(point.time)} x2={x(point.time)} y1={top} y2={height - bottom} stroke={color} strokeDasharray="4 4" opacity="0.6" />
        {point[metric] !== null && <circle cx={x(point.time)} cy={y(point[metric]!)} r="4" fill="white" stroke={color} strokeWidth="2" />}
      </g>}
      <text x={left} y={height - 20} fill="#687085" fontSize="11">{measurement(earliest, 2)}</text>
      <text x={width - right} y={height - 20} textAnchor="end" fill="#687085" fontSize="11">{measurement(latest, 2)}</text>
      <text x={width / 2} y={height - 3} textAnchor="middle" fill="#687085" fontSize="11">Simulation time (s)</text>
    </svg>
    {point && <div id={tooltip} className="chart-tooltip" role="tooltip" style={tooltipPosition ?? { left: `clamp(0px, ${x(point.time) / width * 100}%, calc(100% - 190px))` }}>
      <strong>Frame {point.frame}</strong>
      <span>x: {measurement(point.time, 3)} s</span>
      <span>y: {measurement(point[metric], 3)}{point[metric] === null ? "" : ` ${unit}`}</span>
    </div>}
    </div>
  </div>;
}

export function MetricsChart({ simulationId, active = true, maxSampleGapS, focus }: { simulationId: string; active?: boolean; maxSampleGapS?: number; focus?: EvidenceFocus }) {
  const prefix = useId();
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<{ id: string; token?: number; data: TelemetryPage } | null>(null);
  const [dismissedFocus, setDismissedFocus] = useState<number | null>(null);
  const target = focus && focus.token !== dismissedFocus ? focus : undefined;
  const [selected, setSelected] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [owner, setOwner] = useState(simulationId);
  // Reset during render so a newly selected run never requests the prior run's page.
  if (owner !== simulationId) { setOwner(simulationId); setOffset(0); setPage(null); setSelected(0); setError(null); }

  useEffect(() => {
    if (!active) { setLoading(false); return; }
    const request = new AbortController();
    let current = true;
    const deadline = setTimeout(() => request.abort(), 30_000);
    setLoading(true); setError(null);
    void (async () => {
      let start = offset;
      if (target) {
        const index = await locateFrame(target.frame, async at => {
          const data = await api.getTelemetry(simulationId, { offset: at, limit: 1 }, { signal: request.signal });
          return { total: data.total, items: data.frames };
        }, request.signal);
        if (index === null) throw new ApiError(`No measured telemetry exists at frame ${target.frame}. No nearby frame has been substituted.`);
        start = index;
      }
      if (request.signal.aborted) return;
      const data = await api.getTelemetry(simulationId, { limit: PAGE_SIZE, offset: start }, { signal: request.signal });
      if (!current || request.signal.aborted) return;
      setPage({ id: simulationId, token: target?.token, data }); setSelected(0); setLoading(false);
    })().catch(cause => {
      if (!current) return;
      setPage(null); setError(request.signal.aborted ? "Telemetry request timed out. Retry when the API is available." : cause instanceof ApiError ? cause.message : "Could not load this telemetry page."); setLoading(false);
    }).finally(() => clearTimeout(deadline));
    return () => { current = false; request.abort(); clearTimeout(deadline); };
  }, [simulationId, offset, active, retry, target?.token, target?.frame]);

  const data = page?.id === simulationId && page.token === target?.token && (target || page.data.offset === offset) ? page.data : null;
  const move = (next: number) => { setDismissedFocus(focus?.token ?? null); setOffset(next); };
  const points = useMemo(() => telemetryPoints(data?.frames ?? []), [data]);
  const sample = data?.frames[selected];
  const point = points[selected];
  return <section className="panel metrics-chart" aria-label="Telemetry charts" aria-busy={loading}>
    <div className="panel-header"><div><p className="eyebrow">FINALIZED TELEMETRY</p><h2>Measured motion</h2>
      <p>Pages of up to {PAGE_SIZE} frames. Charts show this page, not a downsampled full run. Hover over a graph to see a point’s coordinates.</p></div></div>
    <div className="form-section">
      {loading && <p role="status">Loading telemetry page…</p>}
      {error && <div role="alert"><p>{error}</p><button className="button secondary small" disabled={!active} onClick={() => setRetry(value => value + 1)}>Retry telemetry</button></div>}
      {data && <>
        {target && <p className="notice" role="status">Opened supporting telemetry at native frame {target.frame}.</p>}
        <p className="muted">{data.frames.length ? `Frames ${data.offset + 1}–${data.offset + data.frames.length} of ${data.total}` : `No frames on this page (${data.total} total)`}. {data.frames.length > 0 && `${measurement(data.frames[0].time_s, 2)}–${measurement(data.frames[data.frames.length - 1].time_s, 2)} s.`}</p>
        {data.total === 0 && <p>No telemetry was recorded for this run.</p>}
        <div className="chart-grid"><SeriesChart points={points} metric="speed" label="Ego speed" unit="m/s" maxSampleGapS={maxSampleGapS} /><SeriesChart points={points} metric="deceleration" label="Longitudinal deceleration" unit="m/s²" maxSampleGapS={maxSampleGapS} /></div>
        {maxSampleGapS !== undefined && Number.isFinite(maxSampleGapS) && maxSampleGapS > 0 && <p className="muted">Time gaps over {measurement(maxSampleGapS, 3)} s also break chart lines. This limit comes from the run’s recorded evaluation settings or timestep.</p>}
        <p className="muted">Speed is the measured velocity magnitude. Deceleration projects measured acceleration against the direction of travel and is unknown at rest or when either vector is missing. Aggregate braking metrics may also use adjacent velocities and recorded scoring settings.</p>
        {sample && point && <div className="telemetry-inspector">
          <div className="field"><label htmlFor={`${prefix}-frame`}>Inspect frame on this page</label><input id={`${prefix}-frame`} type="range" min="0" max={data.frames.length - 1} value={selected} onChange={event => setSelected(Number(event.target.value))} /></div>
          <dl className="frame-values"><div><dt>Frame / time</dt><dd>{sample.frame} / {measurement(sample.time_s, 3)} s</dd></div><div><dt>Speed</dt><dd>{measurement(point.speed, 3)} {point.speed === null ? "" : "m/s"}</dd></div><div><dt>Deceleration</dt><dd>{measurement(point.deceleration, 3)} {point.deceleration === null ? "" : "m/s²"}</dd></div><div><dt>Throttle / brake / steer</dt><dd>{measurement(sample.control.throttle, 2)} / {measurement(sample.control.brake, 2)} / {measurement(sample.control.steer, 2)}</dd></div><div><dt>Collision / rule events</dt><dd>{sample.collisions === null ? "Unknown" : sample.collisions.length} / {sample.traffic_violations === null ? "Unknown" : sample.traffic_violations.length}</dd></div></dl>
        </div>}
      </>}
      <div className="chart-controls"><button className="button secondary small" disabled={!active || loading || !data || data.offset === 0} onClick={() => move(Math.max(0, data!.offset - PAGE_SIZE))}>Previous frames</button>
        <button className="button secondary small" disabled={!active || loading || !data || data.offset + data.frames.length >= data.total} onClick={() => move(data!.offset + PAGE_SIZE)}>Next frames</button></div>
    </div>
  </section>;
}
