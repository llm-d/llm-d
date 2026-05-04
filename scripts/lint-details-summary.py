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
# Allow optional blockquote prefix (e.g. "> ```bash") before the fence characters.
_CODE_FENCE = re.compile(r"^(\s*(?:>\s*)*)(`{3,}|~{3,})")
# Splitter that tokenises a line into alternating non-tag / tag segments so
# tags can be processed in document order with correct multiplicity.
_ANY_TAG = re.compile(
    r"(<details(?:\s[^>]*)?>|</details>|<summary(?:\s[^>]*)?>|</summary>)",
    re.IGNORECASE,
)


def check_file(path: Path) -> list[str]:
    """Return a list of error strings for *path*, empty if the file is clean."""
    lines = path.read_text(encoding="utf-8").splitlines()
    errors: list[str] = []

    in_code_block = False
    fence_pattern: str | None = None  # full fence string, e.g. "```" or "~~~~"

    # Unified stack to track open tags and enforce proper nesting.
    # Each entry is a tuple of (tag_type, lineno) where tag_type is 'details' or 'summary'.
    tag_stack: list[tuple[str, int]] = []

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
                # closing fence: same character, at least as many chars, nothing else on line.
                # Use fence_match.end() instead of line.strip() so that blockquoted
                # fences like "> ```" are recognised as closers correctly.
                fence_chars[0] == fence_pattern[0]
                and len(fence_chars) >= len(fence_pattern)
                and fence_match.end() == len(line.rstrip())
            ):
                in_code_block = False
                fence_pattern = None
        if in_code_block:
            continue

        # Split the line into alternating [text, tag, text, tag, …, text] segments
        # so that tags are processed strictly left-to-right with correct multiplicity.
        # Odd-indexed segments are matched tags; even-indexed are the text between them.
        segments = _ANY_TAG.split(line)
        for idx, segment in enumerate(segments):
            if idx % 2 == 0:
                # Plain text segment — any non-blank content cancels the
                # expectation that the very next thing is a <summary>.
                # Strip blockquote prefixes (e.g., "> ") to get actual content.
                content = re.sub(r'^(\s*>\s*)+', '', segment).strip()
                if summary_expected and content:
                    summary_expected = False
            else:
                # Tag segment — classify and update state.
                tag_lower = segment.lower()
                if _DETAILS_OPEN.match(tag_lower):
                    tag_stack.append(('details', lineno))
                    summary_expected = True
                elif _DETAILS_CLOSE.match(tag_lower):
                    if tag_stack and tag_stack[-1][0] == 'details':
                        tag_stack.pop()
                    elif tag_stack:
                        # Wrong tag type on top of stack - crossed nesting
                        wrong_tag, wrong_lineno = tag_stack[-1]
                        errors.append(
                            f"{path}:{lineno}: </details> closes before <{wrong_tag}> from line {wrong_lineno}"
                        )
                    else:
                        errors.append(
                            f"{path}:{lineno}: dangling </details> with no matching <details>"
                        )
                    summary_expected = False
                elif _SUMMARY_OPEN.match(tag_lower):
                    if not summary_expected:
                        errors.append(
                            f"{path}:{lineno}: <summary> is not immediately inside a <details> block"
                        )
                    summary_expected = False
                    tag_stack.append(('summary', lineno))
                elif _SUMMARY_CLOSE.match(tag_lower):
                    if tag_stack and tag_stack[-1][0] == 'summary':
                        tag_stack.pop()
                    elif tag_stack:
                        # Wrong tag type on top of stack - crossed nesting
                        wrong_tag, wrong_lineno = tag_stack[-1]
                        errors.append(
                            f"{path}:{lineno}: </summary> closes before <{wrong_tag}> from line {wrong_lineno}"
                        )
                    else:
                        errors.append(
                            f"{path}:{lineno}: dangling </summary> with no matching <summary>"
                        )

    for tag_type, lineno in tag_stack:
        errors.append(f"{path}:{lineno}: unclosed <{tag_type}> with no matching </{tag_type}>")

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
