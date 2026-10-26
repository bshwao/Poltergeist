"""Private bundle/TLS/DPAPI proof through normal native Tk consent callbacks.

Credentials and human pairing codes travel through inherited Windows pipes only.
This driver creates fresh workspace-owned profiles and a disposable coordinator,
or verifies an unused profile compiled into a separate validation installation.
It never opens existing settings, starts CARLA, installs, or changes product code.
"""

import argparse
import configparser
from contextlib import closing, contextmanager
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
import sqlite3
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


def scan_owned_logs(base, profile, private_values):
    """An installed profile is outside the fixture folder; scan both roots."""
    logs = set(base.rglob("*.log")) | set(profile.rglob("*.log"))
    for path in logs:
        require(not path.is_symlink() and (path.resolve().is_relative_to(base.resolve())
            or path.resolve().is_relative_to(profile.resolve())), "log_outside_owned_roots")
        raw = path.read_bytes()
        require(all(value.encode() not in raw for value in private_values), "secret_in_owned_log")
    return {"roots": [str(base), str(profile)], "files": len(logs),
        "profile_log_files": sum(path.is_relative_to(profile) for path in logs),
        "all_profile_logs_scanned": True}


def installed_validation_profile(root, bundle, installed_root):
    """Accept only an unused profile compiled into our isolated test installer."""
    identity = "Poltergeist.Private.Companion.Validation"
    evidence = root / "validation-evidence"

    def plain_owned(path, parent):
        require(path.is_absolute(), "installed_path_must_be_absolute")
        require(path.resolve().is_relative_to(parent.resolve()) and path.resolve() != parent.resolve(),
            "installed_path_outside_owned_validation")
        for item in (path, *path.parents):
            if item.exists():
                require(not item.is_symlink() and not (getattr(item.lstat(), "st_file_attributes", 0) & 0x400),
                    "installed_path_is_linked")
            if item == root:
                break
        return path.resolve()

    def ini(path, section):
        require(path.is_file(), "installed_ownership_metadata_missing")
        raw = path.read_bytes()
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(raw.decode("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"))
        require(parser.has_section(section), "installed_ownership_section_missing")
        return parser[section]

    installed_root = plain_owned(Path(installed_root), evidence / "portable-dist")
    metadata_path = installed_root / "installation.ini"
    plain_owned(metadata_path, installed_root)
    metadata = ini(metadata_path, "Installation")
    build = "private-" + digest(bundle / "bundle.json")[:16]
    require(metadata.get("AppId") == identity and metadata.get("Build") == build,
        "installed_identity_or_build_mismatch")
    require(bundle == installed_root / "versions" / build, "installed_runtime_mismatch")
    plain_owned(bundle, installed_root)
    profile = plain_owned(Path(metadata.get("Profile", "")), evidence)
    require(profile.name == "CompanionPrototype" and not profile.is_relative_to(installed_root)
        and not installed_root.is_relative_to(profile), "installed_profile_not_separate")
    marker_path = profile / "installation-owner.ini"
    plain_owned(marker_path, profile)
    owner = ini(marker_path, "Owner")
    require(owner.get("AppId") == identity and owner.get("Profile") == str(profile),
        "installed_profile_ownership_mismatch")
    require({path.name for path in profile.iterdir()} == {marker_path.name},
        "installed_profile_already_used")
    return profile, {"app_id": identity, "build_id": build, "install_root": str(installed_root),
        "installation_ini_sha256": digest(metadata_path), "profile_owner_sha256": digest(marker_path),
        "compiled_profile_used": True, "initial_profile_entries": [marker_path.name],
        "shortcut_invoked": False}


@contextmanager
def suspend_owned_process(process):
    """Suspend only our live Popen child, retaining its authoritative OS handle."""
    import ctypes
    from ctypes import wintypes

    require(os.name == "nt" and process.poll() is None, "suspension_requires_owned_live_windows_child")
    native = ctypes.WinDLL("ntdll")
    for operation in (native.NtSuspendProcess, native.NtResumeProcess):
        operation.argtypes = [wintypes.HANDLE]
        operation.restype = ctypes.c_long
    suspended = False
    witness = {"process_id": process.pid, "physical_pc_sleep": False}
    try:
        status = native.NtSuspendProcess(int(process._handle))
        require(status >= 0, "owned_process_suspension_failed")
        suspended = True
        witness["suspended_at"] = datetime.now(UTC).isoformat()
        yield witness
    finally:
        if suspended:
            status = native.NtResumeProcess(int(process._handle))
            witness["resume_status"] = status
            witness["resumed_at"] = datetime.now(UTC).isoformat()
            require(status >= 0, "owned_process_resume_failed")


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
        lose_pairing_response = {"approve": False, "cancel-pending-approval": False}

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
                operation = path.rsplit("/", 1)[-1]
                event = {"method": method, "operation": operation if operation in ("approve", "session", "cancel-pending-approval") else "pairing"}
                transport_events.append(event)
                try:
                    result = super().request(method, path, **values)
                except PairingClientError as error:
                    status = re.fullmatch(r"The coordinator refused enrollment \(HTTP ([1-5][0-9]{2})\)", str(error))
                    event["error"] = "http_" + status[1] if status else "secure_or_invalid_response"
                    raise
                event["response_fields"] = sorted(result) if isinstance(result, dict) else []
                if lose_pairing_response.get(operation):
                    lose_pairing_response[operation] = False
                    event["lost_committed_response"] = True
                    # Keep real strict HTTPS and process cleanup. Only hide the
                    # successful outcome from the normal approval/cancel caller.
                    raise PairingClientError("Injected lost committed enrollment response")
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

        def pump_until_idle(*, closing=False, expected_action_error=False):
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if closing and window._destroyed:
                    return
                root.update()
                require(not callback_errors or (expected_action_error and callback_errors == ["action_error"]), "native_callback_failed")
                if not closing and window._future is None:
                    if expected_action_error:
                        require(callback_errors == ["action_error"], "expected_native_action_error_missing")
                        callback_errors.clear()
                    return
                time.sleep(.01)
            raise TimeoutError("native_action_deadline")

        first_run_setup = not controller.config.setup_completed
        if first_run_setup:
            checkpoint("complete first-run synthetic setup through native callback")
            buttons = [button for button in window._buttons if button.cget("text") == "Use synthetic for now"]
            require(len(buttons) == 1, "native_synthetic_setup_button_missing")
            buttons[0].invoke()
            pump_until_idle()
            require(controller.config.setup_completed, "native_synthetic_setup_not_saved")
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

        def protection(expected_state="paired"):
            record = WindowsCredentialStore(profile).load()
            require(record and record["state"] == expected_state, "protected_enrollment_state_changed")
            credential, secret = record["approval"]["credential"], record["private_secret"]
            blob = (profile / "remote-enrollment.bin").read_bytes()
            require(credential.encode() not in blob and secret.encode() not in blob, "plaintext_enrollment")
            snapshot = json.dumps(controller.snapshot())
            require(credential not in snapshot and secret not in snapshot, "secret_in_controller_projection")
            return {"device_id": record["created"]["device_id"], "owner_id": record["approval"]["expected_account_id"],
                "credential": credential, "private_secret": secret, "protected_blob_bytes": len(blob),
                "protected_native_dpapi": True, "controller_projection_redacted": True,
                "saved_state": record["state"], "approval_request_id": record["approval"]["request_id"]}

        checkpoint("native bootstrap ready")
        channel.send({"ok": True, "provenance": provenance, "sys_path": list(sys.path),
            "python_version": sys.version.split()[0], "integrity_before": integrity,
            "coordinator_available": False, "argon2_available": False, "normal_constructor_remote_enabled": False,
            "native_window_withdrawn": root.state() == "withdrawn", "normal_constructor_and_tk_callbacks": True,
            "native_first_run_synthetic_setup": first_run_setup,
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
            elif stage in ("approve", "approve_lost"):
                if stage == "approve_lost":
                    require(args.exercise_pending_approval, "pending_approval_mode_not_enabled")
                window.pairing.refresh_button.invoke()
                pump_until_idle()
                displayed = window.pairing._displayed
                require(displayed["claim"]["claimed_account"]["account_id"] == command["owner_id"], "claim_owner_changed")
                lose_pairing_response["approve"] = stage == "approve_lost"
                window.pairing.approve_button.invoke()
                pump_until_idle(expected_action_error=stage == "approve_lost")
                if stage == "approve_lost":
                    require(not lose_pairing_response["approve"], "approval_loss_hook_not_reached")
                    window._apply_snapshot(controller.snapshot())
                expected_state = "pending_approval" if stage == "approve_lost" else "paired"
                require(window.pairing._displayed["state"] == expected_state, "native_approval_state_changed")
                require(not window.pairing.allow_remote_execution.get(), "approval_enabled_session_consent")
                require(not controller.relay_status()["owned"], "approval_connected_without_consent")
                response.update(protection(expected_state))
                if stage == "approve_lost":
                    require("revoked" in window.pairing.summary.get(), "pending_cancel_revocation_not_explained")
                    require(not window.pairing.cancel_approval_button.instate(["disabled"]), "pending_cancel_control_disabled")
                    response.update(approval_reply_lost=True, pending_cancel_revocation_explained=True, consent_off=True)
            elif stage in ("cancel_approval_lost", "cancel_approval"):
                require(args.exercise_pending_approval, "pending_approval_mode_not_enabled")
                store = WindowsCredentialStore(profile)
                before = store.load()
                require(before and before["state"] == "pending_approval", "pending_approval_custody_missing")
                before_blob = store.path.read_bytes()
                require(not window.pairing.cancel_approval_button.instate(["disabled"]), "pending_cancel_control_disabled")
                lose_pairing_response["cancel-pending-approval"] = stage == "cancel_approval_lost"
                window.pairing.cancel_approval_button.invoke()
                pump_until_idle(expected_action_error=stage == "cancel_approval_lost")
                window._apply_snapshot(controller.snapshot())
                require(not window.pairing.allow_remote_execution.get() and not controller.relay_status()["owned"], "pending_cancel_enabled_consent")
                if stage == "cancel_approval_lost":
                    require(not lose_pairing_response["cancel-pending-approval"], "cancel_loss_hook_not_reached")
                    require(store.load() == before and store.path.read_bytes() == before_blob, "lost_cancel_reply_removed_protected_custody")
                    require(window.pairing._displayed["state"] == "pending_approval", "lost_cancel_reply_changed_native_state")
                    response.update(protection("pending_approval"), cancellation_reply_lost=True, protected_custody_preserved=True, consent_off=True)
                else:
                    require(store.load() is None and not store.path.exists(), "confirmed_cancel_retained_protected_custody")
                    require(window.pairing._displayed["state"] == "unpaired", "confirmed_cancel_not_unpaired")
                    response.update(confirmed_cancel=True, protected_custody_removed=True, consent_off=True)
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
    installed = None
    if args.installed_root:
        profile, installed = installed_validation_profile(root, bundle, args.installed_root)
    base = root / "validation-evidence" / ("s4-packaged-consent-" + datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:8])
    base.mkdir()
    if installed is None:
        profile = base / "CompanionPrototype"
    cert, key, database = base / "relay-certificates/ca.pem", base / "relay-certificates/server.key", base / "coordinator.db"
    receipt_path = base / "receipt.json"
    receipt = {"schema_version": "poltergeist-s4-packaged-consent-private-v1", "passed": False,
        "driver_sha256": digest(Path(__file__)),
        "started_at": datetime.now(UTC).isoformat(), "bundle": str(bundle), "profile": str(profile), "steps": [],
        "scope": "One packaged companion profile, two source-coordinator accounts, real loopback HTTPS/WSS, native DPAPI and receipt-backed synthetic worker.",
        "limitations": ["Native Tk callbacks are invoked on a withdrawn window; no visual or physical-input claim.",
            "Same Windows account and machine; development-only fixture certificate; no public hosting or independent-machine proof.",
            "Synthetic test adapter only, with a bounded blocker for cancellation; no CARLA or physical hardware proof.",
            "Coordinator runs separately from the source environment, not from the desktop bundle.",
            "This is partial private S4 evidence and does not complete the S4 acceptance gate."],
        "default_remote_execution_enabled": False, "private_constructor_switch": False,
        "normal_controller_explicit_tk_consent": True, "public_deployment_verified": False,
        "exercise_pending_approval": args.exercise_pending_approval}
    if installed is not None:
        receipt["installed_validation"] = installed
        receipt["limitations"].append("Installed runtime and compiled validation profile; no actual shortcut invocation or visual first-run acceptance.")
    else:
        receipt["limitations"].append("Portable runtime and fresh custom profile; no installer acceptance.")
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
                "--cert", str(cert), "--control-handle", str(handle),
                *(["--exercise-pending-approval"] if args.exercise_pending_approval else [])], cwd=base, env=isolated,
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
        if args.exercise_pending_approval:
            stage("recover lost committed approval and cancellation replies through native Cancel saved approval")
            uncertain = action(first, "begin")
            private_values.append(uncertain["code"])
            require(alice.post("/v1/pairing/claim", json={"code": uncertain["code"]}).status_code == 200, "pending_owner_claim_failed")
            pending = action(first, "approve_lost", owner_id=owner)
            private_values.extend([pending["credential"], pending["private_secret"]])
            pending_device = pending["device_id"]
            require(pending["saved_state"] == "pending_approval" and pending["approval_reply_lost"]
                and pending["consent_off"] and pending["pending_cancel_revocation_explained"], "lost_approval_not_reviewable")
            approved_device = alice.get(f"/v1/devices/{pending_device}")
            require(approved_device.status_code == 200 and approved_device.json()["revoked_at"] is None
                and approved_device.json()["owner"]["account_id"] == owner, "lost_approval_not_committed")
            require(bob.get(f"/v1/devices/{pending_device}").status_code == 404, "pending_device_foreign_read_allowed")
            with httpx.Client(base_url=origin, verify=context, trust_env=False, timeout=10) as device_probe:
                require(device_probe.get("/v1/device/session", headers={"Authorization": "Bearer " + pending["credential"]}).status_code == 200,
                    "committed_pending_credential_not_active")
                cancelled_reply = action(first, "cancel_approval_lost")
                require(cancelled_reply["credential"] == pending["credential"] and cancelled_reply["device_id"] == pending_device
                    and cancelled_reply["approval_request_id"] == pending["approval_request_id"]
                    and cancelled_reply["protected_custody_preserved"] and cancelled_reply["cancellation_reply_lost"]
                    and cancelled_reply["consent_off"], "lost_cancel_reply_changed_protected_binding")
                require(device_probe.get("/v1/device/session", headers={"Authorization": "Bearer " + pending["credential"]}).status_code == 401,
                    "pending_cancel_left_active_credential")
            revoked = alice.get("/v1/devices")
            require(revoked.status_code == 200 and len(revoked.json()) == 1 and revoked.json()[0]["device_id"] == pending_device
                and revoked.json()[0]["revoked_at"] is not None, "pending_cancel_did_not_revoke_exact_device")
            cleared = action(first, "cancel_approval")
            require(cleared["confirmed_cancel"] and cleared["protected_custody_removed"] and cleared["consent_off"], "pending_cancel_retry_not_cleared")
            require(action(first, "inspect")["jobs"] == 0, "pending_approval_recovery_created_work")
            receipt["pending_approval_recovery"] = {"lost_approval": safe(pending), "lost_cancellation": safe(cancelled_reply),
                "confirmed_retry": cleared, "bound_device_revoked": True, "original_credential_denied": True,
                "no_local_jobs": True, "tls_verified": True}
            receipt["limitations"].append("Approval/cancellation response loss is injected after strict HTTPS commits and worker cleanup, before normal native callers receive the result.")
            stage("create a fresh normal pairing after explicit pending approval recovery")
        created = action(first, "begin")
        private_values.append(created["code"])
        require(alice.post("/v1/pairing/claim", json={"code": created["code"]}).status_code == 200, "owner_claim_failed")
        require(bob.post("/v1/pairing/claim", json={"code": created["code"]}).status_code == 400, "second_claim_allowed")
        approved = action(first, "approve", owner_id=owner)
        device, credential = approved["device_id"], approved["credential"]
        private_values.extend([credential, approved["private_secret"]])
        receipt["enrollment"] = safe(approved)
        if args.exercise_pending_approval:
            require(device != pending_device and credential != pending["credential"], "fresh_pairing_reused_cancelled_identity")
            receipt["pending_approval_recovery"].update(fresh_device_id=device, fresh_pairing_distinct=True)
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
        if args.exercise_suspension:
            stage("suspend only the owned packaged process beyond the production heartbeat deadline")
            def device_state():
                with closing(sqlite3.connect(database)) as connection:
                    generation, seen = connection.execute("SELECT connection_generation,last_seen_at FROM devices WHERE id=?",
                        (device,)).fetchone()
                    return {"connection_generation": generation, "last_seen_at": seen}

            before = device_state()
            pending_result = ResultCommand(request_id=uuid4(), simulation_id=simulation_id)
            suspended_at = time.monotonic()
            with suspend_owned_process(first[0]) as suspension:
                receipt["native_process_suspension"] = suspension
                save()
                submit(alice, pending_result)
                # Allow frames already received by the server before suspension
                # to settle, then verify its own heartbeat does not invent activity.
                while time.monotonic() - suspended_at < 5:
                    require(first[0].poll() is None, "suspended_owned_child_exited")
                    time.sleep(.25)
                settled = device_state()
                while time.monotonic() - suspended_at < 100:
                    require(first[0].poll() is None, "suspended_owned_child_exited")
                    time.sleep(.25)
                offline = alice.get("/v1/browser/devices")
                device_view = next((row for row in offline.json() if row["device"]["device_id"] == device), None) if offline.status_code == 200 else None
                require(device_view and device_view["connection"]["state"] == "offline", "suspended_device_not_offline")
                after_gap = device_state()
                require(after_gap["last_seen_at"] == settled["last_seen_at"], "heartbeat_invented_suspended_activity")
                pending = alice.get(f"/v1/devices/{device}/commands/{pending_result.request_id}")
                require(pending.status_code == 200 and pending.json()["status"] == "queued", "suspended_reply_falsely_completed")
                submit(alice, StatusCommand(request_id=uuid4()), expected=503)
                suspension.update(elapsed_s=time.monotonic() - suspended_at, before=before,
                    settled=settled, after_gap=after_gap, offline=True, pending_result_still_queued=True)
            wait_ready()
            after_resume = device_state()
            require(after_resume["connection_generation"] > before["connection_generation"], "suspended_socket_generation_reused")
            resumed_result = complete(pending_result)
            require(resumed_result["reply"]["summary"]["simulation_id"] == simulation_id
                and resumed_result["reply"]["summary"]["status"] == "completed", "resumed_result_identity_changed")
            require(complete(command) == accepted, "suspension_retry_changed_original_ack")
            resumed_inventory = action(first, "inspect")
            require(resumed_inventory["jobs"] == 1 and resumed_inventory["receipts"] == 1, "suspension_duplicated_local_work")
            suspension.update(after_resume=after_resume, original_ack_preserved=True,
                pending_result_reconciled=True, original_jobs=1, original_receipts=1)
            receipt["limitations"].append("Native process suspension exceeds 90 seconds; physical PC sleep, NIC loss and hardware resume remain untested.")
            save()
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
        require(cancel_queued["reply"]["terminal"] is False, "queued_cancel_ack_predicted_cleanup")
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
        require(digest(Path(__file__)) == receipt["driver_sha256"], "driver_changed_during_private_proof")
        stage("scan owned logs and report for private enrollment and browser secrets")
        for stream in streams:
            stream.flush()
        receipt["log_scan"] = scan_owned_logs(base, profile, private_values)
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
    parser.add_argument("--installed-root", help="Verify a workspace validation installation and use its unused compiled profile")
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--profile")
    parser.add_argument("--origin")
    parser.add_argument("--cert")
    parser.add_argument("--control-handle", type=int)
    parser.add_argument("--exercise-suspension", action="store_true",
        help="Suspend only the owned Windows child for 100 seconds and verify reconnect/durable readback")
    parser.add_argument("--exercise-pending-approval", action="store_true",
        help="Lose committed approval/cancellation replies, recover through the native UI, then pair afresh")
    args = parser.parse_args()
    return child_main(args) if args.child else parent_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
