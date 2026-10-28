"""Render regression for the FMA + KEDA guide's kustomize overlays.

The base patch targets the upstream keda-epp queue ScaledObject by kind only
(its name is the envsubst placeholder ${SCALEDOBJECT_NAME}, which kustomize's
patch selector cannot match literally). A name-selector regression would
silently skip the patch and leak ${...} placeholders into the render, breaking
`kubectl apply -n <ns> -k` in the nightly (namespace mismatch).
"""

from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

KEDA_DIR = Path(__file__).resolve().parents[2] / "guides/fast-model-actuation/fast-model-actuation-keda/keda"


def _render(overlay: str) -> str:
    kubectl = shutil.which("kubectl")
    if kubectl:
        result = subprocess.run(
            [kubectl, "kustomize", str(KEDA_DIR / overlay)],
            capture_output=True, text=True, timeout=60,
        )
    else:
        kustomize = shutil.which("kustomize")
        if not kustomize:
            pytest.skip("kubectl or kustomize is required for the render regression")
        result = subprocess.run(
            [kustomize, "build", str(KEDA_DIR / overlay)],
            capture_output=True, text=True, timeout=60,
        )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize("overlay", ["ocp", "k8s"])
def test_overlay_renders_without_placeholders(overlay):
    text = _render(overlay)
    assert "${" not in text


@pytest.mark.parametrize("overlay", ["ocp", "k8s"])
def test_scaledobject_is_retargeted_to_the_requester(overlay):
    docs = [x for x in yaml.safe_load_all(_render(overlay)) if x]
    obj = next(x for x in docs if x.get("kind") == "ScaledObject")
    # The guide applies with `kubectl apply -n ${NAMESPACE} -k`, so the render
    # must not carry its own (placeholder or hardcoded) namespace.
    assert "namespace" not in obj["metadata"]
    assert obj["metadata"]["name"] == "fast-model-actuation-keda-queue"
    spec = obj["spec"]
    assert spec["scaleTargetRef"]["name"] == "fma-requester"
    assert spec["minReplicaCount"] == 0
    assert spec["maxReplicaCount"] == 2
    assert spec["cooldownPeriod"] == 30
    # The scale-down window the guide's 240s scale-to-zero wait budgets for.
    assert spec["advanced"]["horizontalPodAutoscalerConfig"]["behavior"]["scaleDown"]["stabilizationWindowSeconds"] == 30
    assert len(spec["triggers"]) == 1
    assert spec["triggers"][0]["metadata"]["query"].strip() == (
        'sum(llm_d_epp_flow_control_queue_size{inference_pool="fast-model-actuation-keda"})'
    )


def test_ocp_trigger_authentication_has_no_placeholder_namespace():
    docs = [x for x in yaml.safe_load_all(_render("ocp")) if x]
    obj = next(x for x in docs if x.get("kind") == "TriggerAuthentication")
    assert obj["metadata"]["name"] == "keda-prometheus-auth"
    assert "namespace" not in obj["metadata"]
