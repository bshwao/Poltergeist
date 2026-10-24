"""Closed remote messages, finite budgets and outbound relay privacy guards."""

import json
import logging
from threading import Event
from time import monotonic
from uuid import uuid4

import pytest

from backend.remote_receipts import RemoteBinding
from companion.relay_client import RelayClient, RelayClientError, _NoRedirect
from shared.models.coordination import DevicePolicy
from shared.models.remote import (ClientReady, DeviceStatusReply, ExecuteCommand,
    MAX_COMMAND_BYTES, MAX_RELAY_BYTES, RelayCommand, RelayReply, ServerHello,
    StatusCommand, canonical_json, parse_message, payload_digest)


SENTINEL = "private_remote_contract_sentinel"


def execute(**changes):
    value = {"kind": "execute", "request_id": str(uuid4()), "mode": "test",
        "scenario": {"name": "Approved remote baseline", "duration_s": .1,
            "fixed_delta_seconds": .05, "actors": [{"id": "ego", "type": "ego_vehicle"}]}}
    value.update(changes)
    return value


def parse(value):
    return parse_message(json.dumps(value), relay=False)


def test_default_baseline_command_roundtrip_has_a_stable_canonical_digest():
    command = parse(execute())
    assert isinstance(command, ExecuteCommand) and command.agent_id is None
    assert command.options.max_ticks == 10000
    assert parse_message(canonical_json(command), relay=False) == command
    raw = json.loads(canonical_json(command))
    reordered = dict(reversed(list(raw.items())))
    assert payload_digest(command) == payload_digest(reordered)


@pytest.mark.parametrize("field,value", [("agent_id", str(uuid4())), ("package_path", "C:/" + SENTINEL),
    ("python_executable", "C:/" + SENTINEL), ("model_path", SENTINEL), ("shell", SENTINEL),
    ("mode", "native"), ("kind", "register_agent")])
def test_execute_cannot_register_select_or_run_custom_code(field, value):
    with pytest.raises(ValueError) as error:
        parse(execute(**{field: value}))
    assert str(error.value) == "Invalid remote protocol message"


@pytest.mark.parametrize("options", [{"allow_map_reload": True}, {"camera": True}, {"lidar": True},
    {"carla_quality": "Epic"}, {"no_rendering_mode": False}, {"max_ticks": 10001},
    {"max_ticks": True}, {"max_ticks": "100"}, {"interpreter": SENTINEL}])
def test_unsafe_local_options_and_coerced_tick_counts_are_not_wire_fields(options):
    with pytest.raises(ValueError, match="Invalid remote protocol message"):
        parse(execute(options=options))


@pytest.mark.parametrize("changes", [{"duration_s": 121.}, {"duration_s": 120., "fixed_delta_seconds": 1e-308},
    {"duration_s": 120., "fixed_delta_seconds": .001}, {"name": "x" * 129}])
def test_duration_tick_and_name_budgets_refuse_even_finite_overflow_values(changes):
    value = execute()
    value["scenario"].update(changes)
    with pytest.raises(ValueError) as error:
        parse(value)
    assert str(error.value) == "Invalid remote protocol message"


@pytest.mark.parametrize("task", [{"route_owner": "agent", "destination": {"position": {"x": 10.}}, "target_speed_mps": 2.},
    {"route_owner": "framework", "destination": {"position": {"x": 10.}}}])
def test_sdk_only_driving_tasks_are_refused_before_baseline_queue_admission(task):
    value = execute()
    value["scenario"]["driving_task"] = task
    with pytest.raises(ValueError, match="Invalid remote protocol message"):
        parse(value)


def test_supported_baseline_framework_task_and_zero_speed_remain_accepted():
    value = execute()
    value["scenario"]["driving_task"] = {"route_owner": "framework", "target_speed_mps": 0.,
        "destination": {"position": {"x": 10.}}}
    command = parse(value)
    assert command.scenario.driving_task.target_speed_mps == 0.


@pytest.mark.parametrize("raw", ['{"kind":"status","kind":"execute"}',
    '{"kind":"status","request_id":"' + str(uuid4()) + '","metadata":{"token":1,"token":2}}',
    '{"kind":"status","request_id":"' + str(uuid4()) + '","progress":NaN}',
    '{"kind":"status","request_id":"' + str(uuid4()) + '","progress":Infinity}',
    '[' * 1500 + '0' + ']' * 1500])
def test_duplicate_keys_nonfinite_constants_and_deep_json_have_fixed_rejections(raw):
    with pytest.raises(ValueError) as error:
        parse_message(raw, relay=False)
    assert str(error.value) == "Invalid remote protocol message"


def test_encoded_command_and_relay_bounds_are_checked_before_and_after_defaults():
    for size, relay in ((MAX_COMMAND_BYTES, False), (MAX_RELAY_BYTES, True)):
        with pytest.raises(ValueError, match="Invalid remote protocol message"):
            parse_message(b" " * (size + 1), relay=relay)
    value = execute()
    value["scenario"]["description"] = "\u2603" * 6000
    # Raw UTF-8 fits; canonical ASCII escaping and default fields exceed the cap.
    raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
    assert len(raw) < MAX_COMMAND_BYTES
    with pytest.raises(ValueError, match="Invalid remote protocol message"):
        parse_message(raw, relay=False)


def binding():
    return RemoteBinding("https://coordinator.example", uuid4(), uuid4(), 1)


def hello(value, approved_policy, **changes):
    fields = {"device_id": value.device_id, "owner_id": value.owner_id,
        "ownership_generation": value.generation, "connection_generation": 2,
        "policy_revision": 1, "policy": approved_policy}
    fields.update(changes)
    return ServerHello(**fields)


class Socket:
    def __init__(self, messages):
        self.messages = iter(messages)
        self.sent = []

    def recv(self, *, timeout):
        return canonical_json(next(self.messages))

    def send(self, message):
        self.sent.append(parse_message(message))


class Service:
    def __init__(self):
        self.commands = []
        self.stop = None

    def dispatch(self, value, command):
        self.commands.append((value, command))
        if self.stop:
            self.stop()
        return DeviceStatusReply(request_id=command.request_id, accepting=True, active=False, waiting_remote_runs=0)


def test_relay_default_readiness_cannot_dispatch_even_an_approved_command():
    value, policy, service = binding(), DevicePolicy(scopes=["status:read"]), Service()
    server = hello(value, policy)
    command = RelayCommand(device_id=value.device_id, ownership_generation=1, connection_generation=2,
        command=StatusCommand(request_id=uuid4()))
    socket = Socket([server, command])
    client = RelayClient(value.origin, lambda: (value, policy, SENTINEL), service, _ssl_context=object())
    with pytest.raises(RelayClientError, match="unsupported"):
        client._serve(socket, value, policy, SENTINEL)
    assert service.commands == [] and len(socket.sent) == 1
    assert isinstance(socket.sent[0], ClientReady) and socket.sent[0].remote_execution_enabled is False
    assert SENTINEL not in canonical_json(socket.sent[0])


@pytest.mark.parametrize("change", ["owner", "device", "ownership_generation", "policy", "policy_revision"])
def test_changed_server_binding_or_permissions_never_reach_local_dispatch(change):
    value, policy, service = binding(), DevicePolicy(scopes=["status:read"]), Service()
    changes = {"owner_id": uuid4()} if change == "owner" else {"device_id": uuid4()} if change == "device" else (
        {"ownership_generation": 2} if change == "ownership_generation" else
        {"policy": DevicePolicy(scopes=["run:execute:carla"])} if change == "policy" else {"policy_revision": 2})
    socket = Socket([hello(value, policy, **changes)])
    client = RelayClient(value.origin, lambda: (value, policy, SENTINEL), service,
        remote_execution_enabled=True, _ssl_context=object())
    with pytest.raises(RelayClientError, match="locally approved"):
        client._serve(socket, value, policy, SENTINEL)
    assert service.commands == [] and socket.sent == []


def test_stale_connection_generation_cannot_dispatch_after_a_valid_hello():
    value, policy, service = binding(), DevicePolicy(scopes=["status:read"]), Service()
    socket = Socket([hello(value, policy), RelayCommand(device_id=value.device_id,
        ownership_generation=1, connection_generation=1, command=StatusCommand(request_id=uuid4()))])
    client = RelayClient(value.origin, lambda: (value, policy, SENTINEL), service,
        remote_execution_enabled=True, _ssl_context=object())
    with pytest.raises(RelayClientError, match="stale relay generation"):
        client._serve(socket, value, policy, SENTINEL)
    assert service.commands == []


def test_explicit_private_enabled_relay_dispatches_only_the_validated_bound_command():
    value, policy, service = binding(), DevicePolicy(scopes=["status:read"]), Service()
    command = StatusCommand(request_id=uuid4())
    socket = Socket([hello(value, policy), RelayCommand(device_id=value.device_id,
        ownership_generation=1, connection_generation=2, command=command)])
    client = RelayClient(value.origin, lambda: (value, policy, SENTINEL), service,
        remote_execution_enabled=True, _ssl_context=object())
    service.stop = client._stop.set
    client._serve(socket, value, policy, SENTINEL)
    assert service.commands == [(value, command)]
    assert len(socket.sent) == 2 and isinstance(socket.sent[1], RelayReply)
    assert socket.sent[1].connection_generation == 2


def test_handshake_failures_and_verbose_root_logging_do_not_retain_credentials(caplog):
    value, policy, service = binding(), DevicePolicy(scopes=["status:read"]), Service()
    entered = Event()
    calls = []
    class FailingConnector:
        def __init__(self, uri, **options):
            calls.append((uri, options))
            options["logger"].debug("Authorization: Bearer %s", SENTINEL)
        def connect(self):
            entered.set()
            raise RuntimeError("Authorization: Bearer " + SENTINEL)
    client = RelayClient(value.origin, lambda: (value, policy, SENTINEL), service,
        _connector=FailingConnector, _ssl_context=object())
    with caplog.at_level(logging.DEBUG):
        client.start()
        try:
            assert entered.wait(2)
            deadline = monotonic() + 2
            while client.snapshot()["state"] != "offline" and monotonic() < deadline:
                Event().wait(.01)
            assert client.snapshot()["state"] == "offline"
        finally:
            assert client.stop()
    assert SENTINEL not in caplog.text + json.dumps(client.snapshot())
    uri, options = calls[0]
    assert uri == "wss://coordinator.example/v1/device/connect" and SENTINEL not in uri
    assert options["additional_headers"] == {"Authorization": "Bearer " + SENTINEL}
    assert options["proxy"] is None and options["compression"] is None
    assert options["max_size"] == MAX_RELAY_BYTES and options["max_queue"] == 4
    assert options["open_timeout"] == 10 and options["close_timeout"] == 2


def test_redirect_refusal_never_repeats_header_or_location_details():
    error = _NoRedirect.process_redirect(None, RuntimeError("https://" + SENTINEL + "; Authorization=" + SENTINEL))
    assert isinstance(error, RelayClientError)
    assert SENTINEL not in str(error)
