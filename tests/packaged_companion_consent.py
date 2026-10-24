"""Private bundle/TLS/DPAPI proof through normal native Tk consent callbacks.

Credentials and human pairing codes travel through inherited Windows pipes only.
This driver creates fresh workspace-owned profiles and a disposable coordinator.
It never opens existing settings, starts CARLA, installs, or changes product code.
"""

import argparse
from datetime import UTC, datetime
import hashlib
import importlib.util
import json
import logging
import multiprocessing
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import subprocess
import sys
from threading import Event
import time
from uuid import UUID, uuid4


SCOPES = ["status:read", "run:execute:test", "run:cancel", "result:read"]


def require(value, code):
    if not value:
        raise RuntimeError(code)


def wait_for(predicate, *, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if value := predicate():
            return value
        time.sleep(.02)
    raise TimeoutError("owned_fixture_deadline")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def child_main(args):
    print("child bootstrap entered", flush=True)
    from multiprocessing.connection import PipeConnection
    channel = PipeConnection(args.control_handle)
    controller = None
    root = window = log_handler = None
    release, entered = Event(), Event()
    stage = "bootstrap"
    transport_events = []
    def checkpoint(value):
        nonlocal stage
        stage = value
        print("child stage: " + value, flush=True)
    try:
        bundle, profile = Path(args.bundle).resolve(), Path(args.profile).resolve()
        require(Path(sys.executable).resolve() == bundle / "runtime/python.exe", "wrong_interpreter")
        require(sys.flags.isolated and sys.flags.dont_write_bytecode, "missing_isolation_flags")
        from scripts.portable import verify_bundle
        checkpoint("verify initial bundle")
        integrity = verify_bundle(bundle)
        checkpoint("import bundled runtime and GUI")
        from backend.dependencies import ApiRuntime
        from backend.models import RemoteCommandReceiptRecord, SimulationJobRecord, SimulationRecord
        from backend.remote_receipts import RemoteReceiptRepository
        from companion.controller import CompanionController
        from companion.credential_store import WindowsCredentialStore
        from companion.pairing_client import JsonTransport, PairingClient, PairingClientError
        from companion.profile import CompanionConfig, save_config, prepare_logs, configure_gui_environment
        configure_gui_environment(bundle)
        from companion.ui import CompanionWindow
        from companion.relay_client import RelayClient
        from shared.models.coordination import DevicePolicy
        from shared.models.remote import parse_message
        from simulator import SimulationRunner
        from simulator.test_adapter import TestSimulationAdapter
        from sqlalchemy import select
        import websockets.sync.client
        import tkinter as tk
        from tkinter import messagebox

        prefixes = ("backend", "companion", "evaluation", "optimization", "scenarios", "shared", "simulator", "scripts")
        provenance = {name: str(Path(module.__file__).resolve()) for name, module in sys.modules.items()
            if getattr(module, "__file__", None) and (name.split(".")[0] in prefixes
                or name in ("websockets.sync.client", "pydantic", "ssl", "_ssl", "ctypes", "sqlalchemy", "tkinter", "_tkinter"))}
        require(all(Path(path).is_relative_to(bundle) for path in provenance.values()), "source_import_detected")
        require(all(Path(path).resolve().is_relative_to(bundle) for path in sys.path), "source_sys_path_detected")
        require(importlib.util.find_spec("coordinator") is None, "coordinator_present")
        require(importlib.util.find_spec("argon2") is None, "argon_present")
        context = ssl.create_default_context(cafile=args.cert)
        dropped = []

        class BlockingAdapter(TestSimulationAdapter):
            def open(self, scenario):
                if scenario.name == "Proof blocker":
                    entered.set()
                    require(release.wait(90), "blocker_release_deadline")
                return super().open(scenario)

        class DropFirstAck(RelayClient):
            def _serve(self, transport, binding, policy, credential):
                class Socket:
                    def __getattr__(self, name):
                        return getattr(transport, name)

                    def send(self, raw):
                        message = parse_message(raw)
                        if message.kind == "reply" and message.reply.kind == "accepted" and not dropped:
                            dropped.append(str(message.reply.simulation_id))
                            transport.close()
                            raise OSError("Injected lost acceptance acknowledgement")
                        return transport.send(raw)
                return super()._serve(Socket(), binding, policy, credential)

        class DiagnosticTransport(JsonTransport):
            def request(self, method, path, **values):
                event = {"method": method, "operation": path.rsplit("/", 1)[-1] if path.endswith(("approve", "session")) else "pairing"}
                transport_events.append(event)
                try:
                    result = super().request(method, path, **values)
                except PairingClientError as error:
                    status = re.fullmatch(r"The coordinator refused enrollment \(HTTP ([1-5][0-9]{2})\)", str(error))
                    event["error"] = "http_" + status[1] if status else "secure_or_invalid_response"
                    raise
                event["response_fields"] = sorted(result) if isinstance(result, dict) else []
                return result

        def pairing(origin, store):
            return PairingClient(origin, store, _transport=DiagnosticTransport(origin, _ssl_context=context))

        def runtime(settings):
            return ApiRuntime(settings, runner_factory=lambda **values: SimulationRunner(BlockingAdapter(), **values))

        def relay(*values, **options):
            return DropFirstAck(*values, **options, _ssl_context=context)

        default = CompanionController(profile, bundle=bundle)
        require(default._private_remote_execution is False, "normal_constructor_enabled_remote")
        if not profile.exists():
            profile.mkdir()
            save_config(profile, CompanionConfig(setup_completed=True))
        controller = CompanionController(profile, bundle=bundle, runtime_factory=runtime,
            pairing_factory=pairing, relay_factory=relay)
        checkpoint("open new profile")
        controller.open()
        if controller.config.coordinator_origin is None:
            controller.configure_pairing(args.origin)
        require(controller.config.coordinator_origin == args.origin, "saved_origin_changed")
        logger, log_handler = prepare_logs(profile)
        callback_errors = []
        checkpoint("create native Tk interpreter")
        root = tk.Tk()
        root.withdraw()
        gui_libraries = {"tcl": str(root.tk.call("info", "library")),
            "tk": str(root.tk.call("set", "tk_library"))}
        require(all(Path(path).resolve().is_relative_to(bundle / "runtime/tcl")
            for path in gui_libraries.values()), "ambient_gui_scripts_loaded")
        root.report_callback_exception = lambda kind, error, trace: callback_errors.append(kind.__name__)
        messagebox.showerror = lambda *args, **kwargs: callback_errors.append("action_error")
        checkpoint("construct native companion window")
        window = CompanionWindow(root, controller, logger, autostart=False,
            startup_check=lambda: verify_bundle(bundle))

        def pump_until_idle(*, closing=False):
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if closing and window._destroyed:
                    return
                root.update()
                require(not callback_errors, "native_callback_failed")
                if not closing and window._future is None:
                    return
                time.sleep(.01)
            raise TimeoutError("native_action_deadline")

        checkpoint("start native runtime callback")
        window.start_button.invoke()
        pump_until_idle()
        require(controller._runtime is not None, "native_runtime_not_started")
        require(not controller.relay_status()["owned"], "startup_connected_without_consent")

        def inventory():
            with controller._worker.persistence.database.session(write=False) as session:
                rows = session.scalars(select(SimulationRecord)).all()
                return {"runs": [{"id": row.id, "status": row.status} for row in rows],
                    "jobs": len(session.scalars(select(SimulationJobRecord)).all()),
                    "receipts": len(session.scalars(select(RemoteCommandReceiptRecord)).all()),
                    "dropped_ack_run_ids": list(dropped), "blocker_entered": entered.is_set()}

        def protection():
            record = WindowsCredentialStore(profile).load()
            require(record and record["state"] == "paired", "paired_enrollment_missing")
            credential, secret = record["approval"]["credential"], record["private_secret"]
            blob = (profile / "remote-enrollment.bin").read_bytes()
            require(credential.encode() not in blob and secret.encode() not in blob, "plaintext_enrollment")
            snapshot = json.dumps(controller.snapshot())
            require(credential not in snapshot and secret not in snapshot, "secret_in_controller_projection")
            return {"device_id": record["created"]["device_id"], "owner_id": record["approval"]["expected_account_id"],
                "credential": credential, "private_secret": secret, "protected_blob_bytes": len(blob),
                "protected_native_dpapi": True, "controller_projection_redacted": True}

        checkpoint("native bootstrap ready")
        channel.send({"ok": True, "provenance": provenance, "sys_path": list(sys.path),
            "python_version": sys.version.split()[0], "integrity_before": integrity,
            "coordinator_available": False, "argon2_available": False, "normal_constructor_remote_enabled": False,
            "native_window_withdrawn": root.state() == "withdrawn", "normal_constructor_and_tk_callbacks": True,
            "gui_libraries": gui_libraries})
        while True:
            command = channel.recv()
            stage = command["action"]
            response = {"ok": True, "action": stage}
            if stage == "begin":
                for scope, variable in window.pairing.permissions.items():
                    variable.set(scope.value in SCOPES)
                window.pairing.display_name.set("Private packaged consent proof")
                window.pairing.begin_button.invoke()
                pump_until_idle()
                response["code"] = window.pairing.code.get()
            elif stage == "approve":
                window.pairing.refresh_button.invoke()
                pump_until_idle()
                displayed = window.pairing._displayed
                require(displayed["claim"]["claimed_account"]["account_id"] == command["owner_id"], "claim_owner_changed")
                window.pairing.approve_button.invoke()
                pump_until_idle()
                require(window.pairing._displayed["state"] == "paired", "native_approval_missing")
                require(not window.pairing.allow_remote_execution.get(), "approval_enabled_session_consent")
                response.update(protection())
            elif stage == "resume":
                require(window.pairing._displayed["state"] == "paired", "saved_enrollment_not_resumed")
                require(not window.pairing.allow_remote_execution.get(), "reopen_restored_session_consent")
                require(not controller.relay_status()["owned"], "reopen_connected_without_consent")
                response.update(protection(), **inventory())
            elif stage in ("connect", "connect_paused"):
                require(not controller.relay_status()["owned"], "connect_requires_fresh_session")
                require(not window.pairing.allow_remote_execution.get(), "connection_consent_not_reset")
                if stage == "connect":
                    window.pairing.consent_control.invoke()
                require(window.pairing.allow_remote_execution.get() is (stage == "connect"), "native_checkbox_did_not_toggle")
                window.pairing.connect_button.invoke()
                pump_until_idle()
                wait_for(lambda: controller.relay_status()["state"] == "connected")
                window._apply_snapshot(controller.snapshot())
                require(controller.relay_status()["remote_execution_enabled"] is (stage == "connect"), "consent_flag_mismatch")
                response.update(connected=True, explicit_tk_consent=stage == "connect",
                    normal_constructor_remote_enabled=controller._private_remote_execution,
                    relay=controller.relay_status())
            elif stage == "disconnect":
                window.pairing.disconnect_button.invoke()
                pump_until_idle()
                require(not controller.relay_status()["owned"], "native_disconnect_retained_relay")
                require(not window.pairing.allow_remote_execution.get(), "disconnect_retained_session_consent")
                response.update(relay=controller.relay_status(), consent_reset=True)
            elif stage == "restart_runtime":
                window.stop_button.invoke()
                pump_until_idle()
                require(controller._runtime is None and not controller.relay_status()["owned"], "native_stop_retained_runtime")
                require(not window.pairing.allow_remote_execution.get(), "stop_retained_session_consent")
                window.start_button.invoke()
                pump_until_idle()
                require(controller._runtime is not None and not controller.relay_status()["owned"], "restart_connected_without_consent")
                response.update(runtime_restarted=True, consent_reset=True, automatic_reconnect=False)
            elif stage == "inspect":
                response.update(inventory())
            elif stage == "terminal":
                simulation_id = UUID(command["simulation_id"])
                stored = wait_for(lambda: (value if (value := controller._worker.get(simulation_id)).result is not None else None))
                response.update(simulation_id=str(simulation_id), status=stored.simulation.status.value,
                    telemetry_frames=stored.result.telemetry_frames, artifact_count=len(stored.result.artifacts))
            elif stage == "release":
                release.set()
            elif stage == "revoked_reconnect":
                window.pairing.disconnect_button.invoke()
                pump_until_idle()
                require(not window.pairing.allow_remote_execution.get(), "revoked_reconnect_consent_not_reset")
                window.pairing.consent_control.invoke()
                window.pairing.connect_button.invoke()
                pump_until_idle()
                wait_for(lambda: controller.relay_status()["state"] == "offline")
                response.update(revoked_reconnect_offline=True, **inventory())
            elif stage == "close":
                release.set()
                response.update(inventory())
                window.request_close()
                pump_until_idle(closing=True)
                require(not controller._owned, "profile_or_worker_lease_retained")
                response.update(integrity_after=verify_bundle(bundle), clean_controller_close=True)
                channel.send(response)
                return 0
            else:
                raise RuntimeError("unsupported_private_action")
            channel.send(response)
    except BaseException as error:
        try:
            fixed_pairing_errors = {"The coordinator returned an invalid enrollment message": "invalid_enrollment_message",
                "Pairing changed; review the account and permissions again": "pairing_changed",
                "The approved device binding changed": "approved_binding_changed",
                "The saved credential belongs to a different device": "credential_binding_changed"}
            channel.send({"ok": False, "stage": stage, "error_type": type(error).__name__, "transport_events": transport_events,
                "pairing_error": fixed_pairing_errors.get(str(error), "unclassified_fixed_pairing_error") if type(error).__name__ == "PairingClientError" else None,
                "error_code": str(error) if type(error) in (RuntimeError, TimeoutError) and re.fullmatch(r"[a-z_]+", str(error)) else "child_validation_failed"})
        except Exception:
            pass
        return 1
    finally:
        release.set()
        if controller is not None:
            controller.close()
        if root is not None and window is not None and not window._destroyed:
            window._executor.shutdown(wait=True)
            root.destroy()
        if log_handler is not None:
            log_handler.close()
        channel.close()


def parent_main(args):
    root = Path(__file__).resolve().parents[1]
    # The coordinator side deliberately uses current source. This only affects
    # the parent process; -I and ._pth isolate every packaged child.
    sys.path.insert(0, str(root))
    import httpx
    from shared.models.remote import CancelCommand, ExecuteCommand, ResultCommand, StatusCommand, canonical_json
    from scenarios import validate_scenario

    bundle = Path(args.bundle).resolve()
    require(bundle.is_relative_to(root / "validation-evidence/portable-dist"), "bundle_outside_owned_builds")
    require((bundle / "bundle.json").is_file(), "bundle_not_complete")
    base = root / "validation-evidence" / ("s4-packaged-consent-" + datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:8])
    base.mkdir()
    profile = base / "CompanionPrototype"
    cert, key, database = base / "relay-certificates/ca.pem", base / "relay-certificates/server.key", base / "coordinator.db"
    receipt_path = base / "receipt.json"
    receipt = {"schema_version": "poltergeist-s4-packaged-consent-private-v1", "passed": False,
        "started_at": datetime.now(UTC).isoformat(), "bundle": str(bundle), "profile": str(profile), "steps": [],
        "scope": "One packaged companion profile, two source-coordinator accounts, real loopback HTTPS/WSS, native DPAPI and receipt-backed synthetic worker.",
        "limitations": ["Native Tk callbacks are invoked on a withdrawn window; no visual or physical-input claim.",
            "Same Windows account and machine; development-only fixture certificate; no public hosting or independent-machine proof.",
            "Synthetic test adapter only, with a bounded blocker for cancellation; no CARLA, new installer, or physical hardware proof.",
            "Coordinator runs separately from the source environment, not from the desktop bundle.",
            "This is partial private S4 evidence and does not complete the S4 acceptance gate."],
        "default_remote_execution_enabled": False, "private_constructor_switch": False,
        "normal_controller_explicit_tk_consent": True, "public_deployment_verified": False}
    children, streams, browsers, private_values = [], [], [], []
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["TEMP"] = env["TMP"] = str(base)

    def save():
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    def stage(name):
        receipt["stage"] = name
        receipt["steps"].append(name)
        receipt.setdefault("stage_times", []).append({"stage": name, "at": datetime.now(UTC).isoformat()})
        save()
        print(name, flush=True)

    def spawn(label):
        parent_end, child_end = multiprocessing.Pipe(duplex=True)
        handle = child_end.fileno()
        os.set_handle_inheritable(handle, True)
        startup = subprocess.STARTUPINFO()
        startup.lpAttributeList = {"handle_list": [handle]}
        stream = (base / (label + ".log")).open("w", encoding="utf-8")
        streams.append(stream)
        isolated = env.copy()
        isolated["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
        isolated["PYTHONPATH"] = isolated["PYTHONHOME"] = str(base / "hostile-python-environment")
        isolated["TCL_LIBRARY"] = isolated["TK_LIBRARY"] = str(base / "hostile-gui-environment")
        try:
            process = subprocess.Popen([str(bundle / "runtime/python.exe"), "-I", "-B", str(Path(__file__).resolve()),
                "--child", "--bundle", str(bundle), "--profile", str(profile), "--origin", origin,
                "--cert", str(cert), "--control-handle", str(handle)], cwd=base, env=isolated,
                stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT, close_fds=True,
                startupinfo=startup, creationflags=flags)
        finally:
            child_end.close()
        children.append((label, process, parent_end))
        require(parent_end.poll(90), "packaged_bootstrap_deadline")
        answer = parent_end.recv()
        if not answer.get("ok"):
            receipt["child_failure"] = answer
        require(answer.get("ok"), "packaged_bootstrap_failed")
        receipt[label] = {field: value for field, value in answer.items() if field != "ok"}
        save()
        return process, parent_end

    def action(pair, name, **values):
        process, channel = pair
        require(process.poll() is None, "packaged_child_exited_early")
        channel.send({"action": name, **values})
        require(channel.poll(95), "packaged_action_deadline")
        answer = channel.recv()
        if not answer.get("ok"):
            receipt["child_failure"] = answer
        require(answer.get("ok"), "packaged_action_failed_" + name)
        if name == "close":
            require(process.wait(timeout=20) == 0, "packaged_child_nonzero_exit")
        return answer

    def submit(browser, command, expected=202):
        response = browser.post(f"/v1/devices/{device}/commands", content=canonical_json(command))
        require(response.status_code == expected, "unexpected_command_admission_status")
        return response.json()

    def complete(command):
        submit(alice, command)
        def poll():
            response = alice.get(f"/v1/devices/{device}/commands/{command.request_id}")
            require(response.status_code == 200, "owner_command_read_failed")
            body = response.json()
            return body if body["status"] != "queued" else None
        return wait_for(poll)

    def wait_ready():
        command = StatusCommand(request_id=uuid4())
        def ready():
            response = alice.post(f"/v1/devices/{device}/commands", content=canonical_json(command))
            require(response.status_code in (202, 503), "unexpected_ready_status")
            return response.status_code == 202
        wait_for(ready)
        require(complete(command)["reply"]["kind"] == "status", "ready_status_not_completed")

    def safe(answer):
        return {field: value for field, value in answer.items() if field not in ("credential", "private_secret", "code", "ok")}

    try:
        stage("verify latest first-party payload and disabled normal bundle manifest")
        manifest = json.loads((bundle / "bundle.json").read_bytes())
        require(manifest["remote_execution_enabled"] is False, "bundle_default_remote_enabled")
        site = bundle / "runtime/Lib/site-packages"
        packages = ("backend", "shared", "simulator", "scenarios", "evaluation", "optimization", "scripts", "companion")
        source_names, packaged_names = set(), set()
        for package in packages:
            for path in (root / package).rglob("*.py"):
                relative = path.relative_to(root).as_posix()
                if relative == "scripts/verify_coordinator.py":
                    continue
                source_names.add(relative)
                require((site / relative).is_file() and digest(path) == digest(site / relative), "latest_source_payload_mismatch")
            packaged_names.update(path.relative_to(site).as_posix() for path in (site / package).rglob("*.py"))
        require(source_names == packaged_names, "first_party_payload_file_set_changed")
        receipt.update(bundle_manifest_sha256=digest(bundle / "bundle.json"),
            project_wheel=manifest["project_wheel"], latest_first_party_python_files=len(source_names), latest_source_payload_matches=True)
        stage("create private localhost TLS fixture and separate source coordinator")
        spec = importlib.util.spec_from_file_location("private_validation_tls_fixture", root / "tests/test_relay_client.py")
        fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture)
        certificates = fixture.make_certificates(base)
        require(certificates["ca"] == cert and certificates["key"] == key, "fixture_certificate_path_changed")
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        origin = f"https://localhost:{port}"
        server_log = (base / "coordinator.log").open("w", encoding="utf-8")
        streams.append(server_log)
        coordinator = subprocess.Popen([str(root / ".venv/Scripts/python.exe"), "-B", "-m", "coordinator",
            "--database", str(database), "--origin", origin, "--port", str(port), "--certfile", str(certificates["cert"]), "--keyfile", str(key)],
            cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=server_log, stderr=subprocess.STDOUT, creationflags=flags)
        children.append(("coordinator", coordinator, None))
        context = ssl.create_default_context(cafile=cert)
        def healthy():
            require(coordinator.poll() is None, "coordinator_exited_early")
            try:
                with httpx.Client(verify=context, trust_env=False, timeout=1) as probe:
                    return probe.get(origin + "/healthz").status_code == 200
            except httpx.TransportError:
                return False
        wait_for(healthy, timeout=30)
        stage("authenticate two accounts with independent browser sessions")
        for username in ("packaged_relay_alice", "packaged_relay_bob"):
            browser = httpx.Client(base_url=origin, verify=context, trust_env=False, timeout=10,
                headers={"Origin": origin, "X-Poltergeist-Client": "browser", "Content-Type": "application/json"})
            browsers.append(browser)
            password = secrets.token_urlsafe(32)
            private_values.append(password)
            body = {"username": username, "password": password}
            require(browser.post("/v1/auth/register", json=body).status_code == 201, "browser_register_failed")
            response = browser.post("/v1/auth/login", json=body)
            require(response.status_code == 200, "browser_login_failed")
            browser.headers["X-CSRF-Token"] = response.json()["csrf_token"]
            private_values.append(response.json()["csrf_token"])
            private_values.extend(browser.cookies.values())
        alice, bob = browsers
        owner = alice.get("/v1/auth/session").json()["account"]["account_id"]
        stage("pair packaged companion through native Tk approval with native DPAPI")
        first = spawn("first_open")
        created = action(first, "begin")
        private_values.append(created["code"])
        require(alice.post("/v1/pairing/claim", json={"code": created["code"]}).status_code == 200, "owner_claim_failed")
        require(bob.post("/v1/pairing/claim", json={"code": created["code"]}).status_code == 400, "second_claim_allowed")
        approved = action(first, "approve", owner_id=owner)
        device, credential = approved["device_id"], approved["credential"]
        private_values.extend([credential, approved["private_secret"]])
        receipt["enrollment"] = safe(approved)
        recipe = validate_scenario({"name": "Packaged owned experiment", "duration_s": .2,
            "actors": [{"type": "ego_vehicle", "speed_mps": 2}]})
        command = ExecuteCommand(request_id=uuid4(), scenario=recipe, mode="test")
        stage("connect with unchecked consent and deny remote status and execution")
        receipt["paused_connection"] = action(first, "connect_paused")
        submit(alice, StatusCommand(request_id=uuid4()), expected=503)
        submit(alice, command, expected=503)
        require(action(first, "inspect")["jobs"] == 0, "paused_connection_created_job")
        receipt["initial_disconnect"] = action(first, "disconnect")
        receipt["consented_connection"] = action(first, "connect")
        wait_ready()
        stage("deny second-account reads execution result lookup and revocation")
        require(bob.get(f"/v1/devices/{device}").status_code == 404, "foreign_device_read_allowed")
        submit(bob, command, expected=404)
        require(bob.get(f"/v1/devices/{device}/commands/{command.request_id}").status_code == 404, "foreign_command_read_allowed")
        require(bob.post(f"/v1/devices/{device}/revoke", json={}).status_code == 404, "foreign_revoke_allowed")
        require(action(first, "inspect")["jobs"] == 0, "denied_owner_created_job")
        receipt["two_account_isolation"] = True
        stage("execute through outgoing WSS lose acceptance ACK and resolve original durable run")
        accepted = complete(command)
        require(accepted["status"] == "accepted", "execution_not_accepted")
        simulation_id = accepted["simulation_id"]
        require(complete(command) == accepted, "request_retry_changed_identity")
        state = action(first, "inspect")
        require(state["jobs"] == 1 and state["receipts"] == 1 and state["dropped_ack_run_ids"] == [simulation_id], "lost_ack_created_duplicate_job")
        terminal = action(first, "terminal", simulation_id=simulation_id)
        require(terminal["status"] == "completed" and terminal["telemetry_frames"] == 5 and terminal["artifact_count"] > 0, "packaged_run_not_finalized")
        result_request = ResultCommand(request_id=uuid4(), simulation_id=simulation_id)
        result = complete(result_request)
        summary = result["reply"]["summary"]
        require(summary["status"] == "completed" and summary["evidence"]["state"] == "local_only"
            and summary["metrics"]["coverage"]["total_frames"] == 5, "public_result_missing_local_evidence")
        public = json.dumps(result)
        require(all(value not in public for value in (str(profile), credential, "agent_snapshot", "frames.ndjson")), "private_result_disclosure")
        submit(bob, result_request, expected=404)
        receipt.update(execute_request_id=str(command.request_id), simulation_id=simulation_id,
            lost_ack_retry_same_run=True, single_job_after_retry=True, finalized=terminal, public_summary=summary)
        stage("stop runtime resets consent and restart does not connect automatically")
        receipt["runtime_restart"] = action(first, "restart_runtime")
        action(first, "connect_paused")
        for paused in (StatusCommand(request_id=uuid4()),
                ExecuteCommand(request_id=uuid4(), scenario=recipe, mode="test"),
                ResultCommand(request_id=uuid4(), simulation_id=simulation_id),
                CancelCommand(request_id=uuid4(), simulation_id=simulation_id)):
            submit(alice, paused, expected=503)
        require(action(first, "inspect")["jobs"] == 1, "paused_connection_created_second_job")
        action(first, "disconnect")
        action(first, "connect")
        wait_ready()
        receipt["paused_connection_denied_all_operations"] = True
        stage("cancel queued run and cooperatively cancel active synthetic blocker")
        blocker = ExecuteCommand(request_id=uuid4(), scenario=recipe.model_copy(update={"name": "Proof blocker"}), mode="test")
        blocked = complete(blocker)
        wait_for(lambda: action(first, "inspect")["blocker_entered"])
        queued = complete(ExecuteCommand(request_id=uuid4(), scenario=recipe, mode="test"))
        cancel_queued = complete(CancelCommand(request_id=uuid4(), simulation_id=queued["simulation_id"]))
        require(cancel_queued["reply"]["terminal"] is True, "queued_cancel_not_terminal")
        cancelled = action(first, "terminal", simulation_id=queued["simulation_id"])
        require(cancelled["status"] == "cancelled" and cancelled["telemetry_frames"] == 0, "queued_cancel_constructed_runner")
        cancel_active = complete(CancelCommand(request_id=uuid4(), simulation_id=blocked["simulation_id"]))
        require(cancel_active["reply"]["terminal"] is False, "active_cancel_misreported_terminal")
        action(first, "release")
        active = action(first, "terminal", simulation_id=blocked["simulation_id"])
        require(active["status"] == "cancelled", "cooperative_cancel_not_finalized")
        state = action(first, "inspect")
        require(state["jobs"] == 3 and state["receipts"] == 5, "cancellation_receipt_count_wrong")
        receipt["cancellation"] = {"queued": cancelled, "active": active, "jobs": state["jobs"], "receipts": state["receipts"]}
        stage("close and reopen real bundle process retaining DPAPI enrollment receipts and results")
        receipt["first_close"] = action(first, "close")
        second = spawn("second_open")
        resumed = action(second, "resume")
        require(resumed["credential"] == credential and resumed["device_id"] == device
            and resumed["jobs"] == 3 and resumed["receipts"] == 5, "reopen_changed_durable_identity")
        receipt["resume"] = safe(resumed)
        action(second, "connect")
        wait_ready()
        require(complete(command) == accepted, "restart_retry_changed_execution")
        reread = complete(ResultCommand(request_id=uuid4(), simulation_id=simulation_id))
        require(reread["reply"]["summary"] == summary, "restart_changed_finalized_public_result")
        receipt["reopen_result_and_request_identity_preserved"] = True
        stage("revoke owner credential and refuse remote reads execution and reconnect")
        require(alice.post(f"/v1/devices/{device}/revoke", json={}).status_code == 200, "owner_revoke_failed")
        submit(alice, command, expected=404)
        require(alice.get(f"/v1/devices/{device}/commands/{result_request.request_id}").status_code == 404, "revoked_result_read_allowed")
        with httpx.Client(base_url=origin, verify=context, trust_env=False, timeout=10) as device_client:
            require(device_client.get("/v1/device/session", headers={"Authorization": "Bearer " + credential}).status_code == 401,
                "revoked_device_credential_accepted")
        receipt["revoked_reconnect"] = action(second, "revoked_reconnect")
        require(action(second, "terminal", simulation_id=simulation_id)["status"] == "completed", "revocation_deleted_local_result")
        receipt["second_close"] = action(second, "close")
        receipt["revocation_denied_and_local_results_retained"] = True
        require(all(digest(root / relative) == digest(site / relative) for relative in source_names), "source_changed_during_private_proof")
        stage("scan owned logs and report for private enrollment and browser secrets")
        for stream in streams:
            stream.flush()
        for path in base.rglob("*.log"):
            raw = path.read_bytes()
            require(all(value.encode() not in raw for value in private_values), "secret_in_owned_log")
        save()
        raw = receipt_path.read_bytes()
        require(all(value.encode() not in raw for value in private_values), "secret_in_receipt")
        receipt["no_private_credentials_in_logs_or_receipt"] = True
        receipt["passed"] = True
        stage("complete")
        return 0
    except BaseException as error:
        receipt["error_type"] = type(error).__name__
        if isinstance(error, ModuleNotFoundError) and re.fullmatch(r"[A-Za-z0-9_.]+", error.name or ""):
            receipt["missing_module"] = error.name
        receipt["error_code"] = str(error) if type(error) in (RuntimeError, TimeoutError) and re.fullmatch(r"[a-z_]+", str(error)) else "validation_failed"
        return 1
    finally:
        for browser in browsers:
            browser.close()
        outcomes = []
        for label, process, channel in reversed(children):
            terminated = process.poll() is None
            if terminated:
                process.terminate()
            try:
                code = process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                code = process.wait(timeout=10)
            if channel is not None:
                channel.close()
            outcomes.append({"label": label, "exit_code": code, "exited": process.poll() is not None,
                             "terminated_owned_fixture": terminated})
        for stream in streams:
            stream.close()
        owned_keys = [base / "relay-certificates/ca.key", key]
        for owned_key in owned_keys:
            if owned_key.is_file():
                require(owned_key.resolve().parent == base / "relay-certificates", "key_cleanup_path_changed")
                owned_key.unlink()
        receipt.update(owned_processes=outcomes, development_private_key_removed=all(not path.exists() for path in owned_keys),
                       finished_at=datetime.now(UTC).isoformat())
        save()
        print(json.dumps({"passed": receipt["passed"], "receipt": str(receipt_path)}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--profile")
    parser.add_argument("--origin")
    parser.add_argument("--cert")
    parser.add_argument("--control-handle", type=int)
    args = parser.parse_args()
    return child_main(args) if args.child else parent_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
