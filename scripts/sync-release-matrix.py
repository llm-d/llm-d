#!/usr/bin/env python3
"""Regenerate a release's section of the release testing matrix in release/README.md.

Unlike the nightly matrix (a live mirror of main), the release matrix reports e2e
runs made *against a release branch*. Those runs are dispatched with
``matrix_type=<release branch>``, which makes llm-d-infra's reusables check out
that branch and write their badge to ``badges/{badge_name}_{matrix_type}.json``.
Each cell here is a live shields endpoint pointing at that per-release file, so a
re-run of a lane updates the table on its own — no re-render needed.

The block is organised as one section per release *series* (``release-0.9``), each
delimited by its own sentinel pair inside the RELEASE-MATRIX container. A series
that already has a section is regenerated in place; a new series is inserted at
the top. Older sections are never touched: they are frozen records, and not
regenerating them is what keeps them stable as the guide and workflow set drift
on main.

Usage:
  python scripts/sync-release-matrix.py --version v0.9.0            # preview
  python scripts/sync-release-matrix.py --version v0.9.0 --fix      # write
  python scripts/sync-release-matrix.py --version v0.9.0 --branch release-0.9 --fix

There is deliberately no ``--check`` mode: the older sections are snapshots, so
comparing them against main's current guide list would fail spuriously. The
workflow discovery and guide/provider configuration are shared with the nightly
matrix; see scripts/matrix_common.py.
"""

import argparse
import re
import sys

import matrix_common as mc

# ---------------------------------------------------------------------------
# Sentinel markers in the README
# ---------------------------------------------------------------------------

MATRIX_START = "<!-- RELEASE-MATRIX-START -->"
MATRIX_END = "<!-- RELEASE-MATRIX-END -->"

RELEASE_TAG_URL = f"{mc.REPO_URL}/releases/tag"
BRANCH_URL = f"{mc.REPO_URL}/tree"

VERSION_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-.+)?$")

# Matches any per-series section, so we can tell "no sections yet" from
# "sections exist, ours is not among them".
SECTION_RE = re.compile(r"<!-- RELEASE-MATRIX-(\S+?)-START -->")


def series_markers(series: str) -> tuple[str, str]:
    return (
        f"<!-- RELEASE-MATRIX-{series}-START -->",
        f"<!-- RELEASE-MATRIX-{series}-END -->",
    )


def derive_series(version: str) -> str:
    """v0.9.0 -> release-0.9 ; v0.10.1-rc.1 -> release-0.10."""
    m = VERSION_RE.match(version)
    if m is None:
        raise ValueError(
            f"version {version!r} is not of the form vMAJOR.MINOR.PATCH[-suffix]"
        )
    return f"release-{m.group(1)}.{m.group(2)}"


def badge(accelerator: str, filename: str, badge_name: str, engine: str, connector: str, series: str) -> str:
    label = mc.accel_engine_label(engine, accelerator, connector)
    badge_img = mc.badge_endpoint(badge_name, series)
    link = f"{mc.BADGE_BASE}/{filename}"
    return f"[![{label}]({badge_img})]({link})"


def generate_section(workflows: dict, version: str, series: str) -> str:
    heading = (
        f"### [`{version}`]({RELEASE_TAG_URL}/{version})"
        f" — branch [`{series}`]({BRANCH_URL}/{series})"
    )

    header = "| Guide | " + " | ".join(mc.PROVIDER_LABELS[p] for p in mc.PROVIDERS) + " |"
    separator = "|-------|" + "|".join("-----" for _ in mc.PROVIDERS) + "|"
    lines = [header, separator]

    for display_name, guide_path, guide_slugs, connector_filter in mc.GUIDES:
        cells = [f"[{display_name}]({guide_path})"]

        for provider in mc.PROVIDERS:
            badges = [
                badge(acc, fn, bn, eng, conn, series)
                for acc, fn, bn, eng, conn in mc.iter_provider_entries(
                    workflows, guide_slugs, provider, connector_filter
                )
            ]
            cells.append(" ".join(badges))

        lines.append("| " + " | ".join(cells) + " |")

    start, end = series_markers(series)
    table = "\n".join(lines)
    return f"{start}\n{heading}\n\n{table}\n{end}"


def splice_section(container_body: str, section: str, series: str) -> str:
    """Put ``section`` into ``container_body``, leaving other sections untouched.

    Three cases:
      * this series already has a section -> replace it in place
      * other series have sections        -> insert this one at the top
      * no sections at all               -> the body is a placeholder, replace it
    """
    start, end = series_markers(series)

    if mc.extract_matrix(container_body, start, end) is not None:
        return mc.replace_matrix(
            container_body, section[len(start) + 1 : -(len(end) + 1)], start, end
        )

    if SECTION_RE.search(container_body):
        return f"{section}\n\n{container_body.strip()}"

    return section


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        required=True,
        help="release tag, e.g. v0.9.0 or v0.9.0-rc.1",
    )
    parser.add_argument(
        "--branch",
        help="release branch / series (default: derived from --version, e.g. release-0.9)",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="write release/README.md (default: print the section to stdout)",
    )
    args = parser.parse_args()

    try:
        series = args.branch or derive_series(args.version)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    workflows = mc.discover_workflows()
    section = generate_section(workflows, args.version, series)

    if not args.fix:
        print(section)
        return 0

    content = mc.read_readme()
    container = mc.extract_matrix(content, MATRIX_START, MATRIX_END)
    if container is None:
        print(
            f"ERROR: sentinel comments not found in {mc.README_PATH}.\n"
            f"Add '{MATRIX_START}' and '{MATRIX_END}' around the release matrix.",
            file=sys.stderr,
        )
        return 1

    updated_container = splice_section(container, section, series)
    if updated_container.strip() == container.strip():
        print(f"Release matrix for {series} is already up to date.")
        return 0

    updated = mc.replace_matrix(content, updated_container, MATRIX_START, MATRIX_END)
    mc.README_PATH.write_text(updated, encoding="utf-8")
    print(f"Updated {series} section of the release matrix for {args.version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
