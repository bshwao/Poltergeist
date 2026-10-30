"""Deterministic discovery planning and conservative confirmation evidence."""

from shared.models.experimentation import DiscoveryCandidate
from shared.models.optimization import ReproductionRequest

from .reproduction import summarize_reproduction


def candidates(record, trials):
    """Audit the exact search/confirmation journal, retaining incomplete work."""
    request = record.request
    if request.kind != "discover":
        raise ValueError("A discovery journal requires an approved discovery request")
    budget = request.budget
    if len(trials) > budget.max_executions or sum(t.scenario.duration_s for t in trials) > budget.max_simulated_time_s + 1e-9:
        raise ValueError("Discovery journal exceeds its approved budget")
    findings, cursor = [], 0

    def take(role, index, repeat, expected):
        nonlocal cursor
        item = trials[cursor]
        if (item.number != cursor or item.operation_id != record.id or item.role != role
                or item.candidate_index != index or item.repeat_index != repeat or item.agent_index != 0
                or item.scenario != expected):
            raise ValueError("Discovery journal differs from its approved deterministic plan")
        if not item.assessed and cursor != len(trials) - 1:
            raise ValueError("Discovery cannot advance beyond an unassessed trial")
        cursor += 1
        return item

    for index in range(budget.max_search_trials):
        if cursor == len(trials):
            break
        scenario = request.scenarios[index]
        search = take("discovery_search", index, 0, scenario)
        if search.matches is not True:
            continue
        repeats = []
        for repeat in range(budget.confirmation_repeats):
            if cursor == len(trials):
                break
            repeats.append(take("discovery_confirm", index, repeat, scenario))
        complete = len(repeats) == budget.confirmation_repeats and all(t.assessed for t in repeats)
        decision = summarize_reproduction([t.matches for t in repeats], total_repeats=budget.confirmation_repeats,
            required_successes=budget.required_successes) if complete else None
        status = "confirmed" if decision is True else "not_reproduced" if decision is False else "inconclusive" if complete else "pending"
        findings.append(DiscoveryCandidate(candidate_index=index, scenario=scenario,
            search_simulation_id=search.simulation_id, failure_id=search.failure_id,
            confirmation_simulation_ids=[t.simulation_id for t in repeats], status=status,
            required_successes=budget.required_successes, matching_confirmations=sum(t.matches is True for t in repeats),
            negative_confirmations=sum(t.matches is False for t in repeats),
            unknown_confirmations=sum(t.assessed and t.matches is None for t in repeats),
            pending_confirmations=budget.confirmation_repeats - sum(t.assessed for t in repeats),
            reproduction=ReproductionRequest(failure_id=search.failure_id, repeats=budget.confirmation_repeats,
                required_successes=budget.required_successes, minimum_severity=request.goal.minimum_severity)
                if decision is True and search.failure_id is not None else None))
        if len(findings) >= budget.max_candidates:
            break
    if cursor != len(trials):
        raise ValueError("Discovery journal contains work beyond its approved search and confirmation policy")
    return findings
