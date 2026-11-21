#!/usr/bin/env python3
"""Request PR reviews from nearest-ancestor guides/**/OWNERS files.

Reads ``guides/**/OWNERS`` from the base branch checkout and requests non-excluded
guide owners one at a time via ``POST /repos/{repo}/pulls/{pr}/requested_reviewers``
(round-robin across touched guides, up to GitHub's 15-reviewer limit). Logins
rejected with HTTP 422 (such as non-collaborators) are logged by name and
skipped without failing the job or consuming reviewer slots.

To avoid notification spam across pushes, the PR author, anyone already
requested, anyone who already reviewed, and anyone previously requested in issue
timeline events are skipped. Cross-tree sweeps touching more than
``MAX_TOUCHED_GUIDES`` guides are also skipped.

Usage:
  GITHUB_TOKEN=$(gh auth token) scripts/pr-guide-owners.py --pr 1234 --dry-run

Requires ``pull-requests: write`` and ``issues: read`` under GitHub Actions
``GITHUB_TOKEN``. Note: ``--dry-run`` queries ``/repos/{repo}/collaborators/{login}``
to preview collaborator vs. non-collaborator routing, which requires push or
collaborator-read access locally.
"""

import argparse
import os
import re
import socket
import sys
import urllib.error
import urllib.parse
from itertools import zip_longest
from pathlib import Path, PurePosixPath

import yaml

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from github_api import ApiError, api  # noqa: E402

DEFAULT_REPO = "llm-d/llm-d"
DEFAULT_REPO_ROOT = SCRIPTS_DIR.parent

MAX_TOUCHED_GUIDES = 5
MAX_PR_REVIEWERS = 15
REQUEST_TIMEOUT_SECONDS = 30

GITHUB_LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")


def api_list(path: str, token: str, max_pages: int = 30) -> list[dict]:
    """Return paginated items from a GitHub REST list endpoint."""
    sep = "&" if "?" in path else "?"
    results: list[dict] = []
    for page in range(1, max_pages + 1):
        items = api("GET", f"{path}{sep}per_page=100&page={page}", token)
        if not isinstance(items, list) or not items:
            break
        results.extend(items)
        if len(items) < 100:
            break
    return results


def resolve_guide_owners_dir(rel_path: str, repo_root: Path) -> str | None:
    """Return nearest ancestor directory under guides/ containing OWNERS, or None."""
    normalized = rel_path.replace("\\", "/").lstrip("/")
    posix = PurePosixPath(normalized)
    parts = posix.parts
    if len(parts) < 3 or parts[0] != "guides":
        return None
    if any(part in ("", ".", "..") for part in parts):
        return None

    candidate = posix.parent
    # Stop strictly before "guides" so neither guides/OWNERS nor root OWNERS matches.
    while len(candidate.parts) >= 2:
        if (repo_root / Path(*candidate.parts) / "OWNERS").is_file():
            return candidate.as_posix()
        candidate = candidate.parent
    return None


def load_guide_owners(owners_file: Path) -> list[str]:
    """Load deduplicated GitHub logins from an OWNERS file (approvers first)."""
    data = yaml.safe_load(owners_file.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return []

    owners: list[str] = []
    seen_lower: set[str] = set()
    for role in ("approvers", "reviewers"):
        entries = data.get(role)
        if not isinstance(entries, list):
            continue
        for raw in entries:
            if not isinstance(raw, str):
                continue
            login = raw.strip()
            if not GITHUB_LOGIN_RE.fullmatch(login):
                continue
            lower = login.lower()
            if lower not in seen_lower:
                seen_lower.add(lower)
                owners.append(login)
    return owners


def collect_touched_guides(changed_paths: list[str], repo_root: Path) -> dict[str, list[str]]:
    """Map each touched guide directory (sorted) to its ordered OWNERS logins."""
    guide_to_owners: dict[str, list[str]] = {}
    for rel_path in changed_paths:
        guide_dir = resolve_guide_owners_dir(rel_path, repo_root)
        if not guide_dir or guide_dir in guide_to_owners:
            continue
        owners_file = repo_root / Path(*PurePosixPath(guide_dir).parts) / "OWNERS"
        owners = load_guide_owners(owners_file)
        if owners:
            guide_to_owners[guide_dir] = owners
    return dict(sorted(guide_to_owners.items()))


def interleave_guide_owners(
    guide_to_owners: dict[str, list[str]],
    excluded_lower: set[str],
) -> list[str]:
    """Order eligible guide owners round-robin across touched guides."""
    candidates: list[str] = []
    seen_lower = set(excluded_lower)
    for row in zip_longest(*(guide_to_owners[g] for g in sorted(guide_to_owners))):
        for login in row:
            if login is None:
                continue
            lower = login.lower()
            if lower not in seen_lower:
                seen_lower.add(lower)
                candidates.append(login)
    return candidates


def is_collaborator(repo: str, login: str, token: str) -> bool:
    """Return True if login is a collaborator on repo (HTTP 204), False on 404."""
    quoted = urllib.parse.quote(login, safe="")
    try:
        api("GET", f"/repos/{repo}/collaborators/{quoted}", token)
        return True
    except ApiError as exc:
        if exc.status == 404:
            return False
        raise


def request_reviewers_individually(
    repo: str,
    pr_number: int,
    token: str,
    candidates: list[str],
    remaining_slots: int,
) -> tuple[list[str], list[str], list[str]]:
    """Request candidates one at a time up to remaining_slots.

    Returns (requested, skipped_422, skipped_over_cap). HTTP 422 rejections do
    not consume reviewer slots.
    """
    if remaining_slots <= 0:
        return [], [], list(candidates)

    path = f"/repos/{repo}/pulls/{pr_number}/requested_reviewers"
    requested: list[str] = []
    skipped_422: list[str] = []
    for idx, login in enumerate(candidates):
        if len(requested) >= remaining_slots:
            return requested, skipped_422, candidates[idx:]
        try:
            api("POST", path, token, {"reviewers": [login]})
            requested.append(login)
        except ApiError as exc:
            if exc.status == 422:
                skipped_422.append(login)
            else:
                raise
    return requested, skipped_422, []


def is_auth_failure(exc: BaseException) -> bool:
    """True for 401 or non-rate-limit 403 ApiErrors indicating broken token/permissions."""
    if not isinstance(exc, ApiError):
        return False
    if exc.status == 401:
        return True
    if exc.status == 403:
        body = str(exc).split(f" -> HTTP {exc.status}: ", 1)[-1].lower()
        return "rate limit" not in body and "abuse detection" not in body
    return False


def route_pr(
    repo: str,
    pr_number: int,
    token: str,
    repo_root: Path,
    *,
    dry_run: bool = False,
) -> int:
    """Compute and apply guide-owner review requests for one pull request."""
    pr = api("GET", f"/repos/{repo}/pulls/{pr_number}", token)
    if pr.get("state") != "open":
        print(f"PR #{pr_number}: state is {pr.get('state')!r}; skipping.")
        return 0
    if pr.get("draft"):
        print(f"PR #{pr_number}: draft PR; skipping.")
        return 0

    files = api_list(f"/repos/{repo}/pulls/{pr_number}/files", token)
    changed_paths: list[str] = []
    for entry in files:
        if entry.get("filename"):
            changed_paths.append(entry["filename"])
        if entry.get("previous_filename"):
            changed_paths.append(entry["previous_filename"])

    guide_to_owners = collect_touched_guides(changed_paths, repo_root)
    if not guide_to_owners:
        print(f"PR #{pr_number}: no guides with OWNERS touched.")
        return 0

    if len(guide_to_owners) > MAX_TOUCHED_GUIDES:
        print(
            f"PR #{pr_number} touches {len(guide_to_owners)} guides "
            f"(> {MAX_TOUCHED_GUIDES}); skipping automatic guide-owner routing on cross-tree sweep."
        )
        return 0

    author_lower = ((pr.get("user") or {}).get("login") or "").lower()
    currently_requested_lower = {
        u["login"].lower() for u in (pr.get("requested_reviewers") or []) if u.get("login")
    }
    reviews = api_list(f"/repos/{repo}/pulls/{pr_number}/reviews", token)
    reviewed_lower = {
        r["user"]["login"].lower() for r in reviews if (r.get("user") or {}).get("login")
    }
    events = api_list(f"/repos/{repo}/issues/{pr_number}/events", token)
    ever_requested_lower = {
        (ev.get("requested_reviewer") or {}).get("login", "").lower()
        for ev in events
        if ev.get("event") == "review_requested"
        and (ev.get("requested_reviewer") or {}).get("login")
    }

    excluded_lower = (
        ({author_lower} if author_lower else set())
        | currently_requested_lower
        | reviewed_lower
        | ever_requested_lower
    )

    candidates = interleave_guide_owners(guide_to_owners, excluded_lower)
    if not candidates:
        print(f"PR #{pr_number}: all touched guide owners are already notified or excluded.")
        return 0

    active_team_requests = len(pr.get("requested_teams") or [])
    remaining_slots = max(
        0, MAX_PR_REVIEWERS - len(currently_requested_lower) - active_team_requests
    )

    if dry_run:
        would_request: list[str] = []
        skipped_non_collab: list[str] = []
        over_cap: list[str] = []
        for login in candidates:
            if is_collaborator(repo, login, token):
                if len(would_request) < remaining_slots:
                    would_request.append(login)
                else:
                    over_cap.append(login)
            else:
                skipped_non_collab.append(login)

        if would_request:
            print(f"PR #{pr_number}: would request reviewers {would_request} (dry run)")
        if skipped_non_collab:
            print(
                f"PR #{pr_number}: would skip non-collaborator guide owners: "
                f"{', '.join(skipped_non_collab)} (dry run)"
            )
        if over_cap:
            print(
                f"PR #{pr_number}: capped collaborator requests at {MAX_PR_REVIEWERS} total "
                f"(skipping {', '.join(over_cap)}) (dry run)."
            )
        return 0

    requested, skipped_422, over_cap = request_reviewers_individually(
        repo, pr_number, token, candidates, remaining_slots
    )
    if requested:
        print(f"PR #{pr_number}: requested reviewers {requested}")
    if skipped_422:
        print(
            f"PR #{pr_number}: skipped non-collaborator or unrequestable guide owners: "
            f"{', '.join(skipped_422)}"
        )
    if over_cap:
        print(
            f"PR #{pr_number}: capped reviewer requests at {MAX_PR_REVIEWERS} total "
            f"(skipping {', '.join(over_cap)})."
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO))
    parser.add_argument("--pr", type=int, required=True, help="Pull request number")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=DEFAULT_REPO_ROOT,
        help="Path to base-branch repository checkout containing guides/**/OWNERS",
    )
    parser.add_argument("--dry-run", action="store_true", help="log actions without taking them")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("error: set GITHUB_TOKEN or GH_TOKEN", file=sys.stderr)
        return 2

    socket.setdefaulttimeout(REQUEST_TIMEOUT_SECONDS)
    try:
        return route_pr(args.repo, args.pr, token, args.repo_root.resolve(), dry_run=args.dry_run)
    except (ApiError, urllib.error.URLError, TimeoutError, OSError) as exc:
        if is_auth_failure(exc):
            print(f"error: guide-owner routing failed for PR #{args.pr}: {exc}", file=sys.stderr)
            return 1
        print(f"::warning::guide-owner routing skipped for PR #{args.pr}: {exc}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
