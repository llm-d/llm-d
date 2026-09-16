import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
WIZARD = REPO_ROOT / "guides" / "flow-control" / "scripts" / "tuning_wizard.py"
MEMORY_ARGS = (
    "--gpu-blocks", "100", "--isl-mean", "10", "--isl-std", "1",
    "--osl-mean", "10", "--osl-std", "1",
)


def run_wizard(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(WIZARD), *args],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("--throughput", "inf", "--latency-sec", "1"), "must be finite"),
        ((*MEMORY_ARGS, "--paged-attention-efficiency", "1.5"), "range (0, 1]"),
        ((*MEMORY_ARGS, "--correlation-coefficient", "2"), "range [-1, 1]"),
        (("--gpu-blocks", "100", "--isl-mean", "10", "--isl-std", "-1",
          "--osl-mean", "10", "--osl-std", "1"), "cannot be negative"),
    ],
)
def test_invalid_parameters_are_reported_without_traceback(
    args: tuple[str, ...], message: str
) -> None:
    result = run_wizard(*args)

    assert result.returncode != 0
    assert message in result.stderr
    assert "Traceback" not in result.stderr


def test_valid_parameters_still_produce_recommendations() -> None:
    result = run_wizard(*MEMORY_ARGS)

    assert result.returncode == 0
    assert "Gateway Max Concurrency" in result.stdout
