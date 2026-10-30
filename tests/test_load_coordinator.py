"""Capacity evidence must retain overload, identity and incomplete outcomes."""

import asyncio
from collections import Counter
import json
from time import monotonic
from uuid import uuid4

import httpx
import pytest

from scripts import load_coordinator as load


def counters():
    return {"statuses": Counter(), "bytes": 0, "retries": 0, "retry_wait_s": 0.}


def test_overload_retry_keeps_the_original_command_bytes_and_identity():
    identifier = str(uuid4())
    body = json.dumps({"kind": "status", "request_id": identifier})
    requests, stats = [], counters()
    def answer(request):
        requests.append(request)
        return httpx.Response(503, headers={"Retry-After": "0"}, text="Service Unavailable") if len(requests) == 1 else httpx.Response(202, json={"request_id": identifier})
    async def exercise():
        async with httpx.AsyncClient(base_url="http://fixture.test", transport=httpx.MockTransport(answer)) as client:
            return await load.bounded_request(client, "POST", "/commands", content=body,
                deadline=monotonic() + 2, counters=stats)
    response = asyncio.run(exercise())
    assert response.status_code == 202 and len(requests) == 2
    assert [request.content for request in requests] == [body.encode(), body.encode()]
    assert stats["statuses"] == {"503": 1, "202": 1} and stats["retries"] == 1
    assert stats["retry_wait_s"] > 0


def test_retry_after_beyond_the_budget_does_not_retry_early():
    calls, stats = [], counters()
    def answer(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "60"})
    async def exercise():
        async with httpx.AsyncClient(base_url="http://fixture.test", transport=httpx.MockTransport(answer)) as client:
            with pytest.raises(load.CapacityCheckError, match="retry_budget_exhausted"):
                await load.bounded_request(client, "GET", "/commands", deadline=monotonic() + .1, counters=stats)
    asyncio.run(exercise())
    assert len(calls) == 1 and stats["retries"] == 0


@pytest.mark.parametrize("header", ["inf", "nan", "-1"])
def test_invalid_retry_wait_is_reported_instead_of_busy_looping(header):
    async def exercise():
        async with httpx.AsyncClient(base_url="http://fixture.test", transport=httpx.MockTransport(
                lambda request: httpx.Response(503, headers={"Retry-After": header}))) as client:
            with pytest.raises(load.CapacityCheckError, match="invalid_retry_after"):
                await load.bounded_request(client, "GET", "/commands", deadline=monotonic() + .1, counters=counters())
    asyncio.run(exercise())


def test_failed_fixture_writes_unknown_latency_and_omits_private_error_text(tmp_path, monkeypatch):
    def fail(settings):
        raise RuntimeError("private fixture credential must never enter the report")
    monkeypatch.setattr(load, "create_app", fail)
    output = tmp_path / "failure.json"
    assert load.main(["--users", "2", "--rounds", "1", "--output", str(output)]) == 1
    report = json.loads(output.read_text())
    assert report["passed"] is False and report["cleanup_completed"] is True
    assert report["failure_phase"] == "server_startup" and report["latency_measurement_complete"] is False
    assert report["latency_p95_s"] is None and report["connected_devices"] is None
    assert "private fixture credential" not in output.read_text()
    assert list(tmp_path.iterdir()) == [output]


def test_existing_evidence_is_preserved_before_starting_another_fixture(tmp_path, monkeypatch):
    output = tmp_path / "receipt.json"
    output.write_bytes(b"original failed receipt")
    def unexpected(*args, **kwargs):
        pytest.fail("Existing evidence must be checked before starting load")
    monkeypatch.setattr(load, "run", unexpected)
    with pytest.raises(SystemExit):
        load.main(["--output", str(output)])
    assert output.read_bytes() == b"original failed receipt"


def test_request_budget_failure_is_retained_without_fabricated_latency(tmp_path, monkeypatch):
    async def overloaded(*args, **kwargs):
        raise load.CapacityCheckError("retry_budget_exhausted")
    monkeypatch.setattr(load, "bounded_request", overloaded)
    output = tmp_path / "overload.json"
    assert load.main(["--users", "2", "--rounds", "1", "--output", str(output)]) == 1
    report = json.loads(output.read_text())
    assert report["failure_code"] == "retry_budget_exhausted" and report["failure_phase"] == "commands"
    assert report["latency_measurement_complete"] is False and report["latency_p95_s"] is None
    assert report["completed_commands"] == 0 and report["cleanup_completed"] is True
    assert list(tmp_path.iterdir()) == [output]


def test_real_loopback_load_reports_ownership_routing_and_cleanup(tmp_path):
    report = load.run(tmp_path, users=2, rounds=2)
    assert report["passed"] is True and report["protocol_version"] == 2
    assert report["connected_devices"] == 2 and report["cross_owner_denials"] == 2
    assert report["completed_commands"] == report["routed_commands"] == 4
    assert report["latency_measurement_complete"] is True and report["latency_p95_s"] is not None
    assert report["cleanup_completed"] is True and report["server_concurrency_limit"] == 128
