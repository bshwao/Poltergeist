"""The wheel verifier uses real services, preserves evidence and fails closed."""

import json

import pytest

from scripts.verify_coordinator import CoordinatorVerificationError, verify


def test_offline_native_coordinator_smoke_and_durable_evidence(tmp_path):
    output = tmp_path / "new-coordinator-evidence"
    report = verify(output)
    assert report["passed"] and report["stage"] == "complete"
    assert report["checks"] == {
        "native_argon2_authentication": True,
        "local_approval_required": True,
        "two_account_owner_isolation": True,
        "durable_owner_revocation": True,
        "independent_browser_sessions": True,
        "no_plaintext_password_or_bearer_in_storage": True,
        "no_local_execution_runtime_imported": True,
    }
    assert report["modules"]["_argon2_cffi_bindings._ffi"].endswith((".pyd", ".so"))
    assert report["modules"]["_cffi_backend"].endswith((".pyd", ".so"))
    assert json.loads((output / "coordinator-verification.json").read_bytes()) == report
    assert (output / "coordinator.db").is_file()


@pytest.mark.parametrize("existing", ["empty", "database", "evidence"])
def test_existing_output_is_refused_without_mutation(tmp_path, existing):
    output = tmp_path / "existing"
    output.mkdir()
    if existing != "empty":
        name = "coordinator.db" if existing == "database" else "coordinator-verification.json"
        (output / name).write_bytes(b"preserve existing user bytes")
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    with pytest.raises(ValueError, match="Existing evidence"):
        verify(output)
    assert {path.name: path.read_bytes() for path in output.iterdir()} == before


def test_native_hash_failure_is_sanitized_and_never_reported_as_success(tmp_path, monkeypatch):
    from coordinator.auth import AuthService

    def fail_native_hash(*args):
        raise RuntimeError("private password sentinel must never reach evidence")

    monkeypatch.setattr(AuthService, "_hash", fail_native_hash)
    output = tmp_path / "failed-native-check"
    with pytest.raises(CoordinatorVerificationError) as error:
        verify(output)
    evidence = (output / "coordinator-verification.json").read_text(encoding="utf-8")
    report = json.loads(evidence)
    assert not report["passed"] and report["error_type"] == "RuntimeError"
    assert "sentinel" not in evidence + str(error.value)
