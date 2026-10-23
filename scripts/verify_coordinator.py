"""Offline coordinator wheel smoke; no HTTP server, local runtime or simulator."""

import argparse
from datetime import UTC, datetime
import importlib
from importlib.metadata import version
import json
from pathlib import Path
import secrets
import sys
from uuid import uuid4


class CoordinatorVerificationError(RuntimeError):
    """A failed smoke checkpoint; credentials are never included in diagnostics."""


def _require(condition, message):
    if not condition:
        raise CoordinatorVerificationError(message)


def _reserve_output(value):
    path = Path(value).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
            raise ValueError("Choose an ordinary new evidence directory")
    if not path.parent.is_dir():
        raise ValueError("Choose a new evidence directory under an existing parent")
    try:
        path.mkdir()
    except FileExistsError:
        raise ValueError("Existing evidence will not be overwritten; choose a new directory") from None
    return path


def verify(output):
    """Exercise real native password hashing and durable owner-scoped pairing."""
    output = _reserve_output(output)
    database = output / "coordinator.db"
    report = {"schema_version": "poltergeist-coordinator-wheel-smoke-v1", "passed": False,
              "checked_at": datetime.now(UTC).isoformat(), "python": sys.executable,
              "scope": "Offline native Argon2 authentication, two-account pairing, ownership and revocation; no transport or execution.",
              "checks": {}}
    store = None
    modules_before = set(sys.modules)
    try:
        from coordinator.auth import AuthError, AuthService
        from coordinator.pairing import PairingService
        from coordinator.store import Store
        from shared.models.coordination import DevicePolicy, PairingApproveInput, PairingCreateInput

        report["stage"] = "initialize separate fresh storage"
        with database.open("xb"):
            pass
        store = Store(database)
        store.initialize()
        auth = AuthService(store)
        pairing = PairingService(store, auth)

        def denied(action, status):
            try:
                action()
            except AuthError as error:
                _require(error.status == status, "A security refusal returned an unexpected status")
            else:
                raise CoordinatorVerificationError("A security boundary unexpectedly allowed access")

        report["stage"] = "native Argon2 authentication"
        passwords = ["smoke-" + secrets.token_urlsafe(32) for _ in range(2)]
        accounts = [auth.register(name, password) for name, password in zip(("wheel_alice", "wheel_bob"), passwords)]
        logins = [auth.login(account["username"], password) for account, password in zip(accounts, passwords)]
        for account, login in zip(accounts, logins):
            _require(auth.session(login["session_token"])["account_id"] == account["account_id"], "Native login/session identity differs")
        denied(lambda: auth.login("wheel_alice", "this is deliberately the wrong password"), 401)
        with store.transaction(write=False) as connection:
            hashes = [row[0] for row in connection.execute("SELECT password_hash FROM accounts ORDER BY username")]
        _require(len(hashes) == 2 and all(encoded.startswith("$argon2id$") for encoded in hashes), "Native Argon2id hashes were not persisted")
        report["checks"]["native_argon2_authentication"] = True

        report["stage"] = "explicit local approval and owner isolation"
        private_values = passwords + [login["session_token"] for login in logins]

        def enroll(owner, other):
            private = "pa_" + secrets.token_urlsafe(32)
            credential = "pd_" + secrets.token_urlsafe(32)
            created = pairing.create(PairingCreateInput(private_secret=private, display_name="Wheel smoke device",
                requested_scopes=["status:read", "run:execute:test"]), source="wheel-smoke")
            before = len(pairing.devices(owner))
            claim = pairing.claim(owner, created.code, source="wheel-smoke")
            _require(len(pairing.devices(owner)) == before, "Claiming a code registered a device before local approval")
            denied(lambda: pairing.claim(other, created.code, source="wheel-smoke-other"), 400)
            polled = pairing.poll(created.attempt_id, private)
            _require(polled.claimed_account.account_id == claim.claimed_account.account_id, "Private approval owner changed")
            request = PairingApproveInput(request_id=uuid4(), expected_account_id=owner,
                expected_revision=polled.revision, credential=credential,
                policy=DevicePolicy(scopes=["status:read", "run:execute:test"]))
            device = pairing.approve(created.attempt_id, private, request)
            _require(str(device.owner.account_id) == owner, "Approved device belongs to a different account")
            _require(pairing.authenticate_device(credential)["owner_id"] == owner, "Device credential authenticated a different owner")
            denied(lambda: pairing.device(other, device.device_id), 404)
            denied(lambda: pairing.revoke(other, device.device_id), 404)
            private_values.extend((private, credential, created.code))
            return device, credential

        alice, bob = (account["account_id"] for account in accounts)
        first, first_credential = enroll(alice, bob)
        second, second_credential = enroll(bob, alice)
        _require([str(device.device_id) for device in pairing.devices(alice)] == [str(first.device_id)], "First account's device list crossed owners")
        _require([str(device.device_id) for device in pairing.devices(bob)] == [str(second.device_id)], "Second account's device list crossed owners")
        report["checks"]["local_approval_required"] = True
        report["checks"]["two_account_owner_isolation"] = True

        report["stage"] = "durable revoke and session separation"
        pairing.revoke(alice, first.device_id)
        denied(lambda: pairing.authenticate_device(first_credential), 401)
        _require(pairing.authenticate_device(second_credential)["owner_id"] == bob, "Revocation affected another owner's credential")
        store.close()
        store = Store(database)
        store.initialize()
        auth = AuthService(store)
        pairing = PairingService(store, auth)
        denied(lambda: pairing.authenticate_device(first_credential), 401)
        _require(pairing.authenticate_device(second_credential)["owner_id"] == bob, "Device identity did not survive store reopening")
        auth.logout(logins[0]["session_token"])
        denied(lambda: auth.session(logins[0]["session_token"]), 401)
        _require(auth.session(logins[1]["session_token"])["account_id"] == bob, "Logout affected another account")
        report["checks"]["durable_owner_revocation"] = True
        report["checks"]["independent_browser_sessions"] = True

        store.close()
        store = None
        raw = b"".join(path.read_bytes() for path in output.glob("coordinator.db*"))
        _require(not any(value.encode("utf-8") in raw for value in private_values), "Plaintext authentication or pairing secret was persisted")
        report["checks"]["no_plaintext_password_or_bearer_in_storage"] = True
        names = ("coordinator", "coordinator.auth", "coordinator.pairing", "shared.models.coordination",
                 "argon2", "_argon2_cffi_bindings._ffi", "_cffi_backend")
        report["modules"] = {name: importlib.import_module(name).__file__ for name in names}
        report["versions"] = {name: version(name) for name in ("argon2-cffi", "argon2-cffi-bindings", "cffi")}
        imported = set(sys.modules) - modules_before
        _require(not any(name.split(".", 1)[0] in {"backend", "simulator", "carla"} for name in imported),
                 "Coordinator verification imported a local execution runtime")
        report["checks"]["no_local_execution_runtime_imported"] = True
        report["passed"] = True
        report["stage"] = "complete"
    except Exception as error:
        # Native/package exceptions may contain input values. Retain only their
        # type and the fixed checkpoint above, never exception text or traceback.
        report["error_type"] = type(error).__name__
    finally:
        if store is not None:
            store.close()
        with (output / "coordinator-verification.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
    if not report["passed"]:
        raise CoordinatorVerificationError("Coordinator verification failed; inspect its sanitized evidence") from None
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New evidence directory under an existing parent")
    args = parser.parse_args(argv)
    try:
        report = verify(args.output)
    except (OSError, ValueError, CoordinatorVerificationError):
        print("Coordinator verification failed or the evidence path was refused; existing evidence was preserved.", file=sys.stderr)
        return 1
    print(json.dumps({"passed": report["passed"], "evidence": str(args.output.absolute())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
