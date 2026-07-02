#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["pyyaml"]
# ///
"""Validate a guide.yaml against the well-lit-path schema.

Prints all findings and exits non-zero on any error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

TOP_REQUIRED = {"name", "env", "deploy"}
TOP_OPTIONAL = {"_lists", "prerequisites", "verify", "benchmark", "cleanup"}


# ---------------------------------------------------------------------------
# Strict loader: reject duplicate keys in every mapping.
#
# PyYAML's default SafeLoader silently keeps the LAST value when the same key
# appears twice in a mapping. That masked a real bug in optimized-baseline
# where a `cleanup:` step had two `run:` entries — the intended MONITORING-gated
# delete was overwritten by the non_gpu-gated one. This loader raises on the
# duplicate, and the caller turns the exception into a normal finding.
# ---------------------------------------------------------------------------


class _StrictLoader(yaml.SafeLoader):
    pass


def _no_duplicate_keys(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False):
    seen: dict[object, yaml.Node] = {}
    for key_node, _value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
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


def check_step(step, path: str, declared_vars: set[str], f: Findings) -> None:
    if not isinstance(step, dict):
        f.error(
            f"{path}: step must be a map with 'run:' key, got {type(step).__name__}"
        )
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
                if var not in declared_vars:
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

    known = {"run", "when", "skip_in"}
    for k in step:
        if k not in known:
            f.error(f"{path}: unknown step key {k!r} (allowed: {sorted(known)})")


def check_step_list(node, path: str, declared_vars: set[str], f: Findings) -> None:
    # A step-list slot may be either a flat list of steps, or a map of
    # named sub-groups (each value is itself a step list). Both shapes render
    # to the same concatenated bash block.
    if isinstance(node, list):
        for i, step in enumerate(node):
            check_step(step, f"{path}[{i}]", declared_vars, f)
        return
    if isinstance(node, dict):
        for group_name, group_steps in node.items():
            if not isinstance(group_name, str):
                f.error(
                    f"{path}: sub-group name must be a string, got {type(group_name).__name__}"
                )
                continue
            check_step_list(group_steps, f"{path}.{group_name}", declared_vars, f)
        return
    f.error(
        f"{path}: expected a list of steps (or a map of named sub-groups), got {type(node).__name__}"
    )


def check_env(env, f: Findings) -> set[str]:
    declared: set[str] = set()

    if not isinstance(env, dict):
        f.error("env: must be a map")
        return declared

    for k in env:
        if k not in {"static", "source", "derive"}:
            f.error(f"env: unknown key {k!r} (allowed: static, source, derive)")

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
        if isinstance(spec, dict):
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
            allowed = {"default", "values", "sensitive"}
            for k in spec:
                if k not in allowed:
                    f.error(
                        f"env.static.{var}: unknown key {k!r} (allowed: {sorted(allowed)})"
                    )
            if "values" in spec:
                if not isinstance(spec["values"], list):
                    f.error(f"env.static.{var}.values: must be a list")
                if (
                    "default" in spec
                    and spec.get("values")
                    and not spec.get("sensitive")
                ):
                    if str(spec["default"]) not in [str(v) for v in spec["values"]]:
                        f.error(
                            f"env.static.{var}: default {spec['default']!r} not in values {spec['values']}"
                        )
        else:
            pass

    return declared


def discover_modes(guide: dict, f: Findings) -> list[str]:
    """Modes are derived from `verify.endpoint` keys (fallback: `benchmark.endpoint`).
    Modes describe how to talk to the deployed system, so endpoint discovery is
    the natural source. Returns [] if neither section is present."""
    for section in ("verify", "benchmark"):
        ep = (
            guide.get(section, {}).get("endpoint")
            if isinstance(guide.get(section), dict)
            else None
        )
        if isinstance(ep, dict):
            return list(ep.keys())
    return []


def check_verify(node, modes: list[str], declared_vars: set[str], f: Findings) -> None:
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
                check_step_list(v, f"verify.endpoint.{k}", declared_vars, f)
    tests = node.get("tests")
    if tests is not None:
        check_step_list(tests, "verify.tests", declared_vars, f)


def check_benchmark(
    node, modes: list[str], declared_vars: set[str], f: Findings
) -> None:
    if not isinstance(node, dict):
        f.error("benchmark: must be a map")
        return
    for k in node:
        if k not in {"setup", "endpoint", "execute"}:
            f.error(f"benchmark: unknown key {k!r} (allowed: setup, endpoint, execute)")
    if "setup" in node:
        check_step_list(node["setup"], "benchmark.setup", declared_vars, f)
    ep = node.get("endpoint")
    if ep is not None:
        if not isinstance(ep, dict):
            f.error("benchmark.endpoint: must be a map keyed by mode")
        else:
            for k, v in ep.items():
                check_step_list(v, f"benchmark.endpoint.{k}", declared_vars, f)
            # Cross-check with verify.endpoint modes if both are present.
            if modes and set(ep.keys()) != set(modes):
                f.error(
                    f"benchmark.endpoint keys {sorted(ep.keys())} don't match "
                    f"verify.endpoint keys {sorted(modes)} — modes must agree"
                )
    if "execute" in node:
        check_step_list(node["execute"], "benchmark.execute", declared_vars, f)


def check(guide: dict) -> Findings:
    f = Findings()

    if not isinstance(guide, dict):
        f.error(f"top level: must be a map, got {type(guide).__name__}")
        return f

    for k in TOP_REQUIRED:
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

    declared = check_env(guide.get("env", {}), f)
    modes = discover_modes(guide, f)

    if "prerequisites" in guide:
        # Prerequisites are universal (never mode-specific in practice), so treat the
        # value as a step-list bucket — accepts either a flat list of steps OR a map
        # of named sub-groups (e.g. `gaie:`, `namespace:`, `secrets:`).
        check_step_list(guide["prerequisites"], "prerequisites", declared, f)
    # Deploy is a step-list bucket too — each sub-group may be a mode name
    # (e.g. `standalone:`, `gateway:`) or a common sub-group (e.g. `modelserver:`,
    # `monitoring:`). Modes are discovered from `verify.endpoint` keys; the checker
    # does NOT enforce that mode-named sub-groups exist under `deploy:` — you can
    # opt out of a mode by simply not defining it.
    check_step_list(guide.get("deploy", {}), "deploy", declared, f)
    if "verify" in guide:
        check_verify(guide["verify"], modes, declared, f)
    if "benchmark" in guide:
        check_benchmark(guide["benchmark"], modes, declared, f)
    if "cleanup" in guide:
        check_step_list(guide["cleanup"], "cleanup", declared, f)

    return f


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("yaml", type=Path, help="path to guide.yaml")
    args = ap.parse_args()

    try:
        with args.yaml.open() as fh:
            guide = yaml.load(fh, Loader=_StrictLoader)
    except yaml.constructor.ConstructorError as e:
        # Duplicate-key errors carry their own line info in `problem_mark`.
        mark = e.problem_mark
        loc = f"line {mark.line + 1}" if mark else "unknown location"
        print(f"error: {loc}: {e.problem}", file=sys.stderr)
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"error: could not parse YAML: {e}", file=sys.stderr)
        sys.exit(1)

    findings = check(guide)
    findings.report()
    if not findings.ok():
        print(f"\n{len(findings.errors)} error(s) — {args.yaml}", file=sys.stderr)
        sys.exit(1)
    print(f"{args.yaml}: OK")


if __name__ == "__main__":
    main()
