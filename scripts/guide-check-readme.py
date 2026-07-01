#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["pyyaml"]
# ///
"""Validate a guide README against its guide.yaml.

Prints all findings and exits non-zero on any error.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml


ANY_START = re.compile(r'<!--\s*guide:(\S+)\s+start\s*-->')
ANY_END   = re.compile(r'<!--\s*guide:(\S+)\s+end\s*-->')

MARKER_PAIR = re.compile(
    r'(<!--\s*guide:(?P<path>\S+)\s+start\s*-->)'
    r'(?P<body>.*?)'
    r'(<!--\s*guide:(?P=path)\s+end\s*-->)',
    re.DOTALL,
)

BASH_FENCE       = re.compile(r'```bash\n.*?\n```', re.DOTALL)
CICD_SKIP_MARKER = re.compile(r'<!--\s*llm-d-cicd:skip\s+(?:start|end)\s*-->')

_PATH_TOKEN = re.compile(r'\.|\[(\d+)\]')


class Findings:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def ok(self) -> bool:
        return not self.errors

    def report(self) -> None:
        for e in self.errors:
            print(f"error: {e}", file=sys.stderr)


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def path_exists(guide: dict, path: str) -> tuple[bool, str]:
    tokens: list[str | int] = []
    last = 0
    for m in _PATH_TOKEN.finditer(path):
        if m.start() > last:
            tokens.append(path[last:m.start()])
        if m.group(1) is not None:
            tokens.append(int(m.group(1)))
        last = m.end()
    if last < len(path):
        tokens.append(path[last:])
    tokens = [t for t in tokens if t != ""]

    cur = guide
    for t in tokens:
        if isinstance(t, int):
            if not isinstance(cur, list):
                return False, f"path {path!r}: cannot index into non-list"
            if t >= len(cur):
                return False, f"path {path!r}: list index {t} out of range"
            cur = cur[t]
        else:
            if not isinstance(cur, dict) or t not in cur:
                return False, f"path {path!r} not found in YAML"
            cur = cur[t]
    return True, ""


def check_markers(text: str, guide: dict, f: Findings) -> None:
    events = []
    for m in ANY_START.finditer(text):
        events.append((m.start(), "start", m.group(1)))
    for m in ANY_END.finditer(text):
        events.append((m.start(), "end", m.group(1)))
    events.sort()

    stack: list[tuple[str, int]] = []
    for pos, kind, path in events:
        line = line_of(text, pos)
        if kind == "start":
            if stack:
                open_path, open_pos = stack[-1]
                open_line = line_of(text, open_pos)
                f.error(
                    f"line {line}: nested marker — guide:{path} start "
                    f"before guide:{open_path} end (opened at line {open_line})"
                )
            stack.append((path, pos))
        else:
            if not stack:
                f.error(f"line {line}: orphan end marker guide:{path}")
                continue
            open_path, open_pos = stack.pop()
            if open_path != path:
                open_line = line_of(text, open_pos)
                f.error(
                    f"line {line}: mismatched markers — guide:{open_path} start at "
                    f"line {open_line} closed by guide:{path} end"
                )
    if stack:
        open_path, open_pos = stack[-1]
        open_line = line_of(text, open_pos)
        f.error(f"line {open_line}: unclosed marker guide:{open_path} start")


def _is_valid_body(body: str) -> bool:
    """A body is valid if — after stripping cicd:skip HTML comments and one or
    more ```bash fences — only whitespace remains. This admits both the simple
    single-fence case and multi-fence bodies with CI-skip wrappers around
    individual fences."""
    stripped = CICD_SKIP_MARKER.sub("", body)
    stripped = BASH_FENCE.sub("", stripped)
    return stripped.strip() == ""


def check_bodies(text: str, guide: dict, f: Findings) -> None:
    for m in MARKER_PAIR.finditer(text):
        path = m.group("path")
        body = m.group("body")
        line = line_of(text, m.start())

        ok, msg = path_exists(guide, path)
        if not ok:
            f.error(f"line {line}: guide:{path} — {msg}")

        if not _is_valid_body(body):
            f.error(
                f"line {line}: guide:{path} — body between markers must be one or "
                f"more fenced ```bash blocks (with optional "
                f"<!-- llm-d-cicd:skip start/end --> wrappers)"
            )


def check(text: str, guide: dict) -> Findings:
    f = Findings()
    check_markers(text, guide, f)
    if f.ok():
        check_bodies(text, guide, f)
    return f


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--yaml",   required=True, type=Path, metavar="PATH")
    ap.add_argument("--readme", required=True, type=Path, metavar="PATH")
    args = ap.parse_args()

    with args.yaml.open() as fh:
        guide = yaml.safe_load(fh)
    text = args.readme.read_text()

    findings = check(text, guide)
    findings.report()
    if not findings.ok():
        print(f"\n{len(findings.errors)} error(s) — {args.readme}", file=sys.stderr)
        sys.exit(1)
    print(f"{args.readme}: OK")


if __name__ == "__main__":
    main()
