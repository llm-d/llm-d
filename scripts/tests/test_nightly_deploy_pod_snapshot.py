"""Contract tests for the pod-snapshot GKE nightly deploy helper.

The script is run against fake kubectl/helm/gcloud binaries that only log their
argv (``kubectl kustomize`` is passed to the real kubectl, and manifests piped
to ``kubectl apply -f -`` are kept), so the tests check which commands the
guide.yaml emits produce for CI and in what order, what the CI-rendered
manifests look like, and how the GCS bucket reset and the CRD retry behave.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
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
        "REAL_KUBECTL": real_kubectl,
    })
    env.pop("REPO_ROOT", None)
    env.update(extra_env)
    return subprocess.run(["bash", str(SCRIPT)], cwd=ROOT, env=env, text=True, capture_output=True, check=False)


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
    pods_snap = _index(calls, f"kubectl get pods -l {selector} -n {NS} -o json")
    assert (crd < router < snapshot_apply < ms_apply < pod1_guard < pod1_wait < snap_wait
            < guide_scale < guide_rollout < guide_logs < pods_snap)

    # Every step is emitted with the ci context (prerequisites.clone / .secrets are
    # skip_in: [ci]); the sections this script emits never contain them, so the
    # emit provenance header is what pins it.
    emitted = sorted((tmp_path / "out").glob("*.sh"))
    assert [p.name for p in emitted] == ["crds.sh", "modelserver.sh", "router.sh", "snapshot.sh", "wait-restore.sh", "wait-snapshot.sh"]
    assert all("| context: ci |" in p.read_text() for p in emitted)


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
