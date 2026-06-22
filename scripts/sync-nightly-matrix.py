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

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
README_PATH = REPO_ROOT / "release" / "README.md"

# ---------------------------------------------------------------------------
# Sentinel markers in the README
# ---------------------------------------------------------------------------

MATRIX_START = "<!-- NIGHTLY-MATRIX-START -->"
MATRIX_END = "<!-- NIGHTLY-MATRIX-END -->"

# ---------------------------------------------------------------------------
# Table structure
# ---------------------------------------------------------------------------

BADGE_BASE = "https://github.com/llm-d/llm-d/actions/workflows"
SHIELDS_ENDPOINT = "https://img.shields.io/endpoint?url=https://llm-d.github.io/llm-d/badges"

PROVIDERS = ["ibm", "cks", "gke", "amd", "intel"]
PROVIDER_LABELS = {"ibm": "IBM", "cks": "CKS", "gke": "GKE", "amd": "AMD", "intel": "Intel"}

ACCELERATOR_LABELS = {
    "gpu": "GPU",
    "tpu": "TPU",
    "rocm": "ROCm",
    "xpu": "XPU",
}

# (display_name, guide_path, workflow_slugs, connector_filter)
# workflow_slugs: a string or tuple of strings to match parsed guide slugs.
# connector_filter: None matches any connector; a string matches only that variant.
GUIDES = [
    ("Optimized Baseline", "../guides/optimized-baseline/README.md", "optimized-baseline", None),
    ("Precise Prefix Cache Routing", "../guides/precise-prefix-cache-routing/README.md", ("precise-prefix-cache-routing", "precise-prefix-cache"), None),
    ("P/D Disaggregation", "../guides/pd-disaggregation/README.md", "pd-disaggregation", None),
    ("Wide Expert Parallelism", "../guides/wide-ep-lws/README.md", "wide-ep-lws", None),
    ("Tiered Prefix Cache (CPU Offloading)", "../guides/tiered-prefix-cache/README.md", "tiered-prefix-cache", "native"),
    ("Tiered Prefix Cache (LMCache)", "../guides/tiered-prefix-cache/README.md", "tiered-prefix-cache", "lmcache"),
    ("Predicted Latency-Based Routing", "../guides/predicted-latency-routing/README.md", "predicted-latency-routing", None),
    ("Workload Autoscaling (WVA)", "../guides/workload-autoscaling/README.md", "wva", None),
]

# ---------------------------------------------------------------------------
# Workflow filename convention:
#   nightly-e2e-{guide_slug}-{provider}-{offload_dest}-{accelerator}-{engine}-{connector}.yaml
# ---------------------------------------------------------------------------

WORKFLOW_PREFIX = "nightly-e2e-"


def _extract_badge_name(path: Path) -> str | None:
    """Extract the badge_name value from a workflow YAML file."""
    content = path.read_text(encoding="utf-8")
    m = re.search(r"badge_name:\s*(\S+)", content)
    return m.group(1) if m else None


def discover_workflows() -> dict[tuple[str, str, str], list[str]]:
    """Scan the workflows directory and return a mapping.

    Returns:
        dict keyed by (guide_slug, provider, connector) -> sorted list of
        (accelerator, filename, badge_name) tuples.
    """
    result: dict[tuple[str, str, str], list[tuple[str, str, str]]] = {}

    for path in sorted(WORKFLOWS_DIR.glob(f"{WORKFLOW_PREFIX}*.yaml")):
        filename = path.name
        stem = filename.removeprefix(WORKFLOW_PREFIX).removesuffix(".yaml")

        parsed = _parse_workflow_stem(stem)
        if parsed is None:
            continue

        badge_name = _extract_badge_name(path)
        if badge_name is None:
            continue

        guide_slug, provider, _offload_dest, accelerator, _engine, connector = parsed
        key = (guide_slug, provider, connector)
        result.setdefault(key, []).append((accelerator, filename, badge_name))

    for entries in result.values():
        entries.sort()

    return result


def _parse_workflow_stem(stem: str) -> tuple[str, str, str, str, str, str] | None:
    """Parse a workflow stem into its components.

    Returns (guide_slug, provider, offload_dest, accelerator, engine, connector)
    or None if parsing fails.
    """
    for provider in PROVIDERS:
        marker = f"-{provider}-"
        idx = stem.find(marker)
        if idx == -1:
            continue

        guide_slug = stem[:idx]
        suffix = stem[idx + len(marker):]
        parts = suffix.split("-")
        if len(parts) != 4:
            continue

        offload_dest, accelerator, engine, connector = parts
        return (guide_slug, provider, offload_dest, accelerator, engine, connector)

    return None


def badge(accelerator: str, filename: str, badge_name: str) -> str:
    label = ACCELERATOR_LABELS.get(accelerator, accelerator.upper())
    badge_img = f"{SHIELDS_ENDPOINT}/{badge_name}.json"
    link = f"{BADGE_BASE}/{filename}"
    return f"[![{label}]({badge_img})]({link})"


def generate_table(workflows: dict) -> str:
    header = "| Guide | " + " | ".join(PROVIDER_LABELS[p] for p in PROVIDERS) + " |"
    separator = "|-------|" + "|".join("-----" for _ in PROVIDERS) + "|"
    lines = [header, separator]

    for display_name, guide_path, guide_slugs, connector_filter in GUIDES:
        if isinstance(guide_slugs, str):
            guide_slugs = (guide_slugs,)
        cells = [f"[{display_name}]({guide_path})"]

        for provider in PROVIDERS:
            badges = []
            if connector_filter is not None:
                for slug in guide_slugs:
                    key = (slug, provider, connector_filter)
                    badges.extend(badge(acc, fn, bn) for acc, fn, bn in workflows.get(key, []))
            else:
                for key, entries in workflows.items():
                    if key[0] in guide_slugs and key[1] == provider:
                        badges.extend(badge(acc, fn, bn) for acc, fn, bn in entries)

            cells.append(" ".join(badges))

        lines.append("| " + " | ".join(cells) + " |")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# README manipulation
# ---------------------------------------------------------------------------


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
    return pattern.sub(f"{MATRIX_START}\n{new_table}\n{MATRIX_END}", content)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


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

    workflows = discover_workflows()
    expected = generate_table(workflows)

    if current.strip() == expected.strip():
        print("Nightly matrix is up to date.")
        return 0

    if args.fix:
        updated = replace_matrix(content, expected)
        README_PATH.write_text(updated, encoding="utf-8")
        print(f"Updated nightly matrix in {README_PATH}")
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
