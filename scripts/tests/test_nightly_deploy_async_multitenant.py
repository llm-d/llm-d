"""Contract tests for the async-multitenant GKE nightly deploy helper.

The script is run against fake kubectl/helm binaries that only log their
argv, so the tests check what gets rendered and which commands would run. yq
(mikefarah v4) must be real: the script uses it to resize the coordinator
queues' worker pool and to assert on the rendered llm-d-async values.
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
SCRIPT = ROOT / "guides/batch-serving/asynchronous-processing/multitenant/scripts/nightly-deploy-gke.sh"
GUIDE = ROOT / "guides/batch-serving/asynchronous-processing/multitenant"
TRACKED_SOURCES = [
    GUIDE / "manifests/vllm.yaml",
    GUIDE / "manifests/redis.yaml",
    GUIDE / "manifests/inferenceobjectives.yaml",
    GUIDE / "values/redis/quota-only.yaml",
    GUIDE / "values/router/flow-control.yaml",
    GUIDE / "values/redis/quota-only-coordinator.yaml",
    GUIDE / "manifests/coordinator/config.yaml",
    GUIDE / "manifests/coordinator/coordinator.yaml",
]
NS = "nightly-test"

FAKE_KUBECTL = """#!/usr/bin/env bash
echo "kubectl $*" >> "${FAKE_CALLS}"
case "${1:-} ${2:-}" in
  "get crd")           [[ "${FAKE_CRD_PRESENT:-0}" == "1" ]] && exit 0 || exit 1 ;;
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


def _run(tmp_path: Path, output_dir: Path, **extra_env: str) -> subprocess.CompletedProcess[str]:
    if shutil.which("yq") is None:
        pytest.skip("mikefarah yq v4 is required to exercise the deploy script")
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
    })
    env.pop("REPO_ROOT", None)
    env.pop("VLLM_NODE_SELECTOR", None)  # the nightly cluster sets none; keep the default path covered
    env.update(extra_env)
    return subprocess.run(["bash", str(SCRIPT)], cwd=ROOT, env=env, text=True, capture_output=True, check=False)


def _calls(tmp_path: Path) -> list[str]:
    return (tmp_path / "calls.log").read_text().splitlines()


def _docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def test_happy_path_renders_guide_stack_and_coordinator(tmp_path: Path) -> None:
    out = tmp_path / "rendered"
    before = {p: _sha256(p) for p in TRACKED_SOURCES}

    result = _run(tmp_path, out)
    assert result.returncode == 0, result.stderr

    # vLLM: namespace, HF secret, PriorityClass, harness label; EPP selector untouched.
    vllm = _docs(out / "vllm.yaml")[0]
    assert vllm["metadata"]["namespace"] == NS
    pod = vllm["spec"]["template"]
    assert pod["metadata"]["labels"]["app"] == "vllm-1"
    assert pod["metadata"]["labels"]["llm-d.ai/inferenceServing"] == "true"
    assert pod["spec"]["priorityClassName"] == "nightly-gpu-critical"
    ref = pod["spec"]["containers"][0]["env"][1]["valueFrom"]["secretKeyRef"]
    assert ref == {"name": "llm-d-hf-token", "key": "HF_TOKEN"}
    assert "hf-secret" not in (out / "vllm.yaml").read_text()

    # Six lane objectives bound to the single router pool in the nightly namespace.
    objectives = _docs(out / "inferenceobjectives.yaml")
    assert len(objectives) == 6
    assert {o["spec"]["priority"] for o in objectives} == {100, 60, 30, 10, 0, -10}
    assert {o["metadata"]["namespace"] for o in objectives} == {NS}
    assert {o["spec"]["poolRef"]["name"] for o in objectives} == {"llm-d-router"}

    # Router helm invocation: guide values plus the CI overrides (CRD absent here).
    router_args = (out / "helm-router.args").read_text().splitlines()
    assert router_args[:3] == ["upgrade", "--install", "llm-d-router"]
    assert router_args[3] == "oci://ghcr.io/llm-d/charts/llm-d-router-standalone"
    assert "--set" in router_args and "router.epp.flags.v=4" in router_args
    assert "router.monitoring.prometheus.enabled=false" in router_args
    assert router_args[-2:] == ["--version", "v0"]
    assert str(GUIDE / "values/router/flow-control.yaml") in router_args

    # Redis: keyspace notifications appended, persistence still off.
    redis = _docs(out / "redis.yaml")[0]
    args = redis["spec"]["template"]["spec"]["containers"][0]["args"]
    assert args == ["--save", "", "--appendonly", "no", "--notify-keyspace-events", "Kl"]

    # llm-d-async values: six guide queues untouched plus the two coordinator queues.
    values = yaml.safe_load((out / "llm-d-async.values.yaml").read_text())
    queues = values["ap"]["transportConfig"]["queues"]
    assert len(queues) == 8
    igw = f"http://llm-d-router-epp.{NS}.svc.cluster.local:80"
    assert {q["igw_base_url"] for q in queues} == {igw}
    guide_queues = [q for q in queues if q["queue_name"].startswith("team-")]
    assert len(guide_queues) == 6 and all("result_queue_name" in q for q in guide_queues)
    coord = {q["queue_name"]: q for q in queues if q["queue_name"].startswith("coord-")}
    assert set(coord) == {"coord-standard-a", "coord-batch-a"}
    pools = {p["id"]: p for p in values["ap"]["workerPools"]}
    assert set(pools) == {"model-a", "model-b", "coord"}
    assert pools["model-a"] == {"id": "model-a", "workers": 8}  # guide pools untouched
    assert pools["coord"]["workers"] == 16
    for q in coord.values():
        assert q["result_ttl_seconds"] == 3600
        assert "result_queue_name" not in q
        assert q["worker_pool_id"] == "coord"
        assert q["gate_params"]["prefix"] == "quota:a:"
        assert q["gate_params"]["attribute"] == "team"
        assert q["gate_params"]["address"] == f"redis.{NS}.svc.cluster.local:6379"
    assert coord["coord-batch-a"]["gate_params"]["limit"] == "1"
    assert coord["coord-standard-a"]["gate_params"]["limit"] == "2"
    assert values["ap"]["transportConfig"]["urlSecret"]["url"] == f"redis://redis.{NS}.svc.cluster.local:6379"
    text = (out / "llm-d-async.values.yaml").read_text()
    assert "IGW_HOST" not in text and "NAMESPACE" not in text

    async_args = (out / "helm-async.args").read_text().splitlines()
    assert async_args[:4] == ["upgrade", "--install", "llm-d-async", "oci://ghcr.io/llm-d/charts/llm-d-async"]
    assert async_args[-2:] == ["--version", "v0.10.0"]

    # Coordinator: config rendered into a ConfigMap, image pinned, routes/objectives/quota present.
    config = yaml.safe_load((out / "coordinator/coordinator.yaml").read_text())
    assert config["gateway"]["address"] == igw
    steps = config["pipeline"]["steps"]
    assert [s["type"] for s in steps] == ["async-broker", "decode"]
    broker = steps[0]["params"]
    assert broker["redis_url"] == f"redis://redis.{NS}.svc.cluster.local:6379"
    assert broker["tenant_header"] == "X-Team"
    assert {r["tenant"]: r.get("queue") for r in broker["routes"]} == {
        "realtime": None, "standard": "coord-standard-a", "batch": "coord-batch-a"}
    assert broker["objectives"]["interactive"]["reserved"] == "reserved-interactive"
    assert broker["objectives"]["batch"]["overflow"] == "overflow-batch"
    assert broker["quota"] == {"prefix": "quota:a:", "attribute": "team", "limits": {"standard": 2, "batch": 1}}
    assert broker["wait_cap_seconds"] >= broker["timeouts"]["wait"]["default_seconds"]
    assert config["server"]["write_timeout"] == "600s"
    assert config["server"]["secure_serving"] is False  # probes and clients are plain HTTP
    configmap = yaml.safe_load((out / "coordinator/configmap.yaml").read_text())
    assert configmap["metadata"]["name"] == "llm-d-coordinator-config"
    assert yaml.safe_load(configmap["data"]["coordinator.yaml"]) == config
    manifests = _docs(out / "coordinator/coordinator-manifests.yaml")
    assert [d["kind"] for d in manifests] == ["ServiceAccount", "Deployment", "Service"]
    image = manifests[1]["spec"]["template"]["spec"]["containers"][0]["image"]
    assert image == "ghcr.io/llm-d/llm-d-router-coordinator:main"  # guides/env.sh ROUTER_COORDINATOR_*
    assert manifests[2]["spec"]["ports"][0]["port"] == 8080

    # Command sequence: CRDs, vLLM, objectives, router, Redis (+rollout), AP, coordinator (+rollout).
    calls = _calls(tmp_path)
    crd_applies = [c for c in calls if c.startswith("kubectl apply -f https://")]
    assert len(crd_applies) == 2
    assert any("gateway-api-inference-extension" in c for c in crd_applies)
    assert any("llm-d/llm-d-router" in c and "manifests.yaml" in c for c in crd_applies)
    assert any(c == f"kubectl apply -n {NS} -f {out / 'vllm.yaml'}" for c in calls)
    assert any(c == f"kubectl apply -f {out / 'inferenceobjectives.yaml'}" for c in calls)
    helm_calls = [c for c in calls if c.startswith("helm ")]
    assert len(helm_calls) == 2
    assert helm_calls[0].startswith("helm upgrade --install llm-d-router ")
    assert helm_calls[1].startswith("helm upgrade --install llm-d-async ")
    assert f"kubectl rollout status deploy/redis -n {NS} --timeout=300s" in calls
    assert f"kubectl rollout status deploy/llm-d-coordinator -n {NS} --timeout=300s" in calls
    for name in ("configmap.yaml", "coordinator-manifests.yaml"):
        assert f"kubectl apply -n {NS} -f {out / 'coordinator' / name}" in calls
    # Redis is rolled out before llm-d-async is installed, coordinator last.
    assert calls.index(f"kubectl rollout status deploy/redis -n {NS} --timeout=300s") < calls.index(helm_calls[1])
    assert calls.index(helm_calls[1]) < calls.index(f"kubectl apply -n {NS} -f {out / 'coordinator' / 'coordinator-manifests.yaml'}")

    assert {p: _sha256(p) for p in TRACKED_SOURCES} == before


def test_servicemonitor_crd_present_keeps_guide_monitoring(tmp_path: Path) -> None:
    out = tmp_path / "rendered"
    result = _run(tmp_path, out, FAKE_CRD_PRESENT="1")
    assert result.returncode == 0, result.stderr
    router_args = (out / "helm-router.args").read_text().splitlines()
    assert "router.monitoring.prometheus.enabled=false" not in router_args
    assert "router.epp.flags.v=4" in router_args


def test_without_priorityclass_no_priority_is_set(tmp_path: Path) -> None:
    out = tmp_path / "rendered"
    result = _run(tmp_path, out, FAKE_PRIORITYCLASS="0")
    assert result.returncode == 0, result.stderr
    vllm = _docs(out / "vllm.yaml")[0]
    assert "priorityClassName" not in vllm["spec"]["template"]["spec"]


def test_overrides_for_versions(tmp_path: Path) -> None:
    out = tmp_path / "rendered"
    result = _run(tmp_path, out, ASYNC_VERSION="v0.11.0", COORDINATOR_TAG="v0.11.0-rc.2")
    assert result.returncode == 0, result.stderr
    assert (out / "helm-async.args").read_text().splitlines()[-2:] == ["--version", "v0.11.0"]
    manifests = _docs(out / "coordinator/coordinator-manifests.yaml")
    assert manifests[1]["spec"]["template"]["spec"]["containers"][0]["image"] == \
        "ghcr.io/llm-d/llm-d-router-coordinator:v0.11.0-rc.2"
    env_image = _run(tmp_path, tmp_path / "env-image", ROUTER_COORDINATOR_VERSION="v9.9.9")
    assert env_image.returncode == 0, env_image.stderr
    manifests = _docs(tmp_path / "env-image/coordinator/coordinator-manifests.yaml")
    assert manifests[1]["spec"]["template"]["spec"]["containers"][0]["image"] == \
        "ghcr.io/llm-d/llm-d-router-coordinator:v9.9.9"


def test_coord_workers_resizes_the_coordinator_pool(tmp_path: Path) -> None:
    out = tmp_path / "rendered"
    result = _run(tmp_path, out, COORD_WORKERS="24")
    assert result.returncode == 0, result.stderr
    pools = {p["id"]: p for p in yaml.safe_load((out / "llm-d-async.values.yaml").read_text())["ap"]["workerPools"]}
    assert pools["coord"]["workers"] == 24
    assert pools["model-a"]["workers"] == 8 and pools["model-b"]["workers"] == 8


def test_vllm_node_selector_knob(tmp_path: Path) -> None:
    out = tmp_path / "rendered"
    result = _run(tmp_path, out, VLLM_NODE_SELECTOR="cloud.google.com/compute-class=l4-dws-spot")
    assert result.returncode == 0, result.stderr
    spec = _docs(out / "vllm.yaml")[0]["spec"]["template"]["spec"]
    assert spec["nodeSelector"] == {"cloud.google.com/compute-class": "l4-dws-spot"}
    assert spec["priorityClassName"] == "nightly-gpu-critical"
    bad = _run(tmp_path, tmp_path / "bad", VLLM_NODE_SELECTOR="no-equals-sign")
    assert bad.returncode != 0
    assert "VLLM_NODE_SELECTOR must be key=value" in bad.stderr



def test_validator_settings_are_recorded_without_credentials(tmp_path: Path) -> None:
    out = tmp_path / "rendered"
    result = _run(tmp_path, out, AMT_SERVICE_S="10", AMT_LEVELS="20,100", LLMDBENCH_BRANCH="my-branch",
                  LLMDBENCH_HF_TOKEN="hf_secret", AMT_API_KEY="k", AMT_DB_PASSWORD="p")
    assert result.returncode == 0, result.stderr
    lines = (out / "validator.env").read_text().splitlines()
    assert "AMT_SERVICE_S=10" in lines
    assert "AMT_LEVELS=20,100" in lines
    assert "LLMDBENCH_BRANCH=my-branch" in lines
    text = "\n".join(lines)
    for secret in ("hf_secret", "AMT_API_KEY", "AMT_DB_PASSWORD"):
        assert secret not in text
    # A later run without the setting must not inherit it from the earlier file.
    again = _run(tmp_path, out)
    assert again.returncode == 0, again.stderr
    assert "AMT_SERVICE_S=10" not in (out / "validator.env").read_text()

def test_skip_crds_does_not_apply_crds(tmp_path: Path) -> None:
    out = tmp_path / "rendered"
    result = _run(tmp_path, out, SKIP_CRDS="true", FAKE_CRD_APPLY_FAIL="1")
    assert result.returncode == 0, result.stderr
    assert not [c for c in _calls(tmp_path) if c.startswith("kubectl apply -f https://")]
    assert (out / "crds.sh").exists()


def test_missing_namespace_fails_fast(tmp_path: Path) -> None:
    if shutil.which("yq") is None:
        pytest.skip("yq required")
    env = os.environ.copy()
    env.pop("NAMESPACE", None)
    result = subprocess.run(["bash", str(SCRIPT)], cwd=ROOT, env=env, text=True, capture_output=True, check=False)
    assert result.returncode != 0
    assert "NAMESPACE must be exported" in result.stderr


def test_crd_install_retries_then_fails(tmp_path: Path) -> None:
    out = tmp_path / "rendered"
    result = _run(tmp_path, out, FAKE_CRD_APPLY_FAIL="1")
    assert result.returncode != 0
    assert "CRD install failed after 3 attempts" in result.stderr
    crd_applies = [c for c in _calls(tmp_path) if c.startswith("kubectl apply -f https://")]
    assert len(crd_applies) == 3  # first apply of each attempt fails, so one call per attempt
    assert not (out / "vllm.yaml").exists()


def test_rerun_is_idempotent(tmp_path: Path) -> None:
    out = tmp_path / "rendered"
    first = _run(tmp_path, out)
    assert first.returncode == 0, first.stderr
    snapshot = {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    second = _run(tmp_path, out)
    assert second.returncode == 0, second.stderr
    assert {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()} == snapshot
