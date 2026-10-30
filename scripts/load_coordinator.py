"""Disposable loopback load test with distinct owners and simulated companions."""

import argparse
import asyncio
from collections import Counter
import json
import math
from pathlib import Path
import random
import secrets
import shutil
import socket
import statistics
import tempfile
import tracemalloc
from threading import Thread, Event
from time import monotonic
from uuid import uuid4

import httpx
import uvicorn
from websockets.asyncio.client import connect

from coordinator.app import COOKIE, create_app
from coordinator.settings import CoordinatorSettings
from shared.models.coordination import PairingCreateInput, PairingApproveInput
from shared.models.remote import (ClientReady, DeviceStatusReply, RelayReply, RelayPong,
                                  StatusCommand, canonical_json, parse_message)


class CapacityCheckError(RuntimeError):
    """Fixed report codes; never include response bodies or credentials."""


async def bounded_request(client, method, path, *, deadline, counters, jitter=lambda: 0., **kwargs):
    """Retry a frozen request within one absolute budget, honoring overload waits."""
    for attempt in range(16):
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise CapacityCheckError("http_deadline_exceeded")
        try:
            async with asyncio.timeout(remaining):
                response = await client.request(method, path, **kwargs)
        except TimeoutError:
            raise CapacityCheckError("http_deadline_exceeded") from None
        except httpx.TransportError:
            raise CapacityCheckError("http_transport_error") from None
        counters["statuses"][str(response.status_code)] += 1
        counters["bytes"] += len(response.request.content) + len(response.content)
        if response.status_code not in (429, 503):
            return response
        try:
            supplied = float(response.headers.get("retry-after", "0"))
        except ValueError:
            supplied = 0.
        if not math.isfinite(supplied) or supplied < 0:
            raise CapacityCheckError("invalid_retry_after")
        delay = max(supplied, min(1., .05 * 2 ** min(attempt, 5))) + jitter()
        if attempt == 15 or delay >= deadline - monotonic():
            raise CapacityCheckError("retry_budget_exhausted")
        counters["retries"] += 1
        counters["retry_wait_s"] += delay
        await asyncio.sleep(delay)
    raise CapacityCheckError("retry_budget_exhausted")


async def exercise(app, origin, identities, rounds):
    latencies, tasks, browsers = [], [], []
    counters = {"statuses": Counter(), "bytes": 0, "retries": 0, "retry_wait_s": 0.}
    routed, isolated = 0, 0
    phase, failure, connected = "connections", None, 0
    ready = [asyncio.Event() for _ in identities]
    barrier = asyncio.Barrier(len(identities))
    started = monotonic()

    async def companion(device, credential, ready):
        nonlocal routed
        async with connect(origin.replace("http:", "ws:") + "/v1/device/connect",
                           additional_headers={"Authorization": "Bearer " + credential},
                           max_size=262144, max_queue=1, compression=None, proxy=None) as ws:
            hello = parse_message(await ws.recv())
            assert str(hello.device_id) == str(device)
            envelope = {key: getattr(hello, key) for key in ("device_id", "ownership_generation", "connection_generation")}
            await ws.send(canonical_json(ClientReady(**envelope, remote_execution_enabled=True)))
            ready.set()
            async for raw in ws:
                message = parse_message(raw)
                assert message.device_id == hello.device_id
                if message.kind == "ping":
                    await ws.send(canonical_json(RelayPong(**envelope, nonce=message.nonce)))
                else:
                    assert message.kind == "command" and message.command.kind == "status"
                    routed += 1
                    await ws.send(canonical_json(RelayReply(**envelope, reply=DeviceStatusReply(
                        request_id=message.command.request_id, accepting=True, active=False, waiting_remote_runs=0))))

    async def browser(index):
        nonlocal isolated
        device, credential, session = identities[index]
        rng = random.Random(index)
        headers = {"Origin": origin, "X-Poltergeist-Client": "browser", "X-CSRF-Token": session["csrf_token"],
                   "Cookie": f"{COOKIE}={session['session_token']}"}
        async with httpx.AsyncClient(base_url=origin, headers=headers, timeout=15, trust_env=False) as client:
            other = identities[(index + 1) % len(identities)][0]
            denied = await bounded_request(client, "GET", f"/v1/devices/{other}/commands",
                deadline=monotonic() + 15, counters=counters, jitter=lambda: rng.random() * .05)
            if denied.status_code != 404:
                raise CapacityCheckError("cross_owner_denial_failed")
            isolated += 1
            # Overload during the owner probes must not quietly stagger away the
            # intended concurrent command burst.
            await barrier.wait()
            for _ in range(rounds):
                started = monotonic()
                deadline = started + 15
                command = StatusCommand(request_id=uuid4())
                result = await bounded_request(client, "POST", f"/v1/devices/{device}/commands",
                    deadline=deadline, counters=counters, jitter=lambda: rng.random() * .05,
                    content=canonical_json(command), headers={"Content-Type": "application/json"})
                if result.status_code != 202:
                    raise CapacityCheckError("command_admission_failed")
                while True:
                    reply = await bounded_request(client, "GET", f"/v1/devices/{device}/commands/{command.request_id}",
                        deadline=deadline, counters=counters, jitter=lambda: rng.random() * .05)
                    if reply.status_code != 200:
                        raise CapacityCheckError("command_readback_failed")
                    value = reply.json()
                    if value["status"] == "completed":
                        if value["reply"]["request_id"] != str(command.request_id):
                            raise CapacityCheckError("reply_identity_mismatch")
                        break
                    if value["status"] != "queued":
                        raise CapacityCheckError("unexpected_command_state")
                    await asyncio.sleep(.005)
                latencies.append(monotonic() - started)

    try:
        tasks = [asyncio.create_task(companion(device, credential, event))
                 for (device, credential, _), event in zip(identities, ready)]
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in ready)), timeout=30)
        connected = len(identities)
        phase = "commands"
        started = monotonic()
        browsers = [asyncio.create_task(browser(i)) for i in range(len(identities))]
        await asyncio.gather(*browsers)
    except CapacityCheckError as error:
        failure = str(error)
    except TimeoutError:
        failure = "device_ready_timeout"
    except Exception:
        failure = "unexpected_workload_exception"
    finally:
        for task in [*browsers, *tasks]:
            task.cancel()
        await asyncio.gather(*browsers, return_exceptions=True)
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        if any(isinstance(item, Exception) for item in outcomes) and failure is None:
            failure = "companion_protocol_failure"
    elapsed = monotonic() - started
    connected = sum(event.is_set() for event in ready)
    complete = failure is None and len(latencies) == len(identities) * rounds
    ordered = sorted(latencies)
    return {"schema_version": "poltergeist-capacity-v1", "protocol_version": 2,
        "transport": "loopback HTTP/WS fixture", "users": len(identities), "connected_devices": connected,
        "rounds_per_device": rounds, "completed_commands": len(latencies), "routed_commands": routed,
        "cross_owner_denials": isolated, "elapsed_s": elapsed,
        "commands_per_s": len(latencies) / elapsed if complete else None,
        "latency_p50_s": statistics.median(latencies) if complete else None,
        "latency_p95_s": ordered[int((len(ordered)-1)*.95)] if complete else None,
        "latency_max_s": max(latencies) if complete else None, "latency_measurement_complete": complete,
        "http_payload_bytes": counters["bytes"], "http_status_counts": dict(counters["statuses"]),
        "http_retry_count": counters["retries"], "http_retry_wait_s": counters["retry_wait_s"],
        "max_connections": app.state.relay.max_connections,
        "failure_phase": phase if failure else None, "failure_code": failure,
        "passed": complete and routed == len(latencies) and isolated == len(identities),
        "scope": "Simulated companions; excludes TLS cost, physical PCs, CARLA/GPU and provider costs"}


def run(directory, *, users=25, rounds=20):
    if not 2 <= users <= 100 or not 1 <= rounds <= 100:
        raise ValueError("Load fixture supports 2–100 users and 1–100 rounds")
    tracemalloc.start()
    listener = server = thread = None
    phase = "server_startup"
    result = {"schema_version": "poltergeist-capacity-v1", "protocol_version": 2, "users": users,
        "rounds_per_device": rounds, "passed": False, "connected_devices": None,
        "completed_commands": 0, "routed_commands": 0, "cross_owner_denials": 0,
        "latency_measurement_complete": False, "elapsed_s": None, "commands_per_s": None,
        "latency_p50_s": None, "latency_p95_s": None, "latency_max_s": None,
        "http_payload_bytes": 0, "http_status_counts": {}, "http_retry_count": 0,
        "http_retry_wait_s": 0., "max_connections": None,
        "scope": "Simulated companions; excludes TLS cost, physical PCs, CARLA/GPU and provider costs"}
    try:
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        settings = CoordinatorSettings(Path(directory).absolute() / "coordinator.db", origin,
            test_allow_insecure_loopback=True)
        app = create_app(settings)
        server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False, proxy_headers=False,
            ws_max_size=262144, ws_max_queue=1, ws_per_message_deflate=False, timeout_graceful_shutdown=5,
            limit_concurrency=settings.server_connection_limit, timeout_keep_alive=5))
        thread = Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        thread.start()
        deadline = monotonic() + 20
        while not server.started:
            if monotonic() > deadline or not thread.is_alive():
                raise RuntimeError("Load fixture did not start")
            Event().wait(.01)
        phase, identities = "accounts", []
        for index in range(users):
            username, password = f"capacity-{index}", secrets.token_urlsafe(32)
            owner = app.state.auth.register(username, password, source=f"fixture-{index}")["account_id"]
            session = app.state.auth.login(username, password, source=f"fixture-{index}")
            secret, credential = "pa_" + secrets.token_urlsafe(32), "pd_" + secrets.token_urlsafe(32)
            created = app.state.pairing.create(PairingCreateInput(private_secret=secret,
                display_name=f"Capacity device {index}", requested_scopes=["status:read"]), source=f"fixture-{index}")
            claim = app.state.pairing.claim(owner, created.code, source=f"fixture-{index}")
            device = app.state.pairing.approve(created.attempt_id, secret, PairingApproveInput(request_id=uuid4(),
                expected_account_id=owner, expected_revision=claim.revision, credential=credential, policy={"scopes": ["status:read"]}))
            identities.append((device.device_id, credential, session))
        phase = "workload"
        result = asyncio.run(exercise(app, origin, identities, rounds))
    except Exception:
        result.update(passed=False, failure_phase=phase, failure_code="fixture_setup_or_workload_failed")
    finally:
        if server is not None:
            server.should_exit = True
        if thread is not None and thread.ident is not None:
            thread.join(15)
        if listener is not None:
            listener.close()
        result["python_peak_bytes"] = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        result["cleanup_completed"] = thread is None or not thread.is_alive()
        if not result["cleanup_completed"]:
            result.update(passed=False, failure_phase="cleanup", failure_code="fixture_shutdown_timeout")
    result["database_bytes"] = sum(p.stat().st_size for p in Path(directory).glob("coordinator.db*"))
    result["server_concurrency_limit"] = server.config.limit_concurrency if server is not None else None
    result["provisional_targets"] = {"p95_latency_s": 2., "python_peak_bytes": 128 * 1024**2}
    result["target_failures"] = []
    if result["latency_measurement_complete"] and result["latency_p95_s"] > 2.:
        result["target_failures"].append("latency")
    if result["python_peak_bytes"] > 128 * 1024**2:
        result["target_failures"].append("python_allocations")
    result["passed"] = result["passed"] and not result["target_failures"] and result["cleanup_completed"]
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--users", type=int, default=25)
    parser.add_argument("--rounds", type=int, default=20)
    args = parser.parse_args(argv)
    if not 2 <= args.users <= 100 or not 1 <= args.rounds <= 100:
        parser.error("Use 2–100 users and 1–100 rounds")
    if args.output.exists():
        parser.error("Use a new output file; existing evidence is preserved")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    parent = args.output.parent.resolve()
    directory = Path(tempfile.mkdtemp(prefix="poltergeist-capacity-", dir=parent)).resolve()
    if directory == parent or not directory.is_relative_to(parent) or not directory.name.startswith("poltergeist-capacity-"):
        raise ValueError("Fixture directory escaped its evidence parent")
    result = run(directory, users=args.users, rounds=args.rounds)
    if result["cleanup_completed"]:
        try:
            shutil.rmtree(directory)
        except OSError:
            result.update(passed=False, cleanup_completed=False, failure_phase="cleanup", failure_code="fixture_cleanup_failed")
    if not result["cleanup_completed"]:
        result["retained_fixture"] = directory.name
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
