"""Read-only, portable exports of finalized persisted evidence.

Reports never launch a worker, rerun a recipe, recompute scores, or substitute
the latest saved scenario for a run's immutable snapshot.
"""

import csv
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from io import StringIO
import json
import re
from typing import Literal
from uuid import UUID

from backend.job_repository import JobRepository
from backend.persistence_errors import ConflictError
from backend.repositories import FailureRepository, SimulationRepository
from backend.services.persistence_service import PersistenceService
from backend.services.behavior_service import read_behavior_analysis
from backend.services.behavior_report import DiagnosticEvidence, behavior_html, qualified_interpretations
from shared.models.api import JobInfo, SimulationRecord
from shared.models.behavior import BehaviorSettings


ReportFormat = Literal["json", "csv", "html"]
REPORT_SCHEMA = "poltergeist-run-report-v1"
BEHAVIOR_REPORT_SCHEMA = "poltergeist-run-report-v2"

METRIC_UNITS = {
    "collision_count": "episodes", "traffic_violation_count": "episodes",
    "min_clearance_m": "m", "min_ttc_s": "s", "max_deceleration_mps2": "m/s²",
    "hard_braking_count": "episodes", "max_stopping_distance_m": "m",
    "max_stopping_time_s": "s", "severity": "0–100 heuristic",
}


@dataclass(frozen=True)
class ReportExport:
    content: bytes
    media_type: str
    filename: str
    schema: str = REPORT_SCHEMA


def build_report(persistence: PersistenceService, simulation_id: UUID, *, include_telemetry: bool = False,
                 include_behavior: bool = False, behavior_settings: BehaviorSettings | None = None) -> dict:
    """Snapshot one terminal run and every associated failure observation."""
    if type(include_behavior) is not bool:
        raise ValueError("include_behavior must be a boolean")
    if behavior_settings is not None and not include_behavior:
        raise ValueError("Behavior settings require include_behavior")
    with persistence.database.session(write=False) as session:
        stored = SimulationRepository(session).get(simulation_id)
        if stored.result is None:
            raise ConflictError("Simulation output is not finalized yet; export after the run finishes")
        job = JobRepository(session).get(simulation_id, required=False)
        record = SimulationRecord(
            **{name: getattr(stored, name) for name in (
                "simulation", "result", "scenario_id", "revision", "created_at", "updated_at",
            )},
            job=None if job is None else JobInfo(
                cancel_requested=job.cancel_requested, queue_position=None,
                options=job.options, evaluation_settings=job.evaluation_settings,
            ),
        )
        failures = []
        repository = FailureRepository(session)
        while True:
            page = repository.list(simulation_id=simulation_id, limit=1000, offset=len(failures))
            failures.extend(report.model_dump(mode="json") for report in page)
            if len(page) < 1000:
                break

    metrics = stored.result.metrics
    coverage = metrics.coverage
    report = {
        "schema": REPORT_SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(),
        "record": record.model_dump(mode="json"),
        "failures": failures,
        "interpretation": {
            "mode_label": "Synthetic test execution" if stored.simulation.mode == "test" else "CARLA execution",
            "coverage": "complete" if coverage.complete else "unobserved" if coverage.total_frames == 0 else "partial",
            "scored": metrics.severity is not None,
            "notes": [
                "Persisted evidence only; exporting does not rerun or reproduce a simulation.",
                "Null is preserved, never replaced by zero. Null minima can mean unavailable data or no relevant interaction; inspect coverage.",
                "Severity is a search heuristic, not a crash probability or a safety certification. An observed collision can score 100 with partial coverage.",
                "Reproduction flags are manual observations as of export time: true, false, and unknown (null) are distinct.",
                "Test mode uses synthetic physics and is not evidence from CARLA or real driving.",
                "Artifacts remain owned raw captures or telemetry references; this report does not contain encoded video or a simulator replay.",
                "Historical queue position is unavailable. Missing job/runtime/settings metadata remains null.",
            ],
        },
        "telemetry_included": include_telemetry,
        "telemetry": None,
    }
    output = None
    if include_behavior:
        diagnostics = DiagnosticEvidence()
        analysis, output = read_behavior_analysis(persistence, simulation_id, settings=behavior_settings, on_decision=diagnostics)
        if output.result != stored.result:
            raise ConflictError("Finalized output changed while building the report; retry the export")
        report.update({
            "schema": BEHAVIOR_REPORT_SCHEMA,
            "behavior_analysis": analysis.model_dump(mode="json"),
            "agent_reported_diagnostics": diagnostics.export(analysis.coverage.decision_coverage != "unavailable"),
        })
        report["qualified_interpretations"] = qualified_interpretations(report["behavior_analysis"])
        report["interpretation"]["notes"].extend([
            "Behavior measurements use the same analysis engine as the dashboard; effective thresholds and native-frame references are included.",
            "Measured facts, agent-supplied diagnostics and qualified descriptions are separate. None establish internal model reasoning or causality.",
            "Analysis, event and diagnostics retention caps are explicit; consult their coverage and truncation flags before drawing conclusions.",
        ])
    if include_telemetry:
        if output is None:
            output = persistence.read_output(simulation_id)
        report["telemetry"] = [frame.model_dump(mode="json") for frame in output.telemetry]
    return report


def _json(value, *, pretty=False) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2 if pretty else None)


def _pointer(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


def _leaves(value, path=""):
    """JSON Pointer paths and JSON values preserve types, nulls and empty lists."""
    if isinstance(value, dict) and value:
        for key, child in value.items():
            yield from _leaves(child, f"{path}/{_pointer(key)}")
    elif isinstance(value, list) and value:
        for index, child in enumerate(value):
            yield from _leaves(child, f"{path}/{index}")
    else:
        yield path, value


def _unit(path: str, report: dict | None = None) -> str:
    key = path.rsplit("/", 1)[-1]
    if report is not None and re.fullmatch(r"/behavior_analysis/(?:braking/(?:max_deceleration|hard_braking_count)|route/(?:max_deviation|mean_deviation|recorded_progress|recorded_length|recorded_completion_fraction)|events/\d+/responses/\d+)/(?:value|threshold|reference_value)", path):
        parent = report
        for segment in path.split("/")[1:-1]:
            segment = segment.replace("~1", "/").replace("~0", "~")
            parent = parent[int(segment)] if isinstance(parent, list) else parent[segment]
        if isinstance(parent, dict) and "unit" in parent:
            return parent["unit"]
    if key in ("first_frame", "last_frame", "response_frame", "reference_frame", "frame"):
        return "native frame"
    if key.endswith("_bytes"):
        return "bytes"
    if "/metrics/" in path and key in METRIC_UNITS:
        return METRIC_UNITS[key]
    if key == "severity":
        return METRIC_UNITS["severity"]
    if key in ("x", "y", "z"):
        for parent, unit in (("velocity_mps", "m/s"), ("acceleration_mps2", "m/s²"), ("position", "m"), ("bounding_box_extent", "m")):
            if f"/{parent}/" in path:
                return unit
    if "/coverage/" in path and (key.endswith("_frames") or key.endswith("_pairs")):
        return "frames" if key.endswith("_frames") else "pairs"
    for suffix, unit in (("_mps2", "m/s²"), ("_mps", "m/s"), ("_n_s", "N·s"), ("_deg", "degrees"), ("_seconds", "s"), ("_s", "s"), ("_m", "m")):
        if key.endswith(suffix):
            return unit
    return ""


def _csv(report: dict) -> bytes:
    stream = StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\r\n")
    writer.writerow(["section", "json_pointer", "value_json", "unit"])
    # JSON-encoded strings begin with a quote, so user text cannot become a
    # spreadsheet formula. json.loads(value_json) recovers the exact value.
    for path, value in _leaves(report):
        writer.writerow([path.split("/")[1], path, _json(value), _unit(path, report)])
    return stream.getvalue().encode("utf-8-sig")


def _html(report: dict) -> bytes:
    record = report["record"]
    simulation = record["simulation"]
    result = record["result"]
    interpretation = report["interpretation"]
    metrics = result["metrics"]
    rows = "".join(
        f"<tr><th scope='row'>{escape(key.replace('_', ' '))}</th><td>{escape('Unknown / no relevant interaction' if metrics[key] is None else str(metrics[key]))}</td><td>{escape(unit)}</td></tr>"
        for key, unit in METRIC_UNITS.items()
    )
    notes = "".join(f"<li>{escape(note)}</li>" for note in interpretation["notes"])
    behavior = behavior_html(report) if "behavior_analysis" in report else ""
    navigation = ("<nav aria-label='Report sections'><a href='#behavior'>Measured behavior</a> · <a href='#agent-statements'>Agent diagnostics</a> · <a href='#qualified-interpretations'>Qualified interpretations</a> · <a href='#snapshot'>Complete snapshot</a></nav>" if behavior else "")
    content = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Poltergeist run {escape(simulation['id'])}</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;max-width:1100px;margin:40px auto;padding:0 24px;color:#222}}h1,h2,h3,h4{{line-height:1.3;overflow-wrap:anywhere}}h2{{margin-top:36px}}.label,a{{color:#5632a0}}.label{{font-weight:bold}}nav{{padding:16px;background:#f5f4f8}}table{{width:100%;border-collapse:collapse}}th,td{{text-align:left;vertical-align:top;border-bottom:1px solid #ddd;padding:8px;overflow-wrap:anywhere}}caption{{text-align:left;font-weight:600;padding:12px 0}}.table-scroll{{overflow-x:auto;max-width:100%;margin:18px 0}}.table-scroll table{{min-width:680px;font-size:13px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f4f8;padding:20px;font-size:12px}}summary{{cursor:pointer;padding:12px 0}}:focus-visible{{outline:3px solid #7053dc;outline-offset:3px}}.event{{border-top:1px solid #ddd;margin:24px 0;padding-top:12px}}@media(max-width:600px){{body{{padding:0 16px;margin:24px auto;font-size:14px}}pre{{padding:12px}}}}@media print{{@page{{margin:12mm}}body{{margin:0;padding:0;font-size:10px}}nav{{display:none}}.table-scroll{{overflow:visible}}.table-scroll table{{min-width:0;font-size:9px;table-layout:fixed}}th,td{{padding:4px}}thead{{display:table-header-group}}tr,li{{break-inside:avoid}}h1,h2,h3,h4{{break-after:avoid}}pre{{font-size:8px}}}}</style></head>
<body><p class="label">Poltergeist · persisted run report</p><h1>{escape(simulation['scenario']['name'])}</h1>
<p>{escape(interpretation['mode_label'])} · {escape(simulation['status'])} · Coverage: {escape(interpretation['coverage'])} · {"Scored" if interpretation['scored'] else "Unscored"}</p>
<p>Run ID: {escape(simulation['id'])}<br>Exported: {escape(report['generated_at'])}<br>Started: {escape(str(simulation['started_at']))}<br>Finished: {escape(str(simulation['finished_at']))}</p>
{navigation}
<h2>Safety metrics</h2><table><thead><tr><th>Metric</th><th>Recorded value</th><th>Unit</th></tr></thead><tbody>{rows}</tbody></table>
<h2>Interpretation</h2><ul>{notes}</ul>
{behavior}
<h2 id="snapshot">Complete export snapshot</h2><p>Includes the immutable recipe, recorded settings, coverage, runtime provenance and {len(report['failures'])} failure report(s). JSON null values retain their original meaning.</p>
<details><summary>Expand full machine-readable snapshot</summary><pre id="export-snapshot">{escape(_json(report, pretty=True))}</pre></details></body></html>"""
    return content.encode("utf-8")


def export_report(persistence: PersistenceService, simulation_id: UUID, *, format: ReportFormat = "json", include_telemetry: bool = False,
                  include_behavior: bool = False, behavior_settings: BehaviorSettings | None = None) -> ReportExport:
    if format not in ("json", "csv", "html"):
        raise ValueError("Report format must be json, csv, or html")
    if include_telemetry and format != "json":
        raise ValueError("include_telemetry is supported only for JSON reports")
    report = build_report(persistence, simulation_id, include_telemetry=include_telemetry,
                          include_behavior=include_behavior, behavior_settings=behavior_settings)
    content = _json(report, pretty=True).encode("utf-8") if format == "json" else _csv(report) if format == "csv" else _html(report)
    media_type = {"json": "application/json", "csv": "text/csv", "html": "text/html"}[format]
    return ReportExport(content, media_type, f"poltergeist-{simulation_id}.{format}", report["schema"])
