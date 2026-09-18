#!/usr/bin/env python3
"""Validate the Slack channel mapping and regenerate the notify workflow trigger.

Usage:
  python scripts/sync-slack-channels.py --check   # exit 1 if out of sync (default)
  python scripts/sync-slack-channels.py --fix     # regenerate the trigger in-place
  python scripts/sync-slack-channels.py --audit   # print the routing table

.github/slack-channels.yaml is the source of truth: it routes each nightly
workflow to a Slack channel, keyed by file name. The `workflows:` list in
.github/workflows/notify-slack-nightly.yaml is derived from it.

That list has to hold display names because `workflow_run` matches on them and
offers nothing else, which makes it the one part that breaks silently: rename a
workflow and the notifications simply stop, with no error anywhere. Generating
the list -- and checking it here -- turns that silent break into a failure in
the same PR that does the renaming.

The workflow discovery helpers are shared with the testing matrices; see
scripts/matrix_common.py.
"""

import argparse
import difflib
import re
import sys
from pathlib import Path

import matrix_common as mc
import yaml

# ---------------------------------------------------------------------------
# Paths and sentinels
# ---------------------------------------------------------------------------

MAPPING_PATH = mc.REPO_ROOT / ".github" / "slack-channels.yaml"
NOTIFY_WORKFLOW_PATH = mc.REPO_ROOT / ".github" / "workflows" / "notify-slack-nightly.yaml"

TRIGGER_START = "      # SLACK-WORKFLOWS-START"
TRIGGER_END = "      # SLACK-WORKFLOWS-END"

# Indentation of the generated list items inside `on.workflow_run.workflows`.
ITEM_INDENT = "      "

# Every nightly workflow must be routed or explicitly skipped. Globbing
# `nightly-*` rather than matrix_common's `nightly-e2e-*` deliberately widens
# the net: nightly-build-image.yaml is a nightly too, and forcing an explicit
# decision about it is the point of the coverage check.
NIGHTLY_GLOB = "nightly-*.yaml"

CHANNEL_RE = re.compile(r"^#[a-z0-9][a-z0-9._-]*$")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_mapping() -> dict:
    with MAPPING_PATH.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    for key in ("fallback_channel", "channels", "skip"):
        if key not in data:
            raise SystemExit(f"ERROR: {MAPPING_PATH} is missing the top-level '{key}' key.")

    return data


def workflow_display_name(path: Path) -> str | None:
    """Return a workflow's display `name:`.

    Only a top-level `name:` (column zero) is the workflow name; the same key
    nested under jobs or steps names something else entirely.
    """
    m = re.search(r"^name:\s*(.+)$", path.read_text(encoding="utf-8"), re.MULTILINE)
    return m.group(1).strip() if m else None


def discover_nightly_names() -> dict[str, str]:
    """Return {file_name: display_name} for every nightly workflow on disk."""
    result = {}
    for path in sorted(mc.WORKFLOWS_DIR.glob(NIGHTLY_GLOB)):
        name = workflow_display_name(path)
        if name is not None:
            result[path.name] = name
    return result


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate(mapping: dict, on_disk: dict[str, str]) -> list[str]:
    """Return a list of human-readable problems; empty means the mapping is sound."""
    errors: list[str] = []
    channels: dict[str, list[str]] = mapping["channels"] or {}
    skip: dict[str, str] = mapping["skip"] or {}

    # Channel naming, so a typo surfaces here rather than as a Slack API error
    # at 3am. Slack channel names are lowercase by construction.
    for channel in [mapping["fallback_channel"], *channels]:
        if not CHANNEL_RE.match(channel or ""):
            errors.append(f"'{channel}' is not a valid Slack channel name (expected e.g. '#sig-router').")

    routed: dict[str, str] = {}
    for channel, files in channels.items():
        for file_name in files or []:
            if file_name in routed:
                errors.append(
                    f"{file_name} is routed to both '{routed[file_name]}' and '{channel}'. "
                    "A workflow can only notify one channel."
                )
            routed[file_name] = channel

    # A file cannot be both routed and skipped -- that reads as a decision but
    # is really a contradiction, and which one wins would be arbitrary.
    for file_name in sorted(set(routed) & set(skip)):
        errors.append(f"{file_name} appears in both 'channels' and 'skip'. Pick one.")

    # Existence: catches renamed and deleted workflow files.
    for file_name in sorted(set(routed) | set(skip)):
        if file_name not in on_disk:
            errors.append(
                f"{file_name} is listed in {MAPPING_PATH.name} but no such workflow exists "
                f"in {mc.WORKFLOWS_DIR.relative_to(mc.REPO_ROOT)}/."
            )

    # Coverage: the guardrail. A nightly missing from this file is also missing
    # from the generated trigger, so it never fires and nobody notices.
    for file_name in sorted(set(on_disk) - set(routed) - set(skip)):
        errors.append(
            f"{file_name} has no Slack channel assigned. Add it to 'channels' in "
            f"{MAPPING_PATH.name}, or to 'skip' with a reason."
        )

    # A skip without a reason is indistinguishable from an oversight.
    for file_name, reason in sorted(skip.items()):
        if not (reason or "").strip():
            errors.append(f"{file_name} is skipped without a reason. Say why it is not notified.")

    # workflow_run matches on display names, so duplicates among routed
    # workflows would fire this notifier twice for one upstream run.
    seen: dict[str, str] = {}
    for file_name in sorted(routed):
        display = on_disk.get(file_name)
        if display is None:
            continue
        if display in seen:
            errors.append(
                f"{file_name} and {seen[display]} share the display name '{display}'. "
                "workflow_run cannot tell them apart; rename one."
            )
        seen[display] = file_name

    return errors


# ---------------------------------------------------------------------------
# Trigger generation
# ---------------------------------------------------------------------------


def generate_trigger_list(mapping: dict, on_disk: dict[str, str]) -> str:
    """Render the `workflows:` list items for the notify workflow's trigger.

    Skipped workflows are omitted: keeping them would spend trigger entries on
    runs the notifier then discards.

    Names are quoted so a future workflow name containing ':' or '#' cannot
    silently change the meaning of the generated YAML.
    """
    names = sorted(
        on_disk[file_name]
        for files in (mapping["channels"] or {}).values()
        for file_name in (files or [])
        if file_name in on_disk
    )
    return "\n".join(f'{ITEM_INDENT}- "{name}"' for name in names)


def read_notify_workflow() -> str:
    if not NOTIFY_WORKFLOW_PATH.exists():
        raise SystemExit(f"ERROR: {NOTIFY_WORKFLOW_PATH} does not exist.")
    return NOTIFY_WORKFLOW_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def run_audit(mapping: dict, on_disk: dict[str, str]) -> int:
    channels: dict[str, list[str]] = mapping["channels"] or {}
    skip: dict[str, str] = mapping["skip"] or {}

    for channel in sorted(channels):
        files = sorted(channels[channel] or [])
        print(f"\n{channel}  ({len(files)})")
        for file_name in files:
            print(f"    {on_disk.get(file_name, '<MISSING WORKFLOW>')}")
            print(f"        {file_name}")

    print(f"\nnot notified  ({len(skip)})")
    for file_name, reason in sorted(skip.items()):
        print(f"    {file_name}")
        print(f"        {' '.join((reason or '').split())}")

    routed = sum(len(f or []) for f in channels.values())
    print(f"\n{routed} routed + {len(skip)} skipped = {routed + len(skip)} of {len(on_disk)} nightly workflows")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", default=True, help="fail if out of sync (default)")
    group.add_argument("--fix", action="store_true", help="regenerate the trigger list in the notify workflow")
    group.add_argument("--audit", action="store_true", help="print the routing table for human review")
    args = parser.parse_args()

    mapping = load_mapping()
    on_disk = discover_nightly_names()

    if args.audit:
        return run_audit(mapping, on_disk)

    errors = validate(mapping, on_disk)
    if errors:
        print(f"ERROR: {MAPPING_PATH.relative_to(mc.REPO_ROOT)} is not valid.", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    content = read_notify_workflow()
    current = mc.extract_matrix(content, TRIGGER_START, TRIGGER_END)
    if current is None:
        print(
            f"ERROR: sentinel comments not found in {NOTIFY_WORKFLOW_PATH}.\n"
            f"Add '{TRIGGER_START.strip()}' and '{TRIGGER_END.strip()}' around the workflows list.",
            file=sys.stderr,
        )
        return 1

    expected = generate_trigger_list(mapping, on_disk)

    if current.strip("\n") == expected.strip("\n"):
        print("Slack channel mapping is up to date.")
        return 0

    if args.fix:
        updated = mc.replace_matrix(content, expected, TRIGGER_START, TRIGGER_END)
        NOTIFY_WORKFLOW_PATH.write_text(updated, encoding="utf-8")
        print(f"Updated the workflow_run trigger in {NOTIFY_WORKFLOW_PATH.relative_to(mc.REPO_ROOT)}")
        return 0

    diff = difflib.unified_diff(
        current.strip("\n").splitlines(keepends=True),
        expected.strip("\n").splitlines(keepends=True),
        fromfile="notify-slack-nightly.yaml (current)",
        tofile="notify-slack-nightly.yaml (expected)",
        lineterm="",
    )
    print("ERROR: the workflow_run trigger in notify-slack-nightly.yaml is out of sync.", file=sys.stderr)
    print("Run: python scripts/sync-slack-channels.py --fix", file=sys.stderr)
    for line in diff:
        print(line.rstrip("\n"), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
