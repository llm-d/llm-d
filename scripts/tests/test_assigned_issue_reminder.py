"""Tests for the assigned-issue reminder.

The failures worth guarding are the silent ones: unassigning someone who did
reply, reminding someone who already has a pull request, and counting our own
comments as activity.
"""

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

SPEC = importlib.util.spec_from_file_location(
    "assigned_issue_reminder", SCRIPTS_DIR / "assigned-issue-reminder.py"
)
assert SPEC and SPEC.loader
air = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(air)

REPO = "llm-d/llm-d"
NOW = datetime(2026, 10, 2, tzinfo=timezone.utc)


def ago(days: int) -> str:
    return (NOW - timedelta(days=days)).isoformat().replace("+00:00", "Z")


def comment(days: int, body: str = "hi", author: str = "User") -> dict:
    return {
        "__typename": "IssueComment",
        "createdAt": ago(days),
        "body": body,
        "author": {"__typename": author},
    }


def assigned(days: int) -> dict:
    return {"__typename": "AssignedEvent", "createdAt": ago(days)}


def reminder(days: int, author: str = "Bot") -> dict:
    return comment(days, air.REMINDER_MARKER + "\nstatus?", author=author)


def issue(items=(), created=100, number=7, assignees=("alice",), labels=(), linked=0) -> dict:
    return {
        "number": number,
        "createdAt": ago(created),
        "labels": {"nodes": [{"name": name} for name in labels]},
        "assignees": {"nodes": [{"login": login} for login in assignees]},
        "closedByPullRequestsReferences": {"totalCount": linked},
        "timelineItems": {"nodes": list(items)},
    }


def pull(body: str, author: str | None = "alice", title: str = "fix") -> dict:
    return {"title": title, "body": body, "author": {"login": author} if author else None}


# ---------------------------------------------------------------------------
# mentions_issue
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Fixes #7", True),
        ("(#7)", True),
        ("llm-d/llm-d#7", True),
        ("https://github.com/llm-d/llm-d/issues/7", True),
        ("https://github.com/llm-d/llm-d/pull/7", True),
        ("#70", False),
        ("#17", False),
        ("other/repo#7", False),
        ("https://github.com/other/repo/issues/7", False),
        ("no reference", False),
    ],
)
def test_mentions_issue(text, expected):
    assert air.mentions_issue(text, REPO, 7) is expected


# ---------------------------------------------------------------------------
# has_pull_request
# ---------------------------------------------------------------------------


def test_linked_pull_request_counts():
    assert air.has_pull_request(issue(linked=1), [], REPO)


def test_linked_query_includes_closed_pull_requests():
    assert "includeClosedPrs: true" in air.ISSUES_QUERY


def test_assignee_pull_request_mention_counts():
    assert air.has_pull_request(issue(), [pull("Part of #7")], REPO)


def test_mention_in_title_counts():
    assert air.has_pull_request(issue(), [pull("", title="fix #7")], REPO)


def test_other_authors_mention_does_not_count():
    assert not air.has_pull_request(issue(), [pull("Part of #7", author="bob")], REPO)


def test_ghost_author_and_empty_body_are_tolerated():
    pulls = [pull(None, author=None), pull("unrelated")]
    assert not air.has_pull_request(issue(), pulls, REPO)


# ---------------------------------------------------------------------------
# decide
# ---------------------------------------------------------------------------


def test_recent_activity_is_left_alone():
    assert air.decide(issue([comment(5)]), [], REPO, NOW) is None


def test_quiet_issue_gets_a_reminder():
    assert air.decide(issue([comment(31)]), [], REPO, NOW) == "remind"


def test_quiet_issue_without_comments_uses_creation_date():
    assert air.decide(issue(created=31), [], REPO, NOW) == "remind"
    assert air.decide(issue(created=5), [], REPO, NOW) is None


def test_recent_assignment_counts_as_activity():
    assert air.decide(issue([assigned(2)], created=100), [], REPO, NOW) is None


def test_bot_comments_are_not_activity():
    stale_bot = comment(1, "marked stale", author="Bot")
    assert air.decide(issue([comment(40), stale_bot]), [], REPO, NOW) == "remind"


def test_pending_reminder_waits_out_the_grace_period():
    assert air.decide(issue([comment(40), reminder(13)]), [], REPO, NOW) is None


def test_unanswered_reminder_unassigns():
    assert air.decide(issue([comment(40), reminder(14)]), [], REPO, NOW) == "unassign"


def test_reply_after_reminder_restarts_the_clock():
    items = [comment(40), reminder(20), comment(10)]
    assert air.decide(issue(items), [], REPO, NOW) is None


def test_quoting_the_marker_is_a_reply_not_a_reminder():
    quote = comment(1, f"> {air.REMINDER_MARKER}\nstill on it")
    assert air.decide(issue([comment(40), reminder(20), quote]), [], REPO, NOW) is None


def test_reminder_posted_by_a_user_token_is_not_activity():
    items = [comment(40), reminder(14, author="User")]
    assert air.decide(issue(items), [], REPO, NOW) == "unassign"
    assert air.decide(issue([comment(40), reminder(13, author="User")]), [], REPO, NOW) is None


def test_second_reminder_after_a_reply_goes_quiet():
    items = [reminder(80), comment(40)]
    assert air.decide(issue(items), [], REPO, NOW) == "remind"


def test_pull_request_suppresses_everything():
    quiet = issue([comment(40), reminder(20)], linked=1)
    assert air.decide(quiet, [], REPO, NOW) is None
    mentioned = issue([comment(40)])
    assert air.decide(mentioned, [pull("Closes #7")], REPO, NOW) is None


def test_exempt_label_suppresses_everything():
    frozen = issue([comment(40), reminder(20)], labels=["lifecycle/frozen"])
    assert air.decide(frozen, [], REPO, NOW) is None


# ---------------------------------------------------------------------------
# actions and main
# ---------------------------------------------------------------------------


class FakeApi:
    def __init__(self):
        self.calls = []

    def __call__(self, method, path, token, payload=None):
        self.calls.append((method, path, payload))
        return {}


def test_remind_posts_one_comment_mentioning_assignees(monkeypatch):
    fake = FakeApi()
    monkeypatch.setattr(air, "api", fake)
    air.remind(REPO, "t", 7, ["alice", "bob"])
    [(method, path, payload)] = fake.calls
    assert (method, path) == ("POST", f"/repos/{REPO}/issues/7/comments")
    assert payload["body"].startswith(air.REMINDER_MARKER)
    assert "@alice @bob" in payload["body"]
    assert "30 days" in payload["body"] and "14 days" in payload["body"]


def test_unassign_labels_removes_assignees_then_comments(monkeypatch):
    fake = FakeApi()
    monkeypatch.setattr(air, "api", fake)
    air.unassign(REPO, "t", 7, ["alice"])
    assert [(m, p.rsplit("/", 1)[1]) for m, p, _ in fake.calls] == [
        ("POST", "labels"),
        ("DELETE", "assignees"),
        ("POST", "comments"),
    ]
    assert fake.calls[0][2] == {"labels": ["help wanted"]}
    assert fake.calls[1][2] == {"assignees": ["alice"]}
    assert air.REMINDER_MARKER not in fake.calls[2][2]["body"]


def run_main(monkeypatch, argv, issues, fake=None, code=0):
    fake = fake or FakeApi()
    monkeypatch.setattr(air, "api", fake)
    monkeypatch.setattr(
        air,
        "graphql_nodes",
        lambda token, repo, query, connection: issues if connection == "issues" else [],
    )
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setattr(sys, "argv", ["assigned-issue-reminder.py", *argv])
    assert air.main() == code
    return fake


def test_main_dry_run_makes_no_changes(monkeypatch, capsys):
    fake = run_main(monkeypatch, ["--dry-run"], [issue([comment(40)])])
    assert fake.calls == []
    assert "#7: remind alice (dry run)" in capsys.readouterr().out


def test_main_acts_on_quiet_issues_only(monkeypatch):
    issues = [issue([comment(40)], number=1), issue([comment(2)], number=2)]
    fake = run_main(monkeypatch, [], issues)
    assert [path for _, path, _ in fake.calls] == [f"/repos/{REPO}/issues/1/comments"]


def test_main_continues_past_an_api_error_and_fails_at_the_end(monkeypatch, capsys):
    class FailFirst(FakeApi):
        def __call__(self, method, path, token, payload=None):
            super().__call__(method, path, token, payload)
            if "/issues/1/" in path:
                raise air.ApiError(422, method, path, "locked")
            return {}

    issues = [issue([comment(40)], number=1), issue([comment(40)], number=2)]
    fake = run_main(monkeypatch, [], issues, fake=FailFirst(), code=1)
    assert f"/repos/{REPO}/issues/2/comments" in [path for _, path, _ in fake.calls]
    assert "#1: remind failed" in capsys.readouterr().err


def test_main_requires_a_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(sys, "argv", ["assigned-issue-reminder.py"])
    assert air.main() == 2
