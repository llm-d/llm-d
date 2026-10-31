"""The CPU simulator path can run the baseline's control plane without a model.

Render its real Kustomize overlay and run emitted deployment/cleanup snippets
against command stubs, so image/selector/port drift and accidental accelerator,
secret, or monitoring requirements fail before a cluster run.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
GUIDE_DIR = ROOT / "guides/optimized-baseline"
sys.path.insert(0, str(ROOT / "scripts"))
import guide  # noqa: E402

BASH = shutil.which("bash")
if os.name == "nt":
    git_bash = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
    BASH = str(git_bash) if git_bash.is_file() else None

SIM_VARS = {
    "REPO_ROOT": ROOT.as_posix(),
    "ACCELERATOR_TYPE": "cpu",
    "MODEL_SERVER": "sim",
    "INFRA_PROVIDER": "base",
    "MODEL": "meta-llama/Llama-3.2-3B-Instruct",
    "NAMESPACE": "test-baseline-sim",
}


@pytest.fixture(scope="module")
def baseline():
    return guide.Guide.load(GUIDE_DIR)


@pytest.fixture(scope="module")
def simulator():
    kubectl = shutil.which("kubectl")
    if not kubectl:
        pytest.skip("kubectl kustomize is required to render the simulator overlay")
    result = subprocess.run(
        [kubectl, "kustomize", str(GUIDE_DIR / "modelserver/cpu/sim/base")],
        check=True, capture_output=True, text=True,
    )
    manifests = list(yaml.safe_load_all(result.stdout))
    return next(item for item in manifests if item["kind"] == "Deployment")


def test_simulator_is_discoverable_and_requires_no_accelerator_or_secret(baseline, simulator):
    assert baseline.check_yaml().ok()
    template = simulator["spec"]["template"]
    pod = template["spec"]
    container = next(c for c in pod["containers"] if c["name"] == "modelserver")
    assert "llm-d-inference-sim:" in container["image"]
    assert not container["image"].endswith(":latest")
    assert not pod.get("initContainers")
    assert not pod.get("volumes")
    assert not pod.get("resourceClaims")
    assert all("secretKeyRef" not in env.get("valueFrom", {}) for env in container.get("env", []))
    for resources in container["resources"].values():
        assert set(resources) <= {"cpu", "memory"}


def test_simulator_alias_discovery_and_monitoring_ports_match(baseline, simulator):
    template = simulator["spec"]["template"]
    container = next(c for c in template["spec"]["containers"] if c["name"] == "modelserver")
    args = dict(arg[2:].split("=", 1) for arg in container["args"] if "=" in arg)
    assert args["served-model-name"] == baseline.data["support"]["accelerators"]["cpu"]["model"]
    assert "/" not in args["model"], "use a dummy model, not a HuggingFace model with a render dependency"
    assert "render-url" not in args
    router = yaml.safe_load((GUIDE_DIR / "router/optimized-baseline.values.yaml").read_text())
    for key, value in router["router"]["modelServers"]["matchLabels"].items():
        assert template["metadata"]["labels"][key] == value
    monitor = yaml.safe_load((ROOT / "guides/recipes/modelserver/components/monitoring/decode-podmonitor.yaml").read_text())
    for key, value in monitor["spec"]["selector"]["matchLabels"].items():
        assert template["metadata"]["labels"][key] == value
    port = next(p for p in container["ports"] if p["name"] == monitor["spec"]["podMetricsEndpoints"][0]["port"])
    assert int(args["port"]) == port["containerPort"]


def test_simulator_prerequisites_omit_model_download_secret(baseline):
    simulated = baseline.emit(["prerequisites.secrets"], variables=SIM_VARS)
    real = baseline.emit(["prerequisites.secrets"])
    assert "create secret" not in simulated
    assert "HF_TOKEN" not in simulated
    assert "create secret generic llm-d-hf-token" in real


def test_simulator_commands_do_not_change_default_readme_nightlies(baseline):
    # README-based nightlies harvest bash fences outside the skip markers.
    default_markdown = re.sub(
        r"<!-- llm-d-cicd:skip start -->.*?<!-- llm-d-cicd:skip end -->",
        "", baseline.render(), flags=re.DOTALL,
    )
    commands = "\n".join(re.findall(r"```bash\n(.*?)```", default_markdown, flags=re.DOTALL))
    assert "export MODEL_SERVER=sim\n" not in commands
    assert "router/sim.values.yaml" not in commands


@pytest.mark.skipif(not BASH, reason="bash is required for emitted command execution")
@pytest.mark.parametrize("monitoring", ["false", "true"])
def test_emitted_simulator_pipeline_does_not_need_monitoring_when_disabled(baseline, tmp_path, monitoring):
    command_dir = tmp_path / "bin"
    command_dir.mkdir()
    log = tmp_path / "commands.log"
    kubectl = command_dir / "kubectl"
    kubectl.write_text(
        '#!/bin/sh\n'
        'printf "kubectl %s\\n" "$*" >> "$COMMAND_LOG"\n'
        'case "$*" in\n'
        '  "get pods "*) printf "sim-pod\\n" ;;\n'
        '  "get --raw "*) printf "vllm:prefix_cache_hits{model_name=\\"sim\\"} 32\\n'
        'vllm:prefix_cache_queries{model_name=\\"sim\\"} 64\\n'
        'vllm:request_success_total{model_name=\\"sim\\"} 2\\n" ;;\n'
        'esac\n'
    )
    kubectl.chmod(0o755)
    helm = command_dir / "helm"
    helm.write_text('#!/bin/sh\nprintf "helm %s\\n" "$*" >> "$COMMAND_LOG"\n')
    helm.chmod(0o755)
    script = baseline.emit(
        ["env", "prerequisites.gaie", "prerequisites.namespace", "prerequisites.secrets",
         "deploy.router_values", "deploy.accelerator_values", "deploy.monitoring_values",
         "deploy.standalone", "deploy.modelserver.standard", "deploy.monitoring",
         "verify.tests.pod_metrics", "cleanup.modelserver.standard", "cleanup.rest"],
        variables={**SIM_VARS, "MONITORING": monitoring}, contexts=["ci"],
    )
    result = subprocess.run(
        [BASH, "--noprofile", "--norc", "-s"], input=script,
        env={**os.environ, "PATH": str(command_dir) + os.pathsep + os.environ["PATH"],
             "COMMAND_LOG": log.as_posix()},
        capture_output=True, text=True, check=True,
    )
    calls = log.read_text()
    assert "router/sim.values.yaml" in calls
    assert "/modelserver/cpu/sim/base" in calls
    assert "/modelserver/gpu/" not in calls
    assert "create secret" not in calls
    monitoring_calls = [line for line in calls.splitlines() if "/components/monitoring" in line]
    assert len(monitoring_calls) == (2 if monitoring == "true" else 0)
    assert "vllm:prefix_cache_hits{" in result.stdout
    assert "vllm:prefix_cache_queries{" in result.stdout
    assert "vllm:request_success_total{" in result.stdout
