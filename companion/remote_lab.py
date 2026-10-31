"""Owner-scoped, bounded experiment actions over the negotiated companion relay."""

from contextlib import contextmanager
from math import ceil, hypot
from datetime import UTC, datetime

from sqlalchemy import select

from backend.experiment_repository import ExperimentRepository
from backend.job_repository import JobRepository
from backend.models import OptimizationTrialRecord
from backend.optimization_repository import OptimizationRepository
from backend.persistence_errors import ConflictError, NotFoundError
from backend.remote_receipts import RemoteReceiptRepository, experiment_scope
from backend.services.comparison_service import build_suite_report
from backend.services.evidence_analysis_service import build_evidence_analysis
from backend.services.regression_service import build_regression_report
from backend.services.experimentation_service import ExperimentationService, gallery, replay_highlights, read_highlight_evidence
from backend.services.simulation_service import QueueFullError
from backend.repositories import SimulationRepository
from backend.routes.simulations import _record
from shared.models.api import JobInfo, SimulationRecord
from shared.models.runtime import SimulationOptions
from shared.models.evidence import EvidenceFrame
from shared.models.lab import (LabReply, LabReceipt, LabGalleryResult, LabSuitesResult, LabPlansResult,
    LabFeedbacksResult, LabSuiteResult, LabPlanResult, LabOperationResult, LabDiscoveryResult,
    LabSuiteReportResult, LabSuiteRunsResult, LabRunResult, LabHighlightsResult, LabFrameResult, LabFeedbackResult, LabDeletedResult,
    LabAnalysisResult, LabRegressionResult)
from shared.models.remote import ExecuteCommand, RemoteRunOptions, canonical_json, payload_digest, MAX_RELAY_BYTES

from .remote_service import RemoteDenied

MAX_REMOTE_EXECUTIONS = 12
_PUBLIC_ERROR = "The experiment failed on this computer. Review its local diagnostics."
_READS = {"gallery", "suites", "plans", "feedbacks", "suite_runs", "suite_get", "plan_get", "discovery_report", "suite_report", "operation_get", "run_get", "highlights", "frame_get", "analysis_get", "regression_report"}


binding_scope = experiment_scope


def bounded_page(model, values, action, snapshot):
    kept, size = [], 0
    for value in values[:action.limit]:
        length = len(value.model_dump_json().encode("utf-8"))
        if size + length > 128 * 1024:
            break
        kept.append(value)
        size += length
    if values and not kept:
        raise RemoteDenied("result_unavailable")
    return model(items=kept, snapshot_at=snapshot,
        next_offset=action.offset + len(kept) if len(values) > len(kept) else None)


def check_remote_execution(execution, policy, scenario=None):
    options = execution.options
    if (options.allow_map_reload or options.carla_quality is not None or options.no_rendering_mode is not None
            or options.camera or options.lidar or options.max_ticks > min(10000, policy.max_ticks)
            or options.sensor_timeout_s > 10 or options.sensor_settle_s > 2):
        raise RemoteDenied("policy_denied")
    if scenario is not None:
        # Reuse the baseline route, recipe and tick validation of normal remote runs.
        ExecuteCommand.bounded_scenario(scenario)
        if (scenario.duration_s > policy.max_duration_s
                or ceil(scenario.duration_s / scenario.fixed_delta_seconds - 1e-9) > min(options.max_ticks, policy.max_ticks)):
            raise RemoteDenied("policy_denied")


def check_remote_proposal(proposal, policy):
    check_remote_execution(proposal, policy)
    if (proposal.goal.agent_id is not None or proposal.goal.duration_s > policy.max_duration_s
            or proposal.budget.max_executions > MAX_REMOTE_EXECUTIONS
            or proposal.budget.max_search_trials > MAX_REMOTE_EXECUTIONS
            or proposal.budget.max_simulated_time_s > MAX_REMOTE_EXECUTIONS * policy.max_duration_s):
        raise RemoteDenied("policy_denied")


def check_remote_suite(definition, policy):
    check_remote_execution(definition, policy)
    if definition.agents != [None] or definition.trial_budget > MAX_REMOTE_EXECUTIONS:
        raise RemoteDenied("policy_denied")
    for case in definition.cases:
        check_remote_execution(definition, policy, case.scenario)


def public_operation(record):
    outcome = record.outcome
    if record.error is not None and outcome is not None:
        outcome = outcome.model_copy(update={"summary": _PUBLIC_ERROR, "stop_reason": _PUBLIC_ERROR if outcome.stop_reason is not None else None})
    return record.model_copy(update={"error": _PUBLIC_ERROR if record.error is not None else None, "outcome": outcome})


def public_run(record):
    if record.simulation.agent != "baseline" or record.simulation.agent_snapshot is not None or record.job and record.job.agent_snapshot is not None:
        raise RemoteDenied("policy_denied")
    simulation = record.simulation.model_copy(update={"error": _PUBLIC_ERROR if record.simulation.error is not None else None})
    result = record.result.model_copy(update={"simulation": simulation}) if record.result is not None else None
    return record.model_copy(update={"simulation": simulation, "result": result})


def transactional_run(session, stored):
    """Read the acceptance/cancel snapshot in the worker's writer transaction."""
    job = JobRepository(session).get(stored.simulation.id)
    return public_run(SimulationRecord(**{key: getattr(stored, key) for key in
        ("simulation", "result", "scenario_id", "revision", "created_at", "updated_at")},
        job=JobInfo(cancel_requested=job.cancel_requested, queue_position=None, options=job.options,
            evaluation_settings=job.evaluation_settings, agent_snapshot=job.agent_snapshot)))


class RemoteLabService:
    def __init__(self, remote, optimizer_factory):
        self.remote, self.worker = remote, remote.worker
        self.database, self.optimizer_factory = self.worker.persistence.database, optimizer_factory

    def workspace(self, binding):
        return ExperimentationService(self.worker.persistence, owner_scope=binding_scope(binding))

    @staticmethod
    def is_read(command):
        return command.action.kind in _READS

    def receipt(self, binding, command):
        with self.database.session(write=False) as session:
            try:
                receipt = ExperimentRepository(session, "receipt", owner_scope=binding_scope(binding)).get(command.request_id)
            except NotFoundError:
                receipt = None
            legacy = RemoteReceiptRepository(session).lookup(binding, command.request_id)
        if receipt is not None and receipt.digest != payload_digest(command):
            raise RemoteDenied("request_conflict")
        if legacy is not None and command.action.kind != "run_launch":
            raise RemoteDenied("request_conflict")
        return None if receipt is None else receipt.reply

    def _owned_operation(self, workspace, identity):
        with self.database.session(write=False) as session:
            operation = OptimizationRepository(session).get(identity)
        request = operation.request
        if request.kind == "discover":
            plan = workspace.get_document("discovery", request.plan_id)
            if plan.operation_id != operation.id:
                raise RemoteDenied("run_not_found")
        elif request.kind == "suite" and request.suite_id is not None:
            try:
                workspace.get_document("suite", request.suite_id)
            except NotFoundError:
                # Deleting a reusable definition must not erase ownership of
                # the already accepted immutable suite-run evidence.
                with self.database.session(write=False) as session:
                    receipt = workspace.repository(session, "receipt").get(operation.id)
                if receipt.reply.result.kind != "operation" or receipt.reply.result.operation.id != operation.id:
                    raise RemoteDenied("run_not_found")
        else:
            raise RemoteDenied("run_not_found")
        return operation

    def owned_run(self, binding, identity):
        with self.database.session(write=False) as session:
            receipt = RemoteReceiptRepository(session).execution_for_run(binding, identity)
            stored = SimulationRepository(session).get(identity)
            if receipt is not None:
                if receipt.scenario_sha256 != payload_digest(stored.simulation.scenario):
                    raise RemoteDenied("result_unavailable")
                return stored
            trial = session.scalar(select(OptimizationTrialRecord).where(OptimizationTrialRecord.simulation_id == str(identity)))
            if trial is None:
                raise RemoteDenied("run_not_found")
            parent_id, number = trial.operation_id, trial.number
        parent = self._owned_operation(self.workspace(binding), parent_id)
        with self.database.session(write=False) as session:
            journal = OptimizationRepository(session).trials(parent.id)
            job = JobRepository(session).get(identity)
        if (number >= len(journal) or journal[number].simulation_id != identity
                or journal[number].scenario != stored.simulation.scenario or stored.simulation.mode != parent.execution.mode
                or job.options != parent.execution.options or job.evaluation_settings != parent.execution.evaluation_settings):
            raise RemoteDenied("result_unavailable")
        return stored

    def _admission(self, binding, command):
        @contextmanager
        def admit(record, scenario):
            with self.remote.lock:
                policy = self.remote._approved(binding, command)
                if not policy.allow_evidence_read:
                    raise RemoteDenied("permission_denied")
                if record.execution.mode != command.mode:
                    raise RemoteDenied("policy_denied")
                check_remote_execution(record.execution, policy, scenario)
                with self.worker.queue_snapshot():
                    status = self.worker.status()
                    with self.database.session(write=False) as session:
                        waiting = RemoteReceiptRepository(session).count_waiting(binding)
                    if (policy.max_waiting_runs == 0 and (status["active_id"] is not None or status["pending"])
                            or policy.max_waiting_runs > 0 and waiting >= policy.max_waiting_runs):
                        raise QueueFullError("Remote waiting queue is full")
                    yield
        return admit

    def dispatch(self, binding, command, policy):
        if not policy.allow_evidence_read:
            raise RemoteDenied("permission_denied")
        workspace, action = self.workspace(binding), command.action
        original = self.receipt(binding, command) if action.kind not in _READS else None
        if original is not None:
            return original
        saved = []
        def acknowledge(session, result):
            reply = LabReply(request_id=command.request_id, result=result)
            if len(canonical_json(reply).encode()) > MAX_RELAY_BYTES - 4096:
                raise RemoteDenied("result_unavailable")
            ExperimentRepository(session, "receipt", owner_scope=binding_scope(binding)).create(
                LabReceipt(id=command.request_id, digest=payload_digest(command), reply=reply))
            saved.append(reply)
        if action.kind == "gallery":
            result = LabGalleryResult(items=gallery())
        elif action.kind in {"suites", "plans", "feedbacks"}:
            kind, model = {"suites": ("suite", LabSuitesResult), "plans": ("discovery", LabPlansResult), "feedbacks": ("feedback", LabFeedbacksResult)}[action.kind]
            snapshot = action.before or datetime.now(UTC)
            values = workspace.list_documents(kind, limit=action.limit + 1, offset=action.offset, created_before=snapshot)
            result = bounded_page(model, values, action, snapshot)
        elif action.kind == "suite_runs":
            snapshot = action.before or datetime.now(UTC)
            values = workspace.suite_runs(action.id, limit=action.limit + 1, offset=action.offset, created_before=snapshot)
            result = bounded_page(LabSuiteRunsResult, [public_operation(value) for value in values], action, snapshot)
        elif action.kind == "suite_get":
            result = LabSuiteResult(suite=workspace.get_document("suite", action.id))
        elif action.kind in {"suite_save", "suite_update"}:
            check_remote_suite(action.save.definition, policy)
            value = workspace.save_suite(action.save, identity=action.id if action.kind == "suite_update" else None,
                create_id=command.request_id, on_created=lambda session, value: acknowledge(session, LabSuiteResult(suite=value)))
            result = LabSuiteResult(suite=value)
        elif action.kind == "suite_delete":
            workspace.delete_suite(action.id, action.expected_revision,
                on_deleted=lambda session, identity: acknowledge(session, LabDeletedResult(id=identity)))
            result = LabDeletedResult(id=action.id)
        elif action.kind == "plan_propose":
            check_remote_proposal(action.proposal, policy)
            def plan_ack(session, value):
                for scenario in value.scenarios:
                    check_remote_execution(value.proposal, policy, scenario)
                acknowledge(session, LabPlanResult(plan=value))
            value = workspace.propose(action.proposal, identity=command.request_id,
                on_created=plan_ack)
            result = LabPlanResult(plan=value)
        elif action.kind == "plan_get":
            result = LabPlanResult(plan=workspace.get_document("discovery", action.id))
        elif action.kind == "plan_approve":
            plan = workspace.get_document("discovery", action.id)
            check_remote_proposal(plan.proposal, policy)
            if command.mode != plan.proposal.mode:
                raise RemoteDenied("policy_denied")
            value = workspace.approve(action.id, action.approval, self.optimizer_factory(), admission=self._admission(binding, command),
                on_created=lambda session, value: acknowledge(session, LabOperationResult(operation=public_operation(value))))
            result = LabOperationResult(operation=public_operation(value))
        elif action.kind == "suite_run":
            suite = workspace.get_document("suite", action.id)
            check_remote_suite(suite.definition, policy)
            if command.mode != suite.definition.mode:
                raise RemoteDenied("policy_denied")
            value = workspace.launch_suite(action.id, action.launch, self.optimizer_factory(), admission=self._admission(binding, command),
                on_created=lambda session, value: acknowledge(session, LabOperationResult(operation=public_operation(value))))
            result = LabOperationResult(operation=public_operation(value))
        elif action.kind in {"operation_get", "operation_cancel", "operation_resume"}:
            value = self._owned_operation(workspace, action.id)
            if action.kind == "operation_cancel":
                value = self.optimizer_factory().cancel(value.id,
                    on_created=lambda session, value: acknowledge(session, LabOperationResult(operation=public_operation(value))))
            elif action.kind == "operation_resume":
                if value.execution.mode != command.mode:
                    raise RemoteDenied("policy_denied")
                check_remote_execution(value.execution, policy)
                value = self.optimizer_factory().resume(value.id, expected_revision=action.expected_revision,
                    admission=self._admission(binding, command),
                    on_created=lambda session, value: acknowledge(session, LabOperationResult(operation=public_operation(value))))
            result = LabOperationResult(operation=public_operation(value))
        elif action.kind == "discovery_report":
            value = workspace.discovery_report(action.id)
            evidence = value.evidence.model_copy(update={"operation": public_operation(value.evidence.operation)})
            result = LabDiscoveryResult(report=value.model_copy(update={"evidence": evidence,
                "summary": evidence.operation.outcome.summary if evidence.operation.outcome is not None else value.summary}))
        elif action.kind == "suite_report":
            self._owned_operation(workspace, action.id)
            value = build_suite_report(self.worker.persistence, action.id)
            evidence = value.evidence.model_copy(update={"operation": public_operation(value.evidence.operation)})
            result = LabSuiteReportResult(report=value.model_copy(update={"evidence": evidence}))
        elif action.kind == "regression_report":
            operation = self._owned_operation(workspace, action.id)
            if operation.request.kind != "suite" or any(agent.agent_id is not None for agent in operation.comparison_agents):
                raise RemoteDenied("policy_denied")
            result = LabRegressionResult(report=build_regression_report(self.worker.persistence, action.id, action.policy))
        elif action.kind == "run_launch":
            check_remote_execution(action.launch, policy, action.launch.scenario)
            if action.launch.options != SimulationOptions(max_ticks=action.launch.options.max_ticks):
                raise RemoteDenied("policy_denied")
            request = ExecuteCommand(request_id=command.request_id, scenario=action.launch.scenario, mode=command.mode,
                options=RemoteRunOptions(max_ticks=action.launch.options.max_ticks), evaluation_settings=action.launch.evaluation_settings)
            accepted = self.remote._execute(binding, request, policy,
                on_created=lambda session, stored: acknowledge(session, LabRunResult(record=transactional_run(session, stored))))
            result = LabRunResult(record=public_run(_record(self.worker.get(accepted.simulation_id), self.worker)))
        elif action.kind in {"run_get", "run_cancel", "highlights", "frame_get", "analysis_get"}:
            stored = self.owned_run(binding, action.id)
            if action.kind == "analysis_get":
                if stored.simulation.agent != "baseline" or stored.simulation.agent_snapshot is not None:
                    raise RemoteDenied("policy_denied")
                result = LabAnalysisResult(analysis=build_evidence_analysis(self.worker.persistence, action.id))
            elif action.kind == "highlights":
                result = LabHighlightsResult(highlights=replay_highlights(self.worker.persistence, action.id))
            elif action.kind == "frame_get":
                _, frames = read_highlight_evidence(self.worker.persistence, action.id)
                frame = next((value for value in frames if value.frame == action.frame), None)
                if frame is None:
                    raise RemoteDenied("run_not_found")
                velocity = frame.ego.velocity_mps
                result = LabFrameResult(simulation_id=action.id, frame=EvidenceFrame(frame=frame.frame,
                    time_s=frame.time_s, speed_mps=None if velocity is None else hypot(velocity.x, velocity.y, velocity.z),
                    control=frame.control, collisions=None if frame.collisions is None else len(frame.collisions),
                    traffic_violations=None if frame.traffic_violations is None else len(frame.traffic_violations)))
            elif action.kind == "run_cancel":
                def cancel_ack(session, value):
                    acknowledge(session, LabRunResult(record=transactional_run(session, value)))
                value = self.worker.cancel(action.id, on_created=cancel_ack)
                result = LabRunResult(record=public_run(_record(value, self.worker)))
            else:
                result = LabRunResult(record=public_run(_record(stored, self.worker)))
        elif action.kind == "feedback":
            if action.feedback.simulation_id is not None:
                self.owned_run(binding, action.feedback.simulation_id)
            value = workspace.save_feedback(action.feedback, identity=command.request_id,
                on_created=lambda session, value: acknowledge(session, LabFeedbackResult(feedback=value)))
            result = LabFeedbackResult(feedback=value)
        else:
            raise RemoteDenied("invalid_request")
        if saved:
            return saved[0]
        reply = LabReply(request_id=command.request_id, result=result)
        if len(canonical_json(reply).encode()) > MAX_RELAY_BYTES - 4096:
            raise RemoteDenied("result_unavailable")
        if action.kind not in _READS:
            with self.database.session() as session:
                acknowledge(session, result)
            return saved[0]
        return reply
