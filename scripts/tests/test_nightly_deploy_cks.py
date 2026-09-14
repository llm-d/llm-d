"""Regression tests for the CKS workload-autoscaling deploy helper."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "guides/workload-autoscaling/scripts/nightly-deploy-cks.sh"
SOURCE_PATCH = ROOT / "guides/optimized-baseline/modelserver/gpu/vllm/base/patch-vllm.yaml"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _run_deploy_script(tmp_path: Path, output_dir: Path) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    _write_executable(
        fake_bin / "kubectl",
        """#!/usr/bin/env bash
if [[ "$1" == get && "$2" == crd ]]; then
  exit 1
fi
exit 0
""",
    )
    _write_executable(fake_bin / "helm", "#!/usr/bin/env bash\nexit 0\n")

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env.update(
        {
            "NAMESPACE": "nightly-test",
            "OUTPUT_DIR": str(output_dir),
            "WVA_TAG": "",
        }
    )
    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_cks_deploy_uses_nonmutating_idempotent_patch(tmp_path: Path) -> None:
    """A failed/repeated run must not edit or grow the tracked model patch."""

    source_before = _sha256(SOURCE_PATCH)
    output_dir = tmp_path / "rendered"

    first = _run_deploy_script(tmp_path, output_dir)
    assert first.returncode != 0
    assert "CRD scaledobjects.keda.sh not found" in first.stderr
    source_after_first = _sha256(SOURCE_PATCH)
    generated_patch = output_dir / "modelserver/patch-vllm.yaml"
    generated_after_first = generated_patch.read_bytes()

    kustomize = shutil.which("kustomize")
    if kustomize is None:
        pytest.skip("kustomize is required to validate the generated modelserver overlay")
    rendered = subprocess.run(
        [kustomize, "build", str(output_dir / "modelserver")],
        text=True,
        capture_output=True,
        check=False,
    )
    assert rendered.returncode == 0, rendered.stderr
    assert "name: optimized-baseline-nvidia-gpu-vllm-decode" in rendered.stdout

    second = _run_deploy_script(tmp_path, output_dir)
    assert second.returncode != 0
    assert "CRD scaledobjects.keda.sh not found" in second.stderr

    assert source_after_first == source_before == _sha256(SOURCE_PATCH)
    assert generated_patch.read_bytes() == generated_after_first

    patch = yaml.safe_load(generated_after_first)
    assert patch["spec"]["replicas"] == 2
    assert patch["spec"]["template"]["spec"]["priorityClassName"] == "nightly-gpu-critical"
    volumes = patch["spec"]["template"]["spec"]["volumes"]
    mounts = patch["spec"]["template"]["spec"]["containers"][0]["volumeMounts"]
    assert [volume["name"] for volume in volumes] == ["triton-cache"]
    assert [mount["name"] for mount in mounts] == ["triton-cache"]
