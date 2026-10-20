"""Portable views of measured facts, agent statements and qualified descriptions."""

from html import escape
import json


DIAGNOSTIC_RECORD_LIMIT = 200
DIAGNOSTIC_BYTE_LIMIT = 1024 * 1024
CHANNEL_LABELS = {
    "proposed_braking": "Proposed braking", "applied_braking": "Applied braking",
    "proposed_steering_change": "Proposed steering change", "applied_steering_change": "Applied steering change",
    "measured_deceleration": "Measured deceleration", "measured_heading_change": "Measured heading change",
}
ATTRIBUTION_LABELS = {
    "sdk_agent": "Autonomous agent", "baseline_controller": "Baseline controller",
    "legacy_custom": "Custom controller (legacy)", "script_influenced": "Script influenced",
    "unknown": "Unknown", "measured_motion": "Measured vehicle motion",
}


class DiagnosticEvidence:
    """Retain an explicitly bounded prefix, never the agent's observations."""

    def __init__(self):
        self.records = []
        self.total_decisions = self.unresolved_inputs = self.with_diagnostics = self.json_bytes = 0
        self.full = False

    def __call__(self, record):
        self.total_decisions += 1
        self.unresolved_inputs += record.status == "unresolved"
        diagnostics = None if record.agent_result is None else record.agent_result.diagnostics
        if diagnostics is None:
            return
        self.with_diagnostics += 1
        if self.full:
            return
        item = {"frame": record.frame, "time_s": record.time_s, "control_mode": record.control_mode,
                "interventions": list(record.interventions), "diagnostics": diagnostics.model_dump(mode="json")}
        size = len(json.dumps(item, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8"))
        if len(self.records) >= DIAGNOSTIC_RECORD_LIMIT or self.json_bytes + size > DIAGNOSTIC_BYTE_LIMIT:
            self.full = True
            return
        self.records.append(item)
        self.json_bytes += size

    def export(self, available):
        return {
            "source": "recorded_agent_diagnostics", "timeline_available": available,
            "total_decisions": self.total_decisions, "unresolved_inputs": self.unresolved_inputs,
            "decisions_with_diagnostics": self.with_diagnostics, "retained_records": len(self.records),
            "max_records": DIAGNOSTIC_RECORD_LIMIT, "max_record_json_bytes": DIAGNOSTIC_BYTE_LIMIT,
            "retained_record_json_bytes": self.json_bytes, "truncated": self.with_diagnostics > len(self.records),
            "records": self.records,
            "notes": [
                "These are agent-supplied statements, not verified internal reasoning or causal explanations.",
                "The earliest diagnostic-bearing records are retained until either cap is reached; later records remain in the original decision timeline.",
                "The byte cap counts compact UTF-8 JSON record objects before report formatting; formatting and the surrounding report add bytes.",
                "Absent diagnostics or unresolved inputs do not imply that the agent had no internal state. No observation or sensor payload is copied by this collector.",
            ],
        }


def qualified_interpretations(analysis):
    """Describe the same measured channels without attributing perception/intent."""
    descriptions = []
    for event in analysis["events"]:
        anchor = event["anchor"]
        for response in event["responses"]:
            channel = CHANNEL_LABELS[response["channel"]]
            status = response["status"]
            if status == "observed":
                statement = f"{channel}: threshold onset observed {response['latency_s']:.3f} s after the defined anchor."
            elif status == "already_active":
                statement = f"{channel}: already active at the anchor or first observation; onset latency is unknown."
            elif status == "not_observed":
                statement = f"{channel}: no response observed within the measured window."
            else:
                statement = f"{channel}: unavailable; the saved evidence does not establish a response interval."
            statement += " Observation window: " + ("complete." if response["window_complete"] else "incomplete.")
            if response["attribution"] == "script_influenced":
                statement += " Includes scenario assistance; do not credit an independent agent response."
            statement += " This does not establish when a hazard was perceived or why a control was chosen."
            descriptions.append({"event_id": anchor["id"], "anchor_source": anchor["source"],
                "channel": response["channel"], "status": status, "attribution": response["attribution"],
                "statement": statement, "evidence": response["evidence"]})
    return descriptions


def _text(value):
    return escape("Unknown" if value is None else str(value))


def _json(value):
    return escape(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2))


def _table(headers, rows, caption):
    heading = "".join(f"<th scope='col'>{_text(item)}</th>" for item in headers)
    body = "".join("<tr>" + "".join((f'<th scope="row">{cell}</th>' if index == 0 else f"<td>{cell}</td>")
                    for index, cell in enumerate(row)) + "</tr>" for row in rows)
    return f"<div class='table-scroll' tabindex='0' role='region' aria-label='{_text(caption)}'><table><caption>{_text(caption)}</caption><thead><tr>{heading}</tr></thead><tbody>{body}</tbody></table></div>"


def _evidence(spans):
    if not spans:
        return "No supporting frames available"
    return "<br>".join(f"{_text(span['source'])}: frames {span['first_frame']}–{span['last_frame']}, "
                         f"{span['first_time_s']:.3f}–{span['last_time_s']:.3f} s" for span in spans)


def _measure(value):
    if value["value"] is None:
        return "Unavailable"
    return f"{value['value'] * 100:.1f}%" if value["unit"] == "fraction" else f"{value['value']:.3f} {_text(value['unit'])}"


def behavior_html(report):
    """All retained findings are readable offline, keyboard accessible and escaped."""
    a = report["behavior_analysis"]
    diagnostics = report["agent_reported_diagnostics"]
    snapshot = a["agent_snapshot"]
    name = (f"{snapshot['manifest']['name']} · v{snapshot['manifest']['version']} · registration {snapshot['registration_id']}"
            if snapshot else "Built-in baseline" if a["agent"] == "baseline" else "Custom agent; version not recorded")
    source = "Not recorded" if a["agent_inputs"] is None else a["agent_inputs"]["source"]
    measures = [("Maximum deceleration", a["braking"]["max_deceleration"]), ("Hard-braking episodes", a["braking"]["hard_braking_count"]),
        ("Maximum route deviation", a["route"]["max_deviation"]), ("Mean route deviation", a["route"]["mean_deviation"]),
        ("Recorded route progress", a["route"]["recorded_progress"]), ("Recorded route length", a["route"]["recorded_length"]),
        ("Recorded route completion", a["route"]["recorded_completion_fraction"])]
    rows = [[_text(label), _measure(m), _text(f"{m['coverage']}: {m['observed_samples']} / {m['eligible_samples']} eligible samples"),
             _text(m["reason"] or "—"), _evidence(m["evidence"])] for label, m in measures]
    parts = [f"<section id='behavior'><h2>Measured behavior</h2><p>{_text(name)}<br>Input source: {_text(source)} · control authority: {_text(a['control_mode'] or 'Not recorded')} · analysis version {_text(a['schema_version'])}</p>",
        "<p>Task completion, execution status and safety are separate outcomes. Evaluator ground truth does not establish what an agent perceived. Unknown values are never replaced by zero.</p>",
        _table(["Measurement", "Value", "Coverage", "Limit", "Supporting frames"], rows, "Measured braking and route behavior")]
    outcome = a["driving_outcome"]
    parts.append(f"<h3>Recorded driving task</h3><p>{_text('Not recorded' if outcome is None else outcome['status'] + ' · ' + outcome['termination_reason'])}</p><p>{_evidence(a['task_evidence'])}</p>")
    parts.append(f"<h3>Coverage and effective settings</h3><p>Braking settings source: {_text(a['braking']['settings_source'])}. Original safety metrics and their settings are unchanged.</p><pre>{_json(a['coverage'])}</pre><h4>Analysis thresholds and caps</h4><pre>{_json(a['settings'])}</pre><h4>Recorded braking evaluation settings</h4><pre>{_json(a['braking']['settings'])}</pre>")
    parts.append(f"<h3>Events and measured responses</h3><p>{len(a['events'])} of {a['coverage']['total_response_events']} anchors retained. Output truncated: {_text(a['coverage']['response_events_truncated'])}. Zero anchors do not establish a hazard-free run.</p>")
    if not a["events"]:
        parts.append("<p>No response anchors are available.</p>")
    for index, event in enumerate(a["events"]):
        anchor = event["anchor"]
        rows = []
        for response in event["responses"]:
            status = (f"{response['latency_s']:.3f} s after anchor" if response["status"] == "observed" else
                "Already active; onset unknown" if response["status"] == "already_active" else
                "No response observed in measured window" if response["status"] == "not_observed" else "Unavailable")
            interval = f"{status}<br>{'Complete' if response['window_complete'] else 'Incomplete'} observation window; observed through {_text(response['observed_until_s'])} s / ends {response['window_end_s']:.3f} s"
            if response["reason"]:
                interval += "<br>" + _text(response["reason"])
            detail = f"Threshold: {response['threshold']:.3f} {_text(response['unit'])}<br>Value: {_text(response['value'])} · response frame {_text(response['response_frame'])} at {_text(response['response_time_s'])} s<br>Reference frame {_text(response['reference_frame'])}; value {_text(response['reference_value'])}"
            attribution = ATTRIBUTION_LABELS[response["attribution"]]
            if response["attribution"] == "script_influenced":
                attribution += "; includes scenario assistance, not an independent agent response"
            rows.append([_text(CHANNEL_LABELS[response["channel"]]), interval, _text(attribution), detail, _evidence(response["evidence"])])
        parts.append(f"<section class='event' id='behavior-event-{index}'><h4>Event {index + 1}: {_text(anchor['kind'])} · {_text(anchor['actor_id'])}</h4><p>Source: {_text(anchor['source'])} · onset: {_text(anchor['onset'])} · anchor {anchor['time_s']:.3f} s · frame {_text(anchor['frame'])}<br>Scheduled command: {_text(anchor['scheduled_time_s'])} s · action {_text(anchor['event_action'])} · ego script: {_text(anchor['ego_event'])}<br>Evaluator TTC: {_text(anchor['ttc_s'])} s · clearance: {_text(anchor['clearance_m'])} m · limit: {_text(anchor['reason'] or '—')}</p>")
        parts.append(_table(["Signal", "Observed interval", "Attribution", "Threshold and reference", "Supporting frames"], rows, f"Responses to event {index + 1}") + "</section>")
    stops = a["braking"]["stopping_episodes"]
    parts.append(f"<h3>Completed stopping episodes</h3><p>{len(stops)} of {a['braking']['total_stopping_episodes']} episodes retained. Output truncated: {_text(a['braking']['episodes_truncated'])}. Distance is integrated from measured speed.</p>")
    parts.append(_table(["Stop", "Duration", "Distance", "Script influence", "Supporting frames"], [[str(i + 1), f"{s['duration_s']:.3f} s", f"{s['distance_m']:.3f} m",
        _text(f"{'Unknown' if s['script_influenced'] is None else 'Yes' if s['script_influenced'] else 'None recorded'} · {s['intervention_frames']} intervention frames"), _evidence([s["evidence"]])] for i, s in enumerate(stops)], "Completed stops") if stops else "<p>No completed stops were measurable.</p>")
    parts.append("<h3>Evidence limits</h3><ul>" + "".join(f"<li>{_text(note)}</li>" for note in a["interpretation_notes"]) + "</ul></section>")
    parts.append(f"<section id='agent-statements'><h2>Agent-reported diagnostics</h2><p>Agent-supplied statements, not verified internal reasoning or causality. Timeline available: {_text(diagnostics['timeline_available'])}. {diagnostics['retained_records']} of {diagnostics['decisions_with_diagnostics']} diagnostic-bearing decisions retained; truncated: {_text(diagnostics['truncated'])}. {diagnostics['unresolved_inputs']} unresolved inputs.</p><ul>" + "".join(f"<li>{_text(note)}</li>" for note in diagnostics["notes"]) + "</ul>")
    if not diagnostics["records"]:
        parts.append("<p>No agent diagnostics were retained or reported. Missing statements are not reconstructed.</p>")
    for record in diagnostics["records"]:
        parts.append(f"<h3 id='agent-diagnostics-f{record['frame']}'>Input frame {record['frame']} · {record['time_s']:.3f} s</h3><pre>{_json(record)}</pre>")
    parts.append("</section><section id='qualified-interpretations'><h2>Qualified interpretations</h2><p>Descriptions of recorded measurements; no internal reasoning, perception time or causality is inferred.</p><ol>")
    parts.extend(f"<li><strong>{_text(item['event_id'])}</strong> · {_text(item['anchor_source'])} · {_text(ATTRIBUTION_LABELS[item['attribution']])}: {_text(item['statement'])}<p>{_evidence(item['evidence'])}</p></li>" for item in report["qualified_interpretations"])
    parts.append("</ol></section>")
    return "".join(parts)
