"""Contract tests for the async-multitenant GKE nightly deploy helper.

The script is run against fake kubectl/helm binaries that only log their argv
(``kubectl kustomize`` is passed to the real kubectl), so the tests check which
commands the guide.yaml emit produces for CI and what the one CI-rendered
manifest, the model server, looks like. yq (mikefarah v4) must be real.
"""

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
GUIDE = ROOT / "guides/batch-serving/asynchronous-processing/multitenant"
SCRIPT = GUIDE / "scripts/nightly-deploy-gke.sh"
TRACKED_SOURCES = [
    GUIDE / "guide.yaml",
    GUIDE / "README.md",
    GUIDE / "modelserver/gpu/vllm/base/kustomization.yaml",
    GUIDE / "modelserver/gpu/vllm/gke/kustomization.yaml",
    GUIDE / "values/redis/quota-only.yaml",
    GUIDE / "values/router/flow-control-holdback.yaml",
    GUIDE / "values/router/flow-control-evictable.yaml",
    GUIDE / "manifests/coordinator/config.yaml",
]
NS = "nightly-test"

FAKE_KUBECTL = """#!/usr/bin/env bash
echo "kubectl $*" >> "${FAKE_CALLS}"
# Rendering is real: the script builds the guide's model server overlay.
if [[ "${1:-}" == "kustomize" ]]; then exec "${REAL_KUBECTL}" "$@"; fi
case "${1:-} ${2:-}" in
  "get priorityclass") [[ "${FAKE_PRIORITYCLASS:-1}" == "1" ]] && exit 0 || exit 1 ;;
esac
if [[ "${1:-}" == "apply" && "$*" == *"https://"* && "${FAKE_CRD_APPLY_FAIL:-0}" == "1" ]]; then
  exit 1
fi
exit 0
"""

FAKE_HELM = """#!/usr/bin/env bash
echo "helm $*" >> "${FAKE_CALLS}"
exit 0
"""


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(tmp_path: Path, output_dir: Path, *, unset: tuple[str, ...] = (),
         **extra_env: str) -> subprocess.CompletedProcess[str]:
    if shutil.which("yq") is None:
        pytest.skip("mikefarah yq v4 is required to exercise the deploy script")
    real_kubectl = shutil.which("kubectl")
    if real_kubectl is None or Path(real_kubectl).parent == tmp_path / "bin":
        pytest.skip("kubectl is required to render the model server overlay")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    _write_executable(fake_bin / "kubectl", FAKE_KUBECTL)
    _write_executable(fake_bin / "helm", FAKE_HELM)
    calls = tmp_path / "calls.log"
    calls.write_text("")
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env.update({
        "NAMESPACE": NS,
        "OUTPUT_DIR": str(output_dir),
        "CRD_RETRY_DELAY": "0",
        "FAKE_CALLS": str(calls),
        "REAL_KUBECTL": real_kubectl,
    })
    for name in ("REPO_ROOT", "AMT_FLOW_CONTROL", "SKIP_CRDS", *unset):
        env.pop(name, None)
    env.update(extra_env)
    return subprocess.run(["bash", str(SCRIPT)], cwd=ROOT, env=env, text=True, capture_output=True, check=False)


def _calls(tmp_path: Path) -> list[str]:
    return (tmp_path / "calls.log").read_text().splitlines()


def _deployment(path: Path) -> dict:
    deployments = [d for d in yaml.safe_load_all(path.read_text()) if d and d["kind"] == "Deployment"]
    assert len(deployments) == 1
    return deployments[0]


def _helm(tmp_path: Path, release: str) -> str:
    matches = [c for c in _calls(tmp_path) if c.startswith(f"helm upgrade --install {release} ")]
    assert len(matches) == 1, _calls(tmp_path)
    return matches[0]


def test_happy_path_deploys_the_guide_from_guide_yaml(tmp_path: Path) -> None:
    out = tmp_path / "out"
    before = {p: _sha256(p) for p in TRACKED_SOURCES}

    result = _run(tmp_path, out, AMT_SERVICE_S="2.0", AMT_BENCH_TOKEN="secret")
    assert result.returncode == 0, result.stderr
    calls = _calls(tmp_path)

    # CRDs from prerequisites.crds, URLs resolved through guides/env.sh.
    crd_applies = [c for c in calls if c.startswith("kubectl apply -f https://")]
    assert len(crd_applies) == 2
    assert "gateway-api-inference-extension" in crd_applies[0] and crd_applies[1].endswith("/manifests.yaml")

    # Model server: the guide's gke overlay plus the CI PriorityClass.
    vllm = _deployment(out / "vllm.yaml")
    assert vllm["metadata"]["name"] == "async-multitenant-optimized-baseline-nvidia-gpu-vllm-decode"
    assert vllm["spec"]["replicas"] == 1
    pod = vllm["spec"]["template"]
    assert pod["metadata"]["labels"]["llm-d.ai/guide"] == "async-multitenant"
    assert pod["spec"]["priorityClassName"] == "nightly-gpu-critical"
    container = pod["spec"]["containers"][0]
    assert container["args"][0] == "Qwen/Qwen3-32B" and "--tensor-parallel-size=2" in container["args"]
    assert any(e["name"] == "NCCL_TUNER_PLUGIN" for e in container["env"])  # gke variant
    assert f"kubectl apply -n {NS} -f {out / 'vllm.yaml'}" in calls

    # Steps 2 to 4, in order, from guide.yaml.
    router = _helm(tmp_path, "llm-d-router")
    assert f"-f {ROOT}/guides/recipes/router/base.values.yaml" in router
    assert f"-f {GUIDE}/values/router/flow-control-evictable.yaml" in router  # the guide's default
    assert "--set router.epp.flags.v=4" in router
    assert router.endswith(f"-n {NS} --version v0")
    async_ = _helm(tmp_path, "llm-d-async")
    assert f"-f {GUIDE}/values/redis/quota-only.yaml" in async_ and async_.endswith(f"-n {NS} --version v0.10.0")
    sequence = [
        f"kubectl apply -n {NS} -k {GUIDE}/manifests/objectives",
        router,
        f"kubectl apply -n {NS} -k {GUIDE}/manifests/redis",
        f"kubectl -n {NS} rollout status deploy/redis --timeout=300s",
        async_,
        f"kubectl apply -n {NS} -k {GUIDE}/manifests/coordinator",
        f"kubectl -n {NS} rollout status deploy/llm-d-coordinator --timeout=300s",
    ]
    positions = [calls.index(c) for c in sequence]
    assert positions == sorted(positions), calls

    # Steps CI never takes (HF secret, single GPU, Pub/Sub) are not emitted.
    deploy = (out / "deploy.sh").read_text() + (out / "crds.sh").read_text()
    for absent in ("create secret", "modelserver/gpu/vllm/single-gpu", "gcp-setup.sh", "values/pubsub"):
        assert absent not in deploy, absent
    assert not any("create secret" in c or "single-gpu" in c for c in calls)

    # Validator settings, without credential-like names.
    settings = (out / "validator.env").read_text()
    assert "AMT_SERVICE_S=2.0" in settings and "TOKEN" not in settings

    assert {p: _sha256(p) for p in TRACKED_SOURCES} == before


def test_holdback_router_values(tmp_path: Path) -> None:
    result = _run(tmp_path, tmp_path / "out", AMT_FLOW_CONTROL="holdback")
    assert result.returncode == 0, result.stderr
    assert f"-f {GUIDE}/values/router/flow-control-holdback.yaml" in _helm(tmp_path, "llm-d-router")
    assert "AMT_FLOW_CONTROL=holdback" in (tmp_path / "out/validator.env").read_text()


def test_unknown_flow_control_fails_before_deploying(tmp_path: Path) -> None:
    result = _run(tmp_path, tmp_path / "out", AMT_FLOW_CONTROL="static")
    assert result.returncode != 0
    assert "FLOW_CONTROL" in result.stderr
    assert not any(c.startswith(("helm", "kubectl apply")) for c in _calls(tmp_path))


def test_no_priority_class_leaves_the_overlay_unchanged(tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = _run(tmp_path, out, FAKE_PRIORITYCLASS="0")
    assert result.returncode == 0, result.stderr
    assert "priorityClassName" not in _deployment(out / "vllm.yaml")["spec"]["template"]["spec"]


def test_crd_install_retries_then_fails(tmp_path: Path) -> None:
    result = _run(tmp_path, tmp_path / "out", FAKE_CRD_APPLY_FAIL="1")
    assert result.returncode != 0
    assert "CRD install failed after 3 attempts" in result.stderr
    assert len([c for c in _calls(tmp_path) if "gateway-api-inference-extension" in c]) == 3
    assert not any(c.startswith("helm") for c in _calls(tmp_path))


def test_skip_crds(tmp_path: Path) -> None:
    result = _run(tmp_path, tmp_path / "out", SKIP_CRDS="true")
    assert result.returncode == 0, result.stderr
    assert not any("https://" in c for c in _calls(tmp_path))


def test_namespace_is_required(tmp_path: Path) -> None:
    result = _run(tmp_path, tmp_path / "out", unset=("NAMESPACE",))
    assert result.returncode != 0
    assert "NAMESPACE must be exported" in result.stderr


def test_rerun_is_idempotent(tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert _run(tmp_path, out).returncode == 0
    first = (out / "deploy.sh").read_text()
    assert _run(tmp_path, out).returncode == 0
    assert (out / "deploy.sh").read_text() == first


def test_ci_applies_the_overlay_guide_yaml_applies() -> None:
    # The script renders the model server itself (to add the PriorityClass);
    # it must stay the overlay guide.yaml's default step applies.
    guide = yaml.safe_load((GUIDE / "guide.yaml").read_text())
    default = next(s for s in guide["deploy"]["modelserver"] if s["when"] == {"GPUS": ["2"]})
    assert default["run"].splitlines()[0] == "kubectl apply -n ${NAMESPACE} -k ${MT}/modelserver/gpu/vllm/${INFRA_PROVIDER}"
    script = SCRIPT.read_text()
    assert 'kubectl kustomize "${GUIDE_DIR}/modelserver/gpu/vllm/${INFRA_PROVIDER}"' in script
    assert "deploy.modelserver" not in script.split("emit env deploy.router")[1]  # not emitted twice
