#!/usr/bin/env python3
"""Remind assignees of quiet issues, then unassign them if they stay quiet.

An assigned issue looks taken, so other contributors skip it. For every open
issue that has an assignee and no pull request:

1. After REMIND_AFTER without activity, post one comment asking for an update.
2. If nobody comments within UNASSIGN_AFTER of that, remove the assignees and
   add the `help wanted` label.

An issue has a pull request when any pull request is linked to it, or when an
open pull request by one of the assignees mentions it. Any comment (or a new
assignment) after the reminder restarts the clock. Bot comments never count as
activity. Our reminder is found by its marker at the start, whoever posted it.
Issues are never closed.

Usage:
  GITHUB_TOKEN=$(gh auth token) scripts/assigned-issue-reminder.py --dry-run

Requires GITHUB_TOKEN (or GH_TOKEN) with issues: write.
"""

import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone

from github_api import ApiError, api

DEFAULT_REPO = "llm-d/llm-d"

REMIND_AFTER = timedelta(days=30)
UNASSIGN_AFTER = timedelta(days=14)

# Issues carrying one of these labels are never reminded or unassigned.
EXEMPT_LABELS = {"lifecycle/frozen"}
HELP_WANTED_LABEL = "help wanted"

# Hidden marker that identifies our own reminder comment.
REMINDER_MARKER = "<!-- assigned-issue-reminder -->"

REMINDER_BODY = (
    "{marker}\n"
    "{mentions} this issue has had no activity for {days} days. "
    "Please comment with a status update. Without one in {grace} days "
    "you will be unassigned so others can pick it up."
)
UNASSIGN_BODY = (
    "No update after the reminder, so this is unassigned. "
    "Anyone is welcome to pick it up."
)

PAGE_SIZE = 50

ISSUES_QUERY = """
query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    issues(states: OPEN, first: %d, after: $cursor, filterBy: {assignee: "*"}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        createdAt
        labels(first: 50) { nodes { name } }
        assignees(first: 20) { nodes { login } }
        closedByPullRequestsReferences(first: 1, includeClosedPrs: true) { totalCount }
        timelineItems(last: 100, itemTypes: [ISSUE_COMMENT, ASSIGNED_EVENT]) {
          nodes {
            __typename
            ... on IssueComment { createdAt body author { __typename } }
            ... on AssignedEvent { createdAt }
          }
        }
      }
    }
  }
}
""" % PAGE_SIZE

PULLS_QUERY = """
query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: OPEN, first: 100, after: $cursor) {
      pageInfo { hasNextPage endCursor }
      nodes { title body author { login } }
    }
  }
}
"""


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def graphql_nodes(token: str, repo: str, query: str, connection: str) -> list[dict]:
    """Return every node of a paginated repository connection."""
    owner, name = repo.split("/", 1)
    nodes: list[dict] = []
    cursor = None
    while True:
        result = api(
            "POST",
            "/graphql",
            token,
            {"query": query, "variables": {"owner": owner, "name": name, "cursor": cursor}},
        )
        if result.get("errors"):
            raise RuntimeError(f"GraphQL error: {result['errors']}")
        page = result["data"]["repository"][connection]
        nodes.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            return nodes
        cursor = page["pageInfo"]["endCursor"]


def mentions_issue(text: str, repo: str, number: int) -> bool:
    """True if text refers to the issue as #N, owner/repo#N or by URL."""
    pattern = (
        rf"(?<![\w/#-])(?:{re.escape(repo)})?#{number}(?!\d)"
        rf"|github\.com/{re.escape(repo)}/(?:issues|pull)/{number}(?!\d)"
    )
    return re.search(pattern, text) is not None


def assignee_logins(issue: dict) -> list[str]:
    return [a["login"] for a in issue["assignees"]["nodes"]]


def has_pull_request(issue: dict, pulls: list[dict], repo: str) -> bool:
    if issue["closedByPullRequestsReferences"]["totalCount"]:
        return True
    assignees = set(assignee_logins(issue))
    return any(
        pr["author"]
        and pr["author"]["login"] in assignees
        and mentions_issue(f"{pr['title']}\n{pr['body'] or ''}", repo, issue["number"])
        for pr in pulls
    )


def is_bot(item: dict) -> bool:
    return (item.get("author") or {}).get("__typename") == "Bot"


def is_reminder(item: dict) -> bool:
    # Match by marker, not author type: a user token posts it as a User.
    return (item.get("body") or "").startswith(REMINDER_MARKER)


def decide(issue: dict, pulls: list[dict], repo: str, now: datetime) -> str | None:
    """Return "remind", "unassign" or None for one assigned issue."""
    labels = {label["name"] for label in issue["labels"]["nodes"]}
    if labels & EXEMPT_LABELS or has_pull_request(issue, pulls, repo):
        return None

    activity = parse_ts(issue["createdAt"])
    reminder = None
    for item in issue["timelineItems"]["nodes"]:
        created = parse_ts(item["createdAt"])
        if is_reminder(item):
            reminder = created
        elif not is_bot(item):
            activity = max(activity, created)

    if reminder and reminder >= activity:
        return "unassign" if now - reminder >= UNASSIGN_AFTER else None
    return "remind" if now - activity >= REMIND_AFTER else None


def issue_path(repo: str, number: int, resource: str) -> str:
    return f"/repos/{repo}/issues/{number}/{resource}"


def comment(repo: str, token: str, number: int, body: str) -> None:
    api("POST", issue_path(repo, number, "comments"), token, {"body": body})


def remind(repo: str, token: str, number: int, assignees: list[str]) -> None:
    mentions = " ".join(f"@{login}" for login in assignees)
    body = REMINDER_BODY.format(
        marker=REMINDER_MARKER,
        mentions=mentions,
        days=REMIND_AFTER.days,
        grace=UNASSIGN_AFTER.days,
    )
    comment(repo, token, number, body)


def unassign(repo: str, token: str, number: int, assignees: list[str]) -> None:
    # Unassign last among the state changes so a failure leaves the issue
    # assigned and the next run retries.
    api("POST", issue_path(repo, number, "labels"), token, {"labels": [HELP_WANTED_LABEL]})
    api("DELETE", issue_path(repo, number, "assignees"), token, {"assignees": assignees})
    comment(repo, token, number, UNASSIGN_BODY)


ACTIONS = {"remind": remind, "unassign": unassign}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO))
    parser.add_argument("--dry-run", action="store_true", help="log actions without taking them")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("error: set GITHUB_TOKEN or GH_TOKEN", file=sys.stderr)
        return 2

    issues = graphql_nodes(token, args.repo, ISSUES_QUERY, "issues")
    pulls = graphql_nodes(token, args.repo, PULLS_QUERY, "pullRequests")
    now = datetime.now(timezone.utc)

    failures = 0
    for issue in issues:
        action = decide(issue, pulls, args.repo, now)
        if not action:
            continue
        number = issue["number"]
        assignees = assignee_logins(issue)
        print(f"#{number}: {action} {' '.join(assignees)}" + (" (dry run)" if args.dry_run else ""))
        if not args.dry_run:
            try:
                ACTIONS[action](args.repo, token, number, assignees)
            except ApiError as exc:
                failures += 1
                print(f"#{number}: {action} failed: {exc}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
