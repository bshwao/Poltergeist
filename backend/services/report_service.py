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
from typing import Literal
from uuid import UUID

from backend.job_repository import JobRepository
from backend.persistence_errors import ConflictError
from backend.repositories import FailureRepository, SimulationRepository
from backend.services.persistence_service import PersistenceService
from shared.models.api import JobInfo, SimulationRecord


ReportFormat = Literal["json", "csv", "html"]
REPORT_SCHEMA = "poltergeist-run-report-v1"

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


def build_report(persistence: PersistenceService, simulation_id: UUID, *, include_telemetry: bool = False) -> dict:
    """Snapshot one terminal run and every associated failure observation."""
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
    if include_telemetry:
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


def _unit(path: str) -> str:
    key = path.rsplit("/", 1)[-1]
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
        writer.writerow([path.split("/")[1], path, _json(value), _unit(path)])
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
    content = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Poltergeist run {escape(simulation['id'])}</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;max-width:1100px;margin:40px auto;padding:0 24px;color:#222}}h1,h2{{line-height:1.2}}.label{{font-weight:bold;color:#5632a0}}table{{width:100%;border-collapse:collapse}}th,td{{text-align:left;border-bottom:1px solid #ddd;padding:8px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f4f8;padding:20px;font-size:12px}}@media print{{body{{margin:0}}pre{{font-size:9px}}}}</style></head>
<body><p class="label">Poltergeist · persisted run report</p><h1>{escape(simulation['scenario']['name'])}</h1>
<p>{escape(interpretation['mode_label'])} · {escape(simulation['status'])} · Coverage: {escape(interpretation['coverage'])} · {"Scored" if interpretation['scored'] else "Unscored"}</p>
<p>Run ID: {escape(simulation['id'])}<br>Exported: {escape(report['generated_at'])}<br>Started: {escape(str(simulation['started_at']))}<br>Finished: {escape(str(simulation['finished_at']))}</p>
<h2>Safety metrics</h2><table><thead><tr><th>Metric</th><th>Recorded value</th><th>Unit</th></tr></thead><tbody>{rows}</tbody></table>
<h2>Interpretation</h2><ul>{notes}</ul>
<h2>Complete export snapshot</h2><p>Includes the immutable recipe, recorded settings, coverage, runtime provenance and {len(report['failures'])} failure report(s). JSON null values retain their original meaning.</p>
<pre>{escape(_json(report, pretty=True))}</pre></body></html>"""
    return content.encode("utf-8")


def export_report(persistence: PersistenceService, simulation_id: UUID, *, format: ReportFormat = "json", include_telemetry: bool = False) -> ReportExport:
    if format not in ("json", "csv", "html"):
        raise ValueError("Report format must be json, csv, or html")
    if include_telemetry and format != "json":
        raise ValueError("include_telemetry is supported only for JSON reports")
    report = build_report(persistence, simulation_id, include_telemetry=include_telemetry)
    content = _json(report, pretty=True).encode("utf-8") if format == "json" else _csv(report) if format == "csv" else _html(report)
    media_type = {"json": "application/json", "csv": "text/csv", "html": "text/html"}[format]
    return ReportExport(content, media_type, f"poltergeist-{simulation_id}.{format}")
