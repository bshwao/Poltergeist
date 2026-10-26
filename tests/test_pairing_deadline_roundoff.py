"""A coarse, nondecreasing clock must not round a valid budget above its cap."""

from contextlib import contextmanager
from email.message import Message
import json
from types import SimpleNamespace

import pytest

from companion import enrollment_process, enrollment_worker, pairing_client


@pytest.mark.parametrize("clock,budget", [(502.007, 10.), (502.016, 10.), (.2, .1)])
def test_coarse_clock_tie_keeps_worker_input_and_owner_timeout_within_requested_budget(monkeypatch, clock, budget):
    assert (clock + budget) - clock > budget
    observed = []

    class Owner:
        owned = False
        def __init__(self, path, **options):
            observed.append(options)
        def exchange(self, payload):
            value = json.loads(payload)
            observed[0]["socket_timeout_s"] = value["socket_timeout_s"]
            try:
                answer = enrollment_worker.exchange(value)
            except enrollment_worker.Failure as failure:
                answer = {"ok": False, "code": failure.code}
            return json.dumps(answer).encode()
        def close(self):
            return True

    @contextmanager
    def response(*args, **kwargs):
        headers = Message()
        headers["Content-Type"] = "application/json"
        yield SimpleNamespace(headers=headers, read=lambda limit: b'{"accepted":true}')

    monkeypatch.setattr(pairing_client, "monotonic", lambda: clock)
    monkeypatch.setattr(enrollment_process, "EnrollmentProcess", Owner)
    monkeypatch.setattr(enrollment_worker.ssl, "create_default_context",
        lambda **kwargs: SimpleNamespace(check_hostname=True, verify_mode=enrollment_worker.ssl.CERT_REQUIRED))
    monkeypatch.setattr(enrollment_worker.request, "build_opener", lambda *args: SimpleNamespace(open=response))
    client = pairing_client.JsonTransport("https://coordinator.example", _request_timeout_s=budget)
    assert client.request("GET", "/v1/device/session") == {"accepted": True}
    assert 0 < observed[0]["socket_timeout_s"] <= budget
    assert 0 < observed[0]["timeout_s"] <= budget
    assert observed[0]["deadline"] == clock + budget
    assert client.close()
