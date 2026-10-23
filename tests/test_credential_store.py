"""Protected atomic enrollment, with a real Windows DPAPI round trip."""

import hashlib
import hmac
import os

import pytest

from companion.credential_store import CredentialStoreError, MAX_CLEAR_BYTES, WindowsCredentialStore


@pytest.fixture
def primitives():
    # A deterministic test codec with entropy binding, never a production fallback.
    def protect(value, entropy):
        return hmac.digest(entropy, value, "sha256") + bytes(byte ^ 0xAC for byte in value)
    def unprotect(value, entropy):
        clear = bytes(byte ^ 0xAC for byte in value[32:])
        if not hmac.compare_digest(value[:32], hmac.digest(entropy, clear, "sha256")):
            raise ValueError("test codec binding mismatch")
        return clear
    return {"_protect": protect, "_unprotect": unprotect}


def test_atomic_store_contains_no_plaintext_and_is_bound_to_profile(tmp_path, primitives):
    store = WindowsCredentialStore(tmp_path / "first", **primitives)
    assert store.load() is None
    record = {"credential": "pd_sentinel_private_credential", "owner": "local owner"}
    store.save(record)
    assert store.load() == record
    assert b"sentinel_private_credential" not in store.path.read_bytes()
    assert b"local owner" not in store.path.read_bytes()
    copied = WindowsCredentialStore(tmp_path / "second", **primitives)
    copied.profile.mkdir()
    copied.path.write_bytes(store.path.read_bytes())
    with pytest.raises(CredentialStoreError):
        copied.load()
    assert store.load() == record
    assert not list(store.profile.glob("*.tmp"))
    store.clear()
    assert store.load() is None


def test_protection_and_replace_failures_preserve_previous_blob(tmp_path, primitives, monkeypatch):
    store = WindowsCredentialStore(tmp_path, **primitives)
    store.save({"credential": "keep previous protected value"})
    original = store.path.read_bytes()
    def fail(*args):
        raise OSError("exception containing a private credential sentinel")
    broken = WindowsCredentialStore(tmp_path, _protect=fail, _unprotect=primitives["_unprotect"])
    with pytest.raises(CredentialStoreError) as error:
        broken.save({"credential": "never fall back to plaintext"})
    assert "sentinel" not in str(error.value) and store.path.read_bytes() == original
    monkeypatch.setattr("companion.credential_store.os.replace", fail)
    with pytest.raises(CredentialStoreError):
        store.save({"credential": "replacement"})
    assert store.path.read_bytes() == original and store.load()["credential"] == "keep previous protected value"
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("data", [b"", b"plain credential", b"POLTERGEIST-DPAPI-1\x00", b"x" * 90000],
                         ids=["empty", "plaintext", "truncated", "oversized"])
def test_malformed_or_oversized_blob_fails_closed_without_changing_it(tmp_path, primitives, data):
    store = WindowsCredentialStore(tmp_path, **primitives)
    store.path.write_bytes(data)
    with pytest.raises(CredentialStoreError):
        store.load()
    assert store.path.read_bytes() == data


def test_oversized_nonfinite_or_nonobject_record_does_not_replace_saved_data(tmp_path, primitives):
    store = WindowsCredentialStore(tmp_path, **primitives)
    store.save({"previous": True})
    original = store.path.read_bytes()
    for record in ({"huge": "x" * MAX_CLEAR_BYTES}, {"nan": float("nan")}, ["not an object"]):
        with pytest.raises(CredentialStoreError):
            store.save(record)
        assert store.path.read_bytes() == original


@pytest.mark.skipif(os.name != "nt", reason="Native Windows DPAPI is Windows-only")
def test_real_current_user_dpapi_round_trip_and_corruption(tmp_path):
    store = WindowsCredentialStore(tmp_path)
    record = {"credential": "pd_native_private_sentinel", "attempt_secret": "pa_native_private_sentinel"}
    store.save(record)
    original = store.path.read_bytes()
    assert b"private_sentinel" not in original
    assert WindowsCredentialStore(tmp_path).load() == record
    changed = bytearray(original)
    changed[-10] ^= 1
    prefix = len(b"POLTERGEIST-DPAPI-1\x00")
    # Repairing the public outer checksum must not bypass native protection.
    changed[prefix + 4:prefix + 36] = hashlib.sha256(changed[prefix + 36:]).digest()
    store.path.write_bytes(changed)
    with pytest.raises(CredentialStoreError):
        store.load()
    store.path.write_bytes(original)
    assert store.load() == record
