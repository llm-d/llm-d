#!/usr/bin/env python3
"""Request reviews or cc guide OWNERS on pull requests touching guides/**.

Reads nearest-ancestor ``guides/**/OWNERS`` files from the base branch checkout
and routes review notifications for each touched guide:

1. Repo collaborators (``GET /repos/{repo}/collaborators/{login}`` -> 204) are
   requested as PR reviewers via ``requested_reviewers`` (up to GitHub's
   15-reviewer limit).
2. Non-collaborator guide owners (404, or individual 422 rejections from
   ``requested_reviewers``) are mentioned once in an idempotent sticky comment
   marked with ``<!-- guide-owners-cc -->``.

To avoid spam across pushes, the PR author, anyone already requested, anyone
who already reviewed, anyone previously requested in the PR timeline, and anyone
already mentioned in the bot's sticky comment are never re-requested or
re-pinged. Cross-tree sweeps touching more than ``MAX_TOUCHED_GUIDES`` guides
are skipped.

Usage:
  GITHUB_TOKEN=$(gh auth token) scripts/pr-guide-owners.py --pr 1234 --dry-run

Requires GITHUB_TOKEN (or GH_TOKEN) with collaborator-read (push or CI app
token), pull-requests: write, and issues: write access.
"""

import argparse
import os
import re
import sys
import urllib.parse
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

CC_MARKER = "<!-- guide-owners-cc -->"
GITHUB_LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
MENTION_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])@([A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)(?![A-Za-z0-9-])"
)
SINGLE_GUIDE_CC_RE = re.compile(r"^cc guide owners \(`([^`]+)/OWNERS`\):\s*(.+)$")
MULTI_GUIDE_CC_RE = re.compile(r"^- `([^`]+)`:\s*(.+)$")
BOT_LOGINS = {"github-actions[bot]"}


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


def _dedupe_logins(existing: list[str], additions: list[str]) -> list[str]:
    merged: list[str] = []
    seen_lower: set[str] = set()
    for login in [*existing, *additions]:
        lower = login.lower()
        if lower not in seen_lower:
            seen_lower.add(lower)
            merged.append(login)
    return merged


def is_bot_comment(comment: dict) -> bool:
    """True when a comment was authored by a bot login we can edit."""
    user = comment.get("user") or {}
    return user.get("type") == "Bot" and user.get("login") in BOT_LOGINS


def parse_sticky_comment(body: str) -> tuple[set[str], dict[str, list[str]]]:
    """Extract previously mentioned logins and per-guide lists from a sticky comment."""
    already_ccd_lower = {m.group(1).lower() for m in MENTION_RE.finditer(body)}
    by_guide: dict[str, list[str]] = {}
    for raw_line in body.splitlines():
        line = raw_line.strip()
        match = SINGLE_GUIDE_CC_RE.match(line) or MULTI_GUIDE_CC_RE.match(line)
        if not match:
            continue
        guide_dir = match.group(1)
        logins = [m.group(1) for m in MENTION_RE.finditer(match.group(2))]
        if logins:
            by_guide[guide_dir] = _dedupe_logins(by_guide.get(guide_dir, []), logins)
    return already_ccd_lower, by_guide


def find_sticky_comment(comments: list[dict]) -> dict | None:
    """Return the first bot-authored sticky guide-owners comment, if present."""
    for comment in comments:
        body = comment.get("body") or ""
        if body.startswith(CC_MARKER) and is_bot_comment(comment):
            return comment
    return None


def format_sticky_comment(cc_by_guide: dict[str, list[str]]) -> str:
    """Render the sticky cc comment body for one or more guides."""
    non_empty = {g: logins for g, logins in sorted(cc_by_guide.items()) if logins}
    if len(non_empty) == 1:
        guide_dir, logins = next(iter(non_empty.items()))
        mentions = ", ".join(f"@{login}" for login in logins)
        return f"{CC_MARKER}\ncc guide owners (`{guide_dir}/OWNERS`): {mentions}"

    lines = [CC_MARKER, "cc guide owners from `OWNERS`:"]
    for guide_dir, logins in non_empty.items():
        mentions = ", ".join(f"@{login}" for login in logins)
        lines.append(f"- `{guide_dir}`: {mentions}")
    return "\n".join(lines)


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


def _is_reviewer_cap_error(exc: ApiError) -> bool:
    msg = str(exc).lower()
    return ("maximum" in msg or "limit" in msg or "more than 15" in msg) and "reviewer" in msg


def request_reviewers(
    repo: str,
    pr_number: int,
    token: str,
    candidates: list[str],
) -> tuple[list[str], set[str]]:
    """Request reviewers in one batch, falling back per-user on HTTP 422."""
    if not candidates:
        return [], set()

    path = f"/repos/{repo}/pulls/{pr_number}/requested_reviewers"
    try:
        api("POST", path, token, {"reviewers": candidates})
        return list(candidates), set()
    except ApiError as exc:
        if exc.status != 422:
            raise
        if _is_reviewer_cap_error(exc):
            return [], set()
        if len(candidates) == 1:
            return [], {candidates[0].lower()}

    requested: list[str] = []
    rejected_lower: set[str] = set()
    for login in candidates:
        try:
            api("POST", path, token, {"reviewers": [login]})
            requested.append(login)
        except ApiError as exc:
            if exc.status != 422:
                raise
            if _is_reviewer_cap_error(exc):
                break
            rejected_lower.add(login.lower())
    return requested, rejected_lower


def route_pr(
    repo: str,
    pr_number: int,
    token: str,
    repo_root: Path,
    *,
    dry_run: bool = False,
) -> int:
    """Compute and apply guide-owner review requests and sticky cc comments."""
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

    comments = api_list(f"/repos/{repo}/issues/{pr_number}/comments", token)
    sticky_comment = find_sticky_comment(comments)
    already_ccd_lower: set[str] = set()
    existing_cc_by_guide: dict[str, list[str]] = {}
    if sticky_comment:
        already_ccd_lower, existing_cc_by_guide = parse_sticky_comment(
            sticky_comment.get("body") or ""
        )

    excluded_lower = (
        ({author_lower} if author_lower else set())
        | currently_requested_lower
        | reviewed_lower
        | ever_requested_lower
        | already_ccd_lower
    )

    unique_candidates: list[str] = []
    seen_candidates_lower: set[str] = set()
    for owners in guide_to_owners.values():
        for login in owners:
            lower = login.lower()
            if lower not in excluded_lower and lower not in seen_candidates_lower:
                seen_candidates_lower.add(lower)
                unique_candidates.append(login)

    if not unique_candidates:
        print(f"PR #{pr_number}: all touched guide owners are already notified or excluded.")
        return 0

    collaborator_candidates: list[str] = []
    non_collaborator_lower: set[str] = set()
    for login in unique_candidates:
        if is_collaborator(repo, login, token):
            collaborator_candidates.append(login)
        else:
            non_collaborator_lower.add(login.lower())

    active_team_requests = len(pr.get("requested_teams") or [])
    remaining_slots = max(
        0, MAX_PR_REVIEWERS - len(currently_requested_lower) - active_team_requests
    )
    to_request = collaborator_candidates[:remaining_slots]
    skipped_over_cap = collaborator_candidates[remaining_slots:]
    if skipped_over_cap:
        print(
            f"PR #{pr_number}: capped collaborator requests at {MAX_PR_REVIEWERS} total "
            f"(skipping {', '.join(skipped_over_cap)})."
        )

    suffix = " (dry run)" if dry_run else ""
    if to_request:
        if dry_run:
            print(f"PR #{pr_number}: would request reviewers {to_request}{suffix}")
        else:
            requested, rejected_lower = request_reviewers(repo, pr_number, token, to_request)
            if requested:
                print(f"PR #{pr_number}: requested reviewers {requested}")
            if rejected_lower:
                print(
                    f"PR #{pr_number}: reviewer request returned 422 for "
                    f"{sorted(rejected_lower)}; falling back to cc comment."
                )
                non_collaborator_lower.update(rejected_lower)

    new_cc_by_guide: dict[str, list[str]] = {}
    new_cc_logins: list[str] = []
    for guide_dir, owners in guide_to_owners.items():
        additions = [
            login
            for login in owners
            if login.lower() in non_collaborator_lower and login.lower() not in excluded_lower
        ]
        if additions:
            new_cc_by_guide[guide_dir] = additions
            new_cc_logins.extend(additions)

    if not new_cc_logins:
        return 0

    merged_cc_by_guide: dict[str, list[str]] = {
        guide_dir: list(logins) for guide_dir, logins in existing_cc_by_guide.items()
    }
    for guide_dir, additions in new_cc_by_guide.items():
        merged_cc_by_guide[guide_dir] = _dedupe_logins(
            merged_cc_by_guide.get(guide_dir, []), additions
        )

    body = format_sticky_comment(merged_cc_by_guide)
    action = "update" if sticky_comment else "create"
    if dry_run:
        print(f"PR #{pr_number}: would {action} sticky comment{suffix}:\n{body}")
        return 0

    if sticky_comment:
        api("PATCH", f"/repos/{repo}/issues/comments/{sticky_comment['id']}", token, {"body": body})
    else:
        api("POST", f"/repos/{repo}/issues/{pr_number}/comments", token, {"body": body})
    print(f"PR #{pr_number}: {action}d sticky cc comment for {sorted(new_cc_by_guide)}.")
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

    return route_pr(args.repo, args.pr, token, args.repo_root.resolve(), dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
