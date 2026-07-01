#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["pyyaml"]
# ///
"""Render the bash code blocks of a guide's README from its companion guide.yaml.

The README marks regions to be filled from YAML with paired HTML comments:

    <!-- guide:<yaml-path> start -->
    ```bash
    …anything here is replaced on render…
    ```
    <!-- guide:<yaml-path> end -->

Anything outside marker pairs is preserved byte-for-byte. Rendering is
idempotent — running twice produces an identical file.

Usage:
  guide-render.py --yaml <guide.yaml> --readme <README.md>
  guide-render.py --yaml <guide.yaml> --readme <README.md> --dry-run
  guide-render.py --yaml <guide.yaml> --readme <README.md> --check
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

MARKER_PAIR = re.compile(
    r"(<!--\s*guide:(?P<path>\S+)\s+start\s*-->)"
    r"(?P<body>.*?)"
    r"(<!--\s*guide:(?P=path)\s+end\s*-->)",
    re.DOTALL,
)

ANY_START = re.compile(r"<!--\s*guide:(\S+)\s+start\s*-->")
ANY_END = re.compile(r"<!--\s*guide:(\S+)\s+end\s*-->")

_PATH_TOKEN = re.compile(r"\.|\[(\d+)\]")


def navigate(guide: dict, path: str):
    tokens: list[str | int] = []
    last = 0
    for m in _PATH_TOKEN.finditer(path):
        if m.start() > last:
            tokens.append(path[last : m.start()])
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
                raise SystemExit(f"error: path {path!r} — cannot index into non-list")
            if t >= len(cur):
                raise SystemExit(f"error: path {path!r} — list index {t} out of range")
            cur = cur[t]
        else:
            if not isinstance(cur, dict) or t not in cur:
                raise SystemExit(f"error: path {path!r} not found in YAML")
            cur = cur[t]
    return cur


CICD_SKIP_START = "<!-- llm-d-cicd:skip start -->"
CICD_SKIP_END = "<!-- llm-d-cicd:skip end -->"


def _fence(body: str) -> str:
    """Wrap a bash body in a fenced ```bash markdown block."""
    return f"```bash\n{body}\n```"


def _env_static_body(node) -> str:
    if not isinstance(node, dict):
        raise SystemExit("error: env.static must be a map")
    lines: list[str] = []
    for var, spec in node.items():
        if isinstance(spec, dict):
            if spec.get("sensitive"):
                if "default" not in spec:
                    raise SystemExit(
                        f"error: sensitive variable {var!r} has no `default:` to use as README placeholder"
                    )
                lines.append(f"export {var}={spec['default']}")
            elif "default" in spec:
                line = f"export {var}={spec['default']}"
                if spec.get("values"):
                    line += " # options: " + ", ".join(str(v) for v in spec["values"])
                lines.append(line)
            else:
                raise SystemExit(
                    f"error: variable {var!r} has neither a value nor a default — cannot render"
                )
        else:
            lines.append(f"export {var}={spec}")
    return "\n".join(lines)


def _env_source_body(node) -> str:
    """env.source entries are emitted verbatim — write the full path (including any
    `${REPO_ROOT}/` prefix you want) directly in the YAML."""
    if not isinstance(node, list):
        raise SystemExit("error: env.source must be a list")
    return "\n".join(f"source {src}" for src in node)


def _format_filters(step: dict) -> str:
    """Turn a step's `when:` filter into a bash comment prefix.
    Returns an empty string if the step has no when: filter.

    `skip_in:` is rendered structurally (wraps the fence in HTML skip markers),
    not as a bash comment — see `_group_steps`.
    """
    when = step.get("when") or {}
    if not when:
        return ""
    clauses = []
    for var, allowed in when.items():
        values = " or ".join(str(v) for v in allowed)
        clauses.append(f"{var}={values}")
    return "# only when " + " and ".join(clauses) + ":"


def _render_step_body(step: dict) -> str:
    """Bash body for one step, with any `when:` filter prefixed as a comment."""
    body = str(step["run"]).rstrip()
    prefix = _format_filters(step)
    return f"{prefix}\n{body}" if prefix else body


def _flatten_steps(node) -> list[dict]:
    """Walk a step-list node (flat list, single step map, or map of named sub-groups)
    and return one flat list of step dicts in render order."""
    if isinstance(node, dict) and "run" in node:
        return [node]
    if isinstance(node, list):
        for step in node:
            if not isinstance(step, dict) or "run" not in step:
                raise SystemExit(
                    f"error: every step must be a map with a 'run:' key, got {step!r}"
                )
        return list(node)
    if isinstance(node, dict):
        result: list[dict] = []
        for group_steps in node.values():
            result.extend(_flatten_steps(group_steps))
        return result
    raise SystemExit(
        f"error: don't know how to render node of type {type(node).__name__}"
    )


def _is_ci_skip(step: dict) -> bool:
    return "ci" in (step.get("skip_in") or [])


def render_steps(node) -> str:
    """Return markdown-ready content — one or more ```bash fences, with contiguous
    `skip_in: [ci]` steps wrapped in `<!-- llm-d-cicd:skip start/end -->` so a
    README-parsing CI tool can skip them."""
    steps = _flatten_steps(node)
    if not steps:
        return _fence("")

    # Group contiguous steps by ci-skip status so we emit one fence per run.
    groups: list[tuple[bool, list[str]]] = []
    for step in steps:
        rendered = _render_step_body(step)
        ci_skip = _is_ci_skip(step)
        if groups and groups[-1][0] == ci_skip:
            groups[-1][1].append(rendered)
        else:
            groups.append((ci_skip, [rendered]))

    parts: list[str] = []
    for ci_skip, bodies in groups:
        fence = _fence("\n\n".join(bodies))
        if ci_skip:
            parts.append(f"{CICD_SKIP_START}\n{fence}\n{CICD_SKIP_END}")
        else:
            parts.append(fence)
    return "\n".join(parts)


def render_path(guide: dict, path: str) -> str:
    """Return markdown-ready content (already fenced) for the given YAML path."""
    if path == "env.static":
        return _fence(_env_static_body(navigate(guide, path)))
    if path == "env.source":
        return _fence(_env_source_body(navigate(guide, path)))
    return render_steps(navigate(guide, path))


def validate_markers(text: str) -> None:
    events = []
    for m in ANY_START.finditer(text):
        events.append((m.start(), "start", m.group(1)))
    for m in ANY_END.finditer(text):
        events.append((m.start(), "end", m.group(1)))
    events.sort()

    stack: list[tuple[str, int]] = []
    for pos, kind, path in events:
        if kind == "start":
            if stack:
                open_path, open_pos = stack[-1]
                raise SystemExit(
                    f"error: nested marker — guide:{path} start at offset {pos} "
                    f"appears before guide:{open_path} end (open since offset {open_pos})"
                )
            stack.append((path, pos))
        else:
            if not stack:
                raise SystemExit(
                    f"error: orphan end marker guide:{path} end at offset {pos}"
                )
            open_path, open_pos = stack.pop()
            if open_path != path:
                raise SystemExit(
                    f"error: mismatched markers — guide:{open_path} start at {open_pos} "
                    f"closed by guide:{path} end at {pos}"
                )
    if stack:
        open_path, open_pos = stack[-1]
        raise SystemExit(
            f"error: unclosed marker guide:{open_path} start at offset {open_pos}"
        )


def render(guide: dict, text: str) -> str:
    validate_markers(text)

    def replace(match: re.Match) -> str:
        # render_path returns markdown that already contains its own ```bash
        # fence(s) and any cicd:skip wrappers — just inject it between the
        # guide: marker pair.
        body = render_path(guide, match.group("path"))
        return f"{match.group(1)}\n{body}\n{match.group(4)}"

    return MARKER_PAIR.sub(replace, text)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--yaml", required=True, type=Path, metavar="PATH")
    ap.add_argument("--readme", required=True, type=Path, metavar="PATH")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = ap.parse_args()

    with args.yaml.open() as f:
        guide = yaml.safe_load(f)
    original = args.readme.read_text()
    rendered = render(guide, original)

    if args.dry_run:
        sys.stdout.write(rendered)
        return

    if args.check:
        if rendered != original:
            print(
                f"error: {args.readme} is out of date — re-run "
                f"`guide-render.py --yaml {args.yaml} --readme {args.readme}`",
                file=sys.stderr,
            )
            sys.exit(1)
        return

    if rendered != original:
        args.readme.write_text(rendered)
        print(f"updated {args.readme}")
    else:
        print(f"{args.readme} already up to date")


if __name__ == "__main__":
    main()
