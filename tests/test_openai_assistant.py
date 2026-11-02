"""External disclosure, spending and execution remain explicitly bounded."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
from threading import Event
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest

from backend.main import create_app
from backend.routes.assistant import service as dependency, local_only
from backend.services.assistant_service import AssistantService
from backend.services.openai_provider import ProviderError
from shared.models.assistant import AssistantApproval, AssistantRequest
from test_experimentation import settings, client_for as experiment_client, proposal
from test_evidence_analyst import launch


def client_for(settings):
    client = experiment_client(settings)
    # TestClient's synthetic host is not a network IP. Actual client-address
    # admission is exercised without this override in the explicit scope test.
    client.app.dependency_overrides[local_only] = lambda: None
    return client


def reply(name="draft_discovery", arguments=None, **changes):
    return {"status": "completed", "output": [{"type": "function_call", "name": name,
        "arguments": json.dumps(arguments or {"template": "sudden_braking", "failure_kind": "collision"})}],
        "usage": {"input_tokens": 40, "output_tokens": 10}, **changes}


def configured(settings, **changes):
    return settings.model_copy(update={"openai_enabled": True, "openai_api_key": SecretStr("sk-fixture-key-never-network"),
        "openai_model": "fixture-model", **changes})


def request(**changes):
    return {"request_id": str(uuid4()), "kind": "plan", "prompt": "Find following collisions", "proposal": proposal(), **changes}


def review(client, **changes):
    response = client.post("/assistant/previews", json=request(**changes))
    assert response.status_code == 201, response.text
    return response.json()


def send(client, record, **changes):
    return client.post(f"/assistant/requests/{record['id']}/send", json={"digest": record["preview"]["digest"], "approve": True, **changes})


def install(client, settings, provider):
    service = AssistantService(configured(settings), client.app.state.runtime.persistence, provider=provider)
    client.app.dependency_overrides[dependency] = lambda: service
    return service


def test_status_and_disabled_default_do_not_call_provider_or_create_storage(settings):
    app = create_app(settings)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        value = client.get("/assistant/status")
        assert value.status_code == 200 and value.headers["cache-control"] == "no-store"
        assert value.json()["enabled"] is False and value.json()["scope"] == "local_api_only"
        assert app.state.runtime._persistence is app.state.runtime._worker is None
        assert not settings.storage_dir.exists()
        denied = client.post("/assistant/previews", json=request())
        assert denied.status_code == 409
        assert app.state.runtime._persistence is None and not settings.storage_dir.exists()
        assert not (settings.storage_dir / "openai-assistant.sqlite3").exists()


def test_preview_redacts_and_sends_nothing_then_saves_a_reviewable_unapproved_plan(settings):
    calls = []
    def provider(payload, key):
        calls.append(json.loads(payload))
        assert key == "sk-fixture-key-never-network"
        return reply()
    with client_for(settings) as client:
        service = install(client, settings, provider)
        before = client.app.state.runtime._worker
        record = review(client, prompt="Find contacts for person@example.com sk-veryprivatesecret123 C:\\private\\weights.pth https://private.invalid/evidence")
        assert calls == [] and record["state"] == "preview"
        assert client.app.state.runtime._persistence is None
        payload = record["preview"]["payload"]
        assert all(secret not in payload for secret in ("person@example.com", "sk-veryprivatesecret123", "private.invalid", "weights.pth"))
        assert record["preview"]["redactions_applied"] is True
        assert "agent_id" not in json.loads(payload)["input"]
        assert send(client, record, approve=1).status_code == 422
        assert send(client, record, approve=False).status_code == 422
        assert send(client, record, digest="0" * 64).status_code == 409 and calls == []
        result = send(client, record)
        assert result.status_code == 200, result.text
        result = result.json()
        assert result["state"] == "completed" and result["plan"]["operation_id"] is None
        assert result["plan"]["proposal"]["budget"] == proposal()["budget"]
        assert client.app.state.runtime._worker is before is None
        assert len(calls) == 1 and calls[0]["store"] is False and calls[0]["parallel_tool_calls"] is False
        assert set(tool["name"] for tool in calls[0]["tools"]) == {"draft_discovery", "decline_request"}
        assert send(client, record).json() == result and len(calls) == 1
        assert service.store.get(record["id"]).plan.id == service.store.get(record["id"]).id


@pytest.mark.parametrize("value", [
    reply("run_shell", {"command": "delete everything"}),
    reply(arguments={"template": "sudden_braking", "failure_kind": "collision", "budget": {"max_executions": 100000}}),
    reply(arguments={"template": "unknown", "failure_kind": "collision"}),
    reply(status="incomplete"),
    reply(output=[{"type": "message", "content": "Everything is safe"}]),
    reply(output=[{"type": "function_call", "name": "draft_discovery", "arguments": '{"template":"intersection","template":"sudden_braking","failure_kind":"collision"}'}]),
    reply(usage={"input_tokens": 10, "output_tokens": 999999}),
    reply(output=[{"type": "function_call", "name": "draft_discovery", "arguments": "{}"}] * 2),
])
def test_provider_cannot_execute_expand_budgets_or_invent_tool_sequences(settings, value):
    with client_for(settings) as client:
        install(client, settings, lambda *args: value)
        record = review(client)
        result = send(client, record).json()
        assert result["state"] == "failed" and result["plan"] is result["analysis"] is None
        assert client.app.state.runtime._worker is None
        assert client.get("/experimentation/discovery/plans").json() == []


def test_provider_can_decline_unsupported_work_without_creating_plans(settings):
    with client_for(settings) as client:
        install(client, settings, lambda *args: reply("decline_request", {"reason": "unsupported_action"}))
        result = send(client, review(client, prompt="Install and run arbitrary code")).json()
        assert result["state"] == "declined" and "unsupported action" in result["message"]
        assert client.app.state.runtime._worker is None


def test_selected_evidence_is_checked_and_coverage_and_exact_frame_zero_survive(settings):
    with client_for(settings) as client:
        identity = launch(client)["simulation"]["id"]
        evidence = client.get(f"/experimentation/analysis/{identity}").json()
        selected = next(i for i, item in enumerate(evidence["observations"]) if item["kind"] == "contact")
        calls = []
        def provider(payload, key):
            calls.append(json.loads(json.loads(payload)["input"]))
            return reply("report_evidence", {"observation_ids": [selected]})
        install(client, settings, provider)
        record = review(client, kind="analysis", proposal=None, simulation_id=identity,
            observation_ids=[selected], prompt="What contacts were recorded?")
        result = send(client, record).json()
        assert result["state"] == "completed", result
        checked = result["analysis"]
        assert checked["observations"] == [evidence["observations"][selected]]
        assert checked["observations"][0]["references"][0]["frame"] == 0
        assert checked["coverage"] == evidence["coverage"] and checked["limitations"] == evidence["limitations"]
        assert checked["source_digest"] == evidence["source_digest"] and checked["mode"] == "test"
        assert len(calls[0]["observations"]) == 1 and "simulation_id" not in calls[0]
        assert all(item["statement"] not in str(calls[0]) for i, item in enumerate(evidence["observations"]) if i != selected)
        assert len(client.get("/simulations").json()) == 1


def test_analysis_cannot_cite_unselected_evidence_or_add_a_factual_claim(settings):
    with client_for(settings) as client:
        identity = launch(client)["simulation"]["id"]
        install(client, settings, lambda *args: reply("report_evidence", {"observation_ids": [1], "statement": "The vehicle is safe"}))
        record = review(client, kind="analysis", proposal=None, simulation_id=identity, observation_ids=[0])
        assert send(client, record).json()["state"] == "failed"
        install(client, settings, lambda *args: reply("report_evidence", {"observation_ids": [1]}))
        record = review(client, kind="analysis", proposal=None, simulation_id=identity, observation_ids=[0])
        assert send(client, record).json()["state"] == "failed"


def test_changed_evidence_never_reaches_provider(settings):
    with client_for(settings) as client:
        run = launch(client)
        identity = run["simulation"]["id"]
        calls = []
        install(client, settings, lambda *args: calls.append(args))
        record = review(client, kind="analysis", proposal=None, simulation_id=identity, observation_ids=[0])
        trace = next(item for item in run["result"]["artifacts"] if item["kind"] == "telemetry")
        (settings.storage_dir / trace["path"]).write_bytes(b"corrupted")
        assert send(client, record).json()["state"] == "failed" and calls == []


def test_budget_reservations_survive_uncertain_calls_and_reopening(settings):
    with client_for(settings) as client:
        calls = []
        def unavailable(*args):
            calls.append(1)
            raise ProviderError("Provider outcome unknown; this request will not be resent")
        service = install(client, settings, unavailable)
        service.settings = configured(settings, openai_daily_request_limit=1)
        record = review(client)
        assert send(client, record).json()["state"] == "failed"
        reopened = AssistantService(service.settings, service.persistence, provider=lambda *args: pytest.fail("Repeated provider call"))
        assert reopened.run(record["id"], AssistantApproval(digest=record["preview"]["digest"], approve=True)).state == "failed"
        assert len(calls) == 1
        assert send(client, review(client)).status_code == 409


def test_output_cap_and_preview_byte_cap_expiry_and_model_change_are_enforced(settings):
    with client_for(settings) as client:
        calls = []
        service = install(client, settings, lambda *args: calls.append(1) or reply())
        bad = client.post("/assistant/previews", json=request(budget={"max_input_bytes": 1024, "max_output_tokens": 128}))
        assert bad.status_code == 422 and calls == []
        record = review(client)
        service.settings = configured(settings, openai_daily_output_token_limit=128)
        assert send(client, record).status_code == 409 and calls == []
        service.settings = configured(settings, openai_model="different-model")
        assert send(client, record).status_code == 409 and calls == []
        service.settings = configured(settings)
        service.now = lambda: service.store.get(record["id"]).preview.expires_at + timedelta(seconds=1)
        assert send(client, record).status_code == 409 and calls == []


def test_concurrent_approval_sends_once_and_running_receipt_is_not_resubmitted(settings):
    entered, release = Event(), Event()
    with client_for(settings) as client:
        calls = []
        def held(*args):
            calls.append(1); entered.set()
            assert release.wait(15)
            return reply()
        service = install(client, settings, held)
        record = review(client)
        approval = AssistantApproval(digest=record["preview"]["digest"], approve=True)
        with ThreadPoolExecutor(max_workers=1) as worker:
            first = worker.submit(service.run, record["id"], approval)
            try:
                assert entered.wait(10)
                assert service.run(record["id"], approval).state == "running"
                assert len(calls) == 1
            finally:
                release.set()
            assert first.result(15).state == "completed"


def test_provider_errors_never_leak_raw_response_or_key(settings):
    with client_for(settings) as client:
        def failed(*args):
            raise RuntimeError("sk-private-sentinel signed-url=https://private.invalid")
        install(client, settings, failed)
        result = send(client, review(client)).json()
        assert result["state"] == "failed"
        assert "sk-private" not in str(result) and "private.invalid" not in str(result)


def test_a_plan_question_that_cannot_fit_the_discovery_contract_is_rejected_before_spending(settings):
    with client_for(settings) as client:
        calls = []
        install(client, settings, lambda *args: calls.append(1))
        result = client.post("/assistant/previews", json=request(prompt="x" * 501))
        assert result.status_code == 422 and calls == []


def test_account_device_commands_cannot_forward_external_disclosure():
    from pydantic import ValidationError
    from shared.models.lab import LabCommand
    for kind in ("assistant_preview", "assistant_send", "openai"):
        with pytest.raises(ValidationError):
            LabCommand.model_validate({"request_id": str(uuid4()), "action": {"kind": kind}})


@pytest.mark.parametrize("address", ["203.0.113.9", "192.168.1.2"])
def test_nonlocal_clients_cannot_read_or_send_assistant_data_even_with_a_localhost_header(settings, address):
    app = create_app(configured(settings))
    with TestClient(app, client=(address, 50000)) as client:
        assert client.get("/assistant/status", headers={"Host": "localhost"}).status_code == 403
        assert client.get("/assistant/requests").status_code == 403
        assert client.post("/assistant/previews", json=request()).status_code == 403
        assert app.state.runtime._persistence is app.state.runtime._worker is None
    assert not settings.storage_dir.exists()


@pytest.mark.parametrize("status", [302, 401, 403, 429, 500])
def test_real_http_adapter_is_origin_fixed_and_never_retries_or_displays_error_bodies(monkeypatch, status):
    import httpx
    from backend.services import openai_provider as module
    seen = []
    original = httpx.Client
    def handler(request):
        seen.append(request)
        assert str(request.url) == module.ENDPOINT
        assert request.headers["Authorization"] == "Bearer sk-fixture"
        return httpx.Response(status, headers={"Location": "https://other.invalid"},
            stream=httpx.ByteStream(b"sk-secret-sentinel signed-private-url"))
    def client(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return original(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(module.httpx, "Client", client)
    with pytest.raises(ProviderError) as error:
        module.respond("{}", "sk-fixture")
    assert len(seen) == 1
    assert "sk-secret" not in str(error.value) and "signed-private-url" not in str(error.value)


@pytest.mark.parametrize("fault", ["size", "time", "encoding"])
def test_real_http_adapter_bounds_response_reads(monkeypatch, fault):
    import httpx
    from backend.services import openai_provider as module
    original = httpx.Client
    def handler(request):
        return httpx.Response(200, headers={"Content-Encoding": "gzip"} if fault == "encoding" else {},
            stream=httpx.ByteStream(b"x" * 1000))
    monkeypatch.setattr(module.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    if fault == "size":
        monkeypatch.setattr(module, "MAX_RESPONSE_BYTES", 64)
    if fault == "time":
        clock = iter([0., 46.])
        monkeypatch.setattr(module, "monotonic", lambda: next(clock))
    with pytest.raises(ProviderError):
        module.respond("{}", "sk-fixture")
