"""Model-server monitoring for the Coordinator Disaggregation guide.

guides/coord-disaggregation/modelserver/monitoring builds one PodMonitor per role
(encode, prefill, decode) from recipes/modelserver/components/monitoring-epd. A
PodMonitor whose selector or port does not match the pods scrapes nothing and
reports no error, so these tests build the guide's model servers and check that
each PodMonitor selects exactly its role's pods on a port those pods expose.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
GUIDE_DIR = REPO_ROOT / "guides/coord-disaggregation"
ROLES = ["encode", "prefill", "decode"]


def _build(path: Path) -> list[dict]:
    kubectl, kustomize = shutil.which("kubectl"), shutil.which("kustomize")
    if kubectl:
        cmd = [kubectl, "kustomize", str(path)]
    elif kustomize:
        cmd = [kustomize, "build", str(path)]
    else:
        pytest.skip("kubectl or kustomize is required to build the guide's overlays")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def _role_pods() -> dict[str, dict]:
    """Pod template labels and container port names per role of the DisaggregatedSet."""
    [ds] = [d for d in _build(GUIDE_DIR / "modelserver/gpu/vllm/base") if d["kind"] == "DisaggregatedSet"]
    pods = {}
    for role in ds["spec"]["roles"]:
        lwt = role["spec"]["leaderWorkerTemplate"]
        template = lwt.get("leaderTemplate") or lwt["workerTemplate"]
        ports = {p["name"] for c in template["spec"]["containers"] for p in c.get("ports", []) if "name" in p}
        pods[role["name"]] = {"labels": template["metadata"]["labels"], "ports": ports}
    return pods


def _podmonitors() -> dict[str, dict]:
    docs = _build(GUIDE_DIR / "modelserver/monitoring")
    assert {d["kind"] for d in docs} == {"PodMonitor"}
    return {d["metadata"]["name"]: d for d in docs}


def test_monitoring_has_one_podmonitor_per_role():
    assert sorted(_podmonitors()) == sorted(ROLES)


@pytest.mark.parametrize("role", ROLES)
def test_podmonitor_selects_exactly_its_roles_pods(role):
    pods, selector = _role_pods(), _podmonitors()[role]["spec"]["selector"]["matchLabels"]
    matches = [r for r, p in pods.items() if all(p["labels"].get(k) == v for k, v in selector.items())]
    assert matches == [role]


@pytest.mark.parametrize("role", ROLES)
def test_podmonitor_scrapes_a_port_its_pods_expose(role):
    pods, endpoints = _role_pods(), _podmonitors()[role]["spec"]["podMetricsEndpoints"]
    assert endpoints and all(e["port"] in pods[role]["ports"] for e in endpoints)
