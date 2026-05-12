#!/usr/bin/env python3
"""Validate or regenerate the nightly testing matrix in release/README.md.

Usage:
  python scripts/sync-nightly-matrix.py --check   # exit 1 if out of sync (default)
  python scripts/sync-nightly-matrix.py --fix     # regenerate the matrix in-place
"""

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
README_PATH = REPO_ROOT / "release" / "README.md"

MATRIX_START = "<!-- NIGHTLY-MATRIX-START -->"
MATRIX_END = "<!-- NIGHTLY-MATRIX-END -->"

BADGE_BASE = "https://github.com/llm-d/llm-d/actions/workflows"

PLATFORMS = ["ocp", "cks", "gke"]
PLATFORM_LABELS = {"ocp": "OCP", "cks": "CKS", "gke": "GKE"}

# Each entry: (display_name, guide_path, {platform: workflow_slug})
# workflow_slug: the part between 'nightly-e2e-' and '-{platform}.yaml'
ROWS = [
    (
        "Optimized Baseline",
        "../guides/optimized-baseline/README.md",
        {"ocp": "optimized-baseline", "cks": "optimized-baseline", "gke": "optimized-baseline"},
    ),
    (
        "Precise Prefix Cache Aware Routing",
        "../guides/precise-prefix-cache-aware/README.md",
        {"ocp": "precise-prefix-cache", "cks": "precise-prefix-cache", "gke": "precise-prefix-cache"},
    ),
    (
        "P/D Disaggregation",
        "../guides/pd-disaggregation/README.md",
        {"ocp": "pd-disaggregation", "cks": "pd-disaggregation", "gke": "pd-disaggregation"},
    ),
    (
        "Wide Expert Parallelism",
        "../guides/wide-ep-lws/README.md",
        {"ocp": "wide-ep-lws", "cks": "wide-ep-lws", "gke": "wide-ep-lws"},
    ),
    (
        "Tiered Prefix Cache (CPU Offloading)",
        "../guides/tiered-prefix-cache/README.md",
        {"ocp": "tiered-prefix-cache-cpu-offloading", "gke": "tiered-prefix-cache-cpu-offloading"},
    ),
    (
        "Tiered Prefix Cache (LMCache)",
        "../guides/tiered-prefix-cache/README.md",
        {"gke": "tiered-prefix-cache-cpu-offloading-lmcache"},
    ),
    (
        "Predicted Latency-Based Routing",
        "../guides/predicted-latency-based-scheduling/README.md",
        {"ocp": "predicted-latency", "cks": "predicted-latency", "gke": "predicted-latency"},
    ),
    (
        "Workload Autoscaling (WVA)",
        "../guides/workload-autoscaling/README.md",
        {"ocp": "wva", "cks": "wva"},
    ),
]


def workflow_exists(slug: str, platform: str) -> bool:
    return (WORKFLOWS_DIR / f"nightly-e2e-{slug}-{platform}.yaml").exists()


def badge_cell(slug: str, platform: str) -> str:
    label = PLATFORM_LABELS[platform]
    url = f"{BADGE_BASE}/nightly-e2e-{slug}-{platform}.yaml"
    return f"[![{label}]({url}/badge.svg)]({url})"


def generate_table() -> str:
    header = "| Guide | " + " | ".join(PLATFORM_LABELS[p] for p in PLATFORMS) + " |"
    separator = "|-------|" + "|".join("-----" for _ in PLATFORMS) + "|"
    lines = [header, separator]

    for display_name, guide_path, platform_slugs in ROWS:
        cells = [f"[{display_name}]({guide_path})"]
        for platform in PLATFORMS:
            slug = platform_slugs.get(platform)
            if slug and workflow_exists(slug, platform):
                cells.append(badge_cell(slug, platform))
            else:
                cells.append("")
        lines.append("| " + " | ".join(cells) + " |")

    return "\n".join(lines)


def read_readme() -> str:
    return README_PATH.read_text(encoding="utf-8")


def extract_matrix(content: str) -> str | None:
    pattern = re.compile(
        re.escape(MATRIX_START) + r"\n(.*?)\n" + re.escape(MATRIX_END),
        re.DOTALL,
    )
    m = pattern.search(content)
    return m.group(1) if m else None


def replace_matrix(content: str, new_table: str) -> str:
    pattern = re.compile(
        re.escape(MATRIX_START) + r"\n.*?\n" + re.escape(MATRIX_END),
        re.DOTALL,
    )
    replacement = f"{MATRIX_START}\n{new_table}\n{MATRIX_END}"
    return pattern.sub(replacement, content)


def main() -> int:
    if not (REPO_ROOT / ".release-team").exists():
        return 0

    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", default=True, help="fail if matrix is out of sync (default)")
    group.add_argument("--fix", action="store_true", help="regenerate the matrix in release/README.md")
    args = parser.parse_args()

    content = read_readme()
    current = extract_matrix(content)

    if current is None:
        print(
            f"ERROR: sentinel comments not found in {README_PATH}.\n"
            f"Add '{MATRIX_START}' and '{MATRIX_END}' around the table.",
            file=sys.stderr,
        )
        return 1

    expected = generate_table()

    if current.strip() == expected.strip():
        print("Nightly matrix is up to date.")
        return 0

    if args.fix:
        updated = replace_matrix(content, expected)
        README_PATH.write_text(updated, encoding="utf-8")
        print(f"Updated nightly matrix in {README_PATH}")
        return 0

    # --check mode: show diff and fail
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
