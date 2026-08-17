#!/usr/bin/env python3
"""Validate or regenerate the nightly testing matrix in release/README.md.

Usage:
  python scripts/sync-nightly-matrix.py --check   # exit 1 if out of sync (default)
  python scripts/sync-nightly-matrix.py --fix     # regenerate the matrix in-place

The workflow discovery and guide/provider configuration are shared with the
release matrix; see scripts/matrix_common.py.
"""

import argparse
import sys

import matrix_common as mc

# ---------------------------------------------------------------------------
# Sentinel markers in the README
# ---------------------------------------------------------------------------

MATRIX_START = "<!-- NIGHTLY-MATRIX-START -->"
MATRIX_END = "<!-- NIGHTLY-MATRIX-END -->"


def badge(accelerator: str, filename: str, badge_name: str, engine: str) -> str:
    label = mc.accel_engine_label(engine, accelerator)
    badge_img = f"{mc.SHIELDS_ENDPOINT}/{badge_name}.json"
    link = f"{mc.BADGE_BASE}/{filename}"
    return f"[![{label}]({badge_img})]({link})"


def generate_table(workflows: dict) -> str:
    header = "| Guide | " + " | ".join(mc.PROVIDER_LABELS[p] for p in mc.PROVIDERS) + " |"
    separator = "|-------|" + "|".join("-----" for _ in mc.PROVIDERS) + "|"
    lines = [header, separator]

    for display_name, guide_path, guide_slugs, connector_filter in mc.GUIDES:
        cells = [f"[{display_name}]({guide_path})"]

        for provider in mc.PROVIDERS:
            badges = [
                badge(acc, fn, bn, eng)
                for acc, fn, bn, eng in mc.iter_provider_entries(
                    workflows, guide_slugs, provider, connector_filter
                )
            ]
            cells.append(" ".join(badges))

        lines.append("| " + " | ".join(cells) + " |")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    if not (mc.REPO_ROOT / ".release-team").exists():
        return 0

    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", default=True, help="fail if matrix is out of sync (default)")
    group.add_argument("--fix", action="store_true", help="regenerate the matrix in release/README.md")
    args = parser.parse_args()

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
    expected = generate_table(workflows)

    if current.strip() == expected.strip():
        print("Nightly matrix is up to date.")
        return 0

    if args.fix:
        updated = mc.replace_matrix(content, expected, MATRIX_START, MATRIX_END)
        mc.README_PATH.write_text(updated, encoding="utf-8")
        print(f"Updated nightly matrix in {mc.README_PATH}")
        return 0

    import difflib

    diff = difflib.unified_diff(
        current.splitlines(keepends=True),
        expected.splitlines(keepends=True),
        fromfile="release/README.md (current)",
        tofile="release/README.md (expected)",
    )
    print("ERROR: nightly matrix in release/README.md is out of sync.", file=sys.stderr)
    print("Run: python scripts/sync-nightly-matrix.py --fix", file=sys.stderr)
    sys.stderr.writelines(diff)
    return 1


if __name__ == "__main__":
    sys.exit(main())
