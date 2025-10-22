#!/usr/bin/env -S uv run --script
# /// script
# dependencies = []
# ///

import re
import sys
from pathlib import Path
from typing import Set, Tuple


def extract_required_vars(script_content: str) -> Set[str]:
    """extract required env vars from standardized comment block"""
    # match "# - VAR_NAME: description" pattern
    pattern = r'^#\s+-\s+([A-Z_][A-Z0-9_]*):.*$'
    required = set()

    in_block = False
    for line in script_content.split('\n'):
        # start of declaration block
        if '# Required environment variables:' in line:
            in_block = True
            continue

        # end of block when we hit non-comment
        if in_block:
            if not line.strip().startswith('#'):
                break
            match = re.match(pattern, line)
            if match:
                required.add(match.group(1))

    return required


def find_locally_defined_vars(script_content: str) -> Set[str]:
    """find variables that are assigned/defined within the script"""
    defined = set()

    # match various assignment patterns:
    # VAR="value", VAR=$(cmd), VAR=, export VAR="value", etc.
    assign_pattern = r'^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)='

    # match array declarations: VAR=( ... )
    array_pattern = r'^\s*([A-Z_][A-Z0-9_]*)=\('

    for line in script_content.split('\n'):
        # skip comments
        if line.strip().startswith('#'):
            continue

        # check for assignments
        match = re.match(assign_pattern, line)
        if match:
            defined.add(match.group(1))
            continue

        # check for array declarations
        match = re.match(array_pattern, line)
        if match:
            defined.add(match.group(1))

    return defined


def find_used_vars(script_content: str) -> Set[str]:
    """find all ${VAR}, ${VAR:-default}, $VAR references in script"""
    # matches: ${VAR}, ${VAR:-default}, "${VAR}", $VAR, etc.
    # excludes: $1, $@, $*, $?, $$, $!, etc. (special vars)
    pattern = r'\$\{([A-Z_][A-Z0-9_]*)[^}]*\}|\$([A-Z_][A-Z0-9_]*)\b'
    used = set()

    for match in re.finditer(pattern, script_content):
        var = match.group(1) or match.group(2)
        if var:  # skip empty matches
            used.add(var)

    return used


def lint_script(script_path: Path) -> Tuple[bool, list[str]]:
    """check that all used vars are declared. returns (success, errors)"""
    try:
        content = script_path.read_text()
    except Exception as e:
        return False, [f"{script_path}: Failed to read file: {e}"]

    required = extract_required_vars(content)
    used = find_used_vars(content)
    defined_locally = find_locally_defined_vars(content)

    # vars that don't need declaration (shell built-ins, common env vars)
    exempt = {
        'PATH', 'HOME', 'USER', 'PWD', 'SHELL', 'TERM', 'LANG', 'LC_ALL',
        'OLDPWD', 'HOSTNAME', 'TMPDIR', 'EDITOR', 'PAGER',
        # build-related that are typically set by CI/build systems
        'CI', 'GITHUB_ACTIONS', 'GITHUB_WORKSPACE', 'RUNNER_TEMP',
        # AWS credentials often set dynamically
        'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_DEFAULT_REGION',
    }

    # only require declaration for vars that are:
    # 1. Used in the script
    # 2. Not declared in the header
    # 3. Not defined locally in the script
    # 4. Not in the exempt list
    undeclared = (used - required) - defined_locally - exempt

    errors = []
    if undeclared:
        errors.append(
            f"{script_path}: Undeclared environment variables:\n" +
            "\n".join(f"  - {var}" for var in sorted(undeclared))
        )

    return len(errors) == 0, errors


def main():
    if len(sys.argv) < 2:
        print("Usage: lint-envvars.py <script1> [script2 ...]", file=sys.stderr)
        sys.exit(1)

    scripts = [Path(p) for p in sys.argv[1:]]

    all_errors = []
    success_count = 0

    for script in scripts:
        if not script.exists():
            all_errors.append(f"{script}: File not found")
            continue

        success, errors = lint_script(script)
        if success:
            success_count += 1
        else:
            all_errors.extend(errors)

    if all_errors:
        for error in all_errors:
            print(f"WARNING: {error}", file=sys.stderr)
        print(f"\n⚠ {len(all_errors)} warning(s) found", file=sys.stderr)
        sys.exit(0)

    print(f"✓ Checked {success_count} script(s) - all environment variables properly declared")
    sys.exit(0)


if __name__ == '__main__':
    main()
