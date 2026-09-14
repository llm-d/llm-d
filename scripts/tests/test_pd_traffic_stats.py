"""Regression tests for the P/D observability traffic generator."""

import os
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "guides/recipes/observability/generate-traffic-pd.sh"


def test_statistics_start_at_zero(tmp_path: Path) -> None:
    source = SCRIPT.read_text()
    start = source.index('STATS_DIR="/tmp/load_gen_stats_$$"')
    end = source.index('\necho "============================================================"', start)
    stats_setup = source[start:end]
    stats_setup = re.sub(
        r'^STATS_DIR=.*$',
        'STATS_DIR="${TEST_STATS_DIR}"',
        stats_setup,
        count=1,
        flags=re.MULTILINE,
    )

    env = os.environ.copy()
    env["TEST_STATS_DIR"] = str(tmp_path)
    result = subprocess.run(
        ["bash"],
        input=f"{stats_setup}\nget_stats\n",
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0 0 0"
