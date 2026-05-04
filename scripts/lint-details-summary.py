#!/usr/bin/env -S uv run --script
# /// script
# dependencies = []
# ///
"""
Lint Markdown files for unmatched <details>/<summary> HTML tags.

MDX (used by Docusaurus in llm-d.io website) treats inline HTML as JSX and enforces strict tag
pairing, even though GitHub-Flavored Markdown silently ignores unmatched tags.
This script catches those mismatches before they break the website build.

Rules checked:
  1. Every <details> opener must have a matching </details> closer.
  2. Every </details> closer must have a matching <details> opener.
  3. Every <summary> must be immediately preceded (within 2 non-blank lines)
     by a <details> opener.

Tags inside fenced code blocks (``` ... ```) are ignored.
"""

import re
import sys
from pathlib import Path


_DETAILS_OPEN = re.compile(r"<details(\s[^>]*)?>", re.IGNORECASE)
_DETAILS_CLOSE = re.compile(r"</details>", re.IGNORECASE)
_SUMMARY_OPEN = re.compile(r"<summary(\s[^>]*)?>", re.IGNORECASE)
_CODE_FENCE = re.compile(r"^(\s*)(`{3,}|~{3,})")


def check_file(path: Path) -> list[str]:
    """Return a list of error strings for *path*, empty if the file is clean."""
    lines = path.read_text(encoding="utf-8").splitlines()
    errors: list[str] = []

    in_code_block = False
    fence_pattern: str | None = None
    fence_indent: str = ""

    # Stack of line numbers for unmatched <details> openers.
    open_stack: list[int] = []
    # Line number of the most recent <details> opener (used for <summary> check).
    last_details_lineno: int | None = None

    for lineno, line in enumerate(lines, start=1):
        # Track fenced code blocks so we skip tags inside them.
        fence_match = _CODE_FENCE.match(line)
        if fence_match:
            indent, fence_chars = fence_match.group(1), fence_match.group(2)
            if not in_code_block:
                in_code_block = True
                fence_pattern = fence_chars[0]
                fence_indent = indent
            elif (
                fence_chars[0] == fence_pattern
                and len(fence_chars) >= len(fence_pattern)
                and line.strip() == fence_chars.strip()
            ):
                in_code_block = False
                fence_pattern = None
        if in_code_block:
            continue

        if _DETAILS_OPEN.search(line):
            open_stack.append(lineno)
            last_details_lineno = lineno

        if _DETAILS_CLOSE.search(line):
            if open_stack:
                open_stack.pop()
            else:
                errors.append(f"{path}:{lineno}: dangling </details> with no matching <details>")

        if _SUMMARY_OPEN.search(line):
            # A <summary> must be within 2 non-blank lines of a <details> opener.
            if last_details_lineno is None or (lineno - last_details_lineno) > 2:
                errors.append(
                    f"{path}:{lineno}: <summary> is not immediately inside a <details> block"
                )

    for lineno in open_stack:
        errors.append(f"{path}:{lineno}: unclosed <details> with no matching </details>")

    return errors


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: lint-details-summary.py <file.md> [file2.md ...]", file=sys.stderr)
        return 1

    all_errors: list[str] = []
    for arg in sys.argv[1:]:
        all_errors.extend(check_file(Path(arg)))

    for err in all_errors:
        print(err, file=sys.stderr)

    return 1 if all_errors else 0


if __name__ == "__main__":
    sys.exit(main())
