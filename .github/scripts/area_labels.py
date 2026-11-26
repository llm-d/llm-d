#!/usr/bin/env python3
"""Compute path-based label changes for a pull request.

Evaluates .github/labeler.yml (additive area/* labels) and
.github/labeler-sync.yml (synced labels such as docs-only) against a PR's
changed files and prints one "add <label>" or "remove <label>" line per change.

.github/workflows/pr-area-labeler.yaml applies each line with its own
POST/DELETE instead of replacing the PR's whole label set, so a label that
another workflow removes mid-run (for example lgtm on a new push) is never
written back.

The config files keep the actions/labeler v5+ schema. Supported glob syntax is
the subset of minimatch (dot: true) the configs use: `*`, `?`, whole-segment
`**`, and `{a,b}` braces. Anything else raises ValueError, so an unsupported
pattern fails scripts/tests/test_labeler.py instead of silently not matching.

Standard library only: the workflow runs with a write token, so it installs
nothing. Configs are passed as JSON (the workflow converts them with yq).
"""

import argparse
import functools
import json
from pathlib import Path
import re
import sys


def brace_expand(pattern: str) -> list[str]:
    """Expand top-level and nested {a,b,...} braces the way minimatch does."""
    depth = 0
    open_idx = -1
    commas: list[int] = []
    for idx, ch in enumerate(pattern):
        if ch == "{":
            if depth == 0:
                open_idx = idx
                commas = []
            depth += 1
        elif ch == "," and depth == 1:
            commas.append(idx)
        elif ch == "}":
            depth -= 1
            if depth < 0:
                raise ValueError(f"Unmatched '}}' in glob: {pattern!r}")
            if depth == 0:
                if open_idx != -1 and commas:
                    prefix = pattern[:open_idx]
                    suffix = pattern[idx + 1 :]
                    splits = [open_idx] + commas + [idx]
                    parts = [
                        pattern[splits[k] + 1 : splits[k + 1]]
                        for k in range(len(splits) - 1)
                    ]
                    results: list[str] = []
                    for part in parts:
                        for expanded in brace_expand(part):
                            results.extend(brace_expand(prefix + expanded + suffix))
                    return results
                open_idx = -1
    if depth != 0:
        raise ValueError(f"Unclosed '{{' in glob: {pattern!r}")
    return [pattern]


def _single_glob_to_regex(pattern: str) -> str:
    """Translate one brace-free glob into a regex body."""
    if pattern.startswith("!"):
        raise ValueError(f"Negated globs are unsupported: {pattern!r}")
    for unsupported in ("[", "@(", "+(", "*(", "?("):
        if unsupported in pattern:
            raise ValueError(f"Unsupported glob token {unsupported!r} in {pattern!r}")

    for seg in pattern.split("/"):
        if "**" in seg and seg != "**":
            raise ValueError(f"'**' must be a whole path segment: {pattern!r}")

    i = 0
    n = len(pattern)
    out = ["^"]
    while i < n:
        if pattern.startswith("/**/", i):
            out.append("(?:/.+/|/)")
            i += 4
        elif i == 0 and pattern.startswith("**/", i):
            out.append("(?:.+/)?")
            i += 3
        elif pattern.startswith("/**", i) and i + 3 == n:
            out.append("/.+")
            i += 3
        elif pattern == "**":
            out.append(".+")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    out.append("$")
    return "".join(out)


@functools.lru_cache(maxsize=None)
def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Compile a glob (including brace expansion) into one Pattern."""
    branches = [_single_glob_to_regex(p) for p in brace_expand(pattern)]
    if len(branches) == 1:
        return re.compile(branches[0])
    return re.compile("|".join(f"(?:{b})" for b in branches))


def eval_changed_files_entry(
    entry: dict, changed_files: list[str], is_all: bool
) -> bool:
    """Evaluate one changed-files rule object."""
    if not changed_files:
        return False
    results: list[bool] = []
    for match_type, globs in entry.items():
        compiled = [glob_to_regex(g) for g in globs]
        if match_type == "any-glob-to-any-file":
            matched = any(rx.match(f) for rx in compiled for f in changed_files)
        elif match_type == "any-glob-to-all-files":
            # True iff at least ONE glob matches ALL changed files, so a set of
            # allowed paths must be a single brace-expanded glob.
            matched = any(all(rx.match(f) for f in changed_files) for rx in compiled)
        elif match_type == "all-globs-to-all-files":
            matched = all(all(rx.match(f) for f in changed_files) for rx in compiled)
        elif match_type == "all-globs-to-any-file":
            matched = any(all(rx.match(f) for rx in compiled) for f in changed_files)
        else:
            raise ValueError(f"Unsupported matcher: {match_type}")
        results.append(matched)
    return all(results) if is_all else any(results)


def eval_rule(rule: dict, changed_files: list[str], is_all: bool = False) -> bool:
    """Evaluate one rule item (changed-files, all, or any)."""
    if not changed_files:
        return False
    if "changed-files" in rule:
        entries = rule["changed-files"]
        if is_all:
            return all(eval_changed_files_entry(e, changed_files, True) for e in entries)
        return any(eval_changed_files_entry(e, changed_files, False) for e in entries)
    if "all" in rule:
        return all(eval_rule(sub, changed_files, is_all=True) for sub in rule["all"])
    if "any" in rule:
        return any(eval_rule(sub, changed_files, is_all=False) for sub in rule["any"])
    raise ValueError(f"Unsupported rule block: {rule}")


def matching_labels(config: dict, changed_files: list[str]) -> set[str]:
    """Return the labels in config whose rules match changed_files."""
    if not changed_files:
        return set()
    matched = set()
    for label, rules in config.items():
        if rules and all(eval_rule(rule, changed_files) for rule in rules):
            matched.add(label)
    return matched


def label_changes(
    additive: dict, synced: dict, changed_files: list[str], current: set[str]
) -> list[tuple[str, str]]:
    """Return sorted ("add" | "remove", label) changes.

    Additive labels are only ever added. Synced labels are added when they
    match and removed when they no longer do.
    """
    synced_match = matching_labels(synced, changed_files)
    wanted = matching_labels(additive, changed_files) | synced_match
    changes = [("add", label) for label in sorted(wanted - current)]
    changes += [
        ("remove", label)
        for label in sorted((set(synced) - synced_match) & current)
    ]
    return changes


def _read_lines(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--additive", type=Path, required=True,
                        help="labeler.yml as JSON")
    parser.add_argument("--sync", type=Path, required=True,
                        help="labeler-sync.yml as JSON")
    parser.add_argument("--files", type=Path, required=True,
                        help="changed file paths, one per line")
    parser.add_argument("--current", type=Path, required=True,
                        help="labels currently on the PR, one per line")
    args = parser.parse_args(argv)

    additive = json.loads(args.additive.read_text(encoding="utf-8")) or {}
    synced = json.loads(args.sync.read_text(encoding="utf-8")) or {}
    changes = label_changes(
        additive, synced, _read_lines(args.files), set(_read_lines(args.current))
    )
    for action, label in changes:
        print(f"{action} {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
