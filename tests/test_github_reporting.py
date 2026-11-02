"""Commit provenance, selective disclosure and exactly reviewed GitHub writes."""

import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from hashlib import sha1, sha256
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
import pytest

from backend.config import Settings
from backend.main import create_app
from backend.persistence_errors import ConflictError
from backend.routes.github import local_only, service as dependency
from backend.services.github_provider import GitHubClient, GitHubError
from backend.services.github_service import GitHubService, conclusion
from shared.models import Scenario
from shared.models.analysis import RegressionReport
from shared.models.github import CommitBinding, GitHubApproval, GitHubPreviewRequest
from shared.models.optimization import ExecutionSpec, SuiteCase
from shared.models.registry import AgentExternalFileFingerprint, AgentFileFingerprint

REF, CAND = "1" * 40, "2" * 40
REPO = "fixture/driving-models"


def blob_sha(data):
    return sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


class Remote:
    def __init__(self):
        self.blobs, self.trees, self.commits = {}, {}, {}
        self.checks, self.reads, self.writes = [], [], []
        self.lose_reply = False

    def __enter__(self): return self
    def __exit__(self, *args): pass

    def add_blob(self, name, data):
        sha = blob_sha(data)
        self.blobs[sha] = {"sha": sha, "size": len(data), "encoding": "base64", "content": base64.b64encode(data).decode()}
        return {"path": name, "type": "blob", "mode": "100644", "sha": sha, "size": len(data)}

    def add_tree(self, entries):
        raw = json.dumps(entries, sort_keys=True).encode()
        sha = sha1(raw).hexdigest()
        self.trees[sha] = {"sha": sha, "truncated": False, "tree": entries}
        return sha

    def commit(self, repo, sha):
        self.reads.append(("commit", repo, sha)); return deepcopy(self.commits[sha])
    def tree(self, repo, sha):
        self.reads.append(("tree", repo, sha)); return deepcopy(self.trees[sha])
    def blob(self, repo, sha):
        self.reads.append(("blob", repo, sha)); return deepcopy(self.blobs[sha])
    def find_checks(self, repo, payload, app_id):
        self.reads.append(("checks", repo, payload["head_sha"]))
        return deepcopy([item for item in self.checks if item["external_id"] == payload["external_id"] and item["app"]["id"] == app_id])
    def get_check(self, repo, identity):
        return deepcopy(next(item for item in self.checks if item["id"] == identity))
    def create_check(self, repo, payload):
        self.writes.append((repo, deepcopy(payload)))
        check = {**deepcopy(payload), "id": len(self.checks) + 1, "app": {"id": 42}, "html_url": f"https://github.com/{repo}/runs/{len(self.checks) + 1}"}
        self.checks.append(check)
        if self.lose_reply:
            raise GitHubError("Remote outcome unknown; reconcile before doing anything else", uncertain=True)
        return deepcopy(check)


def graph(remote, *, crlf=False, external=None):
    snapshots = []
    for sha, source in ((REF, b"reference source\n"), (CAND, b"candidate source\n")):
        files = {"agent.json": b'{"entry_point":"agent:Driver"}\n', "agent.py": source}
        entries = [remote.add_blob(name, data) for name, data in files.items()]
        package = remote.add_tree(entries)
        root_entries = [{"path": "pkg", "type": "tree", "mode": "040000", "sha": package}]
        models = []
        if external:
            model = b"frozen model bytes"
            model_hash = sha256(model).hexdigest()
            data = (f"version https://git-lfs.github.com/spec/v1\noid sha256:{model_hash}\nsize {len(model)}\n".encode()
                    if external == "lfs" else json.dumps({"sha256": model_hash, "size_bytes": len(model)}).encode())
            root_entries.append(remote.add_blob("model.pin", data))
            models = [AgentExternalFileFingerprint(path=str(Path.cwd() / "private-weights.ckpt"), sha256=model_hash, size_bytes=len(model))]
        root = remote.add_tree(root_entries)
        remote.commits[sha] = {"sha": sha, "tree": {"sha": root}}
        frozen = {name: data.replace(b"\n", b"\r\n") if crlf else data for name, data in files.items()}
        snapshots.append(SimpleNamespace(package_path=str(Path.cwd() / "PRIVATE PACKAGE"), files=[AgentFileFingerprint(path=name, sha256=sha256(data).hexdigest(), size_bytes=len(data))
            for name, data in sorted(frozen.items())], model_files=models))
    return snapshots


@pytest.fixture
def context(tmp_path):
    report = RegressionReport.model_validate(json.loads((Path(__file__).resolve().parents[1] / "frontend/tests/fixtures/review.json").read_text())["regression"])
    report = report.model_copy(deep=True)
    report.controllers[0].name = "PRIVATE REFERENCE NAME"
    report.controllers[1].name = "PRIVATE CANDIDATE NAME"
    for item in report.assessments:
        item.case_name = "PRIVATE CASE NAME"
    operation = SimpleNamespace(request=SimpleNamespace(cases=[SuiteCase(name="PRIVATE SCENARIO", scenario=Scenario.model_validate({
        "name": "PRIVATE LOCATION", "duration_s": .1, "actors": [{"id": "ego", "type": "ego_vehicle"}]}))],
        seeds=[7, 11, 13], repeats=1), execution=ExecutionSpec(mode="test"))
    settings = Settings(_env_file=None, storage_dir=tmp_path / "artifacts", database_url=f"sqlite:///{tmp_path.as_posix()}/runs.db",
        github_enabled=True, github_checks_token=SecretStr("ghs_fixture_never_sent_live"), github_repository=REPO, github_app_id=42)
    remote = Remote()
    snapshots = graph(remote)
    service = GitHubService(settings, lambda: pytest.fail("No simulation worker or live model may be created"), client_factory=lambda key: remote)
    service.report = lambda request: (report, operation, *snapshots)
    request = GitHubPreviewRequest(operation_id=report.operation_id, reference=CommitBinding(commit=REF, package_prefix="pkg"),
        candidate=CommitBinding(commit=CAND, package_prefix="pkg"))
    return SimpleNamespace(settings=settings, remote=remote, snapshots=snapshots, service=service, report=report, operation=operation, request=request)


def approve(record):
    return GitHubApproval(digest=record.preview.digest, approve=True)


def test_preview_verifies_both_commits_without_writing_and_discloses_no_unselected_private_fields(context):
    value = context.service.preview(context.request)
    assert value.state == "preview" and context.remote.writes == []
    assert {row[2] for row in context.remote.reads if row[0] == "commit"} == {REF, CAND}
    assert value.preview.reference.package_files == value.preview.candidate.package_files == 2
    body = json.loads(value.preview.payload)
    assert body["head_sha"] == CAND and body["conclusion"] == "failure"
    assert all(secret not in value.preview.payload for secret in ("PRIVATE", "private-weights", str(context.report.operation_id), context.report.source_digest, "mean_change"))
    assert "configuration" not in body["output"]["summary"].split("```json")[-1] or "```json" not in body["output"]["summary"]


def test_publish_requires_exact_digest_and_is_durable_idempotent_and_reconcilable(context):
    service, remote = context.service, context.remote
    record = service.preview(context.request)
    with pytest.raises(ConflictError):
        service.publish(record.id, GitHubApproval(digest="0" * 64, approve=True))
    with pytest.raises(ValidationError): GitHubApproval(digest=record.preview.digest, approve=1)
    result = service.publish(record.id, approve(record))
    assert result.state == "published" and result.check_run_id == 1
    assert result.check_url == f"https://github.com/{REPO}/runs/1"
    assert len(remote.writes) == 1 and remote.writes[0][1] == json.loads(record.preview.payload)
    assert service.publish(record.id, approve(record)) == result and len(remote.writes) == 1
    assert service.reconcile(record.id).state == "published" and len(remote.writes) == 1
    assert service.store.get(record.id).state == "published"


@pytest.mark.parametrize("field", ["sha", "blob", "extra", "link", "truncated"])
def test_changed_missing_extra_or_ambiguous_git_objects_never_become_publishable(context, field):
    remote = context.remote
    if field == "sha": remote.commits[CAND]["sha"] = REF
    else:
        root = remote.commits[CAND]["tree"]["sha"]
        package = remote.trees[root]["tree"][0]["sha"]
        entry = remote.trees[package]["tree"][0]
        if field == "blob": remote.blobs[entry["sha"]]["content"] = base64.b64encode(b"different").decode()
        if field == "extra": remote.trees[package]["tree"].append(remote.add_blob("extra.py", b"extra code"))
        if field == "link": entry["mode"] = "120000"
        if field == "truncated": remote.trees[package]["truncated"] = True
    with pytest.raises(GitHubError): context.service.preview(context.request)
    assert remote.writes == []


@pytest.mark.parametrize("external", ["lfs", "manifest"])
def test_external_models_require_exact_committed_weight_pins_without_uploading_or_downloading_weights(context, external):
    context.snapshots[:] = graph(context.remote, external=external)
    context.request.reference.model_paths = ["model.pin"]
    context.request.candidate.model_paths = ["model.pin"]
    value = context.service.preview(context.request)
    proof = value.preview.candidate
    assert proof.external_models == 1 and getattr(proof, "lfs_pins" if external == "lfs" else "manifest_pins") == 1
    context.snapshots[1].model_files[0].sha256 = "0" * 64
    context.request.request_id = uuid4()
    with pytest.raises(GitHubError): context.service.preview(context.request)
    assert context.remote.writes == []


def test_windows_checkout_line_endings_are_verified_as_an_explicit_transformation(context):
    context.snapshots[:] = graph(context.remote, crlf=True)
    value = context.service.preview(context.request)
    assert value.preview.candidate.crlf_files == 2 and value.preview.reference.crlf_files == 2


def test_ambiguous_duplicate_model_pin_fields_are_rejected(context):
    from backend.services.github_provenance import CommitVerifier
    context.snapshots[:] = graph(context.remote, external="manifest")
    model = context.snapshots[0].model_files[0]
    raw = ('{"sha256":"' + model.sha256 + '","size_bytes":0,"size_bytes":' + str(model.size_bytes) + '}').encode()
    entry = context.remote.add_blob("ambiguous.pin", raw)
    with pytest.raises(GitHubError):
        CommitVerifier(context.remote, REPO).match(entry, model, external=True)


def test_only_selected_disclosure_categories_are_published_and_configs_paths_names_stay_local(context):
    context.request.disclose = ["metrics", "fingerprints", "run_ids"]
    value = context.service.preview(context.request)
    assert "mean_change" in value.preview.payload and context.report.source_digest in value.preview.payload
    assert str(context.report.operation_id) in value.preview.payload
    assert "PRIVATE" not in value.preview.payload and "PRIVATE LOCATION" not in value.preview.payload
    context.request.request_id = uuid4(); context.request.disclose = ["scenarios"]
    value = context.service.preview(context.request)
    assert "PRIVATE LOCATION" in value.preview.payload and "PRIVATE CANDIDATE NAME" not in value.preview.payload
    assert "configuration_values" in value.preview.payload and "Not disclosed" in value.preview.payload


@pytest.mark.parametrize("execution,state,decision,expected", [
    ("completed", "regression_observed", "regression_observed", "failure"),
    ("completed", "no_regression_observed", "no_regression_observed", "success"),
    ("completed", "inconclusive", "inconclusive", "action_required"),
    ("failed", "inconclusive", "inconclusive", "failure"),
    ("cancelled", "inconclusive", "inconclusive", "cancelled"),
    ("interrupted", "inconclusive", "inconclusive", "action_required"),
])
def test_ci_mapping_never_turns_unknown_or_failed_validation_green(context, execution, state, decision, expected):
    report = context.report
    report.execution_status, report.state = execution, state
    for item in report.assessments:
        item.decision = decision
        if decision == "no_regression_observed":
            item.observed_pairs = item.planned_pairs
    assert conclusion(report, 1)[0] == expected


def test_pending_and_insufficient_pair_evidence_cannot_publish_a_passing_check(context):
    context.report.state = "pending"; context.report.execution_status = "running"
    with pytest.raises(ConflictError): context.service.preview(context.request)
    assert context.remote.writes == []
    context.report.state = "no_regression_observed"; context.report.execution_status = "completed"
    for item in context.report.assessments:
        item.decision = "no_regression_observed"; item.observed_pairs = 0
    assert conclusion(context.report, 1)[0] == "action_required"


def test_report_changes_after_preview_and_expiry_prevent_publication(context):
    record = context.service.preview(context.request)
    context.report.source_digest = "0" * 64
    assert context.service.publish(record.id, approve(record)).state == "failed"
    assert context.remote.writes == []
    context.request.request_id = uuid4()
    fresh = context.service.preview(context.request)
    context.service.now = lambda: fresh.preview.expires_at + timedelta(seconds=1)
    with pytest.raises(ConflictError): context.service.publish(fresh.id, approve(fresh))
    assert context.remote.writes == []


def test_lost_post_response_stays_uncertain_and_reconciliation_never_reposts(context):
    record = context.service.preview(context.request)
    context.remote.lose_reply = True
    value = context.service.publish(record.id, approve(record))
    assert value.state == "uncertain" and len(context.remote.writes) == 1
    assert context.service.publish(record.id, approve(record)).state == "uncertain"
    assert context.service.reconcile(record.id).state == "published" and len(context.remote.writes) == 1


def test_new_journal_or_ci_rerun_recovers_the_same_remote_external_identity(context, tmp_path):
    record = context.service.preview(context.request)
    assert context.service.publish(record.id, approve(record)).state == "published"
    from backend.services.github_store import GitHubStore
    context.service.store = GitHubStore(tmp_path / "reopened-journal")
    context.request.request_id = uuid4()
    again = context.service.preview(context.request)
    assert json.loads(again.preview.payload)["external_id"] == json.loads(record.preview.payload)["external_id"]
    assert context.service.publish(again.id, approve(again)).state == "published"
    assert len(context.remote.writes) == 1


def test_concurrent_approval_and_duplicate_previews_cannot_create_two_checks(context):
    record = context.service.preview(context.request)
    entered, release = Event(), Event()
    original = context.remote.create_check
    def held(repo, body):
        entered.set(); assert release.wait(15); return original(repo, body)
    context.remote.create_check = held
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(context.service.publish, record.id, approve(record))
        try:
            assert entered.wait(10)
            assert context.service.publish(record.id, approve(record)).state == "publishing"
        finally: release.set()
        assert pending.result(15).state == "published"
    context.request.request_id = uuid4()
    again = context.service.preview(context.request)
    with pytest.raises(ConflictError): context.service.publish(again.id, approve(again))
    assert len(context.remote.writes) == 1


def test_remote_edits_or_wrong_app_never_get_adopted_or_overwritten(context):
    record = context.service.preview(context.request)
    assert context.service.publish(record.id, approve(record)).state == "published"
    context.remote.checks[0]["conclusion"] = "success"
    assert context.service.reconcile(record.id).state == "uncertain"
    assert len(context.remote.writes) == 1


def test_status_disabled_and_nonlocal_requests_have_no_storage_or_provider_side_effects(tmp_path):
    settings = Settings(_env_file=None, storage_dir=tmp_path / "artifacts", database_url=f"sqlite:///{tmp_path.as_posix()}/runs.db", github_enabled=False)
    app = create_app(settings)
    with TestClient(app, client=("127.0.0.1", 1)) as client:
        assert client.get("/github/status").json()["enabled"] is False
        body = {"operation_id": str(uuid4()), "reference": {"commit": REF}, "candidate": {"commit": CAND}}
        assert client.post("/github/previews", json=body).status_code == 409
        assert app.state.runtime._persistence is app.state.runtime._worker is None
    assert not settings.storage_dir.exists()
    with TestClient(create_app(settings), client=("203.0.113.1", 1)) as client:
        assert client.get("/github/status", headers={"Host": "localhost"}).status_code == 403
        assert client.get("/github/publications").status_code == 403
    assert not settings.storage_dir.exists()


def test_actual_routes_preserve_approval_headers_and_safe_ownership(context):
    app = create_app(context.settings)
    app.dependency_overrides[dependency] = lambda: context.service
    with TestClient(app, client=("127.0.0.1", 1)) as client:
        response = client.post("/github/previews", json=context.request.model_dump(mode="json"))
        assert response.status_code == 201 and response.headers["cache-control"] == "no-store"
        record = response.json()
        path = f"/github/publications/{record['id']}"
        assert client.post(path + "/publish", json={"approve": False, "digest": record["preview"]["digest"]}).status_code == 422
        assert context.remote.writes == []
        assert client.post(path + "/publish", json={"approve": True, "digest": record["preview"]["digest"]}).json()["state"] == "published"
        assert client.get(path).json()["state"] == "published" and len(client.get("/github/publications").json()) == 1
        assert client.app.state.runtime._worker is None


def test_binding_paths_revisions_links_and_relay_actions_are_allowlisted():
    from shared.models.lab import LabCommand
    for path in ("../escape", "/absolute", "a//b", "a/./b", "a\\b", "C:/secret"):
        with pytest.raises(ValidationError): CommitBinding(commit=REF, package_prefix=path)
    with pytest.raises(ValidationError): CommitBinding(commit="main")
    for link in ("http://example.invalid", "https://user:secret@example.invalid", "https://example.invalid?token=private"):
        with pytest.raises(ValidationError): GitHubPreviewRequest(operation_id=uuid4(), reference={"commit": REF}, candidate={"commit": CAND}, details_url=link)
    with pytest.raises(ValidationError): LabCommand.model_validate({"request_id": str(uuid4()), "action": {"kind": "github_publish"}})


def test_policy_changes_get_distinct_ci_context_names(context):
    first = context.service.preview(context.request)
    context.request.request_id = uuid4()
    context.request.policy.minimum_pairs = 4
    context.report.policy.minimum_pairs = 4
    second = context.service.preview(context.request)
    assert json.loads(first.preview.payload)["name"] != json.loads(second.preview.payload)["name"]
    assert first.preview.policy.minimum_pairs == 3 and second.preview.policy.minimum_pairs == 4


def test_daily_limits_config_revocation_and_saved_context_integrity_are_enforced(context):
    record = context.service.preview(context.request)
    context.settings.github_app_id = 43
    with pytest.raises(ConflictError): context.service.publish(record.id, approve(record))
    context.settings.github_app_id = 42
    with context.service.store.connection() as connection:
        connection.execute("UPDATE github_publications SET context='{}' WHERE id=?", (str(record.id),))
    assert context.service.publish(record.id, approve(record)).state == "failed" and context.remote.writes == []
    context.settings.github_daily_publish_limit = 1
    context.request.request_id = uuid4()
    fresh = context.service.preview(context.request)
    with pytest.raises(ConflictError): context.service.publish(fresh.id, approve(fresh))


def test_actual_report_and_input_readers_use_only_frozen_repository_records(context, monkeypatch):
    from contextlib import nullcontext
    import backend.services.github_service as module
    context.operation.comparison_agents = [SimpleNamespace(snapshot=item) for item in context.snapshots]
    context.operation.request.kind = "suite"
    persistence = SimpleNamespace(database=SimpleNamespace(session=lambda **kwargs: nullcontext(None)))
    context.service._persistence = persistence
    calls = []
    monkeypatch.setattr(module, "build_regression_report", lambda p, identity, policy: calls.append((p, identity, policy)) or context.report)
    monkeypatch.setattr(module, "OptimizationRepository", lambda session: SimpleNamespace(get=lambda identity: context.operation))
    report, operation, reference, candidate = GitHubService.report(context.service, context.request)
    assert reference is context.snapshots[0] and candidate is context.snapshots[1]
    assert calls[0][1:] == (context.request.operation_id, context.request.policy)
    inputs = context.service.inputs(context.request.operation_id)
    assert len(inputs.controllers) == 2 and inputs.controllers[0].package_files == 2
    assert context.remote.reads == context.remote.writes == []


@pytest.mark.parametrize("status", [301, 401, 403, 404, 429, 500])
def test_http_adapter_never_redirects_retries_or_echoes_remote_error_bodies(monkeypatch, status):
    import httpx
    from backend.services import github_provider as module
    seen = []
    original = httpx.Client
    def handler(request):
        seen.append(request)
        assert str(request.url) == f"{module.ORIGIN}/repos/{REPO}/check-runs"
        assert request.headers["X-GitHub-Api-Version"] == module.API_VERSION
        return httpx.Response(status, headers={"Location": "https://other.invalid"}, stream=httpx.ByteStream(b"ghs_secret_sentinel private response"))
    def client(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return original(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(module.httpx, "Client", client)
    with GitHubClient("fixture-key") as remote:
        with pytest.raises(GitHubError) as error:
            remote.create_check(REPO, {})
    assert error.value.uncertain is (status >= 500)
    assert len(seen) == 1 and "ghs_secret" not in str(error.value)


def test_bounded_remote_read_limits_and_truncated_responses_never_mutate(monkeypatch):
    import httpx
    from backend.services import github_provider as module
    original = httpx.Client
    monkeypatch.setattr(module.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, stream=httpx.ByteStream(b"x" * 500))), **kwargs))
    monkeypatch.setattr(module, "MAX_RESPONSE_BYTES", 64)
    with GitHubClient("fixture-key") as remote:
        with pytest.raises(GitHubError): remote.commit(REPO, REF)
        remote.remaining = 0
        with pytest.raises(GitHubError): remote.commit(REPO, REF)


def test_cli_preview_and_status_never_publish_and_publish_needs_a_separate_flag(monkeypatch, capsys):
    from scripts import publish_github_check as cli
    called = []
    monkeypatch.setattr(cli.GitHubService, "status", staticmethod(lambda settings: SimpleNamespace(model_dump_json=lambda **kwargs: '{"enabled":false}')))
    assert cli.main(["status"]) == 0
    assert "enabled" in capsys.readouterr().out
    with pytest.raises(SystemExit): cli.main(["publish", str(uuid4()), "--approve-digest", "a" * 64])
