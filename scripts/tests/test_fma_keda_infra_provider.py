"""INFRA_PROVIDER wiring for the Fast Model Actuation + KEDA guide.

Here the provider selects four things: platform/<INFRA_PROVIDER> (how the
launcher reaches the GPUs), router/monitoring-<INFRA_PROVIDER>.values.yaml (who
scrapes the EPP), keda/<INFRA_PROVIDER> (which Prometheus KEDA queries) and
modelserver/gpu/vllm/<INFRA_PROVIDER> (llm-d-benchmark's model detection). The
nightly lanes pass the provider as `infra_provider`. These tests tie guide.yaml,
those files and the lanes together; what the overlays render to is covered by
test_fma_keda_overlay.py and test_fma_platform_overlay.py.
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

GUIDE_DIR = REPO_ROOT / "guides/fast-model-actuation/fast-model-actuation-keda"
RECIPES = REPO_ROOT / "guides/recipes/router/features"
WORKFLOWS = REPO_ROOT / ".github/workflows"
DATA = yaml.safe_load((GUIDE_DIR / "guide.yaml").read_text())
PROVIDERS = DATA["env"]["static"]["INFRA_PROVIDER"]["values"]
# The router monitoring values under this guide are copies of these recipes,
# renamed so the install command can select one by provider.
MONITORING_RECIPE = {
    "ocp": "monitoring.values.yaml",
    "gke": "monitoring-gke.values.yaml",
}


def _default(expression: str) -> str:
    """'gke' from "${{ inputs.infra_provider || 'gke' }}"."""
    return re.search(r"\|\|\s*'([^']*)'", expression).group(1)


def _lanes() -> dict[str, dict]:
    """The nightly workflows that stand up this guide, by file name."""
    scenario = f"fast-model-actuation/{DATA['name']}"
    lanes = {}
    for path in sorted(WORKFLOWS.glob("nightly-e2e-fast-model-actuation-*.yaml")):
        workflow = yaml.safe_load(path.read_text())
        target = _default(workflow["jobs"]["nightly"]["with"]["standup_scenario"])
        if target == scenario:
            lanes[path.name] = workflow
    return lanes


def _provider(workflow: dict) -> str:
    return _default(workflow["jobs"]["nightly"]["with"]["infra_provider"])


@pytest.mark.parametrize("provider", PROVIDERS)
def test_provider_has_every_overlay_and_values_file(provider):
    for path in (
        f"platform/{provider}/kustomization.yaml",
        f"keda/{provider}/kustomization.yaml",
        f"modelserver/gpu/vllm/{provider}/kustomization.yaml",
        f"router/monitoring-{provider}.values.yaml",
    ):
        assert (GUIDE_DIR / path).is_file(), path


@pytest.mark.parametrize("provider", PROVIDERS)
def test_monitoring_values_match_the_shared_recipe(provider):
    values = GUIDE_DIR / f"router/monitoring-{provider}.values.yaml"
    copy = yaml.safe_load(values.read_text())
    recipe = yaml.safe_load((RECIPES / MONITORING_RECIPE[provider]).read_text())
    assert copy == recipe


@pytest.mark.parametrize("provider", PROVIDERS)
def test_ci_script_selects_the_provider_everywhere(tmp_path, provider):
    """Run the script CI runs, with kubectl and helm stubbed, and read the argv."""
    sections = [
        "env", "deploy.standalone", "deploy.modelserver", "deploy.keda", "cleanup",
    ]
    script = guide.emit_script(
        DATA, sections,
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
    platform = GUIDE_DIR / "platform" / provider
    keda = GUIDE_DIR / "keda" / provider
    install = next(x for x in argv if x.startswith("helm install "))
    assert f"-f {GUIDE_DIR}/router/monitoring-{provider}.values.yaml" in install
    assert f"kubectl apply -n {namespace} -k {platform}" in argv
    assert f"kubectl apply -n {namespace} -k {keda}" in argv
    # Cleanup removes the ScaledObject first (so the HPA stops scaling the
    # requester), then the platform overlay, then drains pods before
    # uninstalling the FMA controllers.
    delete_keda = argv.index(
        f"kubectl delete -n {namespace} -k {keda} --ignore-not-found=true"
    )
    delete_platform = argv.index(
        f"kubectl delete -n {namespace} -k {platform} --ignore-not-found=true"
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
    assert (
        delete_keda < delete_platform < wait_requester < wait_launcher < uninstall_fma
    )


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
