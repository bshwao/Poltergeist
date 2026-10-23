"""Atomic enrollment storage protected by current-user Windows DPAPI."""

import hashlib
import hmac
import json
import os
from pathlib import Path
import struct
from threading import RLock
from uuid import uuid4

MAX_CLEAR_BYTES = 65536
MAX_BLOB_BYTES = MAX_CLEAR_BYTES + 16384
_OUTER = b"POLTERGEIST-DPAPI-1\x00"
_INNER = b"POLTERGEIST-ENROLLMENT-1\x00"


class CredentialStoreError(RuntimeError):
    pass


class CredentialStoreUnavailable(CredentialStoreError):
    pass


def _dpapi(data, entropy, *, decrypt):
    if os.name != "nt":
        raise CredentialStoreUnavailable("Windows user credential protection is unavailable")
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel.LocalFree.restype = wintypes.HLOCAL
    common = [ctypes.POINTER(Blob), wintypes.LPVOID, ctypes.POINTER(Blob), wintypes.LPVOID,
              wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(Blob)]
    crypt.CryptProtectData.argtypes = common
    crypt.CryptProtectData.restype = wintypes.BOOL
    crypt.CryptUnprotectData.argtypes = common
    crypt.CryptUnprotectData.restype = wintypes.BOOL
    source_buffer = ctypes.create_string_buffer(data)
    entropy_buffer = ctypes.create_string_buffer(entropy)
    source = Blob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    additional = Blob(len(entropy), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    try:
        operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
        # UI forbidden; current-user scope. LOCAL_MACHINE is deliberately absent.
        if not operation(ctypes.byref(source), None, ctypes.byref(additional), None, None, 1, ctypes.byref(output)):
            raise CredentialStoreError("Windows user credential protection failed")
        if output.size > MAX_BLOB_BYTES or not output.data:
            raise CredentialStoreError("Windows credential data exceeded its bounds")
        return ctypes.string_at(output.data, output.size)
    finally:
        ctypes.memset(source_buffer, 0, ctypes.sizeof(source_buffer))
        ctypes.memset(entropy_buffer, 0, ctypes.sizeof(entropy_buffer))
        if output.data:
            ctypes.memset(output.data, 0, output.size)
            kernel.LocalFree(ctypes.cast(output.data, wintypes.HLOCAL))


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise ValueError("Duplicate enrollment field")
        result[key] = value
    return result


class WindowsCredentialStore:
    def __init__(self, profile, *, _protect=None, _unprotect=None):
        self.profile = Path(profile)
        if not self.profile.is_absolute():
            raise ValueError("Credential storage requires an absolute companion profile")
        if (_protect is None) != (_unprotect is None):
            raise ValueError("Supply both test protection primitives")
        if _protect is None and os.name != "nt":
            raise CredentialStoreUnavailable("Windows user credential protection is unavailable")
        self.path = self.profile / "remote-enrollment.bin"
        self._lock = RLock()
        self._plain_path()
        binding = os.path.normcase(str(self.profile.resolve())).encode("utf-8")
        self._entropy = b"Poltergeist.Companion.Enrollment.v1:" + hashlib.sha256(binding).digest()
        self._protect = _protect or (lambda value, entropy: _dpapi(value, entropy, decrypt=False))
        self._unprotect = _unprotect or (lambda value, entropy: _dpapi(value, entropy, decrypt=True))

    def _plain_path(self):
        for part in (self.path, *self.path.parents):
            if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
                raise CredentialStoreError("Linked credential storage was refused")

    def save(self, record):
        temporary = None
        with self._lock:
            try:
                self._plain_path()
                if not isinstance(record, dict):
                    raise ValueError("Enrollment must be an object")
                encoded = json.dumps({"version": 1, "record": record}, sort_keys=True,
                    separators=(",", ":"), allow_nan=False).encode("utf-8")
                if len(encoded) > MAX_CLEAR_BYTES:
                    raise ValueError("Enrollment exceeds the credential limit")
                clear = _INNER + hashlib.sha256(encoded).digest() + encoded
                protected = self._protect(clear, self._entropy)
                if not isinstance(protected, bytes) or not 1 <= len(protected) <= MAX_BLOB_BYTES:
                    raise ValueError("Invalid protected enrollment")
                framed = _OUTER + struct.pack("!I", len(protected)) + hashlib.sha256(protected).digest() + protected
                self._plain_path()
                self.profile.mkdir(parents=True, exist_ok=True)
                temporary = self.profile / (".remote-enrollment-" + uuid4().hex + ".tmp")
                with temporary.open("xb") as stream:
                    stream.write(framed)
                    stream.flush()
                    os.fsync(stream.fileno())
                self._plain_path()
                os.replace(temporary, self.path)
            except Exception:
                raise CredentialStoreError("Enrollment could not be protected and saved") from None
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    def load(self):
        with self._lock:
            try:
                self._plain_path()
                if not self.path.exists():
                    return None
                with self.path.open("rb") as stream:
                    data = stream.read(MAX_BLOB_BYTES + 128)
                    if stream.read(1):
                        raise ValueError("Credential file exceeds bounds")
                prefix = len(_OUTER)
                if not data.startswith(_OUTER) or len(data) < prefix + 36:
                    raise ValueError("Invalid protected enrollment framing")
                length = struct.unpack("!I", data[prefix:prefix + 4])[0]
                protected = data[prefix + 36:]
                if not 1 <= length <= MAX_BLOB_BYTES or len(protected) != length:
                    raise ValueError("Invalid protected enrollment length")
                if not hmac.compare_digest(data[prefix + 4:prefix + 36], hashlib.sha256(protected).digest()):
                    raise ValueError("Damaged protected enrollment")
                clear = self._unprotect(protected, self._entropy)
                prefix = len(_INNER)
                if not isinstance(clear, bytes) or not clear.startswith(_INNER) or len(clear) > MAX_CLEAR_BYTES + prefix + 32:
                    raise ValueError("Invalid decrypted enrollment framing")
                encoded = clear[prefix + 32:]
                if not hmac.compare_digest(clear[prefix:prefix + 32], hashlib.sha256(encoded).digest()):
                    raise ValueError("Damaged decrypted enrollment")
                value = json.loads(encoded, object_pairs_hook=_pairs,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite enrollment value")))
                if not isinstance(value, dict) or set(value) != {"version", "record"} or type(value["version"]) is not int or value["version"] != 1 or not isinstance(value["record"], dict):
                    raise ValueError("Unsupported enrollment version")
                return value["record"]
            except Exception:
                raise CredentialStoreError("Protected enrollment could not be read") from None

    def clear(self):
        with self._lock:
            self._plain_path()
            self.path.unlink(missing_ok=True)
