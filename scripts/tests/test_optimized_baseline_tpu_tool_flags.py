"""Regression coverage for Qwen3-Coder tool calling on the TPU dynamic-slice overlays.

Both ``optimized-baseline`` dynamic-slice shapes serve Qwen3-Coder, which needs
``--enable-auto-tool-choice`` and the ``qwen3_xml`` parser. Without them vLLM
rejects any request that carries a ``tools`` list.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
OVERLAY_ROOT = ROOT / "guides/optimized-baseline/modelserver/tpu/v7-dynamic-slice/vllm"
SHAPES = ["2x2x1", "2x2x2"]
MODEL = "Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8"


def render(shape: str) -> str:
    kubectl = shutil.which("kubectl")
    if not kubectl:
        pytest.skip("kubectl is required for the kustomize render regression")
    result = subprocess.run(
        [kubectl, "kustomize", str(OVERLAY_ROOT / shape)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def serving_containers(rendered: str) -> list[tuple[dict, str]]:
    leader_worker_set = next(
        doc
        for doc in yaml.safe_load_all(rendered)
        if doc and doc.get("kind") == "LeaderWorkerSet"
    )
    template = leader_worker_set["spec"]["leaderWorkerTemplate"]
    containers = []
    for template_name in ("leaderTemplate", "workerTemplate"):
        pod_spec = (template.get(template_name) or {}).get("spec", {})
        for container in pod_spec.get("containers", []):
            argv = (container.get("args") or []) + (container.get("command") or [])
            text = " ".join(str(arg) for arg in argv)
            if "vllm serve" in text:
                containers.append((container, text))
    return containers


@pytest.mark.parametrize("shape", SHAPES)
def test_qwen3_coder_tpu_overlay_enables_tool_calling(shape: str) -> None:
    rendered = render(shape)

    # The issue's own detection method: each flag must appear exactly once.
    assert rendered.count("--enable-auto-tool-choice") == 1
    assert rendered.count("--tool-call-parser=qwen3_xml") == 1
    assert "--reasoning-parser" not in rendered

    containers = serving_containers(rendered)
    assert len(containers) == 1, f"expected one vllm serve container in {shape}"
    container, argv = containers[0]
    assert MODEL in argv
    assert "--enable-auto-tool-choice" in argv
    assert "--tool-call-parser=qwen3_xml" in argv

    command = container.get("command") or []
    if command[:1] == ["/bin/bash"]:
        script = command[2]
        subprocess.run(["bash", "-n"], input=script, text=True, check=True)
