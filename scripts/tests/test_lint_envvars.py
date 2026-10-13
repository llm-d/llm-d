"""Regression tests for env var reference detection in lint-envvars.py."""

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "lint-envvars.py"
SPEC = importlib.util.spec_from_file_location("lint_envvars", MODULE_PATH)
assert SPEC and SPEC.loader
lint = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lint)


HEADER = (
    "#!/bin/bash\n"
    "# Required environment variables:\n"
    "# - DECLARED: declared in the header\n"
    "\n"
)


def test_find_used_vars_plain_forms():
    content = 'echo "${A}" "${B:-x}" "$C" $1 $@ $?\n'

    assert lint.find_used_vars(content) == {"A", "B", "C"}


def test_find_used_vars_sees_braced_var_nested_in_default():
    assert lint.find_used_vars('echo "${A:-${B}}"\n') == {"A", "B"}


def test_find_used_vars_sees_bare_var_nested_in_default():
    assert lint.find_used_vars('echo "${A:-$B}"\n') == {"A", "B"}


def test_find_used_vars_sees_all_vars_in_composed_default():
    content = 'VARIANT="${VARIANT:-cu${CUDA_MAJOR}${CUDA_MINOR}}"\n'

    assert lint.find_used_vars(content) == {"VARIANT", "CUDA_MAJOR", "CUDA_MINOR"}


def test_lint_script_flags_undeclared_var_nested_in_default(tmp_path):
    script = tmp_path / "nested.sh"
    script.write_text(HEADER + 'echo "${DECLARED:-${UNDECLARED}}"\n')

    ok, errors = lint.lint_script(script)

    assert not ok
    assert "UNDECLARED" in errors[0]


def test_lint_script_accepts_declared_var_nested_in_default(tmp_path):
    script = tmp_path / "nested_ok.sh"
    script.write_text(
        "#!/bin/bash\n"
        "# Required environment variables:\n"
        "# - OUTER: declared in the header\n"
        "# - INNER: declared in the header\n"
        "\n"
        'echo "${OUTER:-${INNER}}"\n'
    )

    ok, errors = lint.lint_script(script)

    assert ok, errors
