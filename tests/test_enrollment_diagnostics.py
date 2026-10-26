"""Fixture diagnostics retain error categories without retaining enrollment."""

import json
from types import SimpleNamespace

import pytest

from companion.enrollment_process import EnrollmentProcess
from companion.pairing_client import PairingClientError
from enrollment_diagnostics import approve_for_crash_fixture, diagnostic_worker


@pytest.mark.parametrize("pending", [False, True])
def test_preparation_reuses_the_review_or_resumes_the_same_saved_approval(pending):
    state, calls, review = ["attempt"], [], {"revision": 1}
    def status(*, refresh=False):
        if refresh and state[0] == "pending_approval":
            calls.append("resume")
            state[0] = "paired"
        return {"state": state[0], "claim": review, "device_id": "same-device"}
    def approve(displayed, policy):
        assert displayed is review
        calls.append("approve")
        if calls.count("approve") == 1:
            if pending:
                state[0] = "pending_approval"
            raise PairingClientError("The coordinator could not be reached securely")
        return {"state": "paired", "device_id": "same-device"}
    events = []
    result = approve_for_crash_fixture(SimpleNamespace(pairing_status=status, approve_pairing=approve), None, events)
    assert result["device_id"] == "same-device" and events == ["secure_connection"]
    assert calls == (["approve", "resume"] if pending else ["approve", "approve"])


def test_preparation_stops_after_three_network_failures():
    calls, events = [], []
    def status(*, refresh=False):
        if refresh:
            calls.append("read")
            raise PairingClientError("The enrollment request exceeded its time limit")
        return {"state": "attempt"}
    with pytest.raises(PairingClientError, match="time limit"):
        approve_for_crash_fixture(SimpleNamespace(pairing_status=status), None, events)
    assert len(calls) == 3 and events == ["operation_timeout", "operation_timeout"]


def test_preparation_does_not_retry_a_changed_review():
    calls, events = [], []
    def status(*, refresh=False):
        return {"state": "attempt", "claim": {"revision": 1}}
    def approve(*args):
        calls.append("approve")
        raise PairingClientError("Pairing changed; review the account and permissions again")
    with pytest.raises(PairingClientError, match="Pairing changed"):
        approve_for_crash_fixture(SimpleNamespace(pairing_status=status, approve_pairing=approve), None, events)
    assert calls == ["approve"] and events == []


def test_diagnostic_worker_keeps_the_real_protocol_and_omits_private_error_input(tmp_path):
    worker, output = diagnostic_worker(tmp_path / "owned-diagnostic")
    private = "private-enrollment-sentinel"
    value = {"origin": "https://" + private + "@localhost", "method": "GET", "path": "/v1/device/session",
        "body_json": None, "authorization": "Bearer " + private, "trust_pem": None, "socket_timeout_s": 1}
    owner = EnrollmentProcess(worker)
    try:
        answer = owner.exchange(json.dumps(value).encode())
        assert json.loads(answer) == {"ok": False, "code": "invalid"}
    finally:
        assert owner.close()
    assert not owner.owned
    events = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(events) == 1
    assert events[0]["outcome"] == "error" and events[0]["kind"] == "Failure"
    assert set(events[0]) == {"outcome", "kind", "reason_kind", "elapsed_s"}
    assert private not in worker.read_text(encoding="utf-8") + output.read_text(encoding="utf-8") + answer.decode()
