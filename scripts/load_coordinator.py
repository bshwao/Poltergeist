"""Disposable loopback load test with distinct owners and simulated companions."""

import argparse
import asyncio
import json
from pathlib import Path
import secrets
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


async def exercise(app, origin, identities, rounds):
    latencies, tasks = [], []
    http_bytes = 0
    routed, isolated = 0, 0

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
        nonlocal http_bytes, isolated
        device, credential, session = identities[index]
        headers = {"Origin": origin, "X-Poltergeist-Client": "browser", "X-CSRF-Token": session["csrf_token"],
                   "Cookie": f"{COOKIE}={session['session_token']}"}
        async with httpx.AsyncClient(base_url=origin, headers=headers, timeout=15, trust_env=False) as client:
            other = identities[(index + 1) % len(identities)][0]
            denied = await client.get(f"/v1/devices/{other}/commands")
            assert denied.status_code == 404
            isolated += 1
            for _ in range(rounds):
                started = monotonic()
                command = StatusCommand(request_id=uuid4())
                result = await client.post(f"/v1/devices/{device}/commands", content=canonical_json(command),
                    headers={"Content-Type": "application/json"})
                http_bytes += len(result.request.content) + len(result.content)
                assert result.status_code == 202, result.status_code
                while True:
                    reply = await client.get(f"/v1/devices/{device}/commands/{command.request_id}")
                    http_bytes += len(reply.content)
                    assert reply.status_code == 200
                    value = reply.json()
                    if value["status"] == "completed":
                        assert value["reply"]["request_id"] == str(command.request_id)
                        break
                    assert monotonic() - started < 15
                    await asyncio.sleep(.005)
                latencies.append(monotonic() - started)

    try:
        ready = [asyncio.Event() for _ in identities]
        tasks = [asyncio.create_task(companion(device, credential, event))
                 for (device, credential, _), event in zip(identities, ready)]
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in ready)), timeout=30)
        started = monotonic()
        await asyncio.gather(*(browser(i) for i in range(len(identities))))
        elapsed = monotonic() - started
        ordered = sorted(latencies)
        return {"schema_version": "poltergeist-capacity-v1", "transport": "loopback HTTP/WS fixture",
            "users": len(identities), "connected_devices": len(identities), "rounds_per_device": rounds,
            "completed_commands": len(latencies), "routed_commands": routed, "cross_owner_denials": isolated,
            "elapsed_s": elapsed, "commands_per_s": len(latencies) / elapsed,
            "latency_p50_s": statistics.median(latencies), "latency_p95_s": ordered[int((len(ordered)-1)*.95)],
            "latency_max_s": max(latencies), "http_payload_bytes": http_bytes,
            "max_connections": app.state.relay.max_connections,
            "passed": routed == len(latencies) == len(identities) * rounds and isolated == len(identities),
            "scope": "Simulated companions; excludes TLS cost, physical PCs, CARLA/GPU and provider costs"}
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def run(directory, *, users=25, rounds=20):
    if not 2 <= users <= 100 or not 1 <= rounds <= 100:
        raise ValueError("Load fixture supports 2–100 users and 1–100 rounds")
    tracemalloc.start()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
    app = create_app(CoordinatorSettings(Path(directory).absolute() / "coordinator.db", origin,
        test_allow_insecure_loopback=True))
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False, proxy_headers=False,
        ws_max_size=262144, ws_max_queue=1, ws_per_message_deflate=False, timeout_graceful_shutdown=5))
    thread = Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    try:
        deadline = monotonic() + 20
        while not server.started:
            if monotonic() > deadline or not thread.is_alive():
                raise RuntimeError("Load fixture did not start")
            Event().wait(.01)
        identities = []
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
        result = asyncio.run(exercise(app, origin, identities, rounds))
        result["python_peak_bytes"] = tracemalloc.get_traced_memory()[1]
        result["database_bytes"] = sum(p.stat().st_size for p in Path(directory).glob("coordinator.db*"))
        result["provisional_targets"] = {"p95_latency_s": 2., "python_peak_bytes": 128 * 1024**2}
        result["passed"] = (result["passed"] and result["latency_p95_s"] <= 2.
                            and result["python_peak_bytes"] <= 128 * 1024**2)
        return result
    finally:
        server.should_exit = True
        thread.join(15)
        listener.close()
        tracemalloc.stop()
        if thread.is_alive():
            raise RuntimeError("Load fixture failed to stop")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--users", type=int, default=25)
    parser.add_argument("--rounds", type=int, default=20)
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="poltergeist-capacity-", dir=args.output.parent) as directory:
        result = run(directory, users=args.users, rounds=args.rounds)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
