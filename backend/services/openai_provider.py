"""One bounded Responses API request. No SDK retries, redirects or ambient proxy."""

import json
from time import monotonic

import httpx


ENDPOINT = "https://api.openai.com/v1/responses"
MAX_RESPONSE_BYTES = 256 * 1024


class ProviderError(Exception):
    """Safe user-facing diagnostics, never provider bodies or credentials."""


def respond(payload, key):
    deadline = monotonic() + 45
    try:
        with httpx.Client(trust_env=False, follow_redirects=False,
                          timeout=httpx.Timeout(10, connect=5)) as client:
            with client.stream("POST", ENDPOINT, content=payload.encode("utf-8"), headers={
                "Authorization": "Bearer " + key, "Content-Type": "application/json",
                "Accept": "application/json", "Accept-Encoding": "identity",
            }) as response:
                if response.status_code in (401, 403):
                    raise ProviderError("OpenAI refused access. Check the configured API key and model access.")
                if response.status_code == 429:
                    raise ProviderError("OpenAI rate or account limits were reached. This request will not be resent.")
                if response.status_code != 200:
                    raise ProviderError("OpenAI could not complete this request. It will not be resent automatically.")
                if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise ProviderError("OpenAI returned an unsupported encoded response.")
                data = bytearray()
                for chunk in response.iter_raw():
                    if monotonic() > deadline or len(data) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise ProviderError("OpenAI response exceeded the local time or size limit.")
                    data.extend(chunk)
                value = json.loads(data)
                if not isinstance(value, dict):
                    raise ValueError
                return value
    except ProviderError:
        raise
    except (httpx.HTTPError, OSError, ValueError, RecursionError):
        raise ProviderError("OpenAI response was unavailable or invalid. A charge may have occurred; this request will not be resent.") from None
