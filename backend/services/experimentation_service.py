"""Guidance and reviewed plans reuse the existing serial, durable worker."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json

from backend.experiment_repository import ExperimentRepository
from backend.job_repository import JobRepository
from backend.optimization_repository import OptimizationRepository
from backend.persistence_errors import ConflictError, NotFoundError, PersistenceError
from backend.repositories import SimulationRepository
from backend.services.agent_registry_service import AgentRegistryService
from evaluation.behavior_analysis import analyze_behavior
from backend.storage import ArtifactError
from backend.services.simulation_service import SimulationService
from optimization.discovery import candidates
from optimization.objective_function import assess_result
from optimization.search_manager import SearchManager
from scenarios.stress_presets import STRESS_PRESETS
from shared.models.experimentation import (DiscoveryPlan, DiscoveryProposal, DiscoveryReport,
    GalleryScenario, ReplayHighlight, ReplayHighlights, SavedFeedback, SavedSuite)
from shared.models.optimization import DiscoveryRequest, ExecutionSpec, OptimizationView, SearchRequest
from shared.models import TelemetryFrame


def gallery():
    learning = {
        "intersection": "Compare crossing arrival times and inspect recorded conflicts at the junction.",
        "pedestrian_crossing": "Watch a crossing pedestrian and identify the vehicle's measured response.",
        "sudden_braking": "Inspect following distance, lead braking and the ego vehicle's stopping response.",
        "occluded_pedestrian": "Investigate a geometrically occluded crossing; state agents may still receive ideal actor state.",
    }
    result = []
    for preset in STRESS_PRESETS:
        if preset.intended_stress == "low":
            result.append(GalleryScenario(id="starter-" + preset.family, title=preset.family.replace("_", " ").title(),
                description=preset.description, family=preset.family, level="starter",
                learning_goal=learning[preset.family], scenario=preset.scenario()))
        result.append(GalleryScenario(id=preset.id, title=preset.family.replace("_", " ").title() + " · " + preset.intended_stress,
            description=preset.description, family=preset.family, level=preset.intended_stress,
            learning_goal=learning[preset.family], scenario=preset.scenario()))
    return sorted(result, key=lambda item: item.level != "starter")


def plan_digest(plan):
    payload = {name: getattr(plan, name) for name in ("id", "proposal", "search", "scenarios", "agent_snapshot", "maximum_executions", "per_run_duration_s", "created_at", "expires_at", "steps", "limitations")}
    # Pydantic's JSON encoding retains exact typed recipe/identity values.
    encoded = json.dumps(json.loads(plan.model_copy(update=payload).model_dump_json(include=set(payload))),
                         sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return sha256(encoded.encode("utf-8")).hexdigest()


class ExperimentationService:
    def __init__(self, persistence, *, now=None, owner_scope=None):
        self.persistence = persistence
        self.owner_scope = owner_scope
        self.now = now or (lambda: datetime.now(UTC))

    def repository(self, session, kind):
        return ExperimentRepository(session, kind, owner_scope=self.owner_scope)

    def list_documents(self, kind, *, limit=50, offset=0, created_before=None):
        with self.persistence.database.session(write=False) as session:
            values = self.repository(session, kind).list(limit, offset, created_before=created_before)
        if kind == "discovery":
            for value in values:
                self._verify_plan(value)
        return values

    @staticmethod
    def _verify_plan(plan):
        if plan.digest != plan_digest(plan):
            raise PersistenceError("The saved discovery plan failed its integrity check")
        return plan

    def get_document(self, kind, identity):
        with self.persistence.database.session(write=False) as session:
            value = self.repository(session, kind).get(identity)
        return self._verify_plan(value) if kind == "discovery" else value

    def save_suite(self, request, *, identity=None, create_id=None, on_created=None):
        if request.definition.suite_id is not None:
            raise ValueError("Save a suite definition without an attached run identity")
        with self.persistence.database.session() as session:
            repository = self.repository(session, "suite")
            if identity is None:
                result = repository.create(SavedSuite(**({"id": create_id} if create_id is not None else {}), definition=request.definition, description=request.description))
            else:
                previous = repository.get(identity)
                updated = previous.model_copy(update={"revision": request.expected_revision + 1, "definition": request.definition,
                    "description": request.description, "updated_at": self.now()})
                result = repository.replace(updated, request.expected_revision)
            if on_created is not None:
                on_created(session, result)
            return result

    def delete_suite(self, identity, revision, *, on_deleted=None):
        with self.persistence.database.session() as session:
            self.repository(session, "suite").delete(identity, revision)
            if on_deleted is not None:
                on_deleted(session, identity)

    def launch_suite(self, identity, request, optimizer, *, admission=None, on_created=None):
        if self.owner_scope is not None:
            self.get_document("suite", identity)
        try:
            existing = optimizer.get(request.request_id).operation
        except NotFoundError:
            existing = None
        if existing is not None:
            if existing.request.kind != "suite" or existing.request.suite_id != identity or existing.request.suite_revision != request.expected_revision:
                raise ConflictError("This suite launch identity already has different settings")
            return existing
        suite = self.get_document("suite", identity)
        if suite.revision != request.expected_revision:
            raise ConflictError("The saved suite changed; review its current revision before running")
        definition = suite.definition.model_copy(update={"suite_id": suite.id, "suite_revision": suite.revision})
        def link(session, record):
            current = self.repository(session, "suite").get(identity)
            if current.revision != request.expected_revision or current.definition != suite.definition:
                raise ConflictError("The saved suite changed during launch")
            if on_created is not None:
                on_created(session, record)
        return optimizer.submit(definition, operation_id=request.request_id, on_created=link, admission=admission)

    def suite_runs(self, identity, *, limit=50, offset=0, created_before=None):
        self.get_document("suite", identity)
        with self.persistence.database.session(write=False) as session:
            return OptimizationRepository(session).list(suite_id=identity, limit=limit, offset=offset, created_before=created_before)

    def propose(self, proposal, *, identity=None, on_created=None):
        proposal = DiscoveryProposal.model_validate(proposal.model_dump(mode="python"))
        goal, budget = proposal.goal, proposal.budget
        search = SearchRequest(template=goal.template, seed=goal.seed, agent_id=goal.agent_id,
            parameters=goal.parameters, ranges=goal.ranges, failure_kind=goal.failure_kind,
            minimum_severity=goal.minimum_severity, driving_task=goal.driving_task,
            duration_s=goal.duration_s,
            trials=budget.max_search_trials, mode=proposal.mode, options=proposal.options,
            evaluation_settings=proposal.evaluation_settings)
        # Exhaust the pure sampler to validate every bounded recipe and compute
        # a conservative per-run window. This never starts a worker or imports an agent.
        manager, durations, frozen = SearchManager(search), [], []
        snapshot = None if goal.agent_id is None else AgentRegistryService(self.persistence.database).resolve(goal.agent_id)
        for _ in range(budget.max_search_trials):
            scenario = manager.suggest()
            frozen.append(scenario)
            durations.append(scenario.duration_s)
            if proposal.options.max_ticks < scenario.duration_s / scenario.fixed_delta_seconds - 1e-9:
                raise ValueError("Discovery requires enough ticks to observe each complete candidate")
            if snapshot is not None:
                SimulationService._validate_agent_launch(scenario, snapshot, proposal.options)
            manager.observe(None)
        if proposal.mode == "test" and (proposal.options.camera or proposal.options.lidar):
            raise ValueError("Raw recording options require CARLA mode")
        duration = max(durations)
        if duration * (1 + budget.confirmation_repeats) > budget.max_simulated_time_s + 1e-9:
            raise ValueError("Simulated-time budget must cover a complete candidate and its confirmations")
        now = self.now()
        plan = DiscoveryPlan(**({"id": identity} if identity is not None else {}), proposal=proposal, search=search, scenarios=frozen, agent_snapshot=snapshot,
            digest="0" * 64, maximum_executions=min(budget.max_executions,
                budget.max_search_trials + budget.max_candidates * budget.confirmation_repeats),
            per_run_duration_s=duration, created_at=now, expires_at=now + timedelta(hours=24),
            steps=[f"Search up to {budget.max_search_trials} seeded {goal.template.value.replace('_', ' ')} recipes for {goal.failure_kind.value.replace('_', ' ')}.",
                f"Repeat each matching candidate {budget.confirmation_repeats} times with the exact recipe, seed, agent and settings.",
                f"Confirm only after at least {budget.required_successes} matching repetitions; check at most {budget.max_candidates} candidates.",
                f"Stop at {budget.max_executions} executions, {budget.max_simulated_time_s:g} simulated seconds or {budget.max_wall_time_s:g} wall-clock seconds, whichever limits work first.",
                "Retain every negative, failed, inconclusive and unassessed trial, with links to recorded evidence."],
            limitations=["Synthetic mode checks the workflow; only CARLA mode measures simulator driving.",
                "The typed goal selects an observed failure policy. Free text is a description, not an unbounded instruction.",
                "Approval starts the absolute wall-clock budget, including queue time and interruptions. Cancellation and native cleanup can finish after the deadline.",
                "Repeated seeds test repeatability for these recorded conditions; they do not establish general safety or reveal model reasoning."])
        plan.digest = plan_digest(plan)
        with self.persistence.database.session() as session:
            result = self.repository(session, "discovery").create(plan)
            if on_created is not None:
                on_created(session, result)
            return result

    def approve(self, identity, approval, optimizer, *, admission=None, on_created=None):
        plan = self.get_document("discovery", identity)
        if approval.digest != plan.digest:
            raise ConflictError("Plan details changed; review a new plan before approval")
        if plan.operation_id is not None:
            return optimizer.get(plan.operation_id).operation
        if plan.revision != approval.expected_revision:
            raise ConflictError("Plan revision changed; reload it before approval")
        now = self.now()
        if now >= plan.expires_at:
            raise ConflictError("Plan review expired; create a fresh plan before approval")
        deadline = now + timedelta(seconds=plan.proposal.budget.max_wall_time_s)
        request = DiscoveryRequest(**plan.proposal.model_dump(mode="python"), plan_id=plan.id,
            plan_digest=plan.digest, search=plan.search, scenarios=plan.scenarios, approved_at=now, deadline_at=deadline)
        def link(session, record):
            repository = self.repository(session, "discovery")
            current = self._verify_plan(repository.get(identity))
            if current.operation_id is not None or current.revision != approval.expected_revision or current.digest != approval.digest:
                raise ConflictError("This discovery approval changed during admission")
            repository.replace(current.model_copy(update={"revision": current.revision + 1,
                "operation_id": record.id, "approved_at": now, "deadline_at": deadline}), current.revision)
            if on_created is not None:
                on_created(session, record)
        return optimizer.submit(request, operation_id=plan.id, on_created=link, approved_discovery=True,
                                expected_agent_snapshot=plan.agent_snapshot, admission=admission)

    def discovery_report(self, identity):
        plan = self.get_document("discovery", identity)
        if plan.operation_id is None:
            raise ConflictError("Review and approve this plan before requesting its trial report")
        with self.persistence.database.session(write=False) as session:
            repository = OptimizationRepository(session)
            operation, trials = repository.get(plan.operation_id), repository.trials(plan.operation_id)
            if (operation.request != plan.approved_request() or operation.agent_snapshot != plan.agent_snapshot
                    or operation.execution != ExecutionSpec(mode=plan.proposal.mode, options=plan.proposal.options,
                        evaluation_settings=plan.proposal.evaluation_settings)):
                raise PersistenceError("Discovery execution differs from its approved plan")
            for trial in trials:
                child = SimulationRepository(session).get(trial.simulation_id)
                job = JobRepository(session).get(trial.simulation_id)
                if (child.simulation.scenario != trial.scenario or child.simulation.agent_snapshot != plan.agent_snapshot
                        or job.agent_snapshot != plan.agent_snapshot or child.simulation.mode != operation.execution.mode
                        or job.options != operation.execution.options or job.evaluation_settings != operation.execution.evaluation_settings):
                    raise PersistenceError("Discovery child differs from its frozen plan")
                if trial.assessed:
                    if child.result is None:
                        raise PersistenceError("Assessed discovery trial has no finalized evidence")
                    measured = assess_result(child.result, kind=plan.proposal.goal.failure_kind,
                        minimum_severity=plan.proposal.goal.minimum_severity)
                    if (measured.matches, measured.score, measured.reason) != (trial.matches, trial.score, trial.reason):
                        raise PersistenceError("Discovery assessment differs from its measured result")
        findings = candidates(operation, trials)
        confirmed = sum(item.status == "confirmed" for item in findings)
        summary = operation.outcome.summary if operation.outcome is not None else "Discovery is in progress; candidate observations are not confirmed failures."
        return DiscoveryReport(plan=plan, evidence=OptimizationView(operation=operation, trials=trials), candidates=findings,
            confirmed_failures=confirmed, executions_used=len(trials), simulated_time_reserved_s=sum(t.scenario.duration_s for t in trials),
            negative_trials=sum(t.assessed and t.matches is False for t in trials),
            unknown_trials=sum(t.assessed and t.matches is None for t in trials), unassessed_trials=sum(not t.assessed for t in trials),
            summary=summary, limitations=plan.limitations)

    def save_feedback(self, feedback, *, identity=None, on_created=None):
        if feedback.simulation_id is not None:
            stored = self.persistence.get_simulation(feedback.simulation_id)
            if stored.result is None:
                raise ConflictError("Review a finalized experiment before recording outcome feedback")
        with self.persistence.database.session() as session:
            result = self.repository(session, "feedback").create(SavedFeedback(**({"id": identity} if identity is not None else {}), feedback=feedback))
            if on_created is not None:
                on_created(session, result)
            return result


def read_highlight_evidence(persistence, simulation_id):
    """Validate bounded telemetry without reading unrelated camera/LiDAR blobs."""
    result = persistence.get_simulation(simulation_id).result
    if result is None:
        raise ConflictError("Finalize the experiment before reviewing highlights")
    references = [ref for ref in result.artifacts if ref.kind == "telemetry"]
    if len(references) != 1:
        raise PersistenceError("Highlights require one recorded telemetry artifact")
    path = persistence.artifacts.validate_reference(references[0], simulation_id)
    if path.stat().st_size > 128 * 1024**2:
        raise ArtifactError("Highlight evidence exceeds its review limit; use paged telemetry or a local export")
    frames = []
    with path.open("rb") as source:
        while line := source.readline(256 * 1024 + 1):
            if len(line) > 256 * 1024 or not line.endswith(b"\n") or len(frames) >= 100001:
                raise ArtifactError("Highlight evidence exceeds its frame review limit; use paged telemetry")
            frames.append(TelemetryFrame.model_validate_json(line))
    return result, persistence._validate_trace(result, frames)


def replay_highlights(persistence, simulation_id):
    result, frames = read_highlight_evidence(persistence, simulation_id)
    analysis = analyze_behavior(result, frames)
    by_frame, highlights = {frame.frame: frame for frame in frames}, []
    def add(kind, frame, title, description):
        if frame is not None:
            highlights.append(ReplayHighlight(kind=kind, frame=frame.frame, time_s=frame.time_s, title=title, description=description))
    add("collision", next((f for f in frames if f.collisions), None), "Recorded contact", "A collision sensor recorded contact at this frame. Inspect the participants and controls in the evidence.")
    add("traffic_violation", next((f for f in frames if f.traffic_violations), None), "Recorded rule event", "A recorded rule monitor flagged a violation. The exact rule and observation coverage remain in the telemetry.")
    peak = analysis.braking.max_deceleration
    if peak.value is not None and peak.value >= analysis.braking.settings.hard_braking_threshold_mps2 and peak.evidence:
        add("hard_braking", by_frame.get(peak.evidence[0].first_frame), "Peak measured deceleration", f"Recorded peak deceleration was {peak.value:.2f} m/s². Applied controls and scripted interventions are separate evidence.")
    for event in [e for e in analysis.events if e.anchor.kind == "measured_hazard" and e.anchor.frame is not None][:3]:
        add("near_miss", by_frame.get(event.anchor.frame), "Evaluator conflict threshold", f"Recorded geometry crossed an evaluator threshold near {event.anchor.actor_id}. This does not establish that the agent perceived the hazard.")
    if frames:
        add("finish", frames[-1], "Last recorded frame", f"Execution ended as {result.simulation.status.value}. Recorded severity is " + ("unavailable." if result.metrics.severity is None else f"{result.metrics.severity:.1f} / 100; this is a heuristic, not a safety certification."))
    highlights.sort(key=lambda item: (item.time_s, item.frame, item.kind))
    return ReplayHighlights(simulation_id=simulation_id, mode=result.simulation.mode, highlights=highlights,
        total_frames=len(frames), coverage_complete=result.metrics.coverage.complete,
        explanation="Synthetic fixtures illustrate the workflow; these are not measurements of real CARLA driving." if result.simulation.mode == "test" else "Highlights point to saved measured frames. Open each frame to inspect observations and applied controls.",
        limitations=["Highlights are selected evidence, not a complete replay or an explanation of model reasoning.",
            "Missing observations and partial coverage remain unknown. A run without a highlighted failure does not establish safety.",
            "Highlights validate saved telemetry. Individual recording checksums are verified separately when recordings are inspected.",
            "Camera and LiDAR recordings, when captured, remain available through the run's recording viewer."])
