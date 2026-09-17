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


def test_script_on_run_continuation_line_is_linted(tmp_path):
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "check.sh").write_text(
        "#!/bin/sh\n"
        "# Required environment variables:\n"
        "# - MISSING_VAR: never declared by the Dockerfile\n"
    )
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM ubuntu:22.04 AS build\n"
        "RUN --mount=type=cache,target=/var/cache/dnf \\\n"
        "    /tmp/check.sh && \\\n"
        "    rm -f /tmp/check.sh\n"
    )

    runs = lint.find_script_runs(dockerfile.read_text())
    assert runs == [("build", "check.sh", 2)]

    ok, errors = lint.lint_dockerfile(dockerfile, scripts_dir)

    assert not ok
    assert len(errors) == 1
    assert "MISSING_VAR" in errors[0]
    assert "Dockerfile:2:" in errors[0]
