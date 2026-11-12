"""INFRA_PROVIDER wiring for the Fast Model Actuation guide.

A provider needs two overlays: the guide applies platform/<INFRA_PROVIDER>, and
llm-d-benchmark detects the served model from
modelserver/gpu/vllm/<INFRA_PROVIDER>. The nightly lanes pass the provider as
`infra_provider`. These tests tie guide.yaml, the overlays and the lanes
together; what the overlays render to is covered by
test_fma_platform_overlay.py.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import guide  # noqa: E402

GUIDE_DIR = REPO_ROOT / "guides/fast-model-actuation"
WORKFLOWS = REPO_ROOT / ".github/workflows"
DATA = yaml.safe_load((GUIDE_DIR / "guide.yaml").read_text())
PROVIDERS = DATA["env"]["static"]["INFRA_PROVIDER"]["values"]


def _default(expression: str) -> str:
    """'gke' from "${{ inputs.infra_provider || 'gke' }}"."""
    return re.search(r"\|\|\s*'([^']*)'", expression).group(1)


def _lanes() -> dict[str, dict]:
    """The nightly workflows that stand up this guide, by file name."""
    lanes = {}
    for path in sorted(WORKFLOWS.glob("nightly-e2e-fast-model-actuation-*.yaml")):
        workflow = yaml.safe_load(path.read_text())
        scenario = _default(workflow["jobs"]["nightly"]["with"]["standup_scenario"])
        if scenario == DATA["name"]:
            lanes[path.name] = workflow
    return lanes


def _provider(workflow: dict) -> str:
    return _default(workflow["jobs"]["nightly"]["with"]["infra_provider"])


@pytest.mark.parametrize("provider", PROVIDERS)
def test_provider_has_platform_and_detection_overlays(provider):
    for overlay in ("platform", "modelserver/gpu/vllm"):
        assert (GUIDE_DIR / overlay / provider / "kustomization.yaml").is_file()


@pytest.mark.parametrize("provider", PROVIDERS)
def test_ci_applies_and_deletes_the_selected_platform_overlay(tmp_path, provider):
    """Run the script CI runs, with kubectl and helm stubbed, and read the argv."""
    script = guide.emit_script(
        DATA, ["env", "deploy.modelserver", "cleanup"],
        {"INFRA_PROVIDER": provider, "REPO_ROOT": str(REPO_ROOT)}, contexts={"ci"},
    )
    calls = tmp_path / "calls"
    stubs = (
        f'kubectl() {{ echo "kubectl $*" >> "{calls}"; }}\n'
        f'helm() {{ echo "helm $*" >> "{calls}"; }}\n'
    )
    result = subprocess.run(
        ["bash", "-c", stubs + script], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr

    argv = calls.read_text().splitlines()
    namespace = DATA["env"]["static"]["NAMESPACE"]
    overlay = GUIDE_DIR / "platform" / provider
    assert f"kubectl apply -n {namespace} -k {overlay}" in argv
    # Cleanup deletes the platform overlay and drains finalizer-bound pods while
    # the FMA controllers are still running to strip their finalizers.
    delete = argv.index(
        f"kubectl delete -n {namespace} -k {overlay} --ignore-not-found=true"
    )
    wait_requester = argv.index(
        f"kubectl wait --for=delete pod -l app=fma-requester"
        f" -n {namespace} --timeout=120s"
    )
    wait_launcher = argv.index(
        f"kubectl wait --for=delete pod -l app.kubernetes.io/component=launcher"
        f" -n {namespace} --timeout=120s"
    )
    uninstall_fma = argv.index(f"helm uninstall fma -n {namespace}")
    assert delete < wait_requester < wait_launcher < uninstall_fma


def test_ci_cleanup_leaves_cluster_scoped_resources():
    """The nightly cluster is shared, so a CI run must not delete the ClusterRole,
    the namespace or the FMA CRDs; a user on their own cluster still does."""
    user = guide.emit_script(DATA, ["cleanup"])
    ci = guide.emit_script(DATA, ["cleanup"], contexts={"ci"})
    for shared in ("rbac/clusterrole.yaml", "delete namespace", "delete crd"):
        assert shared in user
        assert shared not in ci


def test_nightly_lanes_pass_a_declared_provider():
    lanes = _lanes()
    assert lanes, "no nightly lane stands up this guide"
    for name, workflow in lanes.items():
        assert _provider(workflow) in PROVIDERS, name


def test_lane_names_carry_the_cluster_type():
    """The reusable benchmark workflow picks the cluster from the caller's
    `name:` (it greps for "gke", "ocp", ...), not from `infra_provider`."""
    for name, workflow in _lanes().items():
        assert _provider(workflow) in workflow["name"].lower(), name


def test_consolidate_status_queries_its_lane():
    for name in _lanes():
        path = WORKFLOWS / name.replace("nightly-e2e-", "consolidate-status-", 1)
        status = yaml.safe_load(path.read_text())
        query = status["jobs"]["check_for_success"]["with"]["workflow_name_query"]
        assert _default(query) == name
