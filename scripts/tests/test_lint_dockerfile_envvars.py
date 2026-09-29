"""Regression tests for Dockerfile stage variable tracking."""

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "lint-dockerfile-envvars.py"
SPEC = importlib.util.spec_from_file_location("lint_dockerfile_envvars", MODULE_PATH)
assert SPEC and SPEC.loader
lint = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lint)


def test_child_stage_inherits_base_arg_and_env(tmp_path):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "check.sh").write_text(
        "#!/bin/sh\n"
        "# Required environment variables:\n"
        "# - INHERITED_ARG: declared by the base stage\n"
        "# - INHERITED_ENV: declared by the base stage\n"
    )
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM ubuntu:22.04 AS base\n"
        "ARG INHERITED_ARG=value\n"
        "ENV INHERITED_ENV=value\n"
        "FROM base AS build\n"
        "RUN /tmp/check.sh\n"
    )

    ok, errors = lint.lint_dockerfile(dockerfile, scripts_dir)

    assert ok, errors
