#!/usr/bin/env python3
"""Seed "never run" placeholder badges for a release branch's testing matrix.

Every cell of the Release Testing matrix in release/README.md is a live shields.io
endpoint pointing at ``badges/{badge_name}_{release-branch}.json`` on gh-pages, so a
re-run of one lane updates the table with no re-render. Shields has no
default-if-missing, though: a cell whose endpoint file does not exist renders the
literal text ``custom badge: resource not found``. A freshly cut release branch
therefore starts as a table of broken images and stays partly broken until every
lane has been dispatched at least once.

This script writes a placeholder for every badge the matrix references:

    {"schemaVersion": 1, "label": "<the lane's badge_label>",
     "message": "never run", "color": "lightgrey"}

which is the same shape llm-d-infra's reusable-update-badge.yaml writes, so a real
run replacing a placeholder changes only "message" and "color". ``label`` is copied
from the lane's own ``badge_label:`` rather than recomputed, so a cell's label does
not change when the placeholder is replaced.

Two properties are load-bearing:

* **Create-if-absent.** A placeholder must never overwrite a real result or a
  deliberate "dry-run". That also makes re-running free, which is why release-e2e.yaml
  can seed on every dispatch rather than only the first.
* **The full set, never a filtered one.** The badge list comes from
  matrix_common.matrix_badge_labels(), the same GUIDES x PROVIDERS walk the sync
  scripts use, so it cannot drift from the table. Seeding a subset would leave the
  remaining cells broken, which is the problem being fixed.

Everything is written in a single commit. gh-pages is a Pages branch, and one push
per badge would mean ~50 Pages builds against a soft limit of about ten an hour.
The ref update is not forced, so a lane pushing its own badge concurrently
(reusable-update-badge.yaml has no concurrency group and no retry) makes the update
fail rather than clobber it; the whole sequence is then retried from a fresh read,
which is what keeps it create-if-absent.

Usage:
  scripts/seed-release-badges.py --matrix-type release-0.10
  scripts/seed-release-badges.py --matrix-type release-0.10 --dry-run

Requires GITHUB_TOKEN (or GH_TOKEN) with write access to the repository's contents.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

import matrix_common as mc

API_ROOT = "https://api.github.com"
DEFAULT_REPO = "llm-d/llm-d"
DEFAULT_BRANCH = "gh-pages"
BADGES_DIR = "badges"

PLACEHOLDER_MESSAGE = "never run"
PLACEHOLDER_COLOR = "lightgrey"

# Only ever target a release branch's badge files. "nightly" in particular would
# place placeholders over the *nightly* badges and blank out the Nightly Testing
# matrix, and matrix_common.badge_file_name() gives nightly no suffix, so a typo
# there is silent. release-e2e.yaml validates its own input too; this guard is for
# the times the script is run by hand.
RELEASE_BRANCH_RE = re.compile(r"^release-[0-9]+\.[0-9]+$")

BLOB_MODE = "100644"


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


def placeholder_json(label: str, message: str, color: str) -> str:
    """Render a placeholder endpoint file.

    Field set and order match the heredoc in llm-d-infra's
    reusable-update-badge.yaml, so the diff when a real run lands is two lines.
    """
    return (
        json.dumps(
            {
                "schemaVersion": 1,
                "label": label,
                "message": message,
                "color": color,
            },
            indent=2,
        )
        + "\n"
    )


def existing_badges(repo: str, branch: str, token: str) -> tuple[str, str, set[str]]:
    """Return (head commit sha, base tree sha, badge file names already present)."""
    ref = api("GET", f"/repos/{repo}/git/ref/heads/{branch}", token)
    head = ref["object"]["sha"]

    tree = api("GET", f"/repos/{repo}/git/trees/{head}?recursive=1", token)
    if tree.get("truncated"):
        # A truncated listing would make absent files look creatable and turn
        # create-if-absent into overwrite. Refuse rather than risk it.
        raise RuntimeError(
            f"the {branch} tree of {repo} came back truncated; cannot tell which "
            "badges already exist"
        )

    prefix = f"{BADGES_DIR}/"
    present = {
        entry["path"].removeprefix(prefix)
        for entry in tree["tree"]
        if entry["type"] == "blob" and entry["path"].startswith(prefix)
    }
    return head, tree["sha"], present


def seed_once(repo: str, branch: str, token: str, wanted: dict[str, str]) -> list[str]:
    """One attempt at seeding. Returns the badge files created (empty if none were).

    Raises ApiError with status 409/422 if the branch moved under us, which the
    caller retries from a fresh read.
    """
    head, base_tree, present = existing_badges(repo, branch, token)

    missing = {name: body for name, body in wanted.items() if name not in present}
    if not missing:
        return []

    tree = api(
        "POST",
        f"/repos/{repo}/git/trees",
        token,
        {
            "base_tree": base_tree,
            "tree": [
                {
                    "path": f"{BADGES_DIR}/{name}",
                    "mode": BLOB_MODE,
                    "type": "blob",
                    "content": body,
                }
                for name, body in sorted(missing.items())
            ],
        },
    )

    commit = api(
        "POST",
        f"/repos/{repo}/git/commits",
        token,
        {
            "message": (
                f"badge: seed {len(missing)} placeholder badge(s) "
                f"({PLACEHOLDER_MESSAGE})"
            ),
            "tree": tree["sha"],
            "parents": [head],
        },
    )

    # Deliberately not forced: losing the race is better than overwriting a badge
    # a lane wrote while we were building this commit.
    api(
        "PATCH",
        f"/repos/{repo}/git/refs/heads/{branch}",
        token,
        {"sha": commit["sha"]},
    )

    return sorted(missing)


def seed(repo: str, branch: str, token: str, wanted: dict[str, str], retries: int) -> list[str]:
    """Seed with retries, re-reading gh-pages between attempts."""
    for attempt in range(1, retries + 1):
        try:
            return seed_once(repo, branch, token, wanted)
        except ApiError as exc:
            if exc.status not in (409, 422) or attempt == retries:
                raise
            delay = 2 ** attempt
            print(
                f"{branch} moved while seeding (attempt {attempt}/{retries}): {exc}\n"
                f"  retrying in {delay}s from a fresh read",
                file=sys.stderr,
            )
            time.sleep(delay)

    return []  # unreachable: the loop either returns or raises


def write_step_summary(matrix_type: str, seeded: list[str], total: int) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return

    lines = [
        "",
        f"### Placeholder badges — `{matrix_type}`",
        "",
        f"Seeded **{len(seeded)}** of **{total}** matrix cells "
        f"(**{total - len(seeded)}** already had a badge).",
        "",
    ]
    if seeded:
        lines += [
            "Cells that had never been run, now showing "
            f"`{PLACEHOLDER_MESSAGE}`:",
            "",
        ]
        lines += [f"- `{name}`" for name in seeded]
        lines.append("")

    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--matrix-type",
        required=True,
        help="release branch whose badges to seed, e.g. release-0.10",
    )
    parser.add_argument("--repo", default=DEFAULT_REPO, help=f"default: {DEFAULT_REPO}")
    parser.add_argument(
        "--branch",
        default=DEFAULT_BRANCH,
        help=f"branch holding the badge files (default: {DEFAULT_BRANCH})",
    )
    parser.add_argument("--message", default=PLACEHOLDER_MESSAGE)
    parser.add_argument("--color", default=PLACEHOLDER_COLOR)
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="attempts when the branch moves under us (default: 3)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be created without writing anything",
    )
    args = parser.parse_args()

    if not RELEASE_BRANCH_RE.match(args.matrix_type):
        print(
            f"ERROR: --matrix-type must look like 'release-0.10', got "
            f"{args.matrix_type!r}. Seeding anything else would write over the "
            "badges of another matrix.",
            file=sys.stderr,
        )
        return 1

    labels = mc.matrix_badge_labels()
    if not labels:
        print("ERROR: no badges found in the matrix configuration", file=sys.stderr)
        return 1

    wanted = {
        mc.badge_file_name(badge_name, args.matrix_type): placeholder_json(
            label, args.message, args.color
        )
        for badge_name, label in labels.items()
    }

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("ERROR: GITHUB_TOKEN (or GH_TOKEN) is not set", file=sys.stderr)
        return 1

    try:
        if args.dry_run:
            _head, _base_tree, present = existing_badges(args.repo, args.branch, token)
            seeded = sorted(name for name in wanted if name not in present)
        else:
            seeded = seed(args.repo, args.branch, token, wanted, args.retries)
    except (ApiError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    verb = "would seed" if args.dry_run else "seeded"
    print(
        f"{verb} {len(seeded)} of {len(wanted)} badge(s) for {args.matrix_type} "
        f"({len(wanted) - len(seeded)} already present)"
    )
    for name in seeded:
        print(f"  {name}")

    if not args.dry_run:
        write_step_summary(args.matrix_type, seeded, len(wanted))

    return 0


if __name__ == "__main__":
    sys.exit(main())
