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


def engine_limits(stdout: str) -> set[int]:
    return {
        int(line.split()[-1])
        for line in stdout.splitlines()
        if "--max-num-seqs" in line or "--max-concurrent-requests" in line
    }


DOC_EXAMPLE_ARGS = (
    "--throughput", "12.5", "--latency-sec", "3.45", "--gpu-blocks", "21875",
    "--block-size", "16", "--isl-mean", "4000", "--isl-std", "1500",
    "--osl-mean", "800", "--osl-std", "300", "--shared-prefix", "3000",
    "--z-score", "2.0",
)


@pytest.mark.parametrize(
    ("args", "headroom", "n_active", "engine_limit"),
    [
        # Example from docs/operations/traffic/flow-control-tuning.md, Step 3.
        ((*DOC_EXAMPLE_ARGS, "--enable-prefix-caching"), "0.1", 43, 48),
        # 50 * 1.1 is 55.000000000000007 in floating point.
        (("--throughput", "50", "--latency-sec", "1", *DOC_EXAMPLE_ARGS[4:],
          "--enable-prefix-caching"), "0.1", 50, 55),
        (DOC_EXAMPLE_ARGS, "0.0", 43, 43),
        # A Memory Limit of 46 cannot hold the burst peak of 48, so no headroom.
        ((*DOC_EXAMPLE_ARGS[:4], "--gpu-blocks", "6200", *DOC_EXAMPLE_ARGS[6:],
          "--enable-prefix-caching"), "0.0", 43, 43),
    ],
)
def test_engine_limit_covers_recommended_headroom(
    args: tuple[str, ...], headroom: str, n_active: int, engine_limit: int
) -> None:
    result = run_wizard(*args)

    assert result.returncode == 0, result.stderr
    assert f"Engine Target (N_active): {n_active}" in result.stdout
    assert f"headroom: {headroom} " in result.stdout
    assert engine_limits(result.stdout) == {engine_limit}
