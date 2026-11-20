"""Contract tests for the pod-snapshot GKE nightly deploy helper.

The script is run against fake kubectl/helm/gcloud binaries that only log their
argv (``kubectl kustomize`` is passed to the real kubectl, and manifests piped
to ``kubectl apply -f -`` are kept), so the tests check which commands the
guide.yaml emits produce for CI and in what order, what the CI-rendered
manifests look like, and how the GCS bucket reset, the CRD retry, the
fail-fast pod watch around the waits and the failure dump behave.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "guides/pod-snapshot/scripts/nightly-deploy-gke.sh"
NS = "nightly-test"
BUCKET = "nightly-test-snapshots"

FAKE_KUBECTL = """#!/usr/bin/env bash
echo "kubectl $*" >> "${FAKE_CALLS}"
# Rendering is real: the script builds the guide's snapshot and model server overlays.
if [[ "${1:-}" == "kustomize" ]]; then exec "${REAL_KUBECTL}" "$@"; fi
if [[ "${1:-}" == "apply" && "$*" == *"-f -"* ]]; then
  cat >> "${FAKE_STDIN_APPLIED}"
  echo "---" >> "${FAKE_STDIN_APPLIED}"
  exit 0
fi
if [[ "${1:-}" == "apply" && "$*" == *"https://"* && "${FAKE_CRD_APPLY_FAIL:-0}" == "1" ]]; then
  exit 1
fi
# The cluster has no gVisor nodes unless a test names some.
if [[ "${1:-} ${2:-}" == "get nodes" && "$*" == *"bootID"* ]]; then
  cat "${FAKE_BOOT_IDS}" 2>/dev/null
  exit 0
fi
# The model server pods, as the fail-fast watch and the final pods.json see them.
if [[ "${1:-} ${2:-}" == "get pods" && "$*" == *"-o json"* ]]; then
  cat "${FAKE_PODS_JSON}" 2>/dev/null || echo '{"items":[]}'
  exit 0
fi
# A node reboots under Pod 1: its bootID changes and the Deployment wait fails.
if [[ "${1:-}" == "wait" && "$*" == *"condition=Available"* && -n "${FAKE_BOOT_IDS_AFTER_POD1_WAIT:-}" ]]; then
  printf '%s\\n' "${FAKE_BOOT_IDS_AFTER_POD1_WAIT}" > "${FAKE_BOOT_IDS}"
  exit 1
fi
# A Pod 1 that takes a while: the watch must run alongside the Deployment wait.
if [[ "${1:-}" == "wait" && "$*" == *"condition=Available"* && -n "${FAKE_POD1_WAIT_SECONDS:-}" ]]; then
  sleep "${FAKE_POD1_WAIT_SECONDS}"
fi
"""

FAKE_HELM = """#!/usr/bin/env bash
echo "helm $*" >> "${FAKE_CALLS}"
"""

FAKE_GCLOUD = """#!/usr/bin/env bash
echo "gcloud $*" >> "${FAKE_CALLS}"
if [[ "${1:-} ${2:-}" == "storage ls" && -n "${FAKE_GCS_LS:-}" ]]; then
  printf '%s\\n' "${FAKE_GCS_LS}"
fi
"""


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _run(tmp_path: Path, **extra_env: str) -> subprocess.CompletedProcess[str]:
    real_kubectl = shutil.which("kubectl")
    if real_kubectl is None or Path(real_kubectl).parent == tmp_path / "bin":
        if os.environ.get("CI"):
            pytest.fail("kubectl must be installed on the CI runner for the deploy contract tests")
        pytest.skip("kubectl is required to render the kustomize overlays")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    _write_executable(fake_bin / "kubectl", FAKE_KUBECTL)
    _write_executable(fake_bin / "helm", FAKE_HELM)
    _write_executable(fake_bin / "gcloud", FAKE_GCLOUD)
    (tmp_path / "calls.log").write_text("")
    (tmp_path / "stdin-applied.yaml").write_text("")
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env.update({
        "NAMESPACE": NS,
        "MODEL_SERVER": "vllm",
        "GCS_BUCKET": BUCKET,
        "OUTPUT_DIR": str(tmp_path / "out"),
        "CRD_RETRY_DELAY": "0",
        "FAKE_CALLS": str(tmp_path / "calls.log"),
        "FAKE_STDIN_APPLIED": str(tmp_path / "stdin-applied.yaml"),
        "FAKE_BOOT_IDS": str(tmp_path / "boot-ids"),
        "FAKE_PODS_JSON": str(tmp_path / "pods.json"),
        "POD_POLL_INTERVAL": "0",
        "REAL_KUBECTL": real_kubectl,
    })
    env.pop("REPO_ROOT", None)
    env.update(extra_env)
    return subprocess.run(["bash", str(SCRIPT)], cwd=ROOT, env=env, text=True, capture_output=True, check=False)


def _pods(tmp_path: Path, *statuses: dict) -> None:
    """Writes the pod list the fake kubectl returns, one model server pod per status."""
    items = [
        {"metadata": {"name": f"pod-snapshot-nvidia-gpu-vllm-decode-{i}"}, "status": status}
        for i, status in enumerate(statuses)
    ]
    (tmp_path / "pods.json").write_text(json.dumps({"items": items}))


def _container(name: str = "modelserver", **status) -> dict:
    return {"name": name, "restartCount": 0, "state": {"running": {}}, **status}


def _calls(tmp_path: Path) -> list[str]:
    return (tmp_path / "calls.log").read_text().splitlines()


def _index(calls: list[str], prefix: str) -> int:
    matches = [i for i, c in enumerate(calls) if c.startswith(prefix)]
    assert len(matches) == 1, (prefix, calls)
    return matches[0]


def _applied(tmp_path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all((tmp_path / "stdin-applied.yaml").read_text()) if d]


def _one(docs: list[dict], kind: str) -> dict:
    found = [d for d in docs if d["kind"] == kind]
    assert len(found) == 1, (kind, [d["kind"] for d in docs])
    return found[0]


@pytest.mark.parametrize("model_server", ["vllm", "sglang"])
def test_happy_path_deploys_the_guide_from_guide_yaml(tmp_path: Path, model_server: str) -> None:
    result = _run(tmp_path, MODEL_SERVER=model_server)
    assert result.returncode == 0, result.stderr
    calls = _calls(tmp_path)
    selector = "llm-d.ai/guide=pod-snapshot"

    # The bucket reset comes first: rm, then the emptiness check.
    assert calls[:2] == [
        f"gcloud storage rm -r gs://{BUCKET}/**",
        f"gcloud storage ls gs://{BUCKET}",
    ]

    # deploy.snapshot and deploy.modelserver are rendered with kustomize and piped
    # to kubectl apply -n NS -f -; the manifests are what CI will really apply.
    applied = _applied(tmp_path)
    storage = _one(applied, "PodSnapshotStorageConfig")
    policy = _one(applied, "PodSnapshotPolicy")
    deployment = _one(applied, "Deployment")
    assert storage["metadata"]["name"] == f"{NS}-pod-snapshot-storage-config"
    assert storage["spec"]["snapshotStorageConfig"]["gcs"]["bucket"] == BUCKET
    assert "gcs-bucket-placeholder" not in yaml.safe_dump(applied)
    # Kustomize's namePrefix does not reach this field; the guide's sed must rewrite it too.
    assert policy["spec"]["storageConfigName"] == f"{NS}-pod-snapshot-storage-config"
    assert deployment["metadata"]["name"] == f"pod-snapshot-nvidia-gpu-{model_server}-decode"
    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["template"]["metadata"]["labels"]["llm-d.ai/guide"] == "pod-snapshot"
    assert deployment["spec"]["template"]["spec"]["runtimeClassName"] == "gvisor"
    dep = f"deployment/{deployment['metadata']['name']}"

    # guide.yaml order: CRDs -> router -> snapshot config -> model server -> Pod 1 +
    # PodSnapshot waits -> scale + Pod 2 waits. Each step appears exactly once.
    crd = _index(calls, "kubectl apply -f https://")
    assert "gateway-api-inference-extension" in calls[crd]
    router = _index(calls, "helm install pod-snapshot ")
    assert f"-n {NS}" in calls[router] and "guides/pod-snapshot/router/pod-snapshot.values.yaml" in calls[router]
    applies = [i for i, c in enumerate(calls) if c == f"kubectl apply -n {NS} -f -"]
    assert len(applies) == 2, calls
    snapshot_apply, ms_apply = applies
    # The Deployment is waited on before the emitted `kubectl wait pod -l` (which fails
    # if the ReplicaSet has not created Pod 1 yet); Available, not rollout status, so a
    # >600s cold start is not cut short by the Deployment's progress deadline.
    pod1_guard = _index(calls, f"kubectl wait --for=condition=Available {dep} -n {NS} --timeout=2400s")
    pod1_wait = _index(calls, f"kubectl wait --for=condition=ready pod -l {selector} -n {NS} --timeout=2400s")
    snap_wait = _index(calls, f"kubectl wait --for=condition=Ready podsnapshots --all -n {NS}")
    # verify.tests.restore scales to 2 replicas and waits with `rollout status`
    # (2400s timeout so a fallback cold start still reaches the validator) before
    # reading events and logs.
    guide_scale = _index(calls, f"kubectl scale deployment -l {selector} -n {NS} --replicas=2")
    guide_rollout = _index(calls, f"kubectl rollout status deployment -l {selector} -n {NS} --timeout=2400s")
    guide_logs = _index(calls, f"kubectl logs -l {selector} -n {NS} --tail=20")
    # The fail-fast watch polls the same `get pods -o json` during the waits; the
    # pods.json snapshot is the last one, after every wait.
    pods_snap = max(i for i, c in enumerate(calls) if c == f"kubectl get pods -l {selector} -n {NS} -o json")
    assert (crd < router < snapshot_apply < ms_apply < pod1_guard < pod1_wait < snap_wait
            < guide_scale < guide_rollout < guide_logs < pods_snap)
    # A clean run dumps nothing.
    assert not any(c.startswith("kubectl describe") for c in calls)

    # Every step is emitted with the ci context (prerequisites.clone / .secrets are
    # skip_in: [ci]); the sections this script emits never contain them, so the
    # emit provenance header is what pins it.
    emitted = sorted((tmp_path / "out").glob("*.sh"))
    assert [p.name for p in emitted] == ["crds.sh", "modelserver.sh", "router.sh", "snapshot.sh", "wait-restore.sh", "wait-snapshot.sh"]
    assert all("| context: ci |" in p.read_text() for p in emitted)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        pytest.param(
            {"phase": "Running", "containerStatuses": [_container(
                restartCount=12,
                state={"waiting": {"reason": "CrashLoopBackOff", "message": "back-off 5m0s restarting failed container"}},
                lastState={"terminated": {"exitCode": 128, "reason": "StartError",
                                          "message": "failed to load /opt/sglang/bin/python3: exec format error"}},
            )]},
            "exec format error",
            id="crash-loop",
        ),
        pytest.param(
            {"phase": "Pending", "containerStatuses": [_container(
                state={"waiting": {"reason": "ErrImagePull", "message": "manifest unknown"}},
            )]},
            "ErrImagePull",
            id="image-pull",
        ),
        pytest.param(
            {"phase": "Failed", "reason": "UnexpectedAdmissionError",
             "message": "Allocate failed due to no healthy devices present"},
            "no healthy devices",
            id="pod-failed",
        ),
        pytest.param(
            {"phase": "Running", "containerStatuses": [_container(
                restartCount=1, lastState={"terminated": {"exitCode": 1, "reason": "Error"}},
            )]},
            "restarted 1x",
            id="restarted",
        ),
    ],
)
def test_a_pod_that_cannot_recover_stops_the_wait(tmp_path: Path, status: dict, expected: str) -> None:
    _pods(tmp_path, status)
    started = time.monotonic()
    # Without the watch this wait would take the whole 60s.
    result = _run(tmp_path, FAKE_POD1_WAIT_SECONDS="60")
    elapsed = time.monotonic() - started
    assert result.returncode != 0
    assert elapsed < 30, elapsed
    assert "will not recover" in result.stderr and expected in result.stderr, result.stderr
    calls = _calls(tmp_path)
    # Stopped at the Pod 1 wait: Pod 2 is never attempted.
    assert not any(c.startswith("kubectl scale") for c in calls)
    # The failure dump puts the reason in the step log.
    assert _index(calls, f"kubectl describe pods -l llm-d.ai/guide=pod-snapshot -n {NS}") > _index(
        calls, "kubectl wait --for=condition=Available ")
    assert any(c.startswith(f"kubectl get podsnapshots -n {NS}") for c in calls)


def test_the_waits_tolerate_a_pod_that_is_still_starting(tmp_path: Path) -> None:
    _pods(
        tmp_path,
        {"phase": "Pending", "initContainerStatuses": [_container("init", state={"waiting": {"reason": "PodInitializing"}})],
         "containerStatuses": [_container(state={"waiting": {"reason": "ContainerCreating"}})]},
        {"phase": "Running", "containerStatuses": [_container(ready=True)]},
    )
    result = _run(tmp_path, FAKE_POD1_WAIT_SECONDS="1")
    assert result.returncode == 0, result.stderr


def test_a_failed_run_names_the_nodes_that_rebooted_and_dumps_the_namespace(tmp_path: Path) -> None:
    (tmp_path / "boot-ids").write_text("gpu-a=boot-1\ngpu-b=boot-1\n")
    result = _run(tmp_path, FAKE_BOOT_IDS_AFTER_POD1_WAIT="gpu-a=boot-2\ngpu-b=boot-1")
    assert result.returncode != 0
    rebooted = [line for line in result.stderr.splitlines() if "rebooted" in line]
    assert len(rebooted) == 1, result.stderr
    assert "node gpu-a " in rebooted[0]
    calls = _calls(tmp_path)
    failed_wait = _index(calls, "kubectl wait --for=condition=Available ")
    assert failed_wait < _index(calls, f"kubectl describe pods -l llm-d.ai/guide=pod-snapshot -n {NS}")
    assert failed_wait < _index(calls, f"kubectl get podsnapshots -n {NS} -o wide")
    assert failed_wait < _index(calls, f"kubectl get events -n {NS} --sort-by=.lastTimestamp")


def test_crd_install_gives_up_after_three_attempts(tmp_path: Path) -> None:
    result = _run(tmp_path, FAKE_CRD_APPLY_FAIL="1")
    assert result.returncode != 0
    assert "CRD install failed after 3 attempts" in result.stderr
    assert len([c for c in _calls(tmp_path) if c.startswith("kubectl apply -f https://")]) == 3
    assert not any(c.startswith("helm ") for c in _calls(tmp_path))


def test_bucket_reset_fails_when_the_bucket_stays_non_empty(tmp_path: Path) -> None:
    result = _run(tmp_path, FAKE_GCS_LS=f"gs://{BUCKET}/stuck")
    assert result.returncode != 0
    assert "is still not empty" in result.stderr
    assert _calls(tmp_path) == [
        f"gcloud storage rm -r gs://{BUCKET}/**",
        f"gcloud storage ls gs://{BUCKET}",
    ]


@pytest.mark.parametrize("bad", ["", "triton", "VLLM"])
def test_rejects_unknown_model_servers_before_touching_anything(tmp_path: Path, bad: str) -> None:
    result = _run(tmp_path, MODEL_SERVER=bad)
    assert result.returncode != 0
    assert "MODEL_SERVER" in result.stderr
    assert _calls(tmp_path) == []


def test_rejects_a_malformed_bucket_name_before_touching_anything(tmp_path: Path) -> None:
    result = _run(tmp_path, GCS_BUCKET="gs://Not_A_Bucket")
    assert result.returncode != 0
    assert "unexpected GCS_BUCKET" in result.stderr
    assert _calls(tmp_path) == []
