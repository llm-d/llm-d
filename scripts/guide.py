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

    guide.py check-manifest                         # llm-d.ai publish manifest
    guide.py set-branch release-0.8 guides/*/       # pin BRANCH, re-render

``render`` validates before it writes and refuses to render an invalid guide,
so a normal authoring loop only ever needs ``guide.py render <dir>``.

``emit`` assembles an executable bash script from guide.yaml sections, so
deployment tooling (e.g. the nightly deploy scripts) consumes the guide's
commands instead of copying them.

``check-manifest`` validates ``docs/well-lit-paths/guides.yaml``, the list of
guides llm-d.ai publishes; ``set-branch`` is the release-cut helper that pins
each guide's clone step to the release branch it is published from.

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
TOP_OPTIONAL = {"_lists", "prerequisites", "verify", "benchmark", "cleanup", "support"}
STEP_KEYS = {"run", "when", "skip_in"}
ENV_KEYS = {"static", "source", "derive"}
ENV_VAR_KEYS = {"default", "values", "sensitive", "comment"}

# Step-list sections walked when cross-checking `when:` filters against the
# support matrix.
STEP_SECTIONS = ("prerequisites", "deploy", "verify", "benchmark", "cleanup")

# --- Support matrix (accelerator × engine) ---------------------------------
#
# A guide may declare which ACCELERATOR_TYPE × MODEL_SERVER pairings it
# supports. When present, the matrix is (1) validated against the declared
# variable vocabularies, the guide's `when:` filters, the model-server overlays
# on disk and the nightly E2E workflows; (2) rendered into the README as the
# support table (`<!-- guide:support -->`); and (3) used
# to render sibling `when:` steps as GitHub-collapsible variant groups that the
# llm-d.ai site turns into a page-level accelerator/engine selector.
ACCEL_VAR = "ACCELERATOR_TYPE"
ENGINE_VAR = "MODEL_SERVER"
VARIANT_VARS = (ACCEL_VAR, ENGINE_VAR)
SUPPORT_KEYS = {"engines", "accelerators"}
SUPPORT_ACCEL_KEYS = {"label", "model", "notes", "engines"}
SUPPORT_CELL_KEYS = {"status", "issue"}
SUPPORT_STATUSES = ("validated", "community", "unsupported")
SUPPORTED_STATUSES = {"validated", "community"}


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
        if not isinstance(var, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", var):
            f.error(
                f"env.static.{var}: variable name must be a shell identifier "
                f"matching [A-Za-z_][A-Za-z0-9_]*"
            )
            continue
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


def _declared_values(guide: Any, var: str) -> list[str]:
    """The ``values:`` vocabulary of an ``env.static`` variable, as strings."""
    env = guide.get("env") if isinstance(guide, dict) else None
    static = env.get("static") if isinstance(env, dict) else None
    spec = static.get(var) if isinstance(static, dict) else None
    if isinstance(spec, dict) and isinstance(spec.get("values"), list):
        return [str(v) for v in spec["values"]]
    return []


def _declared_default(guide: Any, var: str) -> str | None:
    env = guide.get("env") if isinstance(guide, dict) else None
    static = env.get("static") if isinstance(env, dict) else None
    spec = static.get(var) if isinstance(static, dict) else None
    if isinstance(spec, dict) and "default" in spec:
        return str(spec["default"])
    return None


def _cell_status(cell: Any) -> tuple[str | None, str | None]:
    """``(status, issue)`` for one support-matrix cell (string or map form)."""
    if isinstance(cell, str):
        return cell, None
    if isinstance(cell, dict):
        status = cell.get("status")
        issue = cell.get("issue")
        return (str(status) if status is not None else None), (str(issue) if issue else None)
    return None, None


def support_status(guide: Any, accel: str, engine: str) -> str:
    """Status of an accelerator/engine pairing; a missing cell is unsupported."""
    sup = guide.get("support") if isinstance(guide, dict) else None
    accels = sup.get("accelerators") if isinstance(sup, dict) else None
    spec = accels.get(accel) if isinstance(accels, dict) else None
    engines = spec.get("engines") if isinstance(spec, dict) else None
    if not isinstance(engines, dict) or engine not in engines:
        return "unsupported"
    status, _issue = _cell_status(engines[engine])
    return status or "unsupported"


def is_supported(guide: Any, accel: str, engine: str) -> bool:
    return support_status(guide, accel, engine) in SUPPORTED_STATUSES


def _iter_steps(node: Any, path: str) -> Iterator[tuple[str, dict]]:
    """Every step under a section, whatever its nesting (lists, named
    sub-groups, mode maps). Tolerant of malformed input — schema errors are
    reported elsewhere."""
    if isinstance(node, dict) and "run" in node:
        yield path, node
    elif isinstance(node, list):
        for i, step in enumerate(node):
            yield from _iter_steps(step, f"{path}[{i}]")
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _iter_steps(v, f"{path}.{k}")


def _check_support(guide: dict, f: Findings) -> None:
    """Schema + consistency checks for the ``support:`` matrix that need only
    the YAML: vocabularies, cell statuses, the default pairing, and that no
    ``when:`` filter targets a pairing the matrix marks unsupported."""
    sup = guide.get("support")
    if not isinstance(sup, dict):
        f.error("support: must be a map with `engines:` and `accelerators:`")
        return
    for k in sup:
        if k not in SUPPORT_KEYS:
            f.error(f"support: unknown key {k!r} (allowed: {sorted(SUPPORT_KEYS)})")

    accel_values = _declared_values(guide, ACCEL_VAR)
    engine_values = _declared_values(guide, ENGINE_VAR)
    if not accel_values or not engine_values:
        f.error(
            f"support: requires env.static.{ACCEL_VAR} and env.static.{ENGINE_VAR} "
            f"to declare `values:`"
        )
        return

    engines = sup.get("engines")
    if not isinstance(engines, dict):
        f.error("support.engines: must map each MODEL_SERVER value to a display label")
        engines = {}
    for eng in engines:
        if str(eng) not in engine_values:
            f.error(f"support.engines.{eng}: not in env.static.{ENGINE_VAR}.values {engine_values}")
    for eng in engine_values:
        if eng not in engines:
            f.error(f"support.engines: missing label for {ENGINE_VAR} value {eng!r}")

    accels = sup.get("accelerators")
    if not isinstance(accels, dict):
        f.error("support.accelerators: must be a map keyed by ACCELERATOR_TYPE value")
        return
    declared_accels = set(accel_values)
    listed_accels = {str(a) for a in accels}
    for a in sorted(declared_accels - listed_accels):
        f.error(f"support.accelerators: missing entry for {ACCEL_VAR} value {a!r}")
    for a in sorted(listed_accels - declared_accels):
        f.error(f"support.accelerators.{a}: not in env.static.{ACCEL_VAR}.values {accel_values}")

    for accel, spec in accels.items():
        p = f"support.accelerators.{accel}"
        if not isinstance(spec, dict):
            f.error(f"{p}: must be a map with `label:` and `engines:`")
            continue
        for k in spec:
            if k not in SUPPORT_ACCEL_KEYS:
                f.error(f"{p}: unknown key {k!r} (allowed: {sorted(SUPPORT_ACCEL_KEYS)})")
        if not isinstance(spec.get("label"), str):
            f.error(f"{p}.label: must be a string")
        if "model" in spec and not isinstance(spec["model"], str):
            f.error(f"{p}.model: must be a string")
        if "notes" in spec and not isinstance(spec["notes"], str):
            f.error(f"{p}.notes: must be a string")
        elif "|" in str(spec.get("notes", "")) or "\n" in str(spec.get("notes", "")).strip():
            f.error(f"{p}.notes: must be a single line without '|' (it is a table cell)")
        cells = spec.get("engines")
        if not isinstance(cells, dict):
            f.error(f"{p}.engines: must map MODEL_SERVER values to a status")
            continue
        for eng, cell in cells.items():
            cp = f"{p}.engines.{eng}"
            if str(eng) not in engine_values:
                f.error(f"{cp}: not in env.static.{ENGINE_VAR}.values {engine_values}")
            if isinstance(cell, dict):
                for k in cell:
                    if k not in SUPPORT_CELL_KEYS:
                        f.error(f"{cp}: unknown key {k!r} (allowed: {sorted(SUPPORT_CELL_KEYS)})")
            elif not isinstance(cell, str):
                f.error(f"{cp}: must be a status string or a map with `status:`/`issue:`")
                continue
            status, issue = _cell_status(cell)
            if status not in SUPPORT_STATUSES:
                f.error(f"{cp}: status must be one of {list(SUPPORT_STATUSES)}, got {status!r}")
            if status == "unsupported" and not issue:
                f.error(f"{cp}: unsupported pairings must link a tracking `issue:`")

    d_acc, d_eng = _declared_default(guide, ACCEL_VAR), _declared_default(guide, ENGINE_VAR)
    if d_acc and d_eng and not is_supported(guide, d_acc, d_eng):
        f.error(f"support: the default pairing {d_acc}/{d_eng} must be supported")

    # `when:` filters must not target pairings the matrix rules out.
    for section in STEP_SECTIONS:
        for path, step in _iter_steps(guide.get(section), section):
            when = step.get("when")
            if not isinstance(when, dict):
                continue
            accs = [str(v) for v in when.get(ACCEL_VAR, [])] if ACCEL_VAR in when else None
            engs = [str(v) for v in when.get(ENGINE_VAR, [])] if ENGINE_VAR in when else None
            if accs is not None and engs is not None:
                bad = [f"{a}/{e}" for a in accs for e in engs if not is_supported(guide, a, e)]
                if bad:
                    f.error(f"{path}: when: targets unsupported pairing(s) {', '.join(bad)}")
            elif accs is not None:
                bad = [a for a in accs if not any(is_supported(guide, a, e) for e in engine_values)]
                if bad:
                    f.error(f"{path}: when: targets accelerator(s) with no supported engine: {', '.join(bad)}")
            elif engs is not None:
                bad = [e for e in engs if not any(is_supported(guide, a, e) for a in accel_values)]
                if bad:
                    f.error(f"{path}: when: targets engine(s) supported on no accelerator: {', '.join(bad)}")


_WORKFLOW_ACCEL = re.compile(
    r"accelerator_type:\s*\$\{\{\s*inputs\.accelerator_type\s*\|\|\s*'([^']+)'\s*\}\}"
)
_WORKFLOW_BACKEND = re.compile(
    r"backend_type:\s*\$\{\{\s*inputs\.backend_type\s*\|\|\s*'([^']+)'\s*\}\}"
)


def _find_repo_root(start: Path) -> Path | None:
    for p in [start, *start.parents]:
        if (p / ".git").exists():
            return p
    return None


def check_support_repo(guide: Any, guide_dir: Path, repo_root: Path | None = None) -> Findings:
    """Cross-check the support matrix against the repository:

    * every supported cell has a ``modelserver/<accel>/<engine>/`` overlay, and
      every engine overlay on disk is a supported cell;
    * every ``validated`` cell has a nightly E2E workflow, and every nightly
      workflow for this guide runs a ``validated`` cell.

    Skipped (empty) when the guide has no ``support:`` matrix.
    """
    f = Findings()
    if not isinstance(guide, dict) or not isinstance(guide.get("support"), dict):
        return f
    accels = guide["support"].get("accelerators")
    if not isinstance(accels, dict):
        return f
    engine_values = set(_declared_values(guide, ENGINE_VAR))

    ms_dir = guide_dir / "modelserver"
    if ms_dir.is_dir():
        for accel, spec in accels.items():
            cells = spec.get("engines") if isinstance(spec, dict) else None
            for eng in (cells or {}):
                status = support_status(guide, str(accel), str(eng))
                if status in SUPPORTED_STATUSES and not (ms_dir / str(accel) / str(eng)).is_dir():
                    f.error(
                        f"support.accelerators.{accel}.engines.{eng}: marked {status} but "
                        f"there is no overlay at modelserver/{accel}/{eng}/"
                    )
        for eng_dir in sorted(p for p in ms_dir.rglob("*") if p.is_dir() and p.name in engine_values):
            accel = eng_dir.parent.relative_to(ms_dir).as_posix()
            if accel == "." or eng_dir.parent == ms_dir:
                continue
            if accel not in {str(a) for a in accels}:
                f.error(
                    f"overlay modelserver/{accel}/{eng_dir.name}/ exists but {accel!r} is not "
                    f"in support.accelerators (nor in env.static.{ACCEL_VAR}.values)"
                )
            elif not is_supported(guide, accel, eng_dir.name):
                f.error(
                    f"overlay modelserver/{accel}/{eng_dir.name}/ exists but support marks "
                    f"{accel}/{eng_dir.name} as {support_status(guide, accel, eng_dir.name)}"
                )

    root = repo_root or _find_repo_root(guide_dir.resolve())
    # nightly-e2e-<guide>-<provider>-acc-<acc>-<engine>-x.yaml; the provider
    # may itself contain hyphens (e.g. amd-ci).
    wf_dir = root / ".github" / "workflows" if root else None
    name = guide.get("name")
    if wf_dir and wf_dir.is_dir() and isinstance(name, str):
        pat = re.compile(
            rf"^nightly-e2e-{re.escape(name)}-[a-z0-9-]+?-acc-(?P<acc>[a-z0-9]+)-(?P<engine>[a-z0-9]+)-x\.ya?ml$"
        )
        nightly: set[tuple[str, str]] = set()
        for wf in sorted(wf_dir.iterdir()):
            m = pat.match(wf.name)
            if m:
                eng, acc = m.group("engine"), m.group("acc")
            else:
                # Workflows named another way (e.g. nightly-e2e-<guide>-<prov>-
                # <tier>-<acc>-<engine>-<connector>.yaml) count when they declare
                # both accelerator_type and backend_type input defaults.
                if not re.match(rf"^nightly-e2e-{re.escape(name)}-.+\.ya?ml$", wf.name):
                    continue
                text = wf.read_text(errors="replace")
                bm = _WORKFLOW_BACKEND.search(text)
                if not bm or not _WORKFLOW_ACCEL.search(text):
                    continue
                eng, acc = bm.group(1), ""
            if eng not in engine_values:
                f.error(
                    f"workflow {wf.name} targets engine {eng!r} which is not in "
                    f"env.static.{ENGINE_VAR}.values ({sorted(engine_values)})"
                )
                continue
            am = _WORKFLOW_ACCEL.search(wf.read_text(errors="replace"))
            accel = am.group(1) if am else acc
            pair = (accel, eng)
            nightly.add(pair)
            status = support_status(guide, *pair)
            if status != "validated":
                f.error(
                    f"workflow {wf.name} runs {pair[0]}/{pair[1]} nightly but support marks it "
                    f"{status} — set it to `validated`"
                )
        for accel, spec in accels.items():
            cells = spec.get("engines") if isinstance(spec, dict) else None
            for eng in (cells or {}):
                if support_status(guide, str(accel), str(eng)) == "validated" and (
                    str(accel), str(eng)
                ) not in nightly:
                    f.error(
                        f"support.accelerators.{accel}.engines.{eng}: marked validated but no "
                        f"nightly-e2e-{name}-*-acc-*-{eng}-x.yaml workflow runs it"
                    )
    return f


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
    if "support" in guide:
        _check_support(guide, f)

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


VARIANTS_START = "<!-- variants:start -->"
VARIANTS_END = "<!-- variants:end -->"
# Markup render_steps emits around variant groups (see _render_variant_group).
VARIANT_MARKUP = re.compile(
    r"<!--\s*variants:(?:start|end)\s*-->"
    r"|<details(?:\s+open)?\s+data-when=\"[^\"]*\">"
    r"|<summary>.*?</summary>"
    r"|</details>",
    re.DOTALL,
)
# Marker paths whose body is generated markdown rather than ```bash fences.
MARKDOWN_PATHS = {"support"}


def _is_valid_body(body: str) -> bool:
    """Valid if, after stripping cicd:skip comments, variant-group markup and
    every ```bash fence, only whitespace remains. Admits both the single-fence
    case and multi-fence bodies with CI-skip wrappers around individual fences."""
    stripped = BASH_FENCE.sub("", body)
    stripped = CICD_SKIP_MARKER.sub("", stripped)
    stripped = VARIANT_MARKUP.sub("", stripped)
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
        if path in MARKDOWN_PATHS:
            continue
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
                notes = []
                if spec.get("values"):
                    notes.append("options: " + ", ".join(str(v) for v in spec["values"]))
                if spec.get("comment"):
                    notes.append(str(spec["comment"]))
                if notes:
                    line += " # " + "; ".join(notes)
                out.append((var, False, line))
            else:
                raise GuideError(
                    f"variable {var!r} has neither a value nor a default"
                )
        else:
            out.append((var, False, f"export {var}={spec}"))
    return out


def _render_env_static(node: Any, source: Any = None) -> str:
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
        lines = [line]
        if groups and groups[-1][0] == sensitive:
            groups[-1][1].extend(lines)
        else:
            groups.append((sensitive, lines))

    if source:
        src_lines = _env_source_body(source).splitlines()
        if groups and not groups[-1][0]:
            groups[-1][1].extend(src_lines)
        else:
            groups.append((False, src_lines))

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


def _plain_fences(steps: list[dict], *, force_skip: bool = False) -> list[str]:
    """Fences for consecutive steps, contiguous ``skip_in: [ci]`` runs (or all
    of them, with ``force_skip``) wrapped in cicd:skip markers."""
    groups: list[tuple[bool, list[str]]] = []
    for step in steps:
        rendered = _render_step_body(step)
        ci_skip = force_skip or "ci" in (step.get("skip_in") or [])
        if groups and groups[-1][0] == ci_skip:
            groups[-1][1].append(rendered)
        else:
            groups.append((ci_skip, [rendered]))
    parts: list[str] = []
    for ci_skip, bodies in groups:
        fence = _fence("\n\n".join(bodies))
        parts.append(f"{CICD_SKIP_START}\n{fence}\n{CICD_SKIP_END}" if ci_skip else fence)
    return parts


def _is_variant_step(step: dict, guide: Any) -> bool:
    """A step is a variant when the guide has a support matrix and its
    ``when:`` filter keys only off ACCELERATOR_TYPE / MODEL_SERVER."""
    when = step.get("when")
    return (
        isinstance(guide, dict)
        and isinstance(guide.get("support"), dict)
        and isinstance(when, dict)
        and bool(when)
        and set(when) <= set(VARIANT_VARS)
    )


def _variant_label(when: dict, guide: dict) -> str:
    sup = guide["support"]
    parts: list[str] = []
    if ACCEL_VAR in when:
        accels = sup.get("accelerators") or {}
        labels = [
            str((accels.get(str(a)) or {}).get("label", a)) if isinstance(accels.get(str(a)), dict) else str(a)
            for a in when[ACCEL_VAR]
        ]
        parts.append(" / ".join(labels))
    if ENGINE_VAR in when:
        engines = sup.get("engines") or {}
        parts.append(" / ".join(str(engines.get(str(e), e)) for e in when[ENGINE_VAR]))
    return " · ".join(parts)


def _data_when(when: dict) -> str:
    return ";".join(
        f"{var}={','.join(str(v) for v in when[var])}" for var in VARIANT_VARS if var in when
    )


def _when_matches_defaults(when: dict, guide: dict) -> bool:
    for var in VARIANT_VARS:
        if var in when:
            default = _declared_default(guide, var)
            if default is None or not _in_values(default, when[var]):
                return False
    return True


def _render_variant_group(steps: list[dict], guide: dict) -> str:
    """A run of sibling variant steps as GitHub-collapsible ``<details>`` blocks.

    Steps with identical ``when:`` filters share one block (in first-seen
    order). The block matching the declared defaults is ``open``; every other
    block's fences are wrapped in cicd:skip markers so a README-parsing runner
    executes only the default path — exactly the commands that used to be
    commented out with "comment out the above and uncomment the below".
    The llm-d.ai preprocessor turns the group into a page-level
    accelerator/engine selector using ``data-when``.
    """
    order: list[str] = []
    by_key: dict[str, tuple[dict, list[dict]]] = {}
    for step in steps:
        key = _data_when(step["when"])
        if key not in by_key:
            order.append(key)
            by_key[key] = (step["when"], [])
        by_key[key][1].append(step)

    parts = [VARIANTS_START]
    for key in order:
        when, group = by_key[key]
        is_default = _when_matches_defaults(when, guide)
        fences = _plain_fences(
            [{k: v for k, v in s.items() if k != "when"} for s in group],
            force_skip=not is_default,
        )
        parts.append(
            f"<details{' open' if is_default else ''} data-when=\"{key}\">\n"
            f"<summary><b>{_variant_label(when, guide)}</b></summary>\n\n"
            + "\n".join(fences)
            + "\n\n</details>"
        )
    parts.append(VARIANTS_END)
    return "\n".join(parts)


def render_steps(node: Any, guide: Any = None) -> str:
    """Markdown for a step list — one or more ```bash fences, with contiguous
    ``skip_in: [ci]`` steps wrapped in cicd:skip markers so a README-parsing CI
    tool can skip them.

    When ``guide`` declares a ``support:`` matrix, consecutive steps whose
    ``when:`` keys only off ACCELERATOR_TYPE / MODEL_SERVER render as a variant
    group (see :func:`_render_variant_group`) instead of ``# only when``
    comments. Guides without a matrix render exactly as before."""
    steps = _flatten_steps(node)
    if not steps:
        return _fence("")

    # A marker that points at one variant step (e.g. `deploy.render[1]`) is
    # placed by the author in its own context — typically an engine tab — so
    # render a bare fence rather than a one-entry variant group. Non-default
    # variants stay wrapped in cicd:skip markers, as in a variant group.
    if isinstance(node, dict) and "run" in node and _is_variant_step(node, guide):
        return "\n".join(
            _plain_fences(
                [{k: v for k, v in node.items() if k != "when"}],
                force_skip=not _when_matches_defaults(node["when"], guide),
            )
        )

    segments: list[tuple[bool, list[dict]]] = []
    for step in steps:
        variant = _is_variant_step(step, guide)
        if segments and segments[-1][0] == variant:
            segments[-1][1].append(step)
        else:
            segments.append((variant, [step]))

    parts: list[str] = []
    for i, (variant, group) in enumerate(segments):
        if variant:
            block = _render_variant_group(group, guide)
            # GFM ends an HTML block (opened by `</details>`) only at a blank
            # line; without one, a following fence would be swallowed as HTML.
            parts.append(block + "\n" if i < len(segments) - 1 else block)
        else:
            parts.extend(_plain_fences(group))
    return "\n".join(parts)


_STATUS_CELL = {
    "validated": "✅ validated",
    "community": "🟡 community",
}


def render_support_table(guide: Any) -> str:
    """GFM table for the ``<!-- guide:support -->`` marker: one row per
    accelerator, one column per engine, plus the served model and, when any
    accelerator sets `notes:`, a trailing Notes column."""
    if not isinstance(guide, dict) or not isinstance(guide.get("support"), dict):
        raise GuideError("guide:support marker needs a `support:` matrix in guide.yaml")
    sup = guide["support"]
    engines = _declared_values(guide, ENGINE_VAR)
    labels = sup.get("engines") or {}
    accels = sup.get("accelerators") or {}
    with_notes = any(isinstance(v, dict) and v.get("notes") for v in accels.values())
    header = ["Accelerator", f"`{ACCEL_VAR}`", "Served model", *[str(labels.get(e, e)) for e in engines]]
    if with_notes:
        header.append("Notes")
    rows = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    for accel in _declared_values(guide, ACCEL_VAR):
        spec = accels.get(accel) if isinstance(accels.get(accel), dict) else {}
        cells_map = spec.get("engines") if isinstance(spec.get("engines"), dict) else {}
        model = spec.get("model")
        row = [str(spec.get("label", accel)), f"`{accel}`", f"`{model}`" if model else "—"]
        for eng in engines:
            if eng not in cells_map:
                row.append("—")
                continue
            status, issue = _cell_status(cells_map[eng])
            if status == "unsupported":
                row.append(f"❌ [not supported]({issue})" if issue else "❌ not supported")
            else:
                row.append(_STATUS_CELL.get(status or "", str(status)))
        if with_notes:
            row.append(str(spec.get("notes", "")).strip())
        rows.append("| " + " | ".join(row) + " |")
    legend = (
        "\n\n✅ validated: covered by a nightly E2E workflow · 🟡 community: maintained by the "
        "hardware vendor or community, not covered by nightly E2E · ❌ not supported: tracked in "
        "the linked issue · — no configuration."
    )
    return "\n".join(rows) + legend


def render_path(guide: Any, path: str, *, include_env_source: bool = False) -> str:
    """Markdown (already fenced) for one marker path."""
    found, value, msg = resolve_path(guide, path)
    if not found:
        raise GuideError(msg)
    if path == "env.static":
        src = (
            guide.get("env", {}).get("source")
            if include_env_source and isinstance(guide, dict) and isinstance(guide.get("env"), dict)
            else None
        )
        return _render_env_static(value, src)
    if path == "env.source":
        return _fence(_env_source_body(value))
    if path == "support":
        return render_support_table(guide)
    return render_steps(value, guide)


def render_md(guide: Any, text: str) -> str:
    """Return ``text`` with every marker body replaced by content rendered from
    ``guide``. Content outside marker pairs is preserved byte-for-byte."""
    markers = check_markers(text)
    if not markers.ok():
        raise GuideError("README markers are malformed — cannot render", markers)

    inline_src = "env.source" not in marker_paths(text)

    def replace(match: re.Match) -> str:
        # render_path returns markdown that already carries its own ```bash
        # fence(s) and cicd:skip wrappers — inject it between the marker pair.
        body = render_path(guide, match.group("path"), include_env_source=inline_src)
        return f"{match.group(1)}\n{body}\n{match.group(4)}"

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
    Nothing touches the filesystem except :meth:`load`, :meth:`write`, and the
    read-only support-matrix cross-check in :meth:`check_yaml`.
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
        """Validate the YAML alone. Empty when no YAML is loaded.

        When the YAML was loaded from disk and declares a ``support:`` matrix,
        this also cross-checks it against the repository (overlays on disk,
        nightly E2E workflows) — see :func:`check_support_repo`. This is the
        one read outside :meth:`load`, and it only reads."""
        if not self.has_yaml:
            return Findings()
        if self._parse_error is not None:
            return Findings([self._parse_error])
        findings = check_yaml(self.data)
        if findings.ok() and self.yaml_path is not None and self.yaml_path.is_file():
            findings.extend(check_support_repo(self.data, self.yaml_path.parent))
        return findings

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


# -- set-branch --------------------------------------------------------------

_BRANCH_VAR = re.compile(r"^(?P<indent>[ \t]+)BRANCH:[ \t]*\S.*$", re.MULTILINE)
_BRANCH_EXPORT = re.compile(r"(export BRANCH=)\S+")
_REF = re.compile(r"^[A-Za-z0-9._/-]+$")


def set_branch_text(yaml_text: str, ref: str) -> str:
    """Pin ``env.static.BRANCH`` and every ``export BRANCH=…`` in a guide.yaml
    to ``ref``. Text-level so comments and layout survive; the caller
    re-renders the README afterwards."""
    if not _REF.match(ref):
        raise GuideError(f"invalid ref {ref!r}")
    out = _BRANCH_VAR.sub(lambda m: f"{m.group('indent')}BRANCH: {ref}", yaml_text)
    return _BRANCH_EXPORT.sub(lambda m: f"{m.group(1)}{ref}", out)


def _cmd_set_branch(args: argparse.Namespace) -> int:
    failed = seen = 0
    for target in _iter_targets(args.targets):
        seen += 1
        g = Guide.load(target)
        if g.yaml_path is None:
            print(f"error: {g.label}: no {GUIDE_YAML}", file=sys.stderr)
            failed += 1
            continue
        text = g.yaml_path.read_text()
        new = set_branch_text(text, args.ref)
        if new != text:
            g.yaml_path.write_text(new)
        g = Guide.load(target)
        findings = g.check()
        if not findings.ok():
            _report_failure(g, findings)
            failed += 1
            continue
        if g.has_md:
            g.write()
        print(f"{g.label}: BRANCH={args.ref}")
    if not seen:
        print("no guides matched", file=sys.stderr)
        return 1
    return 1 if failed else 0


# -- check-manifest ----------------------------------------------------------

DEFAULT_MANIFEST = "docs/well-lit-paths/guides.yaml"
MANIFEST_SECTIONS = ("foundations", "models", "operations")
# Sections whose guides may be published from a README alone (no guide.yaml):
# Operations guides are not held to the Foundations/Models guide.yaml rules.
README_ONLY_SECTIONS = ("operations",)


def _manifest_pillar(name: str) -> str | None:
    """The pillar a section belongs to: ``<pillar>`` itself, or
    ``<pillar>-<sub-category>`` (e.g. ``operations-scaling``, published under
    a nested target such as ``operations/scaling``)."""
    for pillar in MANIFEST_SECTIONS:
        if name == pillar or re.fullmatch(rf"{pillar}-[a-z0-9][a-z0-9-]*", name):
            return pillar
    return None


def check_manifest(data: Any, repo_root: Path) -> Findings:
    """Validate the llm-d.ai publish manifest: every guide directory exists
    with a ``README.md`` (and a ``guide.yaml``, except in Operations
    sections), slugs are unique across sections, child pages exist, and
    titles are present."""
    f = Findings()
    if not isinstance(data, dict) or not isinstance(data.get("sections"), dict):
        f.error("manifest: must be a map with `sections:`")
        return f
    if data.get("version") != 1:
        f.error("manifest: `version: 1` is required")
    seen_dirs: dict[str, str] = {}
    seen_slugs: dict[str, str] = {}
    for name, section in data["sections"].items():
        p = f"sections.{name}"
        pillar = _manifest_pillar(name)
        if pillar is None:
            f.error(
                f"{p}: unknown section (allowed: {list(MANIFEST_SECTIONS)}, "
                "or <section>-<sub-category>)"
            )
        if not isinstance(section, dict):
            f.error(f"{p}: must be a map with `target:` and `guides:`")
            continue
        target = section.get("target")
        if not isinstance(target, str) or not target or target.startswith("/") or ".." in target:
            f.error(f"{p}.target: must be a relative docs path")
        guides = section.get("guides") or []
        if not isinstance(guides, list):
            f.error(f"{p}.guides: must be a list")
            continue
        required = (GUIDE_MD,) if pillar in README_ONLY_SECTIONS else (GUIDE_YAML, GUIDE_MD)
        slugs: set[str] = set()
        for i, entry in enumerate(guides):
            gp = f"{p}.guides[{i}]"
            if not isinstance(entry, dict):
                f.error(f"{gp}: must be a map")
                continue
            d, slug = entry.get("dir"), entry.get("slug")
            if not isinstance(entry.get("title"), str) or not entry["title"].strip():
                f.error(f"{gp}.title: required")
            if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug):
                f.error(f"{gp}.slug: required, lowercase letters, digits and dashes")
            elif slug in slugs:
                f.error(f"{gp}.slug: duplicate slug {slug!r} in {name}")
            elif slug in seen_slugs:
                f.error(
                    f"{gp}.slug: {slug!r} is already used by {seen_slugs[slug]} "
                    "(slugs must be unique across sections)"
                )
            else:
                slugs.add(slug)
                seen_slugs[slug] = gp
            if not isinstance(d, str) or not d.startswith("guides/"):
                f.error(f"{gp}.dir: required, a repo-relative path under guides/")
                continue
            if d in seen_dirs:
                f.error(f"{gp}.dir: {d} is already published by {seen_dirs[d]}")
            seen_dirs[d] = gp
            gdir = repo_root / d
            for req in required:
                if not (gdir / req).is_file():
                    f.error(f"{gp}.dir: {d}/{req} does not exist")
            if "position" in entry and not isinstance(entry["position"], int):
                f.error(f"{gp}.position: must be an integer")
            for j, page in enumerate(entry.get("pages") or []):
                pp = f"{gp}.pages[{j}]"
                if not isinstance(page, dict):
                    f.error(f"{pp}: must be a map with `from:`, `to:`, `title:`")
                    continue
                src, to = page.get("from"), page.get("to")
                if not isinstance(src, str) or not (gdir / src).is_file():
                    f.error(f"{pp}.from: {d}/{src} does not exist")
                if not isinstance(to, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", to or ""):
                    f.error(f"{pp}.to: required, lowercase letters, digits and dashes")
                if not isinstance(page.get("title"), str) or not page["title"].strip():
                    f.error(f"{pp}.title: required")
    return f


def _cmd_check_manifest(args: argparse.Namespace) -> int:
    path = Path(args.manifest)
    if not path.is_file():
        print(f"error: no such file: {path}", file=sys.stderr)
        return 1
    root = _find_repo_root(path.resolve().parent) or Path.cwd()
    data, err = parse_guide_yaml(path.read_text())
    findings = Findings([err]) if err else check_manifest(data, root)
    if findings.ok():
        print(f"{path}: OK")
        return 0
    findings.report()
    print(f"{len(findings.errors)} error(s) — {path}\n", file=sys.stderr)
    return 1


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
            "\n"
            "publishing on llm-d.ai:\n"
            "  guide.py check-manifest                    validate docs/well-lit-paths/guides.yaml\n"
            "  guide.py set-branch release-0.8 guides/*/  pin BRANCH and re-render (release cut)\n"
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

    sb = sub.add_parser(
        "set-branch",
        help="pin BRANCH in guide.yaml to a ref and re-render (release cuts)",
        description=(
            "Rewrite env.static.BRANCH and every `export BRANCH=` in each "
            "guide.yaml to REF, validate, and re-render the README. Run on a "
            "release branch so a guide's clone step checks out the release it "
            "is published with on llm-d.ai."
        ),
    )
    sb.add_argument("ref", metavar="REF", help="branch or tag, e.g. release-0.8")
    sb.add_argument("targets", nargs="+", metavar="TARGET", help="guide directory; repeatable")
    sb.set_defaults(func=_cmd_set_branch)

    cm = sub.add_parser(
        "check-manifest",
        help="validate the llm-d.ai guide publish manifest",
        description=(
            "Validate the manifest listing which guides llm-d.ai publishes and "
            "where: guide dirs exist with guide.yaml + README.md, slugs are "
            "unique per section, child pages exist, titles are present."
        ),
    )
    cm.add_argument(
        "manifest",
        nargs="?",
        default=DEFAULT_MANIFEST,
        metavar="PATH",
        help=f"manifest path (default: {DEFAULT_MANIFEST})",
    )
    cm.set_defaults(func=_cmd_check_manifest)

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
