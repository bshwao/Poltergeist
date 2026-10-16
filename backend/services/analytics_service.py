"""Analytics over one persisted database snapshot, with observation nulls intact."""

from collections import Counter
from typing import Literal

from sqlalchemy import select

from backend.models import FailureRecord, SimulationRecord
from backend.repositories import FailureRepository, SimulationRepository
from backend.services.persistence_service import PersistenceService
from shared.models import SimulationStatus
from shared.models.api import AnalyticsSummary, ModeAnalytics


class AnalyticsService:
    """Keep synthetic and CARLA results separate and avoid paginated undercounts.

    Severity averages include all scored terminal results, including a known
    collision score from an incomplete run. Null scores never become zeros.
    Observed run counts mean a non-null count metric; positive event counts can
    be lower bounds from partially observed traces. These are not coverage rates.
    """

    def __init__(self, persistence: PersistenceService):
        self.persistence = persistence

    def summarize(self, mode: Literal["carla", "test"] | None = None) -> AnalyticsSummary:
        modes = (mode,) if mode is not None else ("carla", "test")
        groups = {}
        scores = {selected: [] for selected in modes}
        for selected in modes:
            groups[selected] = {
                "mode": selected, "total_runs": 0,
                "status_counts": {status.value: 0 for status in SimulationStatus},
                "result_count": 0, "scored_runs": 0, "unscored_results": 0,
                "mean_severity": None, "collision_runs": 0, "collision_observed_runs": 0,
                "traffic_violation_runs": 0, "traffic_observed_runs": 0,
                "failure_reports": 0, "failure_kinds": Counter(),
            }

        # A single short read transaction gives runs and reports one consistent
        # snapshot. Iteration has no repository page limit and no hidden cutoff.
        with self.persistence.database.session(write=False) as session:
            query = select(SimulationRecord)
            if mode is not None:
                query = query.where(SimulationRecord.mode == mode)
            for row in session.scalars(query.execution_options(yield_per=256)):
                stored = SimulationRepository._stored(row)
                simulation = stored.simulation
                group = groups[simulation.mode]
                group["total_runs"] += 1
                group["status_counts"][simulation.status.value] += 1
                if stored.result is None:
                    continue
                group["result_count"] += 1
                metrics = stored.result.metrics
                if metrics.severity is None:
                    group["unscored_results"] += 1
                else:
                    scores[simulation.mode].append(metrics.severity)
                if metrics.collision_count is not None:
                    group["collision_observed_runs"] += 1
                    group["collision_runs"] += int(metrics.collision_count > 0)
                if metrics.traffic_violation_count is not None:
                    group["traffic_observed_runs"] += 1
                    group["traffic_violation_runs"] += int(metrics.traffic_violation_count > 0)

            failures = select(FailureRecord).join(
                SimulationRecord, FailureRecord.simulation_id == SimulationRecord.id,
            )
            if mode is not None:
                failures = failures.where(SimulationRecord.mode == mode)
            # Read the mode projection in this same transaction. Failure payloads
            # are validated rather than trusting arbitrary JSON field values.
            for row, run_mode in session.execute(failures.add_columns(SimulationRecord.mode).execution_options(yield_per=256)):
                report = FailureRepository._stored(row)
                group = groups[run_mode]
                group["failure_reports"] += 1
                group["failure_kinds"][report.kind.value] += 1

        summaries = []
        for selected in modes:
            group = groups[selected]
            known = scores[selected]
            group["scored_runs"] = len(known)
            group["mean_severity"] = sum(known) / len(known) if known else None
            summaries.append(ModeAnalytics(**group))
        return AnalyticsSummary(groups=summaries)
