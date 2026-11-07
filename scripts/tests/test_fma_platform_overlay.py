"""Render regression for the Fast Model Actuation guides' platform overlays.

platform/<INFRA_PROVIDER> is what the guides apply (README step 5). `ocp` must
be a passthrough of modelserver/; `gke` must carry exactly the GKE component's
adaptations: no nvidia RuntimeClass, a privileged launcher with the host driver
tree and /dev mounted, the GPU taint toleration, and the GKE GPU node label on
the LauncherPopulationPolicy. The FMA + KEDA guide reuses the same component.
"""

from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

FMA_DIR = Path(__file__).resolve().parents[2] / "guides/fast-model-actuation"
GUIDES = {
    name: path
    for name, path in {
        "fma": FMA_DIR,
        "fma-keda": FMA_DIR / "fast-model-actuation-keda",
    }.items()
    if (path / "platform/gke").is_dir()
}


def _render(path: Path) -> list[dict]:
    tool = shutil.which("kubectl")
    cmd = [tool, "kustomize", str(path)] if tool else None
    if cmd is None:
        kustomize = shutil.which("kustomize")
        if not kustomize:
            pytest.skip("kubectl or kustomize is required for the render regression")
        cmd = [kustomize, "build", str(path)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return [x for x in yaml.safe_load_all(result.stdout) if x]


def _by_kind(docs: list[dict], kind: str) -> dict:
    return next(x for x in docs if x.get("kind") == kind)


@pytest.mark.parametrize("guide", sorted(GUIDES))
def test_ocp_platform_is_a_passthrough(guide):
    assert _render(GUIDES[guide] / "platform/ocp") == _render(GUIDES[guide] / "modelserver")


@pytest.mark.parametrize("guide", sorted(GUIDES))
def test_gke_platform_adapts_launcher_and_lpp(guide):
    docs = _render(GUIDES[guide] / "platform/gke")
    assert sorted(x["kind"] for x in docs) == sorted(
        x["kind"] for x in _render(GUIDES[guide] / "modelserver")
    )

    pod = _by_kind(docs, "LauncherConfig")["spec"]["podTemplate"]["spec"]
    assert "runtimeClassName" not in pod
    assert {"key": "nvidia.com/gpu", "operator": "Equal", "value": "present",
            "effect": "NoSchedule"} in pod["tolerations"]
    volumes = {v["name"]: v["hostPath"]["path"] for v in pod["volumes"]}
    assert volumes == {"nvidia-driver": "/home/kubernetes/bin/nvidia", "dev": "/dev"}
    launcher = pod["containers"][0]
    assert launcher["securityContext"] == {"privileged": True}
    mounts = {m["name"]: m["mountPath"] for m in launcher["volumeMounts"]}
    assert mounts == {"nvidia-driver": "/usr/local/nvidia", "dev": "/dev"}

    lpp = _by_kind(docs, "LauncherPopulationPolicy")["spec"]
    assert lpp["enhancedNodeSelector"]["labelSelector"]["matchLabels"] == {
        "cloud.google.com/gke-gpu": "true"
    }


@pytest.mark.parametrize("guide", sorted(GUIDES))
@pytest.mark.parametrize("provider", ["base", "ocp", "gke"])
def test_model_detection_overlay_matches_served_model(guide, provider):
    """llm-d-benchmark renders modelserver/gpu/vllm/<INFRA_PROVIDER> to detect
    the served model; every provider must resolve and name the guide's model."""
    docs = _render(GUIDES[guide] / "modelserver/gpu/vllm" / provider)
    assert len(docs) == 1 and docs[0]["kind"] == "Deployment"
    assert docs[0]["spec"]["replicas"] == 0
    args = docs[0]["spec"]["template"]["spec"]["containers"][0]["args"]
    isc = _by_kind(_render(GUIDES[guide] / "modelserver"), "InferenceServerConfig")
    assert args == ["Qwen/Qwen3-0.6B"]
    assert "--model Qwen/Qwen3-0.6B" in isc["spec"]["modelServerConfig"]["options"]
