#!/usr/bin/env -S uv run --script
# /// script
# dependencies = []
# ///
"""
Lint Markdown files for unmatched <details>/<summary> HTML tags.

MDX (used by Docusaurus for the llm-d.ai website) treats inline HTML as JSX
and enforces strict tag pairing, even though GitHub-Flavored Markdown silently
ignores unmatched tags. This script catches those mismatches before they break
the website build.

Rules checked:
  1. Every <details> opener must have a matching </details> closer.
  2. Every </details> closer must have a matching <details> opener.
  3. Every <summary> must be the first non-blank content inside its <details>
     block — any non-tag content between <details> and <summary> is an error.
  4. Every <summary> opener must have a matching </summary> closer.
  5. Every </summary> closer must have a matching <summary> opener.

Tags inside fenced code blocks (``` ... ```) are ignored.
"""

import re
import sys
from pathlib import Path


_DETAILS_OPEN = re.compile(r"<details(\s[^>]*)?>", re.IGNORECASE)
_DETAILS_CLOSE = re.compile(r"</details>", re.IGNORECASE)
_SUMMARY_OPEN = re.compile(r"<summary(\s[^>]*)?>", re.IGNORECASE)
_SUMMARY_CLOSE = re.compile(r"</summary>", re.IGNORECASE)
_CODE_FENCE = re.compile(r"^(\s*)(`{3,}|~{3,})")


def check_file(path: Path) -> list[str]:
    """Return a list of error strings for *path*, empty if the file is clean."""
    lines = path.read_text(encoding="utf-8").splitlines()
    errors: list[str] = []

    in_code_block = False
    fence_pattern: str | None = None  # full fence string, e.g. "```" or "~~~~"

    # Stack of line numbers for unclosed <details> / <summary> openers.
    details_stack: list[int] = []
    summary_stack: list[int] = []

    # True after a <details> opener until <summary> or non-blank content is seen.
    # Used to enforce that <summary> is the first element inside <details>.
    summary_expected = False

    for lineno, line in enumerate(lines, start=1):
        # Track fenced code blocks so we skip tags inside them.
        fence_match = _CODE_FENCE.match(line)
        if fence_match:
            indent, fence_chars = fence_match.group(1), fence_match.group(2)
            if not in_code_block:
                in_code_block = True
                fence_pattern = fence_chars  # store full string, e.g. "```"
            elif (
                # closing fence: same character, at least as many chars, nothing else on line
                fence_chars[0] == fence_pattern[0]
                and len(fence_chars) >= len(fence_pattern)
                and line.strip() == fence_chars.strip()
            ):
                in_code_block = False
                fence_pattern = None
        if in_code_block:
            continue

        # Determine which tags appear on this line (order matters for same-line pairs).
        has_details_open = bool(_DETAILS_OPEN.search(line))
        has_details_close = bool(_DETAILS_CLOSE.search(line))
        has_summary_open = bool(_SUMMARY_OPEN.search(line))
        has_summary_close = bool(_SUMMARY_CLOSE.search(line))

        if has_details_open:
            details_stack.append(lineno)
            summary_expected = True

        if has_details_close:
            if details_stack:
                details_stack.pop()
            else:
                errors.append(
                    f"{path}:{lineno}: dangling </details> with no matching <details>"
                )
            # Fix 1: clear expectation once the block is closed so a <summary>
            # immediately after </details> is not silently accepted.
            summary_expected = False

        if has_summary_open:
            if not summary_expected:
                errors.append(
                    f"{path}:{lineno}: <summary> is not immediately inside a <details> block"
                )
            summary_expected = False
            summary_stack.append(lineno)

        # Fix 3: validate </summary> closers.
        if has_summary_close:
            if summary_stack:
                summary_stack.pop()
            else:
                errors.append(
                    f"{path}:{lineno}: dangling </summary> with no matching <summary>"
                )

        # Fix 2: any non-blank content that is not one of the four tracked tags
        # cancels the expectation that the next element will be <summary>.
        if summary_expected and line.strip() and not (
            has_details_open or has_details_close
            or has_summary_open or has_summary_close
        ):
            summary_expected = False

    for lineno in details_stack:
        errors.append(f"{path}:{lineno}: unclosed <details> with no matching </details>")
    for lineno in summary_stack:
        errors.append(f"{path}:{lineno}: unclosed <summary> with no matching </summary>")

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
