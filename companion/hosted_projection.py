"""Field-by-field disclosure boundary for baseline remote-run summaries.

Callers must authorize the device/run, obtain a validated detached record and
matching job, validate the accepted scenario digest, and check evidence presence
before calling. This pure function opens no database, files, telemetry or sockets.
It performs no generic local-model dump and never interprets exception strings.
"""

import re
from typing import Literal
from uuid import UUID, uuid5

from backend.job_repository import StoredJob
from backend.repositories import StoredSimulation
from shared.models.hosted import (HostedCoverageV1, HostedEvaluationSettingsV1,
    HostedEvidenceV1, HostedProvenanceV1, HostedPublicErrorV1, HostedRunSummaryV1,
    HostedRuntimeV1, HostedSafetyMetricsV1, HostedScenarioV1)


class HostedProjectionError(ValueError):
    """Fixed error text; rejected local values never appear in the exception."""


def _settings(value):
    if value is None:
        return None
    return HostedEvaluationSettingsV1(method_version=value.method_version,
        collision_episode_gap_s=value.collision_episode_gap_s,
        violation_episode_gap_s=value.violation_episode_gap_s,
        speeding_tolerance_mps=value.speeding_tolerance_mps,
        hard_braking_threshold_mps2=value.hard_braking_threshold_mps2,
        stopped_speed_mps=value.stopped_speed_mps,
        max_sample_gap_s=value.max_sample_gap_s,
        ttc_horizon_s=value.ttc_horizon_s, ttc_warning_s=value.ttc_warning_s,
        clearance_warning_m=value.clearance_warning_m,
        braking_full_severity_mps2=value.braking_full_severity_mps2,
        violations_for_full_severity=value.violations_for_full_severity,
        ttc_weight=value.ttc_weight, clearance_weight=value.clearance_weight,
        braking_weight=value.braking_weight, traffic_rule_weight=value.traffic_rule_weight)


def _metrics(value):
    coverage = value.coverage
    return HostedSafetyMetricsV1(collision_count=value.collision_count,
        traffic_violation_count=value.traffic_violation_count,
        min_clearance_m=value.min_clearance_m, min_ttc_s=value.min_ttc_s,
        max_deceleration_mps2=value.max_deceleration_mps2,
        hard_braking_count=value.hard_braking_count,
        max_stopping_distance_m=value.max_stopping_distance_m,
        max_stopping_time_s=value.max_stopping_time_s, severity=value.severity,
        coverage=HostedCoverageV1(total_frames=coverage.total_frames,
            collision_frames=coverage.collision_frames, traffic_rule_frames=coverage.traffic_rule_frames,
            braking_frames=coverage.braking_frames, clearance_frames=coverage.clearance_frames,
            ttc_frames=coverage.ttc_frames, actor_pairs=coverage.actor_pairs,
            clearance_pairs=coverage.clearance_pairs, ttc_pairs=coverage.ttc_pairs,
            has_gaps=coverage.has_gaps, complete=coverage.complete),
        evaluation_settings=_settings(value.evaluation_settings))


def _version(value):
    return value if isinstance(value, str) and re.fullmatch(r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}", value) else None


def _map_name(value):
    # CARLA may return /Game/Carla/Maps/... asset identifiers. Never disclose the
    # directory portion or forward arbitrary free-form/runtime diagnostic text.
    if not isinstance(value, str):
        return None
    basename = value.replace("\\", "/").rsplit("/", 1)[-1]
    if basename.endswith(".umap"):
        basename = basename[:-5]
    return basename if re.fullmatch(r"Town[0-9]{2}(?:HD)?(?:_Opt)?", basename) else None


def _runtime(value):
    if value is None:
        return None
    return HostedRuntimeV1(adapter=value.adapter, server_version=_version(value.server_version),
        client_version=_version(value.client_version), map_name=_map_name(value.map_name),
        fixed_delta_seconds=value.fixed_delta_seconds, seed=value.seed)


def _provenance(result):
    inputs, audit = result.agent_inputs, result.control_audit
    if inputs is None and audit is None:
        return None
    return HostedProvenanceV1(observation_mode=None if inputs is None else inputs.observation_mode,
        source=None if inputs is None else inputs.source,
        ego_state_source=None if inputs is None else inputs.ego_state_source,
        ground_truth_actor_inputs=None if inputs is None else inputs.ground_truth_actor_inputs,
        control_mode=None if audit is None else audit.mode,
        controller=None if audit is None else audit.controller,
        navigation=None if audit is None else audit.navigation)


def project_hosted_run(stored: StoredSimulation, job: StoredJob | None, *, device_id: UUID,
        ownership_generation: int, execute_request_id: UUID, receipt_revision: int,
        scenario_sha256: str, evidence_state: Literal["local_only", "missing"],
        queue_position: int | None = None) -> HostedRunSummaryV1:
    """Publish accepted binding metadata and a minimal baseline lifecycle/result.

    ``receipt_revision`` is the caller's persisted remote projection sequence;
    ``revision`` remains StoredSimulation's revision. Cancellation can change the
    former without changing the latter. The receipt supplies the validated digest
    of the accepted scenario, not a hash of arbitrary local diagnostics/metadata.
    ``evidence_state`` is required because artifact references do not prove files
    still exist. This function neither authorizes requests nor establishes an
    exactly-once delivery, connection-fencing or remote-capability guarantee.
    """
    try:
        if not isinstance(stored, StoredSimulation) or (job is not None and not isinstance(job, StoredJob)):
            raise ValueError("Invalid detached record")
        simulation, result = stored.simulation, stored.result
        if simulation.agent != "baseline" or simulation.agent_snapshot is not None:
            raise ValueError("Only baseline records may be projected")
        if job is not None and (job.simulation_id != simulation.id or job.agent_snapshot is not None):
            raise ValueError("Job does not match the baseline record")
        if result is not None:
            recorded = result.simulation
            fields = ("id", "mode", "status", "progress", "created_at", "started_at", "finished_at")
            if (recorded.agent != "baseline" or recorded.agent_snapshot is not None
                    or any(getattr(recorded, name) != getattr(simulation, name) for name in fields)):
                raise ValueError("Result does not match the baseline record")
        error = None
        if simulation.status.value == "failed":
            error = HostedPublicErrorV1(code="execution_failed",
                message="The experiment failed on this computer. Review its local diagnostics.",
                correlation_id=uuid5(simulation.id, "poltergeist-hosted-error-v1"))
        elif result is None and simulation.status.value in ("completed", "cancelled"):
            error = HostedPublicErrorV1(code="result_unavailable",
                message="The finalized result is unavailable on this computer.",
                correlation_id=uuid5(simulation.id, "poltergeist-hosted-error-v1"))
        return HostedRunSummaryV1(device_id=device_id, ownership_generation=ownership_generation,
            execute_request_id=execute_request_id, receipt_revision=receipt_revision,
            revision=stored.revision, simulation_id=simulation.id, mode=simulation.mode,
            status=simulation.status.value, progress=simulation.progress,
            cancel_requested=None if job is None else job.cancel_requested, queue_position=queue_position,
            created_at=simulation.created_at, started_at=simulation.started_at,
            finished_at=simulation.finished_at, updated_at=stored.updated_at,
            scenario=HostedScenarioV1(name=simulation.scenario.name, sha256=scenario_sha256),
            result_available=result is not None,
            measured_duration_s=None if result is None else result.measured_duration_s,
            metrics=None if result is None else _metrics(result.metrics),
            runtime=None if result is None else _runtime(result.runtime),
            provenance=None if result is None else _provenance(result), error=error,
            evidence=HostedEvidenceV1(state=evidence_state,
                telemetry_frames=None if result is None else result.telemetry_frames,
                artifact_reference_count=None if result is None else len(result.artifacts)))
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise HostedProjectionError("The local run could not be projected safely") from None
