"""Regression tests for the AMD workload-autoscaling deploy helper."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "guides/workload-autoscaling/scripts/nightly-deploy-amd.sh"
SOURCE_PATCH = ROOT / "guides/optimized-baseline/modelserver/amd/vllm/base/patch-vllm.yaml"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_amd_deploy_does_not_mutate_tracked_model_patch(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "kubectl",
        """#!/usr/bin/env bash
if [[ "$1" == apply && "$2" == -k ]]; then
  exit 1
fi
exit 0
""",
    )
    _write_executable(fake_bin / "realpath", """#!/usr/bin/env bash
if [[ "$1" == --version ]]; then
  exit 0
fi
if [[ "$1" == --relative-to=* ]]; then
  printf '..\\n'
fi
""")

    output_dir = tmp_path / "rendered"
    source_before = _sha256(SOURCE_PATCH)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "REPO_ROOT": str(ROOT),
            "NAMESPACE": "nightly-test",
            "OUTPUT_DIR": str(output_dir),
        }
    )

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert _sha256(SOURCE_PATCH) == source_before
    generated = (output_dir / "modelserver/kustomization.yaml").read_text()
    assert "replicas: 2" in generated
    assert "patches:" in generated
