#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["pyyaml"]
# ///
"""Validate and render llm-d well-lit-path guides.

A guide is two files:

  * ``guide.yaml``  — machine-readable source of truth (env, prerequisites,
    deploy, verify, benchmark, cleanup) consumed by CI and deployment tooling.
  * ``README.md``   — human-readable prose whose bash code blocks are *rendered
    from* the YAML. Regions to fill are delimited by paired HTML comments::

        <!-- guide:<yaml-path> start -->
        ```bash
        …replaced on render…
        ```
        <!-- guide:<yaml-path> end -->

    Anything outside a marker pair is preserved byte-for-byte, and rendering is
    idempotent.

This module is both a CLI and an importable library. See ``Guide`` for the API.

Either half can be validated on its own. Marker-path resolution is the only
cross-file check, so it is the only thing lost when checking a markdown file
alone — and the tool says so rather than reporting a bare "OK".

CLI
---
    guide.py check  guides/optimized-baseline       # both halves, discovered
    guide.py check  guides/*/                       # batch — reports every guide
    guide.py check  --yaml g.yaml --md README.md    # explicit paths
    guide.py check  --yaml g.yaml                   # schema only
    guide.py check  --md README.md                  # marker structure only

    guide.py render guides/optimized-baseline       # validate, then write
    guide.py render guides/optimized-baseline --check    # CI: fail if stale
    guide.py render guides --recursive --check       # CI: check every guide
    guide.py render guides/optimized-baseline --dry-run  # print to stdout

    guide.py emit guides/flow-control env deploy.standalone
    guide.py emit guides/flow-control env prerequisites.crds \
        --context ci --var NAMESPACE=my-ns          # bash on stdout, for tooling

``render`` validates before it writes and refuses to render an invalid guide,
so a normal authoring loop only ever needs ``guide.py render <dir>``.

``emit`` assembles an executable bash script from guide.yaml sections, so
deployment tooling (e.g. the nightly deploy scripts) consumes the guide's
commands instead of copying them.

Library
-------
    from guide import Guide

    g = Guide.load("guides/optimized-baseline")   # dir, or guide.yaml path
    g = Guide.load(yaml="g.yaml", md="README.md") # explicit, either optional
    findings = g.check()
    if findings.ok():
        g.write()

Files can be supplied as paths or as text, so callers never need a working
directory::

    g = Guide.from_text(yaml_text=..., md_text=...)   # either may be omitted
    rendered = g.render()
"""

from __future__ import annotations

import argparse
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

import yaml

__all__ = [
    "Finding",
    "Findings",
    "Guide",
    "GuideError",
    "emit_script",
    "parse_guide_yaml",
]


# --------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """A single validation problem.

    ``source`` is ``"yaml"`` or ``"md"`` — which of the two files the
    problem was found in. ``line`` is 1-indexed when known.
    """

    message: str
    source: str = "yaml"
    line: int | None = None
    severity: str = "error"

    def __str__(self) -> str:
        loc = f"line {self.line}: " if self.line is not None else ""
        return f"{loc}{self.message}"


class Findings:
    """An ordered collection of ``Finding``s.

    Truthy when empty-and-clean is *false* — prefer the explicit ``ok()``.
    """

    def __init__(self, items: Iterable[Finding] | None = None) -> None:
        self._items: list[Finding] = list(items or [])

    # -- building ----------------------------------------------------------

    def error(self, message: str, *, source: str = "yaml", line: int | None = None) -> None:
        self._items.append(Finding(message, source, line, "error"))

    def extend(self, other: "Findings" | Iterable[Finding]) -> "Findings":
        self._items.extend(other if not isinstance(other, Findings) else other._items)
        return self

    # -- querying ----------------------------------------------------------

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self._items if f.severity == "error"]

    def ok(self) -> bool:
        return not self.errors

    def for_source(self, source: str) -> list[Finding]:
        return [f for f in self._items if f.source == source]

    def __iter__(self) -> Iterator[Finding]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def report(self, stream=sys.stderr, prefix: str = "") -> None:
        for f in self._items:
            print(f"{prefix}{f.severity}: {f}", file=stream)


class GuideError(Exception):
    """Raised when an operation cannot proceed (e.g. rendering a guide whose
    README markers are malformed). Carries the ``Findings`` that caused it."""

    def __init__(self, message: str, findings: Findings | None = None) -> None:
        super().__init__(message)
        self.findings = findings or Findings()


# --------------------------------------------------------------------------
# YAML loading — strict about duplicate keys
# --------------------------------------------------------------------------
#
# PyYAML's SafeLoader silently keeps the LAST value when a key repeats in a
# mapping. That masked a real bug in optimized-baseline where a `cleanup:` step
# had two `run:` entries and the intended delete was overwritten. Every entry
# point here uses this loader — the previous split scripts used it only in the
# YAML checker, so render and README-validation silently accepted duplicates
# the checker would have rejected.


class _StrictLoader(yaml.SafeLoader):
    pass


def _no_duplicate_keys(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False):
    seen: dict[object, yaml.Node] = {}
    for key_node, _value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in seen
        except TypeError:
            raise yaml.constructor.ConstructorError(
                None,
                None,
                "unhashable mapping key; YAML mapping keys must be hashable",
                key_node.start_mark,
            )
        if duplicate:
            first_line = seen[key].start_mark.line + 1
            dup_line = key_node.start_mark.line + 1
            raise yaml.constructor.ConstructorError(
                None,
                None,
                (
                    f"duplicate key {key!r} in mapping "
                    f"(first at line {first_line}, again at line {dup_line}) — "
                    f"YAML silently keeps only the last value; if you meant two "
                    f"separate steps/entries, add another list item"
                ),
                key_node.start_mark,
            )
        seen[key] = key_node
    return loader.construct_mapping(node, deep=deep)


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _no_duplicate_keys,
)


def parse_guide_yaml(text: str) -> tuple[Any, Finding | None]:
    """Parse guide YAML strictly.

    Returns ``(data, None)`` on success, or ``(None, Finding)`` if the document
    could not be parsed. Parse failures are returned rather than raised so that
    batch validation can report every guide instead of aborting on the first.
    """
    try:
        return yaml.load(text, Loader=_StrictLoader), None
    except yaml.constructor.ConstructorError as e:
        mark = e.problem_mark
        return None, Finding(e.problem, "yaml", mark.line + 1 if mark else None)
    except yaml.YAMLError as e:
        return None, Finding(f"could not parse YAML: {e}", "yaml")


# --------------------------------------------------------------------------
# Marker grammar and YAML path navigation
# --------------------------------------------------------------------------

MARKER_PAIR = re.compile(
    r"(<!--\s*guide:(?P<path>\S+)\s+start\s*-->)"
    r"(?P<body>.*?)"
    r"(<!--\s*guide:(?P=path)\s+end\s*-->)",
    re.DOTALL,
)
ANY_START = re.compile(r"<!--\s*guide:(\S+)\s+start\s*-->")
ANY_END = re.compile(r"<!--\s*guide:(\S+)\s+end\s*-->")

BASH_FENCE = re.compile(r"```bash\n.*?\n```", re.DOTALL)
CICD_SKIP_MARKER = re.compile(r"<!--\s*llm-d-cicd:skip\s+(?:start|end)\s*-->")
CICD_SKIP_START = "<!-- llm-d-cicd:skip start -->"
CICD_SKIP_END = "<!-- llm-d-cicd:skip end -->"

_PATH_TOKEN = re.compile(r"\.|\[(\d+)\]")


def _tokenize_path(path: str) -> list[str | int]:
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
    return [t for t in tokens if t != ""]


def resolve_path(guide: Any, path: str) -> tuple[bool, Any, str]:
    """Resolve a dotted/indexed marker path against the parsed YAML.

    Returns ``(found, value, error_message)``. A single resolver replaces the
    two divergent implementations the split scripts carried — one that raised
    ``SystemExit`` and one that returned a tuple.
    """
    cur = guide
    for t in _tokenize_path(path):
        if isinstance(t, int):
            if not isinstance(cur, list):
                return False, None, f"path {path!r}: cannot index into non-list"
            if t >= len(cur):
                return False, None, f"path {path!r}: list index {t} out of range"
            cur = cur[t]
        else:
            if not isinstance(cur, dict) or t not in cur:
                return False, None, f"path {path!r} not found in YAML"
            cur = cur[t]
    return True, cur, ""


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


# --------------------------------------------------------------------------
# guide.yaml schema validation
# --------------------------------------------------------------------------

TOP_REQUIRED = {"name", "env", "deploy"}
TOP_OPTIONAL = {"_lists", "prerequisites", "verify", "benchmark", "cleanup"}
STEP_KEYS = {"run", "when", "skip_in"}
ENV_KEYS = {"static", "source", "derive"}
ENV_VAR_KEYS = {"default", "values", "sensitive"}


def _check_step(step: Any, path: str, declared: set[str], f: Findings) -> None:
    if not isinstance(step, dict):
        f.error(f"{path}: step must be a map with 'run:' key, got {type(step).__name__}")
        return
    if "run" not in step:
        f.error(f"{path}: step missing required 'run:' key")
    if not isinstance(step.get("run", ""), str):
        f.error(f"{path}: 'run:' must be a string")

    when = step.get("when")
    if when is not None:
        if not isinstance(when, dict):
            f.error(f"{path}: 'when:' must be a map, got {type(when).__name__}")
        else:
            for var, allowed in when.items():
                if var not in declared:
                    f.error(f"{path}: 'when:' references undeclared variable {var!r}")
                if not isinstance(allowed, list):
                    f.error(
                        f"{path}: 'when:.{var}' must be a list, got {type(allowed).__name__}"
                    )

    skip_in = step.get("skip_in")
    if skip_in is not None:
        if not isinstance(skip_in, list):
            f.error(f"{path}: 'skip_in:' must be a list, got {type(skip_in).__name__}")
        elif not all(isinstance(c, str) for c in skip_in):
            f.error(f"{path}: 'skip_in:' entries must be strings")

    for k in step:
        if k not in STEP_KEYS:
            f.error(f"{path}: unknown step key {k!r} (allowed: {sorted(STEP_KEYS)})")


def _check_step_list(node: Any, path: str, declared: set[str], f: Findings) -> None:
    """A step-list slot is either a flat list of steps or a map of named
    sub-groups (each value itself a step list). Both render to the same
    concatenated bash block."""
    if isinstance(node, list):
        for i, step in enumerate(node):
            _check_step(step, f"{path}[{i}]", declared, f)
        return
    if isinstance(node, dict):
        for group_name, group_steps in node.items():
            if not isinstance(group_name, str):
                f.error(
                    f"{path}: sub-group name must be a string, got {type(group_name).__name__}"
                )
                continue
            _check_step_list(group_steps, f"{path}.{group_name}", declared, f)
        return
    f.error(
        f"{path}: expected a list of steps (or a map of named sub-groups), "
        f"got {type(node).__name__}"
    )


def _in_values(value: Any, allowed: list) -> bool:
    """Membership against a declared ``values:`` list, compared as strings —
    YAML may parse entries (or the candidate) as ints or booleans."""
    return str(value) in [str(v) for v in allowed]


def _check_env(env: Any, f: Findings) -> set[str]:
    declared: set[str] = set()

    if not isinstance(env, dict):
        f.error("env: must be a map")
        return declared

    for k in env:
        if k not in ENV_KEYS:
            f.error(f"env: unknown key {k!r} (allowed: {sorted(ENV_KEYS)})")

    src = env.get("source")
    if src is not None:
        if not isinstance(src, list):
            f.error("env.source: must be a list")
        elif not all(isinstance(s, str) for s in src):
            f.error("env.source: entries must be strings")

    static = env.get("static")
    if not isinstance(static, dict):
        f.error("env.static: must be a map")
        return declared

    for var, spec in static.items():
        declared.add(var)
        if not isinstance(spec, dict):
            if spec is None or isinstance(spec, bool):
                # YAML null/true/false would render and emit as the Python
                # repr (`export VAR=None`). Quote the intended string.
                f.error(
                    f"env.static.{var}: value must be a string, got {spec!r} "
                    f"(quote YAML null/booleans)"
                )
            continue
        if "sensitive" in spec and not isinstance(spec["sensitive"], bool):
            # A truthy non-bool (`sensitive: "true"`) would pass the checks
            # below as non-sensitive yet be treated as sensitive by render
            # and emit — reject it before the semantics can diverge.
            f.error(
                f"env.static.{var}.sensitive: must be a YAML boolean, "
                f"got {spec['sensitive']!r}"
            )
        if "default" in spec and (
            spec["default"] is None or isinstance(spec["default"], bool)
        ):
            f.error(
                f"env.static.{var}.default: must be a string, got "
                f"{spec['default']!r} (quote YAML null/booleans)"
            )
        if spec.get("sensitive") is True:
            if "default" not in spec:
                f.error(
                    f"env.static.{var}: sensitive vars must have a `default:` "
                    f"to use as the README placeholder"
                )
        elif "default" not in spec:
            f.error(
                f"env.static.{var}: map form must have either `default:` or `sensitive: true`"
            )
        for k in spec:
            if k not in ENV_VAR_KEYS:
                f.error(f"env.static.{var}: unknown key {k!r} (allowed: {sorted(ENV_VAR_KEYS)})")
        if "values" in spec:
            if not isinstance(spec["values"], list):
                f.error(f"env.static.{var}.values: must be a list")
            elif "default" in spec and spec["values"] and not spec.get("sensitive"):
                if not _in_values(spec["default"], spec["values"]):
                    f.error(
                        f"env.static.{var}: default {spec['default']!r} "
                        f"not in values {spec['values']}"
                    )

    return declared


def discover_modes(guide: dict) -> list[str]:
    """Modes come from ``verify.endpoint`` keys, falling back to
    ``benchmark.endpoint``. Modes describe how to talk to the deployed system,
    so endpoint discovery is the natural source. ``[]`` if neither is present."""
    for section in ("verify", "benchmark"):
        node = guide.get(section)
        ep = node.get("endpoint") if isinstance(node, dict) else None
        if isinstance(ep, dict):
            return list(ep.keys())
    return []


def _check_verify(node: Any, declared: set[str], f: Findings) -> None:
    if not isinstance(node, dict):
        f.error("verify: must be a map")
        return
    for k in node:
        if k not in {"endpoint", "tests"}:
            f.error(f"verify: unknown key {k!r} (allowed: endpoint, tests)")
    ep = node.get("endpoint")
    if ep is not None:
        if not isinstance(ep, dict):
            f.error("verify.endpoint: must be a map keyed by mode")
        else:
            for k, v in ep.items():
                _check_step_list(v, f"verify.endpoint.{k}", declared, f)
    if node.get("tests") is not None:
        _check_step_list(node["tests"], "verify.tests", declared, f)


def _check_benchmark(node: Any, modes: list[str], declared: set[str], f: Findings) -> None:
    if not isinstance(node, dict):
        f.error("benchmark: must be a map")
        return
    for k in node:
        if k not in {"setup", "endpoint", "execute"}:
            f.error(f"benchmark: unknown key {k!r} (allowed: setup, endpoint, execute)")
    if "setup" in node:
        _check_step_list(node["setup"], "benchmark.setup", declared, f)
    ep = node.get("endpoint")
    if ep is not None:
        if not isinstance(ep, dict):
            f.error("benchmark.endpoint: must be a map keyed by mode")
        else:
            for k, v in ep.items():
                _check_step_list(v, f"benchmark.endpoint.{k}", declared, f)
            if modes and set(ep.keys()) != set(modes):
                f.error(
                    f"benchmark.endpoint keys {sorted(ep.keys())} don't match "
                    f"verify.endpoint keys {sorted(modes)} — modes must agree"
                )
    if "execute" in node:
        _check_step_list(node["execute"], "benchmark.execute", declared, f)


def check_yaml(guide: Any) -> Findings:
    """Validate a parsed guide.yaml against the well-lit-path schema."""
    f = Findings()

    if not isinstance(guide, dict):
        f.error(f"top level: must be a map, got {type(guide).__name__}")
        return f

    for k in sorted(TOP_REQUIRED):
        if k not in guide:
            f.error(f"top level: missing required key {k!r}")
    for k in guide:
        if k not in TOP_REQUIRED | TOP_OPTIONAL:
            f.error(
                f"top level: unknown key {k!r} "
                f"(allowed: {sorted(TOP_REQUIRED | TOP_OPTIONAL)})"
            )

    if not isinstance(guide.get("name"), str):
        f.error("name: must be a string")

    declared = _check_env(guide.get("env", {}), f)
    modes = discover_modes(guide)

    if "prerequisites" in guide:
        # Prerequisites are universal (never mode-specific in practice), so this
        # is a step-list bucket: a flat list OR a map of named sub-groups.
        _check_step_list(guide["prerequisites"], "prerequisites", declared, f)

    # `deploy:` is a step-list bucket too — each sub-group may be a mode name
    # (`standalone:`, `gateway:`) or a common sub-group (`modelserver:`). Modes
    # are discovered from `verify.endpoint`; mode-named sub-groups are NOT
    # required to exist, so a guide opts out of a mode by omitting it.
    _check_step_list(guide.get("deploy", {}), "deploy", declared, f)

    if "verify" in guide:
        _check_verify(guide["verify"], declared, f)
    if "benchmark" in guide:
        _check_benchmark(guide["benchmark"], modes, declared, f)
    if "cleanup" in guide:
        _check_step_list(guide["cleanup"], "cleanup", declared, f)

    return f


# --------------------------------------------------------------------------
# README validation
# --------------------------------------------------------------------------


def _walk_markers(text: str) -> list[tuple[int, str, str]]:
    events = [(m.start(), "start", m.group(1)) for m in ANY_START.finditer(text)]
    events += [(m.start(), "end", m.group(1)) for m in ANY_END.finditer(text)]
    events.sort()
    return events


def check_markers(text: str) -> Findings:
    """Validate marker pairing: no nesting, no orphans, no mismatches, none left
    open. Structural — does not consult the YAML."""
    f = Findings()
    stack: list[tuple[str, int]] = []
    for pos, kind, path in _walk_markers(text):
        line = _line_of(text, pos)
        if kind == "start":
            if stack:
                open_path, open_pos = stack[-1]
                f.error(
                    f"nested marker — guide:{path} start before guide:{open_path} "
                    f"end (opened at line {_line_of(text, open_pos)})",
                    source="md",
                    line=line,
                )
            stack.append((path, pos))
        else:
            if not stack:
                f.error(f"orphan end marker guide:{path}", source="md", line=line)
                continue
            open_path, open_pos = stack.pop()
            if open_path != path:
                f.error(
                    f"mismatched markers — guide:{open_path} start at line "
                    f"{_line_of(text, open_pos)} closed by guide:{path} end",
                    source="md",
                    line=line,
                )
    if stack:
        open_path, open_pos = stack[-1]
        f.error(
            f"unclosed marker guide:{open_path} start",
            source="md",
            line=_line_of(text, open_pos),
        )
    return f


def _is_valid_body(body: str) -> bool:
    """Valid if, after stripping cicd:skip comments and every ```bash fence,
    only whitespace remains. Admits both the single-fence case and multi-fence
    bodies with CI-skip wrappers around individual fences."""
    stripped = CICD_SKIP_MARKER.sub("", body)
    stripped = BASH_FENCE.sub("", stripped)
    return stripped.strip() == ""


def check_md(text: str, guide: Any = None) -> Findings:
    """Validate a guide markdown file.

    Without ``guide``, checks everything intrinsic to the markdown: marker
    pairing and body well-formedness. Pass ``guide`` to additionally resolve
    every ``guide:<path>`` against the YAML — the one check that cannot be made
    from the markdown alone. Callers that skip it should say so; see
    ``resolves_paths``.
    """
    f = check_markers(text)
    if not f.ok():
        # Body checks assume well-formed pairing; reporting both at once would
        # bury the real cause under cascading noise.
        return f

    for m in MARKER_PAIR.finditer(text):
        path = m.group("path")
        line = _line_of(text, m.start())
        if guide is not None:
            found, _value, msg = resolve_path(guide, path)
            if not found:
                f.error(f"guide:{path} — {msg}", source="md", line=line)
        if not _is_valid_body(m.group("body")):
            f.error(
                f"guide:{path} — body between markers must be one or more fenced "
                f"```bash blocks (with optional <!-- llm-d-cicd:skip start/end --> wrappers)",
                source="md",
                line=line,
            )
    return f


def marker_paths(text: str) -> list[str]:
    """Every ``guide:<path>`` referenced by the markdown, in document order."""
    return [m.group("path") for m in MARKER_PAIR.finditer(text)]


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def _fence(body: str) -> str:
    return f"```bash\n{body}\n```"


def _env_static_lines(node: Any) -> list[tuple[str, bool, str]]:
    """``(var, is_sensitive, export_line)`` for each variable, in declaration
    order. Shared by render (README fences) and emit (executable scripts)."""
    if not isinstance(node, dict):
        raise GuideError("env.static must be a map")
    out: list[tuple[str, bool, str]] = []
    for var, spec in node.items():
        if isinstance(spec, dict):
            if spec.get("sensitive"):
                if "default" not in spec:
                    raise GuideError(
                        f"sensitive variable {var!r} has no `default:` to use as "
                        f"README placeholder"
                    )
                out.append((var, True, f"export {var}={spec['default']}"))
            elif "default" in spec:
                line = f"export {var}={spec['default']}"
                if spec.get("values"):
                    line += " # options: " + ", ".join(str(v) for v in spec["values"])
                out.append((var, False, line))
            else:
                raise GuideError(
                    f"variable {var!r} has neither a value nor a default"
                )
        else:
            out.append((var, False, f"export {var}={spec}"))
    return out


def _render_env_static(node: Any) -> str:
    """Fenced markdown for ``env.static``.

    Variables marked ``sensitive: true`` render into their own fence wrapped in
    ``<!-- llm-d-cicd:skip -->`` markers. A human still sees the placeholder in
    place, but a README-parsing runner harvests neither the variable nor a
    command from it — so a real credential supplied out-of-band is never
    shadowed by the placeholder value.

    Contiguous runs share a fence, which preserves declaration order. That
    matters because a later variable may reference an earlier one, so the
    sensitive entries cannot simply be hoisted to the end.
    """
    groups: list[tuple[bool, list[str]]] = []
    for _var, sensitive, line in _env_static_lines(node):
        if groups and groups[-1][0] == sensitive:
            groups[-1][1].append(line)
        else:
            groups.append((sensitive, [line]))

    parts: list[str] = []
    for sensitive, lines in groups:
        fence = _fence("\n".join(lines))
        parts.append(f"{CICD_SKIP_START}\n{fence}\n{CICD_SKIP_END}" if sensitive else fence)
    return "\n".join(parts)


def _env_source_body(node: Any) -> str:
    """Emitted verbatim — write the full path (including any ``${REPO_ROOT}/``
    prefix) directly in the YAML."""
    if not isinstance(node, list):
        raise GuideError("env.source must be a list")
    return "\n".join(f"source {src}" for src in node)


def _format_filters(step: dict) -> str:
    """Render a step's ``when:`` filter as a bash comment prefix. ``skip_in:`` is
    rendered structurally instead (it wraps the fence) — see ``render_steps``."""
    when = step.get("when") or {}
    if not when:
        return ""
    clauses = [
        f"{var}={' or '.join(str(v) for v in allowed)}" for var, allowed in when.items()
    ]
    return "# only when " + " and ".join(clauses) + ":"


def _render_step_body(step: dict) -> str:
    body = str(step["run"]).rstrip()
    prefix = _format_filters(step)
    return f"{prefix}\n{body}" if prefix else body


def _flatten_steps(node: Any) -> list[dict]:
    """Flatten a step-list node (flat list, single step map, or map of named
    sub-groups) into one list of steps in render order."""
    if isinstance(node, dict) and "run" in node:
        return [node]
    if isinstance(node, list):
        for step in node:
            if not isinstance(step, dict) or "run" not in step:
                raise GuideError(f"every step must be a map with a 'run:' key, got {step!r}")
        return list(node)
    if isinstance(node, dict):
        out: list[dict] = []
        for group_steps in node.values():
            out.extend(_flatten_steps(group_steps))
        return out
    raise GuideError(f"don't know how to render node of type {type(node).__name__}")


def render_steps(node: Any) -> str:
    """Markdown for a step list — one or more ```bash fences, with contiguous
    ``skip_in: [ci]`` steps wrapped in cicd:skip markers so a README-parsing CI
    tool can skip them."""
    steps = _flatten_steps(node)
    if not steps:
        return _fence("")

    groups: list[tuple[bool, list[str]]] = []
    for step in steps:
        rendered = _render_step_body(step)
        ci_skip = "ci" in (step.get("skip_in") or [])
        if groups and groups[-1][0] == ci_skip:
            groups[-1][1].append(rendered)
        else:
            groups.append((ci_skip, [rendered]))

    parts: list[str] = []
    for ci_skip, bodies in groups:
        fence = _fence("\n\n".join(bodies))
        parts.append(f"{CICD_SKIP_START}\n{fence}\n{CICD_SKIP_END}" if ci_skip else fence)
    return "\n".join(parts)


def render_path(guide: Any, path: str) -> str:
    """Markdown (already fenced) for one marker path."""
    found, value, msg = resolve_path(guide, path)
    if not found:
        raise GuideError(msg)
    if path == "env.static":
        return _render_env_static(value)
    if path == "env.source":
        return _fence(_env_source_body(value))
    return render_steps(value)


def render_md(guide: Any, text: str) -> str:
    """Return ``text`` with every marker body replaced by content rendered from
    ``guide``. Content outside marker pairs is preserved byte-for-byte."""
    markers = check_markers(text)
    if not markers.ok():
        raise GuideError("README markers are malformed — cannot render", markers)

    def replace(match: re.Match) -> str:
        # render_path returns markdown that already carries its own ```bash
        # fence(s) and cicd:skip wrappers — inject it between the marker pair.
        return f"{match.group(1)}\n{render_path(guide, match.group('path'))}\n{match.group(4)}"

    return MARKER_PAIR.sub(replace, text)


# --------------------------------------------------------------------------
# Emitting — executable bash from the YAML
# --------------------------------------------------------------------------
#
# ``emit`` assembles executable bash from guide.yaml sections. Deployment
# tooling (the nightly deploy scripts) consumes guide.yaml through emit, so a
# fix to a guide's commands reaches CI without a second edit.


def _emit_env_lines(env: Any, overrides: dict[str, str]) -> list[str]:
    """Bash lines for the ``env`` section: one export per ``env.static``
    variable in declaration order (the same lines render puts in the README,
    via :func:`_env_static_lines`), then the ``env.source`` lines.

    Overridden values are shell-quoted because they come from a caller and may
    carry spaces or flags. Defaults are emitted verbatim so command
    substitutions like ``$(git rev-parse ...)`` still run at execution time. A
    sensitive variable without an override becomes a comment; its README
    placeholder is never emitted.
    """
    if not isinstance(env, dict) or not isinstance(env.get("static"), dict):
        raise GuideError("env.static must be a map")
    lines: list[str] = []
    for var, sensitive, line in _env_static_lines(env["static"]):
        if var in overrides:
            lines.append(f"export {var}={shlex.quote(overrides[var])}")
        elif sensitive:
            lines.append(f"# {var} is sensitive — provide it out-of-band or via --var")
        else:
            lines.append(line)
    src = env.get("source")
    if src:
        lines.extend(_env_source_body(src).splitlines())
    return lines


_SENSITIVE = object()
"""Sentinel in the resolved-env map: declared sensitive, no override given."""


def _resolved_env_values(env: Any, overrides: dict[str, str]) -> dict[str, Any]:
    """The value each ``env.static`` variable takes for ``when:`` filtering:
    the override when given, the declared default otherwise. A sensitive
    variable without an override resolves to :data:`_SENSITIVE` — its declared
    default is a README placeholder, and branch selection must never key off a
    value the author declared to be fake (see :func:`_step_included`)."""
    resolved: dict[str, Any] = {}
    static = env.get("static") if isinstance(env, dict) else None
    for var, spec in (static or {}).items():
        if var in overrides:
            resolved[var] = overrides[var]
        elif isinstance(spec, dict):
            resolved[var] = _SENSITIVE if spec.get("sensitive") else spec.get("default")
        else:
            resolved[var] = spec
    return resolved


def _step_included(step: dict, contexts: set[str], resolved: dict[str, Any]) -> bool:
    if contexts & set(step.get("skip_in") or []):
        return False
    for var, allowed in (step.get("when") or {}).items():
        value = resolved.get(var, "")
        if value is _SENSITIVE:
            raise GuideError(
                f"when: references sensitive variable {var!r} with no override — "
                f"its README placeholder cannot drive branch selection; pass --var {var}=..."
            )
        if not _in_values(value, allowed):
            return False
    return True


def emit_steps(node: Any, contexts: set[str], resolved: dict[str, Any]) -> str:
    """Bash for a step-list node: the ``run:`` bodies that survive
    ``skip_in``/``when`` filtering, joined by blank lines. ``render`` keeps
    every step and annotates it; ``emit`` produces a script for a single
    context, so filtered-out steps are dropped."""
    steps = [s for s in _flatten_steps(node) if _step_included(s, contexts, resolved)]
    return "\n\n".join(str(s["run"]).rstrip() for s in steps)


def emit_script(
    guide: Any,
    sections: list[str],
    overrides: dict[str, str] | None = None,
    contexts: set[str] | None = None,
    label: str = "<guide>",
) -> str:
    """An executable bash script assembled from ``sections`` in the order
    given. A section is the literal ``env`` or a dot-path resolving to a
    step-list node (``deploy.standalone``, ``prerequisites.crds``, ...).

    The provenance comment records override names but not values. Emitted
    scripts end up in CI logs, and an override may hold a credential.
    """
    overrides = overrides or {}
    contexts = contexts or set()
    if not isinstance(guide, dict):
        raise GuideError(f"top level: must be a map, got {type(guide).__name__}")

    env = guide.get("env") or {}
    static = env.get("static") if isinstance(env, dict) else None
    static = static if isinstance(static, dict) else {}
    unknown = set(overrides) - set(static)
    if unknown:
        raise GuideError(
            f"--var names not declared in env.static: {', '.join(sorted(unknown))}"
        )
    # An override must respect the variable's declared vocabulary. A typo'd
    # value would otherwise sail through and silently when:-filter every
    # gated step out of the script.
    for var, value in overrides.items():
        spec = static.get(var)
        if isinstance(spec, dict) and spec.get("values"):
            if not _in_values(value, spec["values"]):
                raise GuideError(
                    f"--var {var}={value!r} not in declared values {spec['values']}"
                )
    resolved = _resolved_env_values(env, overrides)

    provenance = f"# Emitted by scripts/guide.py from {label} — do not edit."
    detail = f"# sections: {' '.join(sections)}"
    if contexts:
        detail += f" | context: {','.join(sorted(contexts))}"
    if overrides:
        detail += f" | vars: {','.join(sorted(overrides))}"

    parts = ["#!/usr/bin/env bash", provenance, detail, "set -euo pipefail"]
    for section in sections:
        if section == "env":
            body = "\n".join(_emit_env_lines(env, overrides))
        else:
            found, node, msg = resolve_path(guide, section)
            if not found:
                raise GuideError(msg)
            body = emit_steps(node, contexts, resolved)
        parts.append(f"\n# === {section} ===")
        if body:
            parts.append(body)
        else:
            # Mark the empty section so a consumer piping the script to bash
            # can see that filtering removed every step.
            parts.append("# (no steps after skip_in/when filtering)")
    return "\n".join(parts) + "\n"


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

GUIDE_YAML = "guide.yaml"
GUIDE_MD = "README.md"


class Guide:
    """A guide — a ``guide.yaml``, a markdown file, or both.

    Either half may be absent, and the validation methods do whatever the
    loaded halves allow:

    ======================  =========================================
    loaded                  ``check()`` covers
    ======================  =========================================
    yaml only               schema
    md only                 marker pairing + body well-formedness
    both                    the above, plus ``guide:<path>`` resolution
    ======================  =========================================

    Marker-path resolution is the only cross-file check, so it is also the only
    thing lost when validating a markdown file on its own. ``resolves_paths``
    reports whether it ran, so callers never mistake a partial pass for a full
    one.

    Construct from disk with :meth:`load` or from memory with :meth:`from_text`.
    Nothing touches the filesystem except :meth:`load` and :meth:`write`.
    """

    def __init__(
        self,
        data: Any = None,
        md: str | None = None,
        *,
        yaml_path: Path | None = None,
        md_path: Path | None = None,
        parse_error: Finding | None = None,
        has_yaml: bool = True,
    ) -> None:
        self.data = data
        self.md = md
        self.yaml_path = yaml_path
        self.md_path = md_path
        self._parse_error = parse_error
        self._has_yaml = has_yaml

    # -- construction ------------------------------------------------------

    @classmethod
    def from_text(
        cls,
        yaml_text: str | None = None,
        md_text: str | None = None,
        *,
        yaml_path: Path | str | None = None,
        md_path: Path | str | None = None,
    ) -> "Guide":
        """Build from in-memory content. At least one of ``yaml_text`` or
        ``md_text`` is required.

        Paths, if given, are labels used in messages and as defaults for
        :meth:`write` — they are never read.
        """
        if yaml_text is None and md_text is None:
            raise GuideError("need yaml_text, md_text, or both")
        data, err = (parse_guide_yaml(yaml_text) if yaml_text is not None else (None, None))
        return cls(
            data,
            md_text,
            yaml_path=Path(yaml_path) if yaml_path else None,
            md_path=Path(md_path) if md_path else None,
            parse_error=err,
            has_yaml=yaml_text is not None,
        )

    @classmethod
    def load(
        cls,
        target: Path | str | None = None,
        *,
        yaml: Path | str | None = None,
        md: Path | str | None = None,
    ) -> "Guide":
        """Load from disk.

        Three ways to say what to load:

        * ``load("guides/my-guide")`` — a directory; ``guide.yaml`` and
          ``README.md`` are discovered inside it.
        * ``load("guides/my-guide/guide.yaml")`` — a ``guide.yaml``; its sibling
          ``README.md`` is picked up if present.
        * ``load(yaml=..., md=...)`` — explicit paths. Either may be omitted to
          load that half alone.

        ``yaml``/``md`` override anything discovered from ``target``. A file that
        does not exist is an error; to load one half only, just omit the other.
        """
        yaml_path: Path | None = None
        md_path: Path | None = None

        if target is not None:
            target = Path(target)
            if target.is_dir():
                cand_yaml, cand_md = target / GUIDE_YAML, target / GUIDE_MD
            else:
                cand_yaml, cand_md = target, target.parent / GUIDE_MD
            # Discovered paths are best-effort — an explicit flag wins, and a
            # missing sibling is simply not loaded.
            yaml_path = cand_yaml if cand_yaml.is_file() else None
            md_path = cand_md if cand_md.is_file() else None
            if not target.is_dir() and yaml_path is None:
                raise GuideError(f"no such file: {target}")
            if target.is_dir() and yaml_path is None and md_path is None:
                raise GuideError(f"no {GUIDE_YAML} or {GUIDE_MD} in {target}")

        if yaml is not None:
            yaml_path = Path(yaml)
            if not yaml_path.is_file():
                raise GuideError(f"no such file: {yaml_path}")
        if md is not None:
            md_path = Path(md)
            if not md_path.is_file():
                raise GuideError(f"no such file: {md_path}")

        if yaml_path is None and md_path is None:
            raise GuideError("nothing to load — pass a target, --yaml, or --md")

        return cls.from_text(
            yaml_path.read_text() if yaml_path else None,
            md_path.read_text() if md_path else None,
            yaml_path=yaml_path,
            md_path=md_path,
        )

    # -- identity ----------------------------------------------------------

    @property
    def name(self) -> str | None:
        return self.data.get("name") if isinstance(self.data, dict) else None

    @property
    def has_yaml(self) -> bool:
        return self._has_yaml

    @property
    def has_md(self) -> bool:
        return self.md is not None

    @property
    def resolves_paths(self) -> bool:
        """True when :meth:`check` can resolve ``guide:<path>`` markers, i.e.
        both halves are loaded and the YAML parsed. When False, a clean
        :meth:`check_md` means *structurally* valid, not fully valid."""
        return self.has_yaml and self.has_md and self._parse_error is None

    @property
    def label(self) -> str:
        return str(self.yaml_path or self.md_path or self.name or "<guide>")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        halves = "+".join(
            p for p, on in (("yaml", self.has_yaml), ("md", self.has_md)) if on
        )
        return f"<Guide {self.label!r} [{halves}]>"

    # -- validation --------------------------------------------------------

    def check_yaml(self) -> Findings:
        """Validate the YAML alone. Empty when no YAML is loaded."""
        if not self.has_yaml:
            return Findings()
        if self._parse_error is not None:
            return Findings([self._parse_error])
        return check_yaml(self.data)

    def check_md(self) -> Findings:
        """Validate the markdown. Resolves ``guide:<path>`` markers against the
        YAML when it is loaded and parseable; otherwise checks structure only.
        Empty when no markdown is loaded."""
        if not self.has_md:
            return Findings()
        guide = self.data if self._parse_error is None and self.has_yaml else None
        return check_md(self.md, guide)

    def check(self) -> Findings:
        """Validate every loaded half.

        Markdown checks are skipped when the YAML is loaded but invalid — marker
        paths resolve against the YAML, so reporting both would bury the cause.
        """
        findings = self.check_yaml()
        if not findings.ok():
            return findings
        return findings.extend(self.check_md())

    # -- rendering ---------------------------------------------------------

    def _require_parsed_yaml(self, action: str) -> None:
        """Raise unless a guide.yaml is loaded and parsed clean."""
        if not self.has_yaml:
            raise GuideError(f"{self.label}: no guide.yaml loaded to {action} from")
        if self._parse_error is not None:
            raise GuideError(f"{self.label}: {self._parse_error}", Findings([self._parse_error]))

    def render(self) -> str:
        """Render the markdown from the YAML and return it. Needs both halves.
        Does not write."""
        self._require_parsed_yaml("render")
        if self.md is None:
            raise GuideError(f"{self.label}: no markdown loaded to render into")
        return render_md(self.data, self.md)

    def is_current(self) -> bool:
        """True if the markdown already matches what :meth:`render` produces."""
        return self.has_md and self.render() == self.md

    def emit(
        self,
        sections: Iterable[str],
        *,
        variables: dict[str, str] | None = None,
        contexts: Iterable[str] | None = None,
    ) -> str:
        """Executable bash for ``sections``, assembled from the YAML. Needs the
        YAML half only. See :func:`emit_script`."""
        self._require_parsed_yaml("emit")
        return emit_script(
            self.data,
            list(sections),
            dict(variables or {}),
            set(contexts or ()),
            label=str(self.yaml_path or self.name or "<guide>"),
        )

    def write(self, path: Path | str | None = None) -> bool:
        """Render and write. Returns True if the file changed on disk.

        Writing is skipped when the content is unchanged, so this is safe to run
        repeatedly (and keeps mtimes stable for build tools).
        """
        rendered = self.render()
        dest = Path(path) if path is not None else self.md_path
        if dest is None:
            raise GuideError(f"{self.label}: no markdown path to write to")
        if rendered == self.md:
            return False
        dest.write_text(rendered)
        self.md = rendered
        return True


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _iter_targets(targets: list[str], *, recursive: bool = False) -> Iterator[Path]:
    """Expand positional targets.

    A *guide* is a directory containing a ``guide.yaml`` — that is what makes it
    one, and every other directory under ``guides/`` (recipes, templates, prose-
    only sub-guides) has a README.md that is not rendered from anything. So a
    directory without a guide.yaml is skipped when it came from a glob, and
    reported when named explicitly. To validate a lone markdown file, pass
    ``--md``. With ``recursive``, directories containing both ``guide.yaml`` and
    ``README.md`` are discovered below each directory target.
    """
    explicit = len(targets) == 1
    for t in targets:
        p = Path(t)
        if recursive and p.is_dir():
            for guide_yaml in sorted(p.rglob(GUIDE_YAML)):
                guide_dir = guide_yaml.parent
                if (guide_dir / GUIDE_MD).is_file():
                    yield guide_dir
            continue
        if p.is_dir() and not (p / GUIDE_YAML).is_file():
            if explicit:
                raise GuideError(f"no {GUIDE_YAML} in {p} (use --md to check a lone markdown file)")
            continue
        yield p


def _guides(args: argparse.Namespace) -> Iterator[Guide]:
    """Yield the guides a command should operate on.

    Explicit ``--yaml``/``--md`` describe exactly one guide; positional targets
    may describe many.
    """
    if args.yaml or args.md:
        yield Guide.load(yaml=args.yaml, md=args.md)
        return
    for target in _iter_targets(args.targets, recursive=args.recursive):
        yield Guide.load(target)


def _scope_note(g: Guide) -> str:
    """Say what a clean result did *not* cover, so a partial pass is never
    mistaken for a full one."""
    if g.has_yaml and not g.has_md:
        return "  (schema only — no markdown given)"
    if g.has_md and not g.has_yaml:
        return "  (structure only — pass --yaml to resolve marker paths)"
    return ""


def _report_failure(g: Guide, findings: Findings) -> None:
    findings.report()
    print(f"{len(findings.errors)} error(s) — {g.label}\n", file=sys.stderr)


def _selection_error(args: argparse.Namespace) -> str | None:
    """Validate the file-selection flags added by ``add_common``. Enforced once
    in ``main`` for every subcommand whose parser sets the ``_needs_selection``
    default (which ``add_common`` does), so the guard travels with the flags."""
    if (args.yaml or args.md) and args.targets:
        return "pass positional targets or --yaml/--md, not both"
    if getattr(args, "recursive", False) and (args.yaml or args.md):
        return "--recursive requires positional directory targets"
    if not args.yaml and not args.md and not args.targets:
        return "nothing to do — pass a target, --yaml, or --md"
    return None


def _cmd_check(args: argparse.Namespace) -> int:
    failed = seen = 0
    for g in _guides(args):
        seen += 1
        findings = g.check()
        if findings.ok():
            print(f"{g.label}: OK{_scope_note(g)}")
        else:
            _report_failure(g, findings)
            failed += 1
    if not seen:
        print("no guides matched", file=sys.stderr)
        return 1
    return 1 if failed else 0


def _cmd_render(args: argparse.Namespace) -> int:
    failed = seen = 0
    for g in _guides(args):
        seen += 1

        if not g.has_yaml or not g.has_md:
            missing = GUIDE_YAML if not g.has_yaml else "markdown file"
            print(f"error: {g.label}: rendering needs both halves — no {missing}", file=sys.stderr)
            failed += 1
            continue

        # Validate before rendering. A guide that fails its own schema must not
        # have its markdown regenerated from it.
        if not args.no_validate:
            findings = g.check()
            if not findings.ok():
                _report_failure(g, findings)
                failed += 1
                continue

        rendered = g.render()

        if args.dry_run:
            sys.stdout.write(rendered)
        elif args.check:
            if rendered != g.md:
                print(
                    f"error: {g.md_path} is out of date — re-run "
                    f"`guide.py render {g.md_path.parent}`",
                    file=sys.stderr,
                )
                failed += 1
            else:
                print(f"{g.md_path}: up to date")
        else:
            print(f"updated {g.md_path}" if g.write() else f"{g.md_path}: already up to date")

    if not seen:
        print("no guides matched", file=sys.stderr)
        return 1
    return 1 if failed else 0


def _cmd_emit(args: argparse.Namespace) -> int:
    g = Guide.load(args.target)
    if not g.has_yaml:
        print(f"error: {g.label}: emit needs a {GUIDE_YAML}", file=sys.stderr)
        return 1

    # Same contract as render: an invalid guide must not drive a deployment.
    if not args.no_validate:
        findings = g.check_yaml()
        if not findings.ok():
            _report_failure(g, findings)
            return 1

    overrides: dict[str, str] = {}
    for pair in args.var or []:
        name, sep, value = pair.partition("=")
        if not sep or not name:
            print(f"error: --var must be NAME=VALUE, got {pair!r}", file=sys.stderr)
            return 2
        overrides[name] = value

    sys.stdout.write(g.emit(args.sections, variables=overrides, contexts=args.context or []))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="guide.py",
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "files:\n"
            "  guide.py check  guides/my-guide            both halves, discovered\n"
            "  guide.py check  guides/*/                  every guide\n"
            "  guide.py check  --yaml g.yaml --md R.md    explicit paths\n"
            "  guide.py check  --yaml g.yaml              schema only\n"
            "  guide.py check  --md R.md                  marker structure only\n"
            "\n"
            "render needs both halves:\n"
            "  guide.py render guides/my-guide\n"
            "  guide.py render guides/*/ --check          CI: fail if any is stale\n"
            "  guide.py render guides --recursive --check recursively check all guides\n"
            "\n"
            "emit needs the YAML half only:\n"
            "  guide.py emit guides/my-guide env deploy.standalone\n"
            "  guide.py emit guides/my-guide env deploy --context ci --var NAMESPACE=ns\n"
        ),
    )
    sub = ap.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "targets",
            nargs="*",
            metavar="TARGET",
            help="guide directory or guide.yaml path; repeatable",
        )
        p.add_argument("--yaml", metavar="PATH", help="explicit guide.yaml path")
        p.add_argument("--md", metavar="PATH", help="explicit markdown path")
        p.add_argument(
            "--recursive",
            action="store_true",
            help="recursively discover directories containing guide.yaml and README.md",
        )
        # main() runs _selection_error for every namespace carrying this flag.
        p.set_defaults(_needs_selection=True)

    c = sub.add_parser(
        "check",
        help="validate whichever halves you pass; never writes",
        description=(
            "Validate a guide. Pass a directory for both halves, or --yaml/--md "
            "to isolate one. With only --md, marker paths cannot be resolved, so "
            "the result covers structure alone and says so."
        ),
    )
    add_common(c)
    c.set_defaults(func=_cmd_check)

    r = sub.add_parser(
        "render",
        help="validate, then fill the markdown from the YAML",
        description="Render a guide's markdown from its guide.yaml. Needs both halves.",
    )
    add_common(r)
    mode = r.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="fail if the markdown is stale (CI)")
    mode.add_argument("--dry-run", action="store_true", help="print to stdout, do not write")
    r.add_argument(
        "--no-validate",
        action="store_true",
        help="render without validating first (escape hatch; not for CI)",
    )
    r.set_defaults(func=_cmd_render)

    e = sub.add_parser(
        "emit",
        help="print executable bash assembled from guide.yaml sections",
        description=(
            "Emit an executable bash script from guide.yaml, for CI and "
            "deployment tooling. TARGET is a guide directory or a guide.yaml "
            "path. SECTION is the literal `env` or a dot-path to a step list "
            "(e.g. deploy.standalone, prerequisites.crds); sections are "
            "emitted in the order given. Steps whose skip_in matches a "
            "--context tag, or whose when: filter excludes the resolved "
            "variable values, are dropped."
        ),
    )
    e.add_argument("target", metavar="TARGET", help="guide directory or guide.yaml path")
    e.add_argument(
        "sections",
        nargs="+",
        metavar="SECTION",
        help="`env` or a step-list dot-path; repeatable, emitted in order",
    )
    e.add_argument(
        "--var",
        action="append",
        metavar="NAME=VALUE",
        help="override an env.static variable (value is shell-quoted); repeatable",
    )
    e.add_argument(
        "--context",
        action="append",
        metavar="CTX",
        help="drop steps with this skip_in tag, e.g. ci; repeatable",
    )
    e.add_argument(
        "--no-validate",
        action="store_true",
        help="emit without validating first (escape hatch; not for CI)",
    )
    e.set_defaults(func=_cmd_emit)

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "_needs_selection", False) and (err := _selection_error(args)):
        print(f"error: {err}", file=sys.stderr)
        return 2
    try:
        return args.func(args)
    except GuideError as e:
        print(f"error: {e}", file=sys.stderr)
        e.findings.report()
        return 1


if __name__ == "__main__":
    sys.exit(main())
