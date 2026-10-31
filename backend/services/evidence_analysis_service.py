"""Read-only explanations of bounded, validated saved evidence; no model execution."""

from hashlib import sha256
import json
from math import isclose

from backend.persistence_errors import PersistenceError
from backend.storage import ArtifactError
from evaluation.behavior_analysis import analyze_behavior
from shared.models.analysis import (AnalysisCoverage, AnalysisHypothesis,
    AnalysisObservation, AnalysisReference, EvidenceAnalysis)

from .experimentation_service import read_review_evidence, verify_review_metrics


def digest(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def build_evidence_analysis(persistence, simulation_id):
    stored = persistence.get_simulation(simulation_id)
    simulation = stored.simulation
    limits = [
        "This deterministic report describes recorded evidence; it does not inspect a model's internal reasoning or establish causation.",
        "A missing event or a low heuristic score does not establish safety. Partial and unavailable observations remain unknown.",
        "The review is bounded to 128 MiB and 100,001 telemetry frames. Selected links are not a complete replay.",
        "Decision traces and camera/LiDAR recordings are reviewed separately; this report does not validate their integrity.",
    ]
    if simulation.mode == "test":
        limits.insert(0, "Synthetic mode exercises the workflow; these observations are not measurements of CARLA driving.")
    base = dict(simulation_id=simulation_id, mode=simulation.mode, execution_status=simulation.status,
        coverage=AnalysisCoverage(), observations=[], limitations=limits,
        unresolved_questions=["The saved trace alone cannot establish what the model perceived or why it chose an action."])
    if simulation.status in {"queued", "running"}:
        return EvidenceAnalysis(**base, state="pending", summary="Execution has not finalized. No recorded-outcome analysis is available yet.")
    if stored.result is None:
        return EvidenceAnalysis(**base, state="unavailable", summary="This finalized execution has no saved result to analyze.")
    base["source_digest"] = digest(stored.result)
    try:
        result, frames, telemetry_digest = read_review_evidence(persistence, simulation_id)
        verify_review_metrics(result, frames)
        behavior = analyze_behavior(result, frames)
    except (ArtifactError, PersistenceError, ValueError, OSError):
        return EvidenceAnalysis(**base, state="unavailable",
            summary="Saved telemetry is unavailable or failed validation. Frame-level conclusions cannot be verified.")
    base.update(source_digest=digest(result), telemetry_digest=telemetry_digest, execution_status=result.simulation.status)
    by_frame = {frame.frame: frame for frame in frames}
    audit = result.control_audit
    interventions = [] if audit is None else audit.interventions
    matched = [item for item in interventions if item.frame in by_frame
        and isclose(item.time_s, by_frame[item.frame].time_s, abs_tol=1e-6)]
    base["coverage"] = AnalysisCoverage(telemetry_frames=len(frames), telemetry_gaps=behavior.coverage.telemetry_gaps,
        safety_complete=result.metrics.coverage.complete, interventions_recorded=audit is not None)
    observations = base["observations"]
    def record(kind, statement, refs, *, value=None, unit=None, coverage="complete"):
        observations.append(AnalysisObservation(kind=kind, statement=statement, value=value, unit=unit,
            coverage=coverage, references=refs[:8]))
    def reference(frame, field, source="telemetry"):
        return AnalysisReference(source=source, field=field, frame=frame.frame, time_s=frame.time_s)
    def summary_ref(field):
        return AnalysisReference(source="result", field=field)
    record("execution", f"The saved execution ended as {result.simulation.status.value} with {len(frames)} recorded telemetry frames.",
        [summary_ref("simulation.status"), summary_ref("telemetry_frames")], value=float(len(frames)), unit="recorded frames")
    for field, kind, label in (("collisions", "contact", "contact"), ("traffic_violations", "traffic_rule", "rule-event")):
        observed = [frame for frame in frames if getattr(frame, field) is not None]
        events = [frame for frame in observed if getattr(frame, field)]
        coverage = "complete" if frames and len(observed) == len(frames) else "partial" if observed else "unavailable"
        if not observed:
            record(kind, f"There are no reviewed {label} observations; absence of an event cannot be established.",
                [summary_ref("metrics.coverage")], coverage="unavailable")
        else:
            record(kind, f"{len(events)} recorded frames contain {label} events among {len(observed)} observed frames out of {len(frames)} reviewed frames. These are frame counts, not distinct event episodes.",
                [reference(frame, field) for frame in (events[:3] or observed[:1])],
                value=float(len(events)), unit="frames with events", coverage=coverage)
    peak = behavior.braking.max_deceleration
    if peak.value is None:
        record("braking", "Peak deceleration is unavailable from the reviewed motion observations.",
            [summary_ref("metrics.coverage")], coverage="unavailable")
    else:
        peak_frames = [by_frame[span.first_frame] for span in peak.evidence if span.first_frame in by_frame]
        record("braking", f"Peak measured deceleration is {peak.value:.3g} m/s² across {peak.observed_samples} of {peak.eligible_samples} eligible motion observations.",
            [reference(frame, "ego") for frame in peak_frames],
            value=peak.value, unit="m/s²", coverage=peak.coverage)
    if audit is None:
        record("intervention", "No control audit was saved. Scripted intervention coverage is unknown.",
            [summary_ref("control_audit")], coverage="unavailable")
    else:
        refs = [reference(by_frame[item.frame], "interventions", "control_audit") for item in matched[:3]]
        record("intervention", f"The stored control audit records {len(interventions)} scripted interventions; {len(matched)} have matching reviewed frame/time references. Proposed and applied controls are separate from model intent.",
            refs or [summary_ref("control_audit.interventions")], value=float(len(interventions)), unit="audit records",
            coverage="complete" if len(matched) == len(interventions) else "partial")
    hypotheses = []
    if peak.value is not None and peak.evidence:
        peak_time = peak.evidence[0].first_time_s
        preceding = next((item for item in matched if item.kind == "scripted_ego_braking" and 0 < peak_time - item.time_s <= .5), None)
        if preceding is not None:
            hypotheses.append(AnalysisHypothesis(statement="A nearby scripted braking intervention could affect interpretation of this braking response.",
                review="Compare the recorded proposed/applied controls and subsequent motion. Temporal proximity alone does not establish causation.",
                references=[reference(by_frame[preceding.frame], "interventions", "control_audit")]))
    complete = (bool(frames) and result.simulation.status == "completed" and result.metrics.coverage.complete
        and not behavior.coverage.telemetry_gaps and audit is not None and len(matched) == len(interventions)
        and all(item.coverage == "complete" for item in observations))
    state = "complete" if complete else "partial" if frames else "unavailable"
    base["unresolved_questions"].append("Decision traces and recordings may add context; they cannot by themselves establish internal model reasoning.")
    if not complete:
        base["unresolved_questions"].append("Incomplete execution, missing observations or timeline gaps prevent conclusions about the unobserved interval.")
    return EvidenceAnalysis(**base, state=state, hypotheses=hypotheses,
        summary=f"Reviewed {len(frames)} saved frames from a {result.simulation.status.value} execution. " +
            ("Recorded safety coverage is complete for this trace." if complete else "Evidence is incomplete; missing observations remain unknown."))
