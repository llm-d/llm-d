"""Minimal GitHub API client shared by the scripts in this directory."""

import json
import urllib.error
import urllib.request

API_ROOT = "https://api.github.com"


class ApiError(RuntimeError):
    """A GitHub API call returned an unexpected status."""

    def __init__(self, status: int, method: str, path: str, body: str):
        super().__init__(f"{method} {path} -> HTTP {status}: {body.strip()[:400]}")
        self.status = status


def api(method: str, path: str, token: str, payload: dict | None = None) -> dict:
    """Call the GitHub API and return the decoded JSON body."""
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(f"{API_ROOT}{path}", data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        request.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(request) as response:
            body = response.read().decode()
    except urllib.error.HTTPError as exc:
        raise ApiError(exc.code, method, path, exc.read().decode()) from exc

    return json.loads(body) if body else {}
