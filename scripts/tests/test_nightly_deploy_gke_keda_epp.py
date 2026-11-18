"""Contract tests for the KEDA + EPP GKE nightly deploy script.

The script runs against fake kubectl and helm binaries that log their argv
(``kubectl kustomize`` is passed to the real kubectl), so the tests check which
commands it runs and in what order, what the applied overlay looks like, and how
it fails when a cluster prerequisite or a trigger series is missing. The trigger
queries and names are covered by test_keda_epp.py.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "guides/workload-autoscaling/scripts/nightly-deploy-gke-keda-epp.sh"
WORKFLOWS = ROOT / ".github/workflows"
LANE = "nightly-e2e-workload-autoscaling-keda-epp-gke-acc-gpu-vllm-x.yaml"
NS = "nightly-test"
FRONTEND = "http://frontend.llm-d-monitoring.svc.cluster.local:9090"

FAKE_KUBECTL = """#!/usr/bin/env bash
echo "kubectl $*" >> "${FAKE_CALLS}"
if [[ "${1:-}" == "kustomize" ]]; then exec "${REAL_KUBECTL}" "$@"; fi
# A missing prerequisite: the matching `kubectl get` fails.
if [[ "${1:-}" == "get" && -n "${FAKE_MISSING:-}" && "$*" == *"${FAKE_MISSING}"* ]]
then
  exit 1
fi
# Prometheus lacks a trigger series: the check pod exits non-zero.
if [[ "${1:-} ${2:-}" == "run keda-epp-promcheck" && -n "${FAKE_SERIES_MISSING:-}" ]]
then
  exit 1
fi
# Keep what the script applies, rendered by the real kubectl.
if [[ "${1:-} ${2:-}" == "apply -k" ]]; then
  "${REAL_KUBECTL}" kustomize "$3" > "${FAKE_APPLIED}"
fi
"""

FAKE_HELM = """#!/usr/bin/env bash
echo "helm $*" >> "${FAKE_CALLS}"
"""


def _run(tmp_path: Path, **extra_env: str) -> subprocess.CompletedProcess[str]:
    real_kubectl = shutil.which("kubectl")
    if real_kubectl is None:
        pytest.skip("kubectl is required to render the kustomize overlay")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name, content in (("kubectl", FAKE_KUBECTL), ("helm", FAKE_HELM)):
        (fake_bin / name).write_text(content)
        (fake_bin / name).chmod((fake_bin / name).stat().st_mode | stat.S_IXUSR)
    (tmp_path / "calls.log").write_text("")
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "REAL_KUBECTL": real_kubectl,
        "FAKE_CALLS": str(tmp_path / "calls.log"),
        "FAKE_APPLIED": str(tmp_path / "applied.yaml"),
        "NAMESPACE": NS,
        "OUTPUT_DIR": str(tmp_path / "overlay"),
        **extra_env,
    }
    return subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60,
    )


def _calls(tmp_path: Path) -> list[str]:
    return (tmp_path / "calls.log").read_text().splitlines()


def _index(calls: list[str], prefix: str) -> int:
    return next(i for i, call in enumerate(calls) if call.startswith(prefix))


def _applied(tmp_path: Path, kind: str) -> dict:
    docs = yaml.safe_load_all((tmp_path / "applied.yaml").read_text())
    return next(x for x in docs if x and x["kind"] == kind)


def _release(calls: list[str]) -> str:
    helm_call = calls[_index(calls, "helm ")]
    return re.match(r"helm upgrade --install (\S+) ", helm_call).group(1)


def test_happy_path_runs_the_steps_in_order(tmp_path):
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = _calls(tmp_path)
    epp = f"kubectl rollout status deployment/{_release(calls)}-epp"
    steps = [
        "helm upgrade --install",
        "kubectl apply -k",
        "kubectl rollout status deployment/optimized-baseline-nvidia-gpu-vllm-decode",
        # The warm-up creates the per-model series in the EPP process that serves
        # it, so the EPP must have finished rolling out first.
        epp,
        "kubectl run keda-epp-warmup",
        "kubectl run keda-epp-promcheck",
        "kubectl wait scaledobject/",
    ]
    order = [_index(calls, step) for step in steps]
    assert order == sorted(order), calls


def test_router_is_installed_with_the_gmp_monitoring_values(tmp_path):
    assert _run(tmp_path).returncode == 0
    calls = _calls(tmp_path)
    install = calls[_index(calls, "helm ")]
    values = f"{ROOT}/guides/recipes/router/features/monitoring-gke.values.yaml"
    assert f"-f {values}" in install
    assert f"-n {NS}" in install


@pytest.mark.parametrize("missing, named", [
    ("crd scaledobjects.keda.sh", "KEDA"),
    ("crd podmonitorings.monitoring.googleapis.com", "GMP managed collection"),
    ("service frontend", "GMP frontend"),
])
def test_missing_prerequisite_fails_before_anything_is_installed(
    tmp_path, missing, named,
):
    result = _run(tmp_path, FAKE_MISSING=missing)
    assert result.returncode == 1
    assert named in result.stderr
    assert not [x for x in _calls(tmp_path) if x.startswith(("helm ", "kubectl apply"))]


def test_decode_starts_at_the_scaledobject_floor(tmp_path):
    """The scale-event validator expects desiredReplicas == min before its burst."""
    assert _run(tmp_path).returncode == 0
    decode = _applied(tmp_path, "Deployment")
    scaled = _applied(tmp_path, "ScaledObject")
    assert scaled["spec"]["scaleTargetRef"]["name"] == decode["metadata"]["name"]
    assert decode["spec"]["replicas"] == scaled["spec"]["minReplicaCount"]


@pytest.mark.parametrize("env, address", [
    ({}, FRONTEND),
    ({"MONITORING_NAMESPACE": "other"}, FRONTEND.replace("llm-d-monitoring", "other")),
])
def test_keda_queries_the_gmp_frontend_by_default(tmp_path, env, address):
    assert _run(tmp_path, **env).returncode == 0
    triggers = _applied(tmp_path, "ScaledObject")["spec"]["triggers"]
    assert [x["metadata"]["serverAddress"] for x in triggers] == [address, address]


def test_prometheus_address_override_does_not_need_the_frontend(tmp_path):
    result = _run(
        tmp_path,
        PROMETHEUS_ADDRESS="http://prom.example:9090",
        FAKE_MISSING="service frontend",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    triggers = _applied(tmp_path, "ScaledObject")["spec"]["triggers"]
    assert {x["metadata"]["serverAddress"] for x in triggers} == {
        "http://prom.example:9090"
    }
    assert not [
        x for x in _calls(tmp_path) if x.startswith("kubectl get service frontend")
    ]


def test_series_check_is_confirmed_by_the_script_itself(tmp_path):
    """`kubectl run -i` can miss a short-lived pod's output, so the confirmation
    must not depend on it. The fake kubectl prints nothing."""
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    markers = (
        "Verifying Prometheus has the series",
        "both series present",
        "Waiting for the ScaledObject",
    )
    check, confirmed, wait = (out.index(x) for x in markers)
    assert check < confirmed < wait


def test_missing_series_fails_without_the_confirmation(tmp_path):
    result = _run(tmp_path, FAKE_SERIES_MISSING="1")
    assert result.returncode == 1
    assert "has no series matching" in result.stderr
    assert "both series present" not in result.stdout
    assert not [
        x for x in _calls(tmp_path) if x.startswith("kubectl wait scaledobject/")
    ]


def test_lane_and_warmup_use_the_epp_host_of_the_router_release(tmp_path):
    """The workflow hardcodes the EPP host: it must be the one the script's router
    release produces, which is also where the warm-up request goes."""
    assert _run(tmp_path).returncode == 0
    host = f"{_release(_calls(tmp_path))}-epp"
    inputs = yaml.safe_load((WORKFLOWS / LANE).read_text())["jobs"]["nightly"]["with"]
    assert f"bash {SCRIPT.relative_to(ROOT)}" in inputs["custom_deploy_script"]
    assert inputs["gateway_host"] == host
    assert f"http://{host}:80/v1/completions" in (tmp_path / "calls.log").read_text()


def test_consolidate_status_queries_the_lane():
    path = WORKFLOWS / LANE.replace("nightly-e2e-", "consolidate-status-", 1)
    status = yaml.safe_load(path.read_text())
    query = status["jobs"]["check_for_success"]["with"]["workflow_name_query"]
    assert re.search(r"\|\|\s*'([^']*)'", query).group(1) == LANE
