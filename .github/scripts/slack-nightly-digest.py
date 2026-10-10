#!/usr/bin/env python3
"""Build one Slack digest covering a day of nightly results.

Usage:
  # Render without posting -- works locally against the real repository.
  GH_TOKEN=$(gh auth token) python .github/scripts/slack-nightly-digest.py \
      --repo llm-d/llm-d --dry-run

  # Replay a specific window (both bounds inclusive of the API's resolution).
  GH_TOKEN=$(gh auth token) python .github/scripts/slack-nightly-digest.py \
      --repo llm-d/llm-d --since 2026-10-05T20:00:00Z --until 2026-10-06T20:00:00Z --dry-run

  # In CI: write `digests` to $GITHUB_OUTPUT for the posting matrix.
  python .github/scripts/slack-nightly-digest.py \
      --repo "$GITHUB_REPOSITORY" --github-output

This replaces a per-run notifier that posted one message per failing nightly.
The nightlies are staggered across 01:00-18:30 UTC, so that produced 6-8
separate messages a day in #llm-d-ci-alerts, none of which answered "how was
last night?" without scrolling.

Nothing is accumulated between runs. The alternative -- having each nightly
append to a shared store that this script drains -- needs locking for the
nightlies that finish at the same minute, loses an alert outright when a write
fails, and has to be pruned. The Actions API already holds every result, so this
script just asks it, once, for the whole window. That also makes the digest
replayable for any past window via --since/--until.

Routing is keyed off the workflow's FILE name, because file names are stable
while display names are not. See .github/slack-channels.yaml.
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MAPPING_PATH = REPO_ROOT / ".github" / "slack-channels.yaml"
OWNER_IDS_PATH = REPO_ROOT / ".github" / "slack-owner-ids.yaml"
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

API_ROOT = "https://api.github.com"

# Jobs that publish the gh-pages badge rather than exercise the guide. They run
# with `if: always()`, so they are present even when the test failed.
BADGE_JOB_PREFIX = "update-badge"

FAILED_CONCLUSIONS = ("failure", "timed_out")
UNSTABLE_CONCLUSIONS = ("neutral", "stale", "action_required")
STARTUP_FAILURE_CONCLUSIONS = ("startup_failure",)
BADGE_FAILED_CONCLUSIONS = FAILED_CONCLUSIONS + UNSTABLE_CONCLUSIONS + STARTUP_FAILURE_CONCLUSIONS

# Outcomes that put a nightly in the digest's failure list.
NOTIFIABLE_OUTCOMES = ("failure", "timed_out", "unstable")

# Above this many failures the per-failure detail stops being useful -- the
# answer is "main is broken", not forty individual investigations -- and the
# message would approach Slack's 40k text limit. Collapse to one line each.
TERSE_THRESHOLD = 25

# Display names all begin with this. Stripping it buys back width in every line.
NAME_PREFIX = "Nightly - "


# ---------------------------------------------------------------------------
# GitHub API
# ---------------------------------------------------------------------------


def api_get(path: str, token: str, *, allow_404: bool = False) -> dict | None:
    request = urllib.request.Request(
        f"{API_ROOT}{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "llm-d-slack-nightly-digest",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if allow_404 and exc.code == 404:
            return None
        raise SystemExit(f"ERROR: GET {path} returned {exc.code} {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"ERROR: GET {path} failed: {exc.reason}") from exc


def fetch_scheduled_runs(repo: str, workflow_file: str, since: datetime, until: datetime, token: str) -> list[dict]:
    """Return this workflow's scheduled runs on main that started inside the window.

    Deliberately not filtered to `status=completed`: the 18:30 nightly can still
    be in flight when the digest runs, and that has to read as "still running"
    rather than as "never ran".

    The `created` range is also applied client-side against run_started_at. The
    API filter keeps the response small; the local filter is what makes the
    window exact, and keeps this correct if the server-side range syntax ever
    changes under us.
    """
    query = urllib.parse.urlencode(
        {
            "event": "schedule",
            "branch": "main",
            "created": f"{iso(since)}..{iso(until)}",
            "per_page": 100,
        }
    )
    data = api_get(
        f"/repos/{repo}/actions/workflows/{workflow_file}/runs?{query}",
        token,
        # A workflow file that is not registered on the default branch yet --
        # added in an unmerged PR, or just renamed -- 404s here. That is a
        # "no run", not a reason to abandon the whole digest.
        allow_404=True,
    )
    if data is None:
        return []

    runs = []
    for run in data.get("workflow_runs", []):
        started = parse_time(run.get("run_started_at") or run.get("created_at"))
        if started is not None and since <= started <= until:
            runs.append(run)
    runs.sort(key=lambda run: parse_time(run.get("run_started_at") or run.get("created_at")) or since)
    return runs


def fetch_jobs(repo: str, run_id: int, token: str) -> list[dict]:
    # A nightly has a handful of jobs; one page is plenty.
    data = api_get(f"/repos/{repo}/actions/runs/{run_id}/jobs?per_page=100", token)
    return (data or {}).get("jobs", [])


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_duration(started: str | None, ended: str | None) -> str:
    """Render a run's wall-clock duration as "1h 12m" / "47m" / "38s"."""
    start, end = parse_time(started), parse_time(ended)
    if start is None or end is None:
        return "unknown"

    seconds = int((end - start).total_seconds())
    if seconds < 0:
        return "unknown"
    if seconds < 60:
        return f"{seconds}s"

    hours, minutes = divmod(seconds // 60, 60)
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


def format_window(since: datetime, until: datetime) -> str:
    """Render the digest window as "05 Oct 20:00 -> 06 Oct 20:00 UTC"."""
    start = since.astimezone(timezone.utc)
    end = until.astimezone(timezone.utc)
    return f"{start:%d %b %H:%M} → {end:%d %b %H:%M} UTC"


# ---------------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------------


def is_badge_job(name: str) -> bool:
    # Jobs from a called reusable workflow are reported as "<caller> / <inner>".
    return name == BADGE_JOB_PREFIX or name.startswith(f"{BADGE_JOB_PREFIX} / ")


def nightly_outcome(jobs: list[dict], run_conclusion: str | None = None) -> tuple[str, str | None, bool]:
    """Separate the guide's result from the badge job's result.

    The run-level conclusion cannot be used directly: update-badge runs with
    `if: always()`, so a green test whose badge push to gh-pages failed comes out
    as a failed *run*. Reporting that verbatim tells a channel its guide is
    broken when it is not, and credible false positives are exactly what makes
    people stop trusting an alert channel.

    Returns (outcome, failing_job_name, badge_failed) where outcome is one of
    "success", "failure", "timed_out", "unstable", "cancelled" or "skipped".
    """
    test_jobs = [job for job in jobs if not is_badge_job(job.get("name", ""))]
    badge_failed = any(
        job.get("conclusion") in BADGE_FAILED_CONCLUSIONS
        for job in jobs
        if is_badge_job(job.get("name", ""))
    )

    # Report the first failure in job order, which is the one that actually
    # broke the run rather than a downstream casualty.
    for job in test_jobs:
        if job.get("conclusion") in FAILED_CONCLUSIONS:
            return job["conclusion"], job.get("name"), badge_failed

    for job in test_jobs:
        if job.get("conclusion") in UNSTABLE_CONCLUSIONS:
            return "unstable", job.get("name"), badge_failed

    if any(job.get("conclusion") == "cancelled" for job in test_jobs):
        return "cancelled", None, badge_failed

    # Every job skipped is not a pass -- nothing ran, so there is no result to
    # report. Reporting green here would be the same false-confidence bug as
    # reporting a badge failure as a guide failure, just in the other direction.
    if test_jobs and all(job.get("conclusion") == "skipped" for job in test_jobs):
        return "skipped", None, badge_failed

    # Keep an update-badge failure separate from the guide result. It is still
    # sent to the central alert channel, but should not page guide owners.
    if badge_failed and all(job.get("conclusion") in ("success", "skipped") for job in test_jobs):
        return "success", None, badge_failed

    if run_conclusion in FAILED_CONCLUSIONS:
        return run_conclusion, None, badge_failed
    if run_conclusion == "startup_failure":
        return "failure", None, badge_failed
    if run_conclusion in UNSTABLE_CONCLUSIONS:
        return "unstable", None, badge_failed

    return "success", None, badge_failed


# ---------------------------------------------------------------------------
# Workflow metadata
# ---------------------------------------------------------------------------


def load_workflow(workflow_file: str) -> dict:
    path = WORKFLOWS_DIR / workflow_file
    if not path.is_file():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}


def has_active_schedule(workflow_file: str) -> bool:
    """True when the workflow file actually carries an uncommented cron.

    Seven routed nightlies (the IBM/OCP lanes) have their whole `schedule:`
    block commented out. Without this check the digest would report all seven as
    "did not run" every single day, which is noise that trains people to ignore
    the section that exists to catch a genuinely stalled schedule.
    """
    data = load_workflow(workflow_file)
    # PyYAML follows YAML 1.1, where the bare key `on` is the boolean True.
    triggers = data.get("on")
    if triggers is None:
        triggers = data.get(True)
    if not isinstance(triggers, dict):
        return False
    schedule = triggers.get("schedule") or []
    if not isinstance(schedule, list):
        return False
    return any(isinstance(entry, dict) and entry.get("cron") for entry in schedule)


def workflow_display_name(workflow_file: str) -> str:
    """Return the workflow's display `name:`, falling back to the file name.

    Read with a regex rather than from the parsed YAML because only a top-level
    `name:` at column zero is the workflow name.
    """
    path = WORKFLOWS_DIR / workflow_file
    if path.is_file():
        match = re.search(r"^name:\s*(.+)$", path.read_text(encoding="utf-8"), re.MULTILINE)
        if match:
            return match.group(1).strip().strip("'\"")
    return workflow_file


def resolve_scenario(workflow_file: str) -> str | None:
    """Return the guide directory a nightly exercises, e.g. "optimized-baseline"."""
    path = WORKFLOWS_DIR / workflow_file
    if not path.is_file():
        return None

    for line in path.read_text(encoding="utf-8").splitlines():
        if re.match(r"^\s*standup_scenario:", line):
            match = re.search(r"\|\|\s*['\"]([^'\"]+)['\"]", line)
            if match:
                return match.group(1)
        if re.match(r"^\s*guide_name:", line):
            match = re.match(r"^\s*guide_name:\s*['\"]?([^'\"\s]+)", line)
            if match:
                return match.group(1)
    return None


# ---------------------------------------------------------------------------
# Routing and owners
# ---------------------------------------------------------------------------


def load_mapping() -> dict:
    with MAPPING_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_slack_ids() -> dict[str, str]:
    try:
        with OWNER_IDS_PATH.open(encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {}
    return {str(login).lower(): user_id for login, user_id in raw.items()}


def emit_warning(message: str, *, json_output: bool = False) -> None:
    """Keep diagnostics off stdout when the caller requests JSON output."""
    print(message, file=sys.stderr if json_output else sys.stdout)


def owner_mentions(scenario: str | None, slack_ids: dict[str, str], *, json_output: bool = False) -> list[str]:
    """Return Slack mentions for the owners of a guide.

    Resolved per guide rather than per workflow: OWNERS lives at
    guides/<scenario>/OWNERS, so every lane of a guide has the same owners by
    construction. Grouping the digest by guide therefore de-duplicates the
    mentions exactly, instead of guessing at it.
    """
    if not scenario:
        return []
    owners_path = REPO_ROOT / "guides" / scenario / "OWNERS"
    if not owners_path.is_file():
        return []
    with owners_path.open(encoding="utf-8") as fh:
        owners = yaml.safe_load(fh) or {}

    logins = {
        str(login).lower()
        for role in ("approvers", "reviewers")
        for login in (owners.get(role) or [])
    }

    def mapped(login: str) -> bool:
        value = slack_ids.get(login)
        return isinstance(value, str) and bool(re.fullmatch(r"[UW][A-Z0-9]+", value))

    mentions = {f"<@{slack_ids[login]}>" for login in logins if mapped(login)}
    # Logins absent from the mapping have no Slack account and are skipped.
    missing = sorted(login for login in logins if login in slack_ids and not mapped(login))
    if missing:
        emit_warning(
            f"::warning::No Slack user ID mapping for {scenario} owners: {', '.join(missing)}. "
            f"Add them to {OWNER_IDS_PATH.relative_to(REPO_ROOT)}.",
            json_output=json_output,
        )
    return sorted(mentions)


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


class Result:
    """One routed nightly's state for the digest window."""

    def __init__(self, workflow_file: str, channel: str):
        self.workflow_file = workflow_file
        self.channel = channel
        self.display_name = workflow_display_name(workflow_file)
        self.scenario = resolve_scenario(workflow_file)
        self.status = "unscheduled"
        self.failing_job: str | None = None
        self.badge_failed = False
        self.run: dict | None = None

    @property
    def short_name(self) -> str:
        name = self.display_name
        return name[len(NAME_PREFIX):] if name.startswith(NAME_PREFIX) else name

    @property
    def is_failure(self) -> bool:
        return self.status in NOTIFIABLE_OUTCOMES


def collect(repo: str, mapping: dict, since: datetime, until: datetime, token: str) -> list[Result]:
    """Resolve every routed nightly's state for the window."""
    results: list[Result] = []

    for channel, files in (mapping.get("channels") or {}).items():
        for workflow_file in sorted(files or []):
            result = Result(workflow_file, channel)
            results.append(result)

            if not has_active_schedule(workflow_file):
                result.status = "unscheduled"
                continue

            runs = fetch_scheduled_runs(repo, workflow_file, since, until, token)
            if not runs:
                result.status = "no_run"
                continue

            # The newest run wins. When a SIG re-runs a nightly that failed on a
            # transient cluster error, that re-run is the current truth about the
            # guide -- reporting the earlier failure would leave the digest
            # showing an already-fixed problem as the last word.
            run = runs[-1]
            result.run = run

            if run.get("status") != "completed":
                result.status = "running"
                continue

            jobs = fetch_jobs(repo, run["id"], token) if run.get("conclusion") != "success" else []
            outcome, failing_job, badge_failed = nightly_outcome(jobs, run.get("conclusion"))
            result.status = outcome
            result.failing_job = failing_job
            result.badge_failed = badge_failed

    return results


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------


def escape_mrkdwn(text: str) -> str:
    """Escape the three characters Slack reserves in message text."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def group_title(results: list[Result]) -> str:
    """A heading for a guide's group, derived from what its lanes have in common.

    The common word prefix of the display names, minus the trailing "E2E": for
    the tiered-prefix-cache lanes that is "Tiered Prefix Cache", which is both
    shorter and more accurate than title-casing the directory slug would be
    (`pd-disaggregation` is written "P/D Disaggregation").
    """
    stems = [re.sub(r"\s*\([^()]*\)\s*$", "", result.short_name).split() for result in results]
    common: list[str] = []
    for words in zip(*stems):
        if len(set(words)) != 1:
            break
        common.append(words[0])
    while common and common[-1] == "E2E":
        common.pop()
    if common:
        return " ".join(common)
    scenario = results[0].scenario or Path(results[0].workflow_file).stem
    return scenario.replace("-", " ").title()


def lane_label(result: Result, title: str) -> str:
    """What distinguishes this lane from its siblings under `title`.

    "Tiered Prefix Cache CPU Offloading LMCache E2E (AMD ROCM)" under the group
    heading "Tiered Prefix Cache" becomes "CPU Offloading LMCache (AMD ROCM)",
    so the guide name is not repeated on every line. Falls back to the full name
    whenever the remainder would be empty -- the lane must stay identifiable,
    and several guides differ only outside the parenthetical.
    """
    name = result.short_name
    remainder = name[len(title):] if title and name.startswith(title) else name
    remainder = re.sub(r"\bE2E\b", "", remainder)
    remainder = re.sub(r"\s{2,}", " ", remainder).strip()
    # A bare "(GKE TPU)" reads better without the brackets; a mixed
    # "CPU Offloading (AMD ROCM)" keeps them to separate lane from platform.
    bare = re.fullmatch(r"\(([^()]*)\)", remainder)
    if bare:
        remainder = bare.group(1).strip()
    return remainder or name


def status_icon(result: Result) -> str:
    return {
        "timed_out": ":hourglass_flowing_sand:",
        "failure": ":red_circle:",
        "unstable": ":large_orange_circle:",
    }.get(result.status, ":grey_question:")


def failure_lines(result: Result, title: str, matrix_url: str, *, terse: bool) -> list[str]:
    """Render one failing nightly, in the same vocabulary the old per-run message used."""
    run = result.run or {}
    url = run.get("html_url", "")
    number = run.get("run_number", "?")
    attempt = run.get("run_attempt", 1) or 1
    sha = (run.get("head_sha") or "")[:7]
    duration = format_duration(run.get("run_started_at") or run.get("created_at"), run.get("updated_at"))

    state = {"timed_out": "timed out after", "unstable": "unstable after"}.get(result.status, "failed after")
    label = escape_mrkdwn(lane_label(result, title))
    head = f"{status_icon(result)}  *{label}*  ·  {state} {duration}  ·  `{sha}`"
    # A re-run landing hours off the usual schedule is confusing without this.
    if attempt > 1:
        head += f"  (attempt {attempt})"

    if terse:
        return [f"{head}  ·  <{url}|run #{number}>"]

    detail = []
    if result.failing_job:
        verb = {"timed_out": "timed out in", "unstable": "unstable in"}.get(result.status, "failed in")
        detail.append(f"{verb} `{escape_mrkdwn(result.failing_job)}`")
    detail.append(f"<{url}|run #{number}>")
    detail.append(f"<{matrix_url}|matrix>")
    return [head, "       " + "  ·  ".join(detail)]


def build_message(
    repo: str,
    results: list[Result],
    since: datetime,
    until: datetime,
    slack_ids: dict[str, str],
    unrouted: list[str],
    *,
    json_output: bool = False,
) -> str | None:
    """Render one channel's digest, or None when there is nothing to report."""
    matrix_url = f"https://github.com/{repo}/blob/main/release/README.md"

    failures = [result for result in results if result.is_failure]
    no_runs = [result for result in results if result.status == "no_run"]
    badge_only = [result for result in results if result.status == "success" and result.badge_failed]

    # Posting a daily all-green message was considered and declined: this is an
    # alert channel. Silence means "nothing to do".
    if not (failures or no_runs or badge_only):
        return None

    scheduled = [result for result in results if result.status != "unscheduled"]
    passed = [result for result in results if result.status == "success"]
    terse = len(failures) > TERSE_THRESHOLD

    if terse:
        headline = (
            f":rotating_light:  *Nightly CI digest*  ·  "
            f"*{len(failures)} of {len(scheduled)} nightlies failed* — main is likely broken  ·  "
            f"{format_window(since, until)}"
        )
    elif failures:
        headline = (
            f":rotating_light:  *Nightly CI digest*  ·  "
            f"{len(failures)} of {len(scheduled)} nightlies failed  ·  {format_window(since, until)}"
        )
    else:
        headline = f":warning:  *Nightly CI digest*  ·  no failures  ·  {format_window(since, until)}"

    lines = [headline]

    # Group by guide. Owners are a property of the guide, so this is what lets
    # each owner be mentioned once instead of once per lane.
    grouped: dict[str, list[Result]] = {}
    for result in sorted(failures, key=lambda r: (r.scenario or "", r.short_name)):
        grouped.setdefault(result.scenario or result.workflow_file, []).append(result)

    for scenario, group in grouped.items():
        title = group_title(group)
        lines.append("")
        lines.append(f"*{escape_mrkdwn(title)}*")
        for result in group:
            lines.extend(failure_lines(result, title, matrix_url, terse=terse))
        mentions = owner_mentions(group[0].scenario, slack_ids, json_output=json_output)
        if mentions:
            lines.append("       owners: " + " ".join(mentions))

    if no_runs:
        lines.append("")
        lines.append(f"*Did not run* ({len(no_runs)})  ·  scheduled but no run in this window")
        for result in sorted(no_runs, key=lambda r: r.short_name):
            lines.append(f"       :white_circle:  {escape_mrkdwn(result.short_name)}")

    if badge_only:
        lines.append("")
        lines.append(f"*Guide passed, `update-badge` failed* ({len(badge_only)})")
        for result in sorted(badge_only, key=lambda r: r.short_name):
            run = result.run or {}
            lines.append(
                f"       :warning:  {escape_mrkdwn(result.short_name)}  ·  "
                f"<{run.get('html_url', '')}|run #{run.get('run_number', '?')}>"
            )

    footer = [f"{len(passed)} passed"]
    unscheduled = len(results) - len(scheduled)
    if unscheduled:
        footer.append(f"{unscheduled} not scheduled")
    other = [result for result in results if result.status in ("cancelled", "skipped", "running")]
    if other:
        footer.append(f"{len(other)} cancelled/skipped/in flight")
    footer.append(f"<{matrix_url}|matrix>")

    lines.append("")
    lines.append("  ·  ".join(footer))

    if unrouted:
        lines.append(
            f":grey_question:  _{len(unrouted)} nightly workflow(s) are routed nowhere_: "
            + ", ".join(f"`{name}`" for name in sorted(unrouted))
        )

    return "\n".join(lines)


def build_payload(channel: str, text: str) -> dict:
    return {
        "channel": channel,
        "text": text,
        "mrkdwn": True,
        # Without this Slack expands a preview card for every GitHub link, and a
        # digest full of run links turns the channel into a wall.
        "unfurl_links": False,
        "unfurl_media": False,
    }


def find_unrouted(mapping: dict) -> list[str]:
    """Nightly workflows on disk that are neither routed nor explicitly skipped.

    The pre-commit hook is what should catch these, but it only runs on changed
    files, so a stale mapping can reach main. Surfacing it in the digest costs
    one line and makes the gap visible instead of silent.
    """
    routed = {
        file_name
        for files in (mapping.get("channels") or {}).values()
        for file_name in (files or [])
    }
    skipped = set(mapping.get("skip") or {})
    on_disk = {path.name for path in WORKFLOWS_DIR.glob("nightly-*.yaml")}
    return sorted(on_disk - routed - skipped)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def write_github_output(digests: list[dict], fallback_channel: str) -> None:
    """Emit the posting matrix plus the channel the failure notice should use.

    The matrix is driven by `channels` (names only) rather than by the payloads,
    for two reasons. A matrix built from an empty array is a hard error in
    Actions rather than a no-op, so `has_digests` is what the posting job gates
    on -- and it is a string the job compares to 'true', so an unset output
    fails closed instead of expanding an empty matrix. And a matrix value is
    interpolated into the job's display name: keying on channel names gives
    `post (#llm-d-ci-alerts)` instead of a job name containing the whole digest.

    `fallback_channel` is emitted here so the failure notice does not have to
    re-read the mapping -- reading the mapping may be the thing that broke.
    """
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        print("::warning::GITHUB_OUTPUT is not set; skipping step outputs", file=sys.stderr)
        return
    payloads = {payload["channel"]: payload for payload in digests}
    with open(output_path, "a", encoding="utf-8") as fh:
        fh.write(f"channels={json.dumps(sorted(payloads), ensure_ascii=False)}\n")
        fh.write(f"payloads={json.dumps(payloads, ensure_ascii=False)}\n")
        fh.write(f"has_digests={'true' if payloads else 'false'}\n")
        fh.write(f"count={len(payloads)}\n")
        fh.write(f"fallback_channel={fallback_channel}\n")


def write_step_summary(lines: list[str]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


STATUS_ORDER = (
    "failure",
    "timed_out",
    "unstable",
    "no_run",
    "running",
    "cancelled",
    "skipped",
    "success",
    "unscheduled",
)


def summarise(results: list[Result]) -> list[str]:
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: STATUS_ORDER.index(kv[0]) if kv[0] in STATUS_ORDER else 99)
    return [f"{status}: {count}" for status, count in ordered]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def resolve_window(args: argparse.Namespace) -> tuple[datetime, datetime]:
    until = parse_time(args.until) or datetime.now(timezone.utc)
    since = parse_time(args.since) or until - timedelta(hours=args.window_hours)
    if since >= until:
        raise SystemExit(f"ERROR: --since ({iso(since)}) must be before --until ({iso(until)}).")
    return since, until


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="owner/name of the repository")
    parser.add_argument("--window-hours", type=float, default=24.0, help="how far back to look (default 24)")
    parser.add_argument("--since", default="", help="window start, ISO 8601 (overrides --window-hours)")
    parser.add_argument("--until", default="", help="window end, ISO 8601 (default now)")
    parser.add_argument("--channel", default="", help="post to this channel instead of the mapped ones")
    parser.add_argument("--dry-run", action="store_true", help="print the digest and exit without emitting outputs")
    parser.add_argument("--json", action="store_true", help="print only the payload JSON array")
    parser.add_argument(
        "--github-output",
        action="store_true",
        help="write the posting matrix (channels/payloads/has_digests) to $GITHUB_OUTPUT",
    )
    args = parser.parse_args()

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit("ERROR: set GH_TOKEN (locally: GH_TOKEN=$(gh auth token)).")

    since, until = resolve_window(args)
    mapping = load_mapping()
    fallback = mapping.get("fallback_channel") or ""
    slack_ids = load_slack_ids()
    unrouted = find_unrouted(mapping)

    results = collect(args.repo, mapping, since, until, token)

    # One digest per channel. Only #llm-d-ci-alerts is in use today, but
    # slack-channels.yaml is structured for more and this should not be the
    # thing that has to change when a SIG wants its own channel.
    by_channel: dict[str, list[Result]] = {}
    for result in results:
        by_channel.setdefault(args.channel or result.channel, []).append(result)

    digests: list[dict] = []
    rendered: list[tuple[str, str]] = []
    for channel, channel_results in by_channel.items():
        text = build_message(
            args.repo,
            channel_results,
            since,
            until,
            slack_ids,
            # Attribute the config warning to one channel only, so a future
            # multi-channel setup does not repeat it everywhere.
            unrouted if channel == fallback or len(by_channel) == 1 else [],
            json_output=args.json,
        )
        if text is None:
            continue
        rendered.append((channel, text))
        digests.append(build_payload(channel, text))

    if args.json:
        print(json.dumps(digests, ensure_ascii=False))
        return 0

    print(f"window        : {iso(since)} .. {iso(until)}")
    print(f"routed        : {len(results)} nightly workflows")
    print(f"states        : {', '.join(summarise(results))}")
    if unrouted:
        print(f"::warning::unrouted nightly workflows: {', '.join(unrouted)}")

    if not rendered:
        print("nothing to report; no digest will be posted.")
        write_step_summary(["### Nightly Slack digest", "", "Nothing to report — no message posted."])
        if args.github_output:
            write_github_output([], fallback)
        return 0

    summary = ["### Nightly Slack digest", "", f"- **Window**: `{iso(since)}` .. `{iso(until)}`"]
    for channel, text in rendered:
        print(f"channel       : {channel}")
        print("-" * 72)
        print(text)
        print("-" * 72)
        summary += ["", f"- **Channel**: `{channel}`", "", "```", text, "```"]
    write_step_summary(summary)

    if args.dry_run:
        print("(--dry-run: no outputs emitted, nothing posted)")
        return 0

    if args.github_output:
        write_github_output(digests, fallback)

    return 0


if __name__ == "__main__":
    sys.exit(main())
