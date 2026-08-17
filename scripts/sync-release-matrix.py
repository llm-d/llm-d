#!/usr/bin/env python3
"""Validate or regenerate the release testing matrix in release/README.md.

Unlike the nightly matrix (a live mirror of main), the release matrix is a
*frozen snapshot* captured at release time: each cell renders a static shields
badge baked from the badge status read at the moment a ``v*`` tag was pushed.
The block is prefixed with a caption linking the release tag.

Usage:
  python scripts/sync-release-matrix.py --fix --version v0.8.1 --status-file status.json
  python scripts/sync-release-matrix.py --check --version v0.8.1 --status-file status.json

The status file is a JSON object mapping ``badge_name`` -> ``{"message", "color"}``,
typically produced by the release-matrix workflow after reading the live badges.
The workflow discovery and guide/provider configuration are shared with the
nightly matrix; see scripts/matrix_common.py.
"""

import argparse
import json
import sys
from pathlib import Path

import matrix_common as mc

# ---------------------------------------------------------------------------
# Sentinel markers in the README
# ---------------------------------------------------------------------------

MATRIX_START = "<!-- RELEASE-MATRIX-START -->"
MATRIX_END = "<!-- RELEASE-MATRIX-END -->"

RELEASE_TAG_URL = "https://github.com/llm-d/llm-d/releases/tag"

# Fallback shown when a badge has no captured status (e.g. endpoint unavailable).
DEFAULT_MESSAGE = "not run"
DEFAULT_COLOR = "lightgrey"


def _shields_escape(text: str) -> str:
    """Escape a value for a shields.io static badge path segment.

    Dashes become ``--``, underscores ``__``, and spaces ``_``.
    """
    return text.replace("-", "--").replace("_", "__").replace(" ", "_")


def static_badge(accelerator: str, filename: str, status: dict, engine: str) -> str:
    # Label by engine + accelerator (e.g. "vLLM GPU", "SGLang GPU", "TRTLLM GPU")
    # via the shared helper, so multiple same-accelerator engines in one cell stay
    # distinguishable and match the nightly matrix labels exactly.
    label = mc.accel_engine_label(engine, accelerator)
    message = status.get("message") or DEFAULT_MESSAGE
    color = status.get("color") or DEFAULT_COLOR
    segment = f"{_shields_escape(label)}-{_shields_escape(message)}-{_shields_escape(color)}"
    badge_img = f"https://img.shields.io/badge/{segment}"
    link = f"{mc.BADGE_BASE}/{filename}"
    return f"[![{label}]({badge_img})]({link})"


def generate_body(workflows: dict, version: str, statuses: dict) -> str:
    caption = f"**Release snapshot:** [`{version}`]({RELEASE_TAG_URL}/{version})"

    header = "| Guide | " + " | ".join(mc.PROVIDER_LABELS[p] for p in mc.PROVIDERS) + " |"
    separator = "|-------|" + "|".join("-----" for _ in mc.PROVIDERS) + "|"
    lines = [header, separator]

    for display_name, guide_path, guide_slugs, connector_filter in mc.GUIDES:
        cells = [f"[{display_name}]({guide_path})"]

        for provider in mc.PROVIDERS:
            badges = [
                static_badge(acc, fn, statuses.get(bn, {}), eng)
                for acc, fn, bn, eng in mc.iter_provider_entries(
                    workflows, guide_slugs, provider, connector_filter
                )
            ]
            cells.append(" ".join(badges))

        lines.append("| " + " | ".join(cells) + " |")

    table = "\n".join(lines)
    return f"{caption}\n\n{table}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", required=True, help="release tag, e.g. v0.8.1 or v0.9.0-rc1")
    parser.add_argument(
        "--status-file",
        required=True,
        help="JSON mapping badge_name -> {message, color}",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", default=True, help="fail if matrix is out of sync (default)")
    group.add_argument("--fix", action="store_true", help="regenerate the matrix in release/README.md")
    args = parser.parse_args()

    try:
        statuses = json.loads(Path(args.status_file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"ERROR: could not read status file {args.status_file}: {exc}", file=sys.stderr)
        return 1

    content = mc.read_readme()
    current = mc.extract_matrix(content, MATRIX_START, MATRIX_END)

    if current is None:
        print(
            f"ERROR: sentinel comments not found in {mc.README_PATH}.\n"
            f"Add '{MATRIX_START}' and '{MATRIX_END}' around the table.",
            file=sys.stderr,
        )
        return 1

    workflows = mc.discover_workflows()
    expected = generate_body(workflows, args.version, statuses)

    if current.strip() == expected.strip():
        print("Release matrix is up to date.")
        return 0

    if args.fix:
        updated = mc.replace_matrix(content, expected, MATRIX_START, MATRIX_END)
        mc.README_PATH.write_text(updated, encoding="utf-8")
        print(f"Updated release matrix in {mc.README_PATH} for {args.version}")
        return 0

    import difflib

    diff = difflib.unified_diff(
        current.splitlines(keepends=True),
        expected.splitlines(keepends=True),
        fromfile="release/README.md (current)",
        tofile="release/README.md (expected)",
    )
    print("ERROR: release matrix in release/README.md is out of sync.", file=sys.stderr)
    print(
        "Run: python scripts/sync-release-matrix.py --fix "
        f"--version {args.version} --status-file {args.status_file}",
        file=sys.stderr,
    )
    sys.stderr.writelines(diff)
    return 1


if __name__ == "__main__":
    sys.exit(main())
