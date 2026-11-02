"""Explicitly approved, artifact-verified regression checks on exact commits."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import re
from urllib.parse import urlsplit

from backend.optimization_repository import OptimizationRepository
from backend.persistence_errors import ConflictError
from backend.services.github_provider import GitHubClient, GitHubError
from backend.services.github_provenance import CommitVerifier
from backend.services.github_store import GitHubStore
from backend.services.regression_service import build_regression_report
from shared.models.github import (GitHubControllerInputs, GitHubInputs, GitHubModelInput, GitHubPreview,
    GitHubPreviewRequest, GitHubPublication, GitHubStatus)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def fingerprint(value):
    return sha256(canonical(value).encode()).hexdigest()


def conclusion(report, candidate):
    if report.execution_status in {"queued", "running"} or report.state == "pending":
        raise ConflictError("Wait for a finalized suite before publishing a CI conclusion")
    if report.execution_status == "cancelled":
        return "cancelled", "Suite cancelled"
    if report.execution_status == "failed":
        return "failure", "Suite execution failed"
    if report.execution_status != "completed":
        return "action_required", "Suite execution needs review"
    selected = [item for item in report.assessments if item.compared_agent_index == candidate]
    if any(item.decision == "regression_observed" for item in selected):
        return "failure", "Measured regression observed"
    if selected and all(item.decision == "no_regression_observed" and item.observed_pairs == item.planned_pairs
                        and item.observed_pairs >= report.policy.minimum_pairs for item in selected):
        return "success", "No selected regression observed"
    return "action_required", "Evidence is inconclusive"


class GitHubService:
    def __init__(self, settings, persistence, *, client_factory=GitHubClient, now=None):
        self.settings, self._persistence, self.client_factory = settings, persistence, client_factory
        self.store = GitHubStore(settings.storage_dir)
        self.now = now or (lambda: datetime.now(UTC))

    @property
    def persistence(self):
        if callable(self._persistence):
            self._persistence = self._persistence()
        return self._persistence

    @staticmethod
    def status(settings):
        return GitHubStatus(enabled=settings.github_enabled,
            configured=bool(settings.github_checks_token and settings.github_checks_token.get_secret_value().strip()
                            and settings.github_repository and settings.github_app_id),
            repository=settings.github_repository, app_id=settings.github_app_id,
            check_name=settings.github_check_name, daily_publish_limit=settings.github_daily_publish_limit)

    def require_provider(self, preview=None):
        status = self.status(self.settings)
        if not status.enabled or not status.configured:
            raise ConflictError("GitHub reporting is disabled or unconfigured. Configure an explicit App token, App ID and repository first.")
        if preview is not None and (preview.repository != self.settings.github_repository or preview.app_id != self.settings.github_app_id):
            raise ConflictError("The GitHub repository or App identity changed; review a fresh preview")
        if preview is not None and json.loads(preview.payload).get("name") != self.check_name(preview.policy, preview.candidate_index):
            raise ConflictError("The configured GitHub check context changed; review a fresh preview")

    def check_name(self, policy, candidate):
        return f"{self.settings.github_check_name} / {fingerprint(policy.model_dump(mode='json'))[:12]} / c{candidate}"

    def report(self, request):
        report = build_regression_report(self.persistence, request.operation_id, request.policy)
        if request.candidate_index >= len(report.controllers):
            raise ValueError("Choose a recorded candidate controller, after the reference")
        conclusion(report, request.candidate_index)
        with self.persistence.database.session(write=False) as session:
            operation = OptimizationRepository(session).get(request.operation_id)
        reference = operation.comparison_agents[0].snapshot
        candidate = operation.comparison_agents[request.candidate_index].snapshot
        if reference is None or candidate is None:
            raise ConflictError("GitHub commit binding requires two frozen registered controller artifacts")
        return report, operation, reference, candidate

    def inputs(self, operation_id):
        self.require_provider()
        with self.persistence.database.session(write=False) as session:
            operation = OptimizationRepository(session).get(operation_id)
        if operation.request.kind != "suite":
            raise ConflictError("GitHub reporting requires a frozen suite run")
        return GitHubInputs(operation_id=operation_id, controllers=[GitHubControllerInputs(
            agent_index=index, supported=item.snapshot is not None,
            package_path=None if item.snapshot is None else item.snapshot.package_path,
            package_files=0 if item.snapshot is None else len(item.snapshot.files),
            models=[] if item.snapshot is None else [GitHubModelInput(index=i, sha256=model.sha256, size_bytes=model.size_bytes)
                for i, model in enumerate(item.snapshot.model_files)]) for index, item in enumerate(operation.comparison_agents)])

    def payload(self, request, report, operation, reference, candidate):
        result, title = conclusion(report, request.candidate_index)
        summary = [title + ".", f"Reference commit: `{reference.commit}`. Candidate commit: `{candidate.commit}`.",
            "Scope: selected candidate and selected measurement policy only. Missing or unselected evidence is not cleared; this is not a safety or causal claim.",
            "Commit verification covers package inputs and declared external-model pins. Runtime, environment and effective configuration remain identified by the frozen local suite, not certified as Git contents.",
            "Full reproduction requires the retained frozen suite journal. No raw traces, recordings, credentials, configuration values or local filesystem paths are uploaded."]
        selected = [item for item in report.assessments if item.compared_agent_index == request.candidate_index]
        disclosure = {}
        if "metrics" in request.disclose:
            disclosure["policy"] = report.policy.model_dump(mode="json")
            disclosure["execution_status"] = report.execution_status
            disclosure["assessments"] = [item.model_dump(mode="json", exclude={"case_name", "explanation", "compared_agent_index"}) for item in selected]
        if "fingerprints" in request.disclose:
            disclosure["source_digest"] = report.source_digest
            disclosure["plan_digest"] = report.plan_digest
            disclosure["controllers"] = [item.model_dump(mode="json", exclude={"name", "version", "registration_id"})
                                          for item in (report.controllers[0], report.controllers[request.candidate_index])]
            disclosure["commit_proofs"] = [reference.model_dump(mode="json"), candidate.model_dump(mode="json")]
        if "run_ids" in request.disclose:
            disclosure["suite_run_id"] = str(report.operation_id)
            disclosure["pairs"] = [item.model_dump(mode="json", exclude={"reason", "reference_values", "compared_values", "differences"})
                                    for item in report.pairs if item.compared_agent_index == request.candidate_index]
        if "scenarios" in request.disclose:
            disclosure["reproduction_index"] = {"cases": [item.model_dump(mode="json") for item in operation.request.cases],
                "seeds": operation.request.seeds, "repeats": operation.request.repeats,
                "mode": operation.execution.mode, "options": operation.execution.options.model_dump(mode="json"),
                "evaluation_settings": operation.execution.evaluation_settings.model_dump(mode="json"),
                "configuration_values": "Not disclosed; use the retained frozen suite journal", "reference_commit": reference.commit, "candidate_commit": candidate.commit}
        if disclosure:
            summary.append("```json\n" + json.dumps(disclosure, indent=2, ensure_ascii=False, allow_nan=False) + "\n```")
        body = {"name": self.check_name(report.policy, request.candidate_index), "head_sha": candidate.commit, "status": "completed", "conclusion": result,
                "output": {"title": title, "summary": "\n\n".join(summary)}}
        if request.details_url is not None:
            body["details_url"] = request.details_url
        # Deterministic across journal restores/CI reruns. Private source identity
        # contributes only an opaque hash, never the undisclosed values.
        body["external_id"] = "poltergeist:" + fingerprint({"repository": self.settings.github_repository,
            "app_id": self.settings.github_app_id, "operation_id": str(report.operation_id),
            "source_digest": report.source_digest, "candidate": request.candidate_index, "body": body})
        encoded = canonical(body)
        if len(encoded.encode()) > 60000:
            raise ValueError("Selected GitHub disclosure exceeds 60,000 bytes; choose fewer categories")
        if self.settings.github_checks_token.get_secret_value() in encoded or re.search(r"\b(?:gh[pousr]_|github_pat_|sk-|hf_)[A-Za-z0-9_-]{12,}", encoded):
            raise ValueError("Potential credential text was found in selected evidence. Remove that disclosure or redact the source before publishing")
        return body

    def preview(self, request):
        self.require_provider()
        report, operation, reference_snapshot, candidate_snapshot = self.report(request)
        with self.client_factory(self.settings.github_checks_token.get_secret_value()) as remote:
            verifier = CommitVerifier(remote, self.settings.github_repository)
            reference = verifier.verify(request.reference, reference_snapshot)
            candidate = verifier.verify(request.candidate, candidate_snapshot)
        body = self.payload(request, report, operation, reference, candidate)
        context = canonical({"request": request.model_dump(mode="json"), "report": report.model_dump(mode="json")})
        if len(context.encode()) > 2 * 1024**2:
            raise ValueError("The local publication context exceeds its storage limit")
        now = self.now()
        preview = GitHubPreview(id=request.request_id, operation_id=request.operation_id, repository=self.settings.github_repository, app_id=self.settings.github_app_id,
            candidate_index=request.candidate_index, policy=report.policy.model_copy(deep=True), execution_status=report.execution_status,
            conclusion=body["conclusion"], digest="0" * 64,
            source_digest=report.source_digest, context_digest=sha256(context.encode()).hexdigest(),
            created_at=now, expires_at=now + timedelta(hours=1), reference=reference, candidate=candidate,
            disclose=request.disclose, payload=canonical(body))
        preview.digest = fingerprint(preview.model_dump(mode="json", exclude={"digest"}))
        return self.store.create(GitHubPublication(id=preview.id, preview=preview, state="preview",
            message="Commit/package/model pins verified. Review the exact outgoing check JSON; nothing has been published."), context)

    @staticmethod
    def validated_check(check, preview):
        payload = json.loads(preview.payload)
        valid = (type(check.get("id")) is int and check["id"] > 0 and isinstance(check.get("app"), dict)
            and type(check["app"].get("id")) is int
            and check["app"].get("id") == preview.app_id
            and all(check.get(key) == payload[key] for key in ("name", "head_sha", "external_id", "status", "conclusion"))
            and isinstance(check.get("output"), dict)
            and all(check["output"].get(key) == value for key, value in payload["output"].items())
            and ("details_url" not in payload or check.get("details_url") == payload["details_url"]))
        try:
            url = urlsplit(check.get("html_url", ""))
            valid = (valid and url.scheme == "https" and url.hostname == "github.com" and url.port in {None, 443}
                and not url.username and not url.password and not url.query and not url.fragment
                and url.path.lower().startswith("/" + preview.repository.lower() + "/"))
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise GitHubError("The remote check differs from the approved App, commit, identity or output. Inspect GitHub; no duplicate or overwrite was attempted.", uncertain=True)
        return check["id"], check["html_url"]

    def publish(self, identity, approval):
        self.require_provider()
        record = self.store.get(identity)
        self.require_provider(record.preview)
        body = json.loads(record.preview.payload)
        key = body["external_id"]
        record, raw_context, send = self.store.reserve(identity, approval.digest, now=self.now(), key=key,
            limit=self.settings.github_daily_publish_limit)
        if not send:
            return record
        mutating = False
        try:
            if (fingerprint(record.preview.model_dump(mode="json", exclude={"digest"})) != approval.digest
                    or sha256(raw_context.encode()).hexdigest() != record.preview.context_digest):
                raise GitHubError("The saved publication context failed its integrity check. Nothing was published.")
            context = json.loads(raw_context)
            request = GitHubPreviewRequest.model_validate(context["request"])
            report, *_ = self.report(request)
            if report.model_dump(mode="json") != context["report"]:
                raise GitHubError("Regression evidence changed after preview. Review a fresh report and disclosure.")
            with self.client_factory(self.settings.github_checks_token.get_secret_value()) as remote:
                verifier = CommitVerifier(remote, record.preview.repository)
                for proof in (record.preview.reference, record.preview.candidate):
                    if verifier.commit_tree(proof.commit) != proof.tree:
                        raise GitHubError("The pinned GitHub commit/tree identity changed")
                found = remote.find_checks(record.preview.repository, body, record.preview.app_id)
                if len(found) > 1:
                    raise GitHubError("Multiple matching remote checks need manual review; no duplicate was created", uncertain=True)
                if found:
                    check = found[0]
                else:
                    mutating = True
                    check = remote.create_check(record.preview.repository, body)
                check_id, url = self.validated_check(check, record.preview)
            return self.store.finish(record.model_copy(update={"state": "published", "check_run_id": check_id,
                "check_url": url, "message": "The exact approved check is published on the verified candidate commit. No simulation or repository code was changed."}))
        except GitHubError as error:
            state, message = ("uncertain" if error.uncertain else "failed"), str(error)
        except Exception:
            state = "uncertain" if mutating else "failed"
            message = "Publication could not be confirmed. Inspect saved history and reconcile; this write will not be repeated automatically."
        return self.store.finish(record.model_copy(update={"state": state, "message": message}))

    def reconcile(self, identity):
        self.require_provider()
        record = self.store.get(identity)
        self.require_provider(record.preview)
        if record.state == "preview":
            raise ConflictError("This preview has not been approved for publication")
        try:
            with self.client_factory(self.settings.github_checks_token.get_secret_value()) as remote:
                found = ([remote.get_check(record.preview.repository, record.check_run_id)] if record.check_run_id else
                    remote.find_checks(record.preview.repository, json.loads(record.preview.payload), record.preview.app_id))
                if len(found) != 1:
                    raise GitHubError("A unique matching check was not confirmed within the bounded read. No write was made.", uncertain=True)
                identity, url = self.validated_check(found[0], record.preview)
            return self.store.finish(record.model_copy(update={"state": "published", "check_run_id": identity,
                "check_url": url, "message": "The exact approved check was confirmed by a read-only GitHub reconciliation."}))
        except GitHubError as error:
            return self.store.finish(record.model_copy(update={"state": "uncertain", "message": str(error)}))
