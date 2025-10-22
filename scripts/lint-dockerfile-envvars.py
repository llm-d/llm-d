#!/usr/bin/env -S uv run --script
# /// script
# dependencies = []
# ///

import re
import sys
from pathlib import Path
from typing import Dict, Set, Tuple, List


def parse_script_requirements(script_path: Path) -> Set[str]:
    """extract required env vars from script header block"""
    try:
        content = script_path.read_text()
    except:
        return set()

    pattern = r'^#\s+-\s+([A-Z_][A-Z0-9_]*):.*$'
    required = set()

    in_block = False
    for line in content.split('\n'):
        if '# Required environment variables:' in line:
            in_block = True
            continue

        if in_block:
            if not line.strip().startswith('#'):
                break
            match = re.match(pattern, line)
            if match:
                required.add(match.group(1))

    return required


class DockerfileParser:
    def __init__(self, dockerfile_path: Path):
        self.path = dockerfile_path
        self.content = dockerfile_path.read_text()
        self.stages: Dict[str, Dict[str, Set[str]]] = {}
        self.current_stage = None

    def parse(self):
        """parse dockerfile and track ARG/ENV declarations per stage"""
        lines = self.content.split('\n')
        i = 0

        while i < len(lines):
            line = lines[i].strip()

            # handle line continuations
            while line.endswith('\\') and i + 1 < len(lines):
                i += 1
                line = line[:-1] + ' ' + lines[i].strip()

            # remove inline comments (but not in strings)
            if '#' in line:
                # simple heuristic: only strip comments outside quotes
                parts = line.split('#')
                if len(parts) > 1:
                    line = parts[0].strip()

            # detect new build stage
            if line.upper().startswith('FROM'):
                match = re.match(r'FROM\s+.*?\s+AS\s+(\w+)', line, re.IGNORECASE)
                if match:
                    self.current_stage = match.group(1)
                else:
                    self.current_stage = 'default'

                if self.current_stage not in self.stages:
                    self.stages[self.current_stage] = {'ARG': set(), 'ENV': set()}

            # track ARG declarations
            elif line.upper().startswith('ARG'):
                if self.current_stage:
                    match = re.match(r'ARG\s+([A-Z_][A-Z0-9_]*)(?:=.*)?', line, re.IGNORECASE)
                    if match:
                        var_name = match.group(1)
                        self.stages[self.current_stage]['ARG'].add(var_name)

            # track ENV declarations
            elif line.upper().startswith('ENV'):
                if self.current_stage:
                    # handle: ENV VAR=value, ENV VAR value, ENV VAR1=v1 VAR2=v2
                    env_line = line[3:].strip()
                    # split by spaces, but respect = signs
                    for pair in re.findall(r'([A-Z_][A-Z0-9_]*)(?:=\S+|\s+\S+)?', env_line):
                        self.stages[self.current_stage]['ENV'].add(pair)

            i += 1

    def get_available_vars(self, stage: str) -> Set[str]:
        """get all available vars (ARG + ENV) for a stage"""
        if stage not in self.stages:
            return set()
        return self.stages[stage]['ARG'] | self.stages[stage]['ENV']


def find_script_runs(dockerfile_content: str) -> List[Tuple[str, str, int]]:
    """find all RUN commands that execute scripts, return (stage, script_path, line_num)"""
    runs = []
    current_stage = None
    line_num = 0

    for line in dockerfile_content.split('\n'):
        line_num += 1
        stripped = line.strip()

        # track stage
        if stripped.upper().startswith('FROM'):
            match = re.match(r'FROM\s+.*?\s+AS\s+(\w+)', stripped, re.IGNORECASE)
            if match:
                current_stage = match.group(1)
            else:
                current_stage = 'default'

        # find script executions
        if stripped.upper().startswith('RUN'):
            # match: RUN /path/to/script.sh or RUN chmod ... && /path/to/script.sh
            script_matches = re.findall(r'(/[^\s]+\.sh)', stripped)
            for script in script_matches:
                # normalize path (remove /tmp/ prefix if present)
                script_name = Path(script).name
                runs.append((current_stage, script_name, line_num))

    return runs


def lint_dockerfile(dockerfile_path: Path, scripts_dir: Path) -> Tuple[bool, List[str]]:
    """check that all script requirements are met in dockerfile"""
    parser = DockerfileParser(dockerfile_path)
    parser.parse()

    script_runs = find_script_runs(parser.content)

    errors = []

    for stage, script_name, line_num in script_runs:
        # find the script file
        script_path = None
        for candidate in scripts_dir.rglob(script_name):
            script_path = candidate
            break

        if not script_path:
            continue  # script not found in our tree, might be external

        # get requirements
        required_vars = parse_script_requirements(script_path)
        if not required_vars:
            continue  # no requirements declared

        # get available vars in this stage
        available_vars = parser.get_available_vars(stage)

        # check for missing vars
        missing = required_vars - available_vars

        if missing:
            errors.append(
                f"{dockerfile_path}:{line_num}: Script '{script_name}' in stage '{stage}' requires:\n" +
                "\n".join(f"  - {var} (not declared as ARG or ENV)" for var in sorted(missing))
            )

    return len(errors) == 0, errors


def main():
    if len(sys.argv) < 3:
        print("Usage: lint-dockerfile-envvars.py <scripts-dir> <Dockerfile> [<Dockerfile>...]", file=sys.stderr)
        sys.exit(1)

    scripts_dir = Path(sys.argv[1])
    dockerfiles = [Path(arg) for arg in sys.argv[2:]]

    if not scripts_dir.exists():
        print(f"Error: Scripts directory not found: {scripts_dir}", file=sys.stderr)
        sys.exit(1)

    all_errors = []
    for dockerfile in dockerfiles:
        if not dockerfile.exists():
            print(f"Error: Dockerfile not found: {dockerfile}", file=sys.stderr)
            sys.exit(1)

        success, errors = lint_dockerfile(dockerfile, scripts_dir)
        all_errors.extend(errors)

    if all_errors:
        for error in all_errors:
            print(f"WARNING: {error}", file=sys.stderr)
        print(f"\n⚠ {len(all_errors)} warning(s) found", file=sys.stderr)
        sys.exit(0)

    print(f"✓ All Dockerfiles validated successfully")
    sys.exit(0)


if __name__ == '__main__':
    main()
