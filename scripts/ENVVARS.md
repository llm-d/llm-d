# Environment Variable Declaration Standard

## Overview

All shell scripts in `docker/scripts/` must declare their required environment
variables in a standardized header block. The `lint-envvars.py` tool enforces
this at lint time.

## Header Block Pattern

```bash
#!/bin/bash
set -Eeuo pipefail

# script description goes here
#
# Required environment variables:
# - VAR_NAME: description of what this var is for
# - ANOTHER_VAR: another description
# - THIRD_VAR: yet another description
```

## Rules

1. The block must start with `# Required environment variables:`
2. Each variable must be listed on its own line with the pattern:
   `# - VAR_NAME: description`
3. Variable names must be uppercase with underscores (e.g., `CUDA_MAJOR`, `VIRTUAL_ENV`)
4. The block ends at the first non-comment line
5. All environment variables referenced in the script (via `${VAR}` or `$VAR`)
   must be declared

## Exempt Variables

Common shell/system variables don't need to be declared:

- `PATH`, `HOME`, `USER`, `PWD`, `SHELL`, `TERM`
- `LANG`, `LC_ALL`, `HOSTNAME`, `TMPDIR`
- CI-related: `CI`, `GITHUB_ACTIONS`, `GITHUB_WORKSPACE`, `RUNNER_TEMP`

## Running the Linter

The linter runs automatically via pre-commit hooks. You can also run it manually:

```bash
# single script
./scripts/lint-envvars.py docker/scripts/cuda/builder/build-nvshmem.sh

# multiple scripts
./scripts/lint-envvars.py docker/scripts/**/*.sh
```

## Example

See `docker/scripts/cuda/builder/build-compiled-wheels.sh` for a complete example.

## Dockerfile Validation

The `lint-dockerfile-envvars.py` tool validates that Dockerfiles properly
declare all environment variables required by scripts they execute.

### How It Works

1. Parses script header blocks to extract required variables
2. Parses Dockerfiles to find `ARG` and `ENV` declarations per build stage
3. Finds all `RUN` commands that execute scripts
4. Validates that all required vars are available when the script runs

### Running the Dockerfile Linter

```bash
# check specific dockerfile
./scripts/lint-dockerfile-envvars.py docker/Dockerfile.cuda docker/scripts

# check all dockerfiles
for df in docker/Dockerfile.*; do
  ./scripts/lint-dockerfile-envvars.py "$df" docker/scripts
done
```

## Integration

Both linters are integrated into:

- **Pre-commit hooks**: Run automatically on `git commit`
- **CI/CD**: Run in the `pre-commit` job in `.github/workflows/build-image.yml`

To set up pre-commit locally:

```bash
pip install pre-commit
pre-commit install
```
