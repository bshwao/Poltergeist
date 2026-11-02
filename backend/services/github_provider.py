"""Bounded official-origin GitHub REST calls; no retries or credential discovery."""

import json
from time import monotonic
from urllib.parse import quote, urlencode

import httpx

ORIGIN = "https://api.github.com"
API_VERSION = "2026-03-10"
MAX_RESPONSE_BYTES = 2 * 1024**2


class GitHubError(Exception):
    def __init__(self, message, *, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


class GitHubClient:
    def __init__(self, token):
        self.token = token
        self.client = None
        self.remaining = 128
        self.wire_bytes = 0
        self.deadline = monotonic() + 60

    def __enter__(self):
        self.client = httpx.Client(trust_env=False, follow_redirects=False, timeout=httpx.Timeout(10, connect=5))
        return self

    def __exit__(self, *args):
        self.client.close()

    def request(self, path, *, payload=None):
        mutation = payload is not None
        if self.remaining <= 0 or monotonic() >= self.deadline:
            raise GitHubError("GitHub verification exceeded its bounded request/time budget")
        self.remaining -= 1
        try:
            with self.client.stream("POST" if mutation else "GET", ORIGIN + path,
                    headers={"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
                        "Content-Type": "application/json", "Accept-Encoding": "identity",
                        "X-GitHub-Api-Version": API_VERSION},
                    content=None if payload is None else json.dumps(payload, separators=(",", ":")).encode()) as response:
                if response.status_code != (201 if mutation else 200):
                    if response.status_code in {401, 403, 404}:
                        message = "GitHub access was refused or the pinned repository/object is unavailable. Check the selected App installation and repository permissions."
                    elif response.status_code == 429:
                        message = "GitHub rate limits were reached. No request will be resubmitted automatically."
                    else:
                        message = "GitHub did not accept the request. Review configuration and saved publication history."
                    raise GitHubError(message, uncertain=mutation and response.status_code >= 500)
                if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise GitHubError("GitHub returned an unsupported encoded response", uncertain=mutation)
                data = bytearray()
                for chunk in response.iter_raw():
                    self.wire_bytes += len(chunk)
                    if len(data) + len(chunk) > MAX_RESPONSE_BYTES or self.wire_bytes > 16 * 1024**2 or monotonic() >= self.deadline:
                        raise GitHubError("GitHub response exceeded the local size/time limit", uncertain=mutation)
                    data.extend(chunk)
                value = json.loads(data)
                if not isinstance(value, dict):
                    raise ValueError
                return value
        except GitHubError:
            raise
        except (httpx.HTTPError, OSError, ValueError, RecursionError):
            raise GitHubError("GitHub response was unavailable or invalid. Inspect publication history before taking further action.", uncertain=mutation) from None

    def commit(self, repository, sha):
        return self.request(f"/repos/{repository}/git/commits/{sha}")

    def tree(self, repository, sha):
        return self.request(f"/repos/{repository}/git/trees/{sha}")

    def blob(self, repository, sha):
        return self.request(f"/repos/{repository}/git/blobs/{sha}")

    def create_check(self, repository, payload):
        return self.request(f"/repos/{repository}/check-runs", payload=payload)

    def get_check(self, repository, identity):
        return self.request(f"/repos/{repository}/check-runs/{identity}")

    def find_checks(self, repository, payload, app_id):
        found = []
        for page in range(1, 4):
            query = urlencode({"check_name": payload["name"], "filter": "all", "per_page": 100, "page": page})
            value = self.request(f"/repos/{repository}/commits/{payload['head_sha']}/check-runs?{query}")
            items = value.get("check_runs")
            if not isinstance(items, list) or len(items) > 100 or type(value.get("total_count")) is not int or value["total_count"] < 0:
                raise GitHubError("GitHub returned an invalid check listing")
            found.extend(item for item in items if isinstance(item, dict) and item.get("external_id") == payload["external_id"]
                         and isinstance(item.get("app"), dict) and item["app"].get("id") == app_id)
            if page * 100 >= value["total_count"]:
                return found
        raise GitHubError("The bounded check listing cannot exclude an existing publication. Inspect GitHub manually; no duplicate was created.")
