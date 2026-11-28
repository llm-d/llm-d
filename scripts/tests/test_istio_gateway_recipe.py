"""Render regressions for the Istio Gateway recipe and HPA overlay."""

from pathlib import Path
import shutil
import subprocess

import pytest
import yaml


ISTIO_DIR = Path(__file__).resolve().parents[2] / "guides/recipes/gateway/istio"


def _render(path: Path) -> str:
    kubectl = shutil.which("kubectl")
    if kubectl:
        command = [kubectl, "kustomize", str(path)]
    else:
        kustomize = shutil.which("kustomize")
        if not kustomize:
            pytest.skip("kubectl or kustomize is required for the render regression")
        command = [kustomize, "build", str(path)]

    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return result.stdout


def _gateway_configmap(rendered: str) -> dict:
    return next(
        document
        for document in yaml.safe_load_all(rendered)
        if document and document.get("kind") == "ConfigMap"
    )


def test_istio_gateway_has_fixed_replicas_and_proxy_resources():
    configmap = _gateway_configmap(_render(ISTIO_DIR))
    deployment = yaml.safe_load(configmap["data"]["deployment"])
    container = deployment["spec"]["template"]["spec"]["containers"][0]

    assert deployment["spec"]["replicas"] == 1
    assert container["name"] == "istio-proxy"
    assert container["resources"] == {
        "limits": {"cpu": "8", "memory": "16Gi"},
        "requests": {"cpu": 2, "memory": "4Gi"},
    }
    assert "horizontalPodAutoscaler" not in configmap["data"]


def test_istio_gateway_hpa_overlay_adds_hpa_without_dropping_resources():
    configmap = _gateway_configmap(_render(ISTIO_DIR.parent / "istio-hpa"))
    deployment = yaml.safe_load(configmap["data"]["deployment"])
    hpa = yaml.safe_load(configmap["data"]["horizontalPodAutoscaler"])

    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["template"]["spec"]["containers"][0]["resources"]
    assert hpa["spec"]["minReplicas"] == 1
    assert hpa["spec"]["maxReplicas"] == 5
    assert hpa["spec"]["metrics"] == [
        {
            "type": "Resource",
            "resource": {
                "name": "cpu",
                "target": {"type": "Utilization", "averageUtilization": 60},
            },
        }
    ]
