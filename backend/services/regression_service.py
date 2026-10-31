"""Descriptive regression patterns in frozen, matched and observed suite trials."""

from math import fsum, isfinite, sqrt

from backend.persistence_errors import PersistenceError
from backend.storage import ArtifactError
from shared.models.analysis import (FrozenController, RegressionAssessment, RegressionPair,
    RegressionPolicy, RegressionReport, RegressionReviewCoverage)

from .comparison_service import METRICS, read_suite_evidence
from .evidence_analysis_service import digest
from .experimentation_service import MAX_REVIEW_BYTES, ReviewBudget, ReviewLimitError, read_review_evidence, verify_review_metrics

LOWER_IS_WORSE = {"min_ttc_s", "min_clearance_m"}


def _controller(index, agent):
    snapshot = agent.snapshot
    if snapshot is None:
        return FrozenController(agent_index=index, registration_id=agent.agent_id, name="Built-in baseline",
            version=None, snapshot_digest=None, execution_digest=None, python_digest=None,
            dependencies_digest=None, configuration_digest=None)
    manifest = snapshot.manifest.model_dump(mode="json")
    for field in ("agent_id", "name", "version"):
        manifest.pop(field, None)
    execution = {"manifest": manifest, "configuration": snapshot.configuration,
        "files": sorted((item.path, item.sha256, item.size_bytes) for item in snapshot.files if item.path != "agent.json"),
        "models": sorted((item.sha256, item.size_bytes) for item in snapshot.model_files),
        "control": snapshot.agent_control_mode}
    return FrozenController(agent_index=index, registration_id=agent.agent_id, name=snapshot.manifest.name,
        version=snapshot.manifest.version, snapshot_digest=digest(snapshot), execution_digest=digest(execution),
        python_digest=snapshot.python_sha256, configuration_digest=digest(snapshot.configuration),
        dependencies_digest=digest(sorted((item.sha256, item.size_bytes) for item in snapshot.runtime_files)))


def _input_contract(value):
    if value is None:
        return None
    result = value.model_dump(mode="json")
    result["assistance"] = sorted(result["assistance"])
    result["sensors"] = sorted(result["sensors"], key=lambda item: item["id"])
    return result


def _control(value):
    return None if value is None else (value.mode, value.controller, value.navigation)


def _pair_context_reason(left, right, mode):
    if left is None or right is None or left.result is None or right.result is None:
        return "The matched pair has not finalized"
    if any(child.simulation.status != "completed" for child in (left, right)):
        return "The pair includes an unfinished, failed or cancelled execution"
    before, after = left.result.runtime, right.result.runtime
    if before is None or after is None:
        return "Pair has no comparable recorded runtime"
    if any(getattr(before, field) != getattr(after, field)
           for field in ("adapter", "map_name", "fixed_delta_seconds", "client_version", "server_version")):
        return "Recorded runtime versions or map/timestep differ"
    if mode == "carla" and any(runtime.client_version is None or runtime.server_version is None for runtime in (before, after)):
        return "CARLA version evidence is incomplete"
    return None


def _metric_observed(metrics, metric):
    """Require the selected measurement's whole-window coverage, not another's."""
    coverage = metrics.coverage
    if not coverage.total_frames or coverage.has_gaps or getattr(metrics, metric) is None:
        return False
    if metric == "severity":
        return coverage.complete
    fields = {
        "collision_count": "collision_frames", "traffic_violation_count": "traffic_rule_frames",
        "max_deceleration_mps2": "braking_frames", "hard_braking_count": "braking_frames",
        "min_clearance_m": "clearance_frames", "min_ttc_s": "ttc_frames",
    }
    if getattr(coverage, fields[metric]) != coverage.total_frames:
        return False
    if metric == "min_clearance_m":
        return coverage.clearance_pairs == coverage.actor_pairs
    if metric == "min_ttc_s":
        return coverage.ttc_pairs == coverage.actor_pairs
    return True


def _spread(values):
    if not values:
        return None, None
    scale = max(abs(value) for value in values)
    normalized = [value / scale for value in values] if scale else [0.] * len(values)
    mean = fsum(normalized) / len(values)
    deviation = None if len(values) < 2 else sqrt(fsum((value - mean)**2 for value in normalized) / (len(values) - 1)) * scale
    return mean * scale, deviation if deviation is None or isfinite(deviation) else None


def build_regression_report(persistence, operation_id, policy=None):
    policy = RegressionPolicy() if policy is None else RegressionPolicy.model_validate(
        policy.model_dump(mode="json") if hasattr(policy, "model_dump") else policy)
    suite, children, _ = read_suite_evidence(persistence, operation_id)
    operation = suite.evidence.operation
    controllers = [_controller(index, agent) for index, agent in enumerate(operation.comparison_agents)]
    notes = []
    if len(controllers) < 2:
        notes.append("Choose at least two frozen registered controller versions in a saved suite, with the reference first.")
    missing_dependencies = [index for index, agent in enumerate(operation.comparison_agents) if agent.snapshot is not None and not agent.snapshot.runtime_files]
    if missing_dependencies:
        notes.append(f"Runtime dependency inventories were not recorded for controllers {', '.join(map(str, missing_dependencies))}. Environment equivalence and attribution to weights alone cannot be established.")
    if any(item.execution_digest is None for item in controllers):
        notes.append("Version-regression decisions require two frozen registered controller artifacts. The built-in baseline has no recorded artifact version; its ordinary suite comparisons remain available.")
    for item in controllers[1:]:
        if item.python_digest != controllers[0].python_digest or item.dependencies_digest != controllers[0].dependencies_digest:
            notes.append(f"Controller {item.agent_index} has different recorded Python/dependency fingerprints. Observed stack differences cannot be attributed to model weights alone.")
    budget = ReviewBudget(MAX_REVIEW_BYTES, 100001)
    validated, evidence_digests, limited = {}, {}, 0
    distinct_versions = len({item.execution_digest for item in controllers if item.execution_digest}) > 1
    if distinct_versions:
        for identity, child in children.items():
            result = child.result
            if result is None or child.simulation.status != "completed":
                validated[identity] = "Run is incomplete or has no saved result"
                continue
            try:
                reviewed, frames, fingerprint = read_review_evidence(persistence, identity, budget=budget)
                if reviewed != result:
                    raise PersistenceError("Result changed during evidence review")
                verify_review_metrics(reviewed, frames)
                validated[identity] = None
                evidence_digests[str(identity)] = fingerprint
                del frames
            except (ArtifactError, PersistenceError, ValueError, OSError) as error:
                validated[identity] = "Saved telemetry is unavailable or failed validation"
                if isinstance(error, ReviewLimitError):
                    limited += 1
                    validated[identity] = "The bounded evidence-review budget cannot cover this run; use paged evidence or exports"
    pairs, assessments = [], []
    trials = {item.simulation_id: item for item in suite.evidence.trials}
    for case in suite.cases:
        for comparison in case.paired_comparisons:
            controller = controllers[comparison.compared_agent_index]
            case_pairs = []
            for pair in comparison.pairs:
                left = children.get(pair.reference_simulation_id)
                right = children.get(pair.compared_simulation_id)
                reason = None
                if not controllers[0].execution_digest or not controller.execution_digest:
                    reason = "Two frozen registered controller versions are required"
                elif controller.execution_digest == controllers[0].execution_digest:
                    reason = "The recorded controller artifacts/configuration are identical"
                elif context_reason := _pair_context_reason(left, right, operation.execution.mode):
                    reason = context_reason
                elif validated.get(pair.reference_simulation_id) or validated.get(pair.compared_simulation_id):
                    reason = validated.get(pair.reference_simulation_id) or validated.get(pair.compared_simulation_id)
                elif not all(trials[identity].assessed for identity in (pair.reference_simulation_id, pair.compared_simulation_id)):
                    reason = "The matched trial journal has not finished assessment"
                elif left.result.agent_inputs is None or right.result.agent_inputs is None:
                    reason = "Agent input provenance was not recorded"
                elif _input_contract(left.result.agent_inputs) != _input_contract(right.result.agent_inputs):
                    reason = "Observation sources, sensors or declared assistance differ"
                elif _control(left.result.control_audit) is None or _control(right.result.control_audit) is None:
                    reason = "Control authority was not recorded"
                elif _control(left.result.control_audit) != _control(right.result.control_audit):
                    reason = "Control authority or navigation assistance differs"
                elif left.result.runtime.resolved_spawns != right.result.runtime.resolved_spawns:
                    reason = "Recorded resolved spawn transforms differ"
                elif any(child.result.metrics.evaluation_settings != operation.execution.evaluation_settings for child in (left, right)):
                    reason = "Recorded scoring settings are missing or differ from the frozen plan"
                differences, reference_values, compared_values, unavailable = {}, {}, {}, []
                for metric in policy.thresholds:
                    before = None if left is None or left.result is None else getattr(left.result.metrics, metric)
                    after = None if right is None or right.result is None else getattr(right.result.metrics, metric)
                    observed = not reason and all(_metric_observed(child.result.metrics, metric) for child in (left, right))
                    change = after - before if observed else None
                    differences[metric] = change if change is None or isfinite(change) else None
                    reference_values[metric], compared_values[metric] = before, after
                    if not reason and differences[metric] is None:
                        unavailable.append(metric)
                comparable = not reason and any(value is not None for value in differences.values())
                if reason is None:
                    reason = ("Matched frozen inputs, runtime, spawns, control and scoring; complete coverage for available selected measurements."
                        if comparable else "No selected measurement has complete coverage and available values in both runs.")
                    if unavailable:
                        reason += f" Unavailable selected measurements: {', '.join(unavailable)} (incomplete coverage or missing values)."
                item = RegressionPair(case_index=case.case_index, compared_agent_index=comparison.compared_agent_index,
                    seed=pair.seed, repeat_index=pair.repeat_index, reference_simulation_id=pair.reference_simulation_id,
                    compared_simulation_id=pair.compared_simulation_id, comparable=comparable,
                    reason=reason,
                    differences=differences, reference_values=reference_values, compared_values=compared_values)
                pairs.append(item)
                case_pairs.append(item)
            for metric, threshold in policy.thresholds.items():
                values = [item.differences[metric] for item in case_pairs if item.differences[metric] is not None]
                sign = -1 if metric in LOWER_IS_WORSE else 1
                worsening = sum(value * sign > threshold for value in values)
                decision = "inconclusive"
                if len(values) >= policy.minimum_pairs:
                    if worsening == len(values):
                        decision = "regression_observed"
                    elif len(values) == len(case_pairs) and not worsening:
                        decision = "no_regression_observed"
                mean, deviation = _spread(values)
                assessments.append(RegressionAssessment(case_index=case.case_index, case_name=case.name,
                    compared_agent_index=comparison.compared_agent_index, metric=metric, unit=METRICS[metric],
                    worsening_direction="lower" if sign == -1 else "higher", threshold=threshold, decision=decision,
                    planned_pairs=len(case_pairs), observed_pairs=len(values), worsening_pairs=worsening,
                    mean_change=mean, minimum_change=min(values) if values else None, maximum_change=max(values) if values else None,
                    sample_stddev=deviation,
                    explanation=f"{worsening} of {len(values)} observed pairs exceed the worsening threshold; {len(case_pairs) - len(values)} planned pairs are unavailable. At least {policy.minimum_pairs} observed pairs with a consistent pattern are required. Differences and spread are descriptive, not statistical significance."))
    pending = operation.status in {"queued", "running"}
    observed = sum(item.decision == "regression_observed" for item in assessments)
    state = "pending" if pending else "regression_observed" if observed else "inconclusive"
    if (not pending and assessments and all(item.decision == "no_regression_observed" for item in assessments)
            and operation.status == "completed" and len(suite.evidence.trials) == operation.request.trial_budget):
        state = "no_regression_observed"
    source = {"journal": suite.evidence, "telemetry": evidence_digests, "results": {str(identity): {
        "simulation": child.simulation, "metrics": None if child.result is None else child.result.metrics,
        "runtime": None if child.result is None else child.result.runtime,
        "inputs": None if child.result is None else child.result.agent_inputs,
        "control": None if child.result is None else _control(child.result.control_audit)} for identity, child in children.items()}}
    # Serialize typed nested objects before hashing; paths remain inside the digest.
    source["journal"] = suite.evidence.model_dump(mode="json")
    for value in source["results"].values():
        for key, item in value.items():
            if hasattr(item, "model_dump"):
                value[key] = item.model_dump(mode="json")
    return RegressionReport(operation_id=operation_id, execution_status=operation.status, policy=policy,
        source_digest=digest(source), plan_digest=digest(operation.request), state=state,
        summary=("The frozen suite is still running; current observations are provisional." if pending else
            f"{observed} case/controller/metric comparisons show a repeated worsening pattern under the selected policy. " +
            ("All selected comparisons have complete paired evidence with no threshold-exceeding change." if state == "no_regression_observed" else
             "Unresolved or unselected measurements are not cleared by this report.")),
        controllers=controllers, context_notes=notes[:16], pairs=pairs, assessments=assessments,
        evidence_review=RegressionReviewCoverage(available_runs=sum(value is None for value in validated.values()),
            unavailable_runs=sum(value is not None for value in validated.values()), limited_runs=limited,
            frames_read=100001 - budget.remaining_frames, bytes_read=MAX_REVIEW_BYTES - budget.remaining_bytes,
            frame_limit=100001, byte_limit=MAX_REVIEW_BYTES),
        limitations=["This is an observed, threshold-based pattern in matched frozen trials, not a probability of failure, a causal attribution or a general safety ranking.",
            "Raw differences are compared minus reference. Lower TTC/clearance is worse; other selected metrics worsen when higher.",
            "Each selected measurement requires its own complete frame/pair coverage and no timeline gaps. Severity requires complete safety coverage. Missing observations never become zero.",
            "A negative/clear result requires every planned pair and at least the policy's minimum count. Failed, cancelled, corrupted or incompatible evidence is unavailable.",
            "A positive pattern requires every observed paired change to exceed the threshold and at least the minimum count. Mixed patterns are inconclusive; sample spread is descriptive.",
            "Frozen snapshots describe the executed artifacts/configuration. They do not establish that those files remain installed or that model weights alone caused a difference.",
            "Reproduce using the frozen suite request and version snapshots in its existing evidence export; later edits or deletion of a reusable definition do not replace that journal.",
            "Synthetic fixtures check this workflow. CARLA, hardware and learned inference can vary between repeated seeds."])
