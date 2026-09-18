#!/usr/bin/env python3
"""Build the Slack message for a finished nightly run.

Usage:
  # Render without posting -- works locally against any real run.
  GH_TOKEN=$(gh auth token) python .github/scripts/notify-slack-nightly.py \
      --repo llm-d/llm-d --run-id 32211600170 --dry-run

  # In CI: write `channel` and `payload` to $GITHUB_OUTPUT for the posting step.
  python .github/scripts/notify-slack-nightly.py \
      --repo "$GITHUB_REPOSITORY" --run-id "$RUN_ID" --github-output

The run is fetched from the API by id rather than read out of the workflow_run
event payload. That keeps one code path for the real trigger and for a manual
replay, and -- more importantly -- the event payload cannot answer the question
this script has to answer: see nightly_outcome() below.

Routing is keyed off the workflow's file name, taken from the run's `path`,
because file names are stable while display names are not. See
.github/slack-channels.yaml.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MAPPING_PATH = REPO_ROOT / ".github" / "slack-channels.yaml"

API_ROOT = "https://api.github.com"

# Jobs that publish the gh-pages badge rather than exercise the guide. They run
# with `if: always()`, so they are present even when the test failed.
BADGE_JOB_PREFIX = "update-badge"

FAILED_CONCLUSIONS = ("failure", "timed_out")

# Outcomes worth a message. The notify workflow also filters on the run-level
# conclusion so it can skip spinning up a runner, but this is the authoritative
# check: a manual replay bypasses that filter, and "nothing to say about this
# run" is a property of the run, not of how the script was invoked.
#
# "cancelled" is the common exclusion: every nightly sets cancel-in-progress, so
# dispatching one while a run is in flight cancels that run, which is not news.
# "skipped" means no job actually exercised the guide.
NOTIFIABLE_OUTCOMES = ("success", "failure", "timed_out")


# ---------------------------------------------------------------------------
# GitHub API
# ---------------------------------------------------------------------------


def api_get(path: str, token: str) -> dict:
    request = urllib.request.Request(
        f"{API_ROOT}{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "llm-d-notify-slack-nightly",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"ERROR: GET {path} returned {exc.code} {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"ERROR: GET {path} failed: {exc.reason}") from exc


def fetch_run(repo: str, run_id: str, token: str) -> dict:
    return api_get(f"/repos/{repo}/actions/runs/{run_id}", token)


def fetch_jobs(repo: str, run_id: str, token: str) -> list[dict]:
    # A nightly has a handful of jobs; one page is plenty.
    data = api_get(f"/repos/{repo}/actions/runs/{run_id}/jobs?per_page=100", token)
    return data.get("jobs", [])


# ---------------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------------


def is_badge_job(name: str) -> bool:
    # Jobs from a called reusable workflow are reported as "<caller> / <inner>".
    return name == BADGE_JOB_PREFIX or name.startswith(f"{BADGE_JOB_PREFIX} / ")


def nightly_outcome(jobs: list[dict]) -> tuple[str, str | None, bool]:
    """Separate the guide's result from the badge job's result.

    The run-level conclusion cannot be used directly: update-badge runs with
    `if: always()`, so a green test whose badge push to gh-pages failed comes out
    as a failed *run*. Reporting that verbatim tells a channel its guide is
    broken when it is not, and credible false positives are exactly what makes
    people stop trusting an alert channel.

    Returns (outcome, failing_job_name, badge_failed) where outcome is one of
    "success", "failure", "timed_out" or "cancelled".
    """
    test_jobs = [job for job in jobs if not is_badge_job(job.get("name", ""))]
    badge_failed = any(
        job.get("conclusion") in FAILED_CONCLUSIONS for job in jobs if is_badge_job(job.get("name", ""))
    )

    # Report the first failure in job order, which is the one that actually
    # broke the run rather than a downstream casualty.
    for job in test_jobs:
        if job.get("conclusion") in FAILED_CONCLUSIONS:
            return job["conclusion"], job.get("name"), badge_failed

    if any(job.get("conclusion") == "cancelled" for job in test_jobs):
        return "cancelled", None, badge_failed

    # Every job skipped is not a pass -- nothing ran, so there is no result to
    # report. Reporting green here would be the same false-confidence bug as
    # reporting a badge failure as a guide failure, just in the other direction.
    if test_jobs and all(job.get("conclusion") == "skipped" for job in test_jobs):
        return "skipped", None, badge_failed

    return "success", None, badge_failed


def format_duration(started: str | None, ended: str | None) -> str:
    """Render a run's wall-clock duration as "1h 12m" / "47m" / "38s"."""
    if not started or not ended:
        return "unknown"
    try:
        start = datetime.fromisoformat(started.replace("Z", "+00:00"))
        end = datetime.fromisoformat(ended.replace("Z", "+00:00"))
    except ValueError:
        return "unknown"

    seconds = int((end - start).total_seconds())
    if seconds < 0:
        return "unknown"
    if seconds < 60:
        return f"{seconds}s"

    hours, minutes = divmod(seconds // 60, 60)
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def load_mapping() -> dict:
    with MAPPING_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def resolve_channel(mapping: dict, workflow_file: str) -> tuple[str | None, bool]:
    """Return (channel, is_fallback) for a workflow file name.

    A file listed under `skip` returns (None, False): not notifying it is a
    recorded decision, not a gap.

    Anything else unknown falls back rather than failing. A red notification job
    would be a second alert channel nobody watches, and it muddies "did the
    nightly fail, or did the notifier fail?". The place an unrouted workflow is
    meant to be caught is scripts/sync-slack-channels.py --check, in the PR that
    adds it.
    """
    if workflow_file in (mapping.get("skip") or {}):
        return None, False

    for channel, files in (mapping.get("channels") or {}).items():
        if workflow_file in (files or []):
            return channel, False

    return mapping.get("fallback_channel"), True


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------


def escape_mrkdwn(text: str) -> str:
    """Escape the three characters Slack reserves in message text."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_message(repo: str, run: dict, outcome: str, failing_job: str | None, badge_failed: bool) -> str:
    name = escape_mrkdwn(run.get("name") or "Unknown workflow")
    url = run.get("html_url", "")
    number = run.get("run_number", "?")
    attempt = run.get("run_attempt", 1) or 1
    sha = (run.get("head_sha") or "")[:7]
    duration = format_duration(run.get("run_started_at") or run.get("created_at"), run.get("updated_at"))
    matrix_url = f"https://github.com/{repo}/blob/main/release/README.md"

    # Coloured circles rather than check/cross marks: a column of them is faster
    # to scan, and they do not read as emoji reactions on the message.
    if outcome == "timed_out":
        headline = f":hourglass_flowing_sand:  *{name}*  ·  timed out after {duration}  ·  `{sha}`"
    elif outcome == "failure":
        headline = f":red_circle:  *{name}*  ·  failed after {duration}  ·  `{sha}`"
    elif outcome != "success":
        # Defensive: main() filters these out, so reaching here means a new
        # conclusion appeared and must not be painted green.
        headline = f":grey_question:  *{name}*  ·  {outcome}  ·  {duration}  ·  `{sha}`"
    elif badge_failed:
        headline = f":warning:  *{name}*  ·  test passed, `update-badge` failed  ·  {duration}  ·  `{sha}`"
    else:
        headline = f":large_green_circle:  *{name}*  ·  {duration}  ·  `{sha}`"

    # A re-run landing hours off the usual schedule is confusing without this.
    if attempt > 1:
        headline += f"  (attempt {attempt})"

    if outcome in FAILED_CONCLUSIONS:
        detail = []
        if failing_job:
            detail.append(f"failed in `{escape_mrkdwn(failing_job)}`")
        detail.append(f"<{url}|run #{number}>")
        detail.append(f"<{matrix_url}|matrix>")
        return headline + "\n" + "  ·  ".join(detail)

    return f"{headline}  ·  <{url}|run #{number}>"


def build_payload(channel: str, text: str) -> dict:
    return {
        "channel": channel,
        "text": text,
        "mrkdwn": True,
        # Without this Slack expands a preview card for every GitHub link, and a
        # handful of messages turns the channel into a wall.
        "unfurl_links": False,
        "unfurl_media": False,
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def write_github_output(channel: str, payload: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        print("::warning::GITHUB_OUTPUT is not set; skipping step outputs", file=sys.stderr)
        return
    with open(output_path, "a", encoding="utf-8") as fh:
        fh.write(f"channel={channel}\n")
        fh.write(f"payload={payload}\n")


def write_step_summary(lines: list[str]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="owner/name of the repository")
    parser.add_argument("--run-id", required=True, help="id of the nightly run to report on")
    parser.add_argument("--channel", default="", help="post to this channel instead of the mapped one")
    parser.add_argument("--dry-run", action="store_true", help="print the payload and exit without emitting outputs")
    parser.add_argument("--json", action="store_true", help="print only the payload JSON")
    parser.add_argument("--github-output", action="store_true", help="write channel/payload to $GITHUB_OUTPUT")
    args = parser.parse_args()

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit("ERROR: set GH_TOKEN (locally: GH_TOKEN=$(gh auth token)).")

    run = fetch_run(args.repo, args.run_id, token)
    jobs = fetch_jobs(args.repo, args.run_id, token)

    workflow_file = Path(run.get("path", "")).name
    outcome, failing_job, badge_failed = nightly_outcome(jobs)

    if outcome not in NOTIFIABLE_OUTCOMES:
        print(f"{workflow_file} run {args.run_id} concluded {outcome!r}; nothing worth notifying.")
        if args.github_output:
            write_github_output("", "")
        return 0

    mapping = load_mapping()
    channel, is_fallback = (args.channel, False) if args.channel else resolve_channel(mapping, workflow_file)

    if channel is None:
        print(f"{workflow_file} is listed as not notified in {MAPPING_PATH.name}; nothing to send.")
        if args.github_output:
            write_github_output("", "")
        return 0

    if is_fallback:
        print(
            f"::warning::{workflow_file} has no Slack channel assigned; "
            f"falling back to {channel}. Add it to .github/slack-channels.yaml."
        )

    text = build_message(args.repo, run, outcome, failing_job, badge_failed)
    if is_fallback:
        text = f":grey_question:  _unrouted workflow_ `{workflow_file}`\n{text}"

    payload = json.dumps(build_payload(channel, text), ensure_ascii=False)

    if args.json:
        print(payload)
        return 0

    print(f"workflow file : {workflow_file}")
    print(f"upstream event: {run.get('event')}")
    print(f"run conclusion: {run.get('conclusion')}")
    print(f"test outcome  : {outcome}" + (f" (in {failing_job})" if failing_job else ""))
    print(f"badge failed  : {badge_failed}")
    print(f"channel       : {channel}" + ("  [fallback]" if is_fallback else ""))
    print("-" * 72)
    print(text)
    print("-" * 72)

    write_step_summary(
        [
            "### Slack notification",
            "",
            f"- **Channel**: `{channel}`" + ("  _(fallback)_" if is_fallback else ""),
            f"- **Workflow file**: `{workflow_file}`",
            f"- **Test outcome**: `{outcome}`" + (f" in `{failing_job}`" if failing_job else ""),
            f"- **Badge job failed**: `{badge_failed}`",
            "",
            "```",
            text,
            "```",
        ]
    )

    if args.dry_run:
        print("(--dry-run: no outputs emitted, nothing posted)")
        return 0

    if args.github_output:
        write_github_output(channel, payload)

    return 0


if __name__ == "__main__":
    sys.exit(main())
