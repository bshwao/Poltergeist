"""One bounded enrollment exchange; builtin-only so -I works outside a checkout.

Input and output use anonymous pipes. No credential, URL or exception is logged.
The parent owns the entire process tree and terminates it at its total deadline.
A killed request may already have committed remotely; its outcome is uncertain.
"""

import json
import math
import ssl
import sys
from urllib import error, parse, request

MAX_INPUT = 524288
MAX_OUTPUT = 131072
MAX_BODY = 32768
MAX_RESPONSE = 65536


class Failure(Exception):
    def __init__(self, code):
        self.code = code


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise Failure("redirect")


def pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise ValueError("Duplicate field")
        result[key] = value
    return result


def load(value):
    return json.loads(value, object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite value")))


def exchange(value):
    if type(value) is not dict or set(value) != {"origin", "method", "path", "body_json", "authorization", "trust_pem", "socket_timeout_s"}:
        raise Failure("invalid")
    origin, path, method = value["origin"], value["path"], value["method"]
    if not isinstance(origin, str) or len(origin) > 256:
        raise Failure("invalid")
    address = parse.urlsplit(origin)
    if (address.scheme != "https" or not address.hostname or address.username is not None
            or address.password is not None or address.path or address.query or address.fragment):
        raise Failure("invalid")
    if (method not in ("GET", "POST") or not isinstance(path, str) or len(path) > 4096
            or not path.startswith("/v1/") or any(part in path for part in ("?", "#", "\\", ".."))):
        raise Failure("invalid")
    headers = {"Accept": "application/json", "User-Agent": "Poltergeist-Companion/0.1"}
    authorization = value["authorization"]
    if authorization is not None:
        if not isinstance(authorization, str) or len(authorization) > 512 or any(ord(char) < 32 or ord(char) == 127 for char in authorization):
            raise Failure("invalid")
        headers["Authorization"] = authorization
    data = value["body_json"]
    if data is not None:
        if not isinstance(data, str):
            raise Failure("invalid")
        data = data.encode("utf-8")
        if len(data) > MAX_BODY:
            raise Failure("invalid")
        load(data)
        headers["Content-Type"] = "application/json"
    trust = value["trust_pem"]
    if trust is not None and (not isinstance(trust, str) or not trust or len(trust) > 262144):
        raise Failure("invalid")
    timeout = value["socket_timeout_s"]
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 10:
        raise Failure("invalid")
    context = ssl.create_default_context(cadata=trust)
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    opener = request.build_opener(request.ProxyHandler({}), NoRedirect(), request.HTTPSHandler(context=context))
    operation = request.Request(origin + path, data=data, headers=headers, method=method)
    try:
        with opener.open(operation, timeout=timeout) as response:
            content = response.read(MAX_RESPONSE + 1)
            if len(content) > MAX_RESPONSE or response.headers.get_content_type() != "application/json":
                raise Failure("response")
            return {"ok": True, "value": load(content)}
    except error.HTTPError as failure:
        status = failure.code
        failure.close()
        return {"ok": False, "code": "http", "status": status} if type(status) is int and 400 <= status <= 599 else {"ok": False, "code": "unreachable"}


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise Failure("invalid")
        answer = exchange(load(raw))
    except Failure as failure:
        answer = {"ok": False, "code": failure.code}
    except Exception:
        answer = {"ok": False, "code": "unreachable"}
    try:
        encoded = json.dumps(answer, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_OUTPUT:
            encoded = b'{"ok":false,"code":"response"}'
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
