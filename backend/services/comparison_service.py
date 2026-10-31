"""Read-only summaries of the durable matched-seed experiment journal."""

from collections import Counter
from math import sqrt

from backend.optimization_repository import OptimizationRepository
from backend.job_repository import JobRepository
from backend.persistence_errors import ConflictError, PersistenceError
from backend.repositories import SimulationRepository
from shared.models.optimization import OptimizationView
from shared.models.comparison import ComparisonDistribution, ComparisonGroup, ComparisonPair, ComparisonReport, PairedComparison

METRICS = {"severity": "0–100 heuristic", "collision_count": "episodes", "min_ttc_s": "s",
           "min_clearance_m": "m", "max_deceleration_mps2": "m/s²", "hard_braking_count": "episodes",
           "traffic_violation_count": "episodes", "max_stopping_distance_m": "m", "max_stopping_time_s": "s"}


def distribution(values, eligible, unit):
    # Normalize before summation/squaring to keep finite, extreme valid measures
    # representable; no confidence interval or significance claim is inferred.
    scale = max((abs(v) for v in values), default=0)
    normalized = [v / scale for v in values] if scale else [0.0 for _ in values]
    center = sum(normalized) / len(values) if values else None
    mean = None if center is None else center * scale
    deviation = None if len(values) < 2 else sqrt(sum((v - center) ** 2 for v in normalized) / (len(values) - 1)) * scale
    return ComparisonDistribution(eligible_results=eligible, observed_results=len(values), unavailable_results=eligible - len(values),
        unit=unit, mean=mean, minimum=min(values) if values else None, maximum=max(values) if values else None, sample_stddev=deviation)


def build_comparison(persistence, operation_id):
    with persistence.database.session(write=False) as session:
        repository = OptimizationRepository(session)
        operation = repository.get(operation_id)
        if operation.request.kind != "compare":
            raise ConflictError("A comparison report requires a compare operation")
        trials = repository.trials(operation_id)
        children = {trial.simulation_id: SimulationRepository(session).get(trial.simulation_id) for trial in trials}
        jobs = {trial.simulation_id: JobRepository(session).get(trial.simulation_id) for trial in trials}
    request = operation.request
    return _comparison(operation, trials, children, jobs, request)


def _comparison(operation, trials, children, jobs, request, *, role="compare", number_offset=0, candidate_offset=0):
    groups = []
    indexed = {}
    for number, trial in enumerate(trials):
        count = len(request.agents)
        if (trial.number != number + number_offset or trial.role != role or number >= request.trial_budget
                or trial.agent_index != number % count
                or trial.repeat_index != (number // count) % request.repeats
                or trial.candidate_index != candidate_offset + number // (count * request.repeats)):
            raise PersistenceError("Comparison journal differs from its recorded execution plan")
    for index, agent in enumerate(operation.comparison_agents):
        selected = [trial for trial in trials if trial.agent_index == index]
        finalized = []
        statuses, sources, tasks = Counter(), Counter(), Counter()
        for trial in selected:
            child = children[trial.simulation_id]
            job = jobs[trial.simulation_id]
            expected = request.scenario.model_copy(update={"seed": request.seeds[trial.candidate_index - candidate_offset]})
            if (child.simulation.agent_snapshot != agent.snapshot or job.agent_snapshot != agent.snapshot
                    or child.simulation.agent != ("baseline" if agent.snapshot is None else "custom")
                    or child.simulation.mode != operation.execution.mode
                    or job.options != operation.execution.options or job.evaluation_settings != operation.execution.evaluation_settings
                    or child.simulation.scenario != expected):
                raise PersistenceError("Comparison child identity or paired scenario differs from its journal")
            indexed[(index, trial.candidate_index - candidate_offset, trial.repeat_index)] = child
            statuses[child.simulation.status.value] += 1
            if child.result is not None:
                finalized.append(child.result)
                sources["not_recorded" if child.result.agent_inputs is None else child.result.agent_inputs.source] += 1
                tasks["not_recorded" if child.result.driving_outcome is None else child.result.driving_outcome.status] += 1
        completed = [r for r in finalized if r.simulation.status == "completed"]
        snapshot = agent.snapshot
        groups.append(ComparisonGroup(agent_index=index, registration_id=agent.agent_id,
            name="Built-in baseline" if snapshot is None else snapshot.manifest.name,
            version=None if snapshot is None else snapshot.manifest.version,
            planned_runs=len(request.seeds) * request.repeats, simulation_ids=[t.simulation_id for t in selected],
            status_counts=dict(statuses), assessed_runs=sum(t.assessed for t in selected),
            matching_runs=sum(t.assessed and t.matches is True for t in selected),
            negative_runs=sum(t.assessed and t.matches is False for t in selected),
            unknown_runs=sum(t.assessed and t.matches is None for t in selected), unassessed_runs=sum(not t.assessed for t in selected),
            completed_runs=len(completed), partial_coverage_results=sum(not r.metrics.coverage.complete for r in finalized),
            input_sources=dict(sources), task_outcomes=dict(tasks),
            metrics={key: distribution([getattr(r.metrics, key) for r in completed if getattr(r.metrics, key) is not None], len(finalized), unit)
                     for key, unit in METRICS.items()}))
    paired = []
    for agent_index in range(1, len(groups)):
        pairs, differences = [], []
        for seed_index, seed in enumerate(request.seeds):
            for repeat in range(request.repeats):
                left, right = indexed.get((0, seed_index, repeat)), indexed.get((agent_index, seed_index, repeat))
                delta = None
                if left is None or right is None or left.result is None or right.result is None:
                    reason = "Pair has not finalized"
                elif any(r.simulation.status != "completed" for r in (left, right)):
                    reason = "Pair includes a failed or cancelled execution"
                elif any(not r.result.metrics.coverage.complete or r.result.metrics.severity is None for r in (left, right)):
                    reason = "Pair lacks complete safety coverage or a recorded severity"
                elif left.result.runtime is None or right.result.runtime is None:
                    reason = "Pair has no comparable recorded runtime"
                elif any(getattr(left.result.runtime, field) != getattr(right.result.runtime, field)
                         for field in ("adapter", "map_name", "fixed_delta_seconds", "client_version", "server_version")):
                    reason = "Recorded runtime versions or map/timestep differ"
                elif operation.execution.mode == "carla" and any(r.result.runtime.client_version is None or r.result.runtime.server_version is None for r in (left, right)):
                    reason = "CARLA version evidence is incomplete"
                else:
                    delta = right.result.metrics.severity - left.result.metrics.severity
                    differences.append(delta)
                    reason = "Observed completed pair with full safety coverage; descriptive difference only"
                pairs.append(ComparisonPair(seed=seed, repeat_index=repeat,
                    reference_simulation_id=None if left is None else left.simulation.id,
                    compared_simulation_id=None if right is None else right.simulation.id, severity_difference=delta, reason=reason))
        paired.append(PairedComparison(compared_agent_index=agent_index, difference=distribution(differences, len(pairs), "severity points, compared minus reference"), pairs=pairs))
    return ComparisonReport(evidence=OptimizationView(operation=operation, trials=trials), groups=groups, paired_comparisons=paired, notes=[
        "Agents receive matched scenario/seed/repetition cells through one serial queue, in the recorded agent order. This is not a real-world safety ranking or a causal experiment.",
        "Metric distributions include completed runs with available values. Failed/cancelled or null-valued finalized runs are unavailable; unfinalized runs are outside the denominator. Completed partial-coverage measures are retained and their count is explicit.",
        "Each metric has its own observed denominator. Missing values never become zero. Standard deviation is descriptive sample spread and is unavailable with fewer than two observations.",
        "Paired severity differences require completed, fully observed runs and compatible recorded runtimes. Positive differences mean a higher heuristic severity than agent zero, not a failure probability.",
        "Different observation sources, route ownership or control authority can change the information/assistance available to an agent. Inspect input-source counts and the frozen snapshots before interpreting differences.",
        "Seeds do not guarantee deterministic learned inference, hardware behavior or CARLA execution. Repetitions are separate observed runs, not proof of generalization.",
        "Summary metrics are stored safety/task outcomes. Open individual run links for the Phase 8 behavior analysis, settings, interventions and frame evidence. No model or simulator is started by this report.",
    ])


def build_suite_report(persistence, operation_id):
    from shared.models.experimentation import SuiteCaseComparison, SuiteReport
    from shared.models.optimization import ComparisonRequest
    with persistence.database.session(write=False) as session:
        repository = OptimizationRepository(session)
        operation = repository.get(operation_id)
        if operation.request.kind != "suite":
            raise ConflictError("A suite report requires a suite operation")
        trials = repository.trials(operation_id)
        children = {t.simulation_id: SimulationRepository(session).get(t.simulation_id) for t in trials}
        jobs = {t.simulation_id: JobRepository(session).get(t.simulation_id) for t in trials}
    request, cases = operation.request, []
    per_case = len(request.agents) * len(request.seeds) * request.repeats
    if len(trials) > request.trial_budget or any(t.number != i for i, t in enumerate(trials)):
        raise PersistenceError("Suite journal exceeds or differs from its frozen plan")
    for index, case in enumerate(request.cases):
        specification = ComparisonRequest(scenario=case.scenario, agents=request.agents, seeds=request.seeds,
            repeats=request.repeats, mode=request.mode, options=request.options,
            evaluation_settings=request.evaluation_settings, failure_kind=request.failure_kind, minimum_severity=request.minimum_severity)
        report = _comparison(operation, trials[index * per_case:(index + 1) * per_case], children, jobs, specification,
            role="suite", number_offset=index * per_case, candidate_offset=index * len(request.seeds))
        cases.append(SuiteCaseComparison(case_index=index, name=case.name, groups=report.groups,
            paired_comparisons=report.paired_comparisons, notes=report.notes))
    return SuiteReport(evidence=OptimizationView(operation=operation, trials=trials), cases=cases)
