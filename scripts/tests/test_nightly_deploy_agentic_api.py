"""Contract tests for the agentic-api GKE nightly deploy helper.

The script is run against fake kubectl/helm binaries that only log their argv
(``kubectl kustomize`` is passed to the real kubectl, and manifests piped to
``kubectl apply -f -`` are kept), so the tests check which commands the two
guide.yaml emits produce for CI and what the CI-rendered manifests look like.
yq (mikefarah v4) and envsubst must be real.
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
GUIDE = ROOT / "guides/agentic-api"
SCRIPT = GUIDE / "scripts/nightly-deploy-gke.sh"
VALIDATOR = ROOT / ".github/scripts/e2e/e2e-validate-agentic-api.sh"
NS = "nightly-test"
BASE = "optimized-baseline"

FAKE_KUBECTL = """#!/usr/bin/env bash
echo "kubectl $*" >> "${FAKE_CALLS}"
# Rendering is real: the script builds the base guide's model server overlay.
if [[ "${1:-}" == "kustomize" ]]; then exec "${REAL_KUBECTL}" "$@"; fi
if [[ "${1:-}" == "apply" && "$*" == *"-f -"* ]]; then
  cat >> "${FAKE_STDIN_APPLIED}"
  echo "---" >> "${FAKE_STDIN_APPLIED}"
  exit 0
fi
case "${1:-} ${2:-}" in
  "get priorityclass") [[ "${FAKE_PRIORITYCLASS:-1}" == "1" ]] && exit 0 || exit 1 ;;
  "get secret") exit 1 ;;
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


def _run(tmp_path: Path, **extra_env: str) -> subprocess.CompletedProcess[str]:
    for tool in ("yq", "envsubst", "openssl"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} is required to exercise the deploy script")
    real_kubectl = shutil.which("kubectl")
    if real_kubectl is None or Path(real_kubectl).parent == tmp_path / "bin":
        pytest.skip("kubectl is required to render the model server overlay")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    _write_executable(fake_bin / "kubectl", FAKE_KUBECTL)
    _write_executable(fake_bin / "helm", FAKE_HELM)
    (tmp_path / "calls.log").write_text("")
    (tmp_path / "stdin-applied.yaml").write_text("")
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env.update({
        "NAMESPACE": NS,
        "OUTPUT_DIR": str(tmp_path / "out"),
        "CRD_RETRY_DELAY": "0",
        "FAKE_CALLS": str(tmp_path / "calls.log"),
        "FAKE_STDIN_APPLIED": str(tmp_path / "stdin-applied.yaml"),
        "REAL_KUBECTL": real_kubectl,
    })
    for name in ("REPO_ROOT", "POSTGRES_STORAGE_CLASS", "MODELSERVER_TIMEOUT"):
        env.pop(name, None)
    env.update(extra_env)
    return subprocess.run(["bash", str(SCRIPT)], cwd=ROOT, env=env, text=True, capture_output=True, check=False)


def _calls(tmp_path: Path) -> list[str]:
    return (tmp_path / "calls.log").read_text().splitlines()


def _index(calls: list[str], prefix: str) -> int:
    matches = [i for i, c in enumerate(calls) if c.startswith(prefix)]
    assert len(matches) == 1, (prefix, calls)
    return matches[0]


def _modelserver(tmp_path: Path) -> dict:
    docs = yaml.safe_load_all((tmp_path / "out/modelserver.yaml").read_text())
    deployments = [d for d in docs if d and d["kind"] == "Deployment"]
    assert len(deployments) == 1
    return deployments[0]


def test_happy_path_deploys_both_guides_from_guide_yaml(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    calls = _calls(tmp_path)

    # The base guide's prerequisites.gaie, URL resolved through guides/env.sh.
    crd = _index(calls, "kubectl apply -f https://")
    assert "gateway-api-inference-extension" in calls[crd]

    # The base guide's router: deploy.standalone with its own values, release
    # name optimized-baseline, which is what agentic-api resolves the EPP from.
    router = _index(calls, f"helm install {BASE} ")
    assert f"-n {NS}" in calls[router]
    assert f"guides/{BASE}/router/{BASE}.values.yaml" in calls[router]

    # The model server: the gke overlay with the two CI-only deviations.
    ms = _modelserver(tmp_path)
    assert ms["spec"]["replicas"] == 1
    assert ms["spec"]["template"]["spec"]["priorityClassName"] == "nightly-gpu-critical"
    assert "--enable-auto-tool-choice" in ms["spec"]["template"]["spec"]["containers"][0]["args"]
    ms_apply = _index(calls, f"kubectl apply -n {NS} -f {tmp_path}/out/modelserver.yaml")
    ms_wait = _index(calls, f"kubectl rollout status -n {NS} deployment/{ms['metadata']['name']} ")

    # The guide's own secret step, run although it is skip_in: [ci].
    secret = _index(calls, f"kubectl create secret generic agentic-api-postgres -n {NS} ")
    assert "--from-literal=database-url=postgres://postgres:" in calls[secret]

    # PostgreSQL and agentic-api, only once vLLM is serving. The PVC gets an
    # explicit StorageClass, the guide's manifest is otherwise unchanged.
    postgres = _index(calls, f"kubectl apply -n {NS} -f {tmp_path}/out/postgres.yaml")
    rendered = [d for d in yaml.safe_load_all((tmp_path / "out/postgres.yaml").read_text()) if d]
    guide = [d for d in yaml.safe_load_all((GUIDE / "manifests/postgres.yaml").read_text()) if d]
    pvc = next(d for d in rendered if d["kind"] == "PersistentVolumeClaim")
    assert pvc["spec"].pop("storageClassName") == "standard-rwo"
    assert rendered == guide
    api_wait = _index(calls, f"kubectl rollout status -n {NS} deployment/agentic-api ")
    assert crd < router < ms_apply < ms_wait < secret < postgres < api_wait

    # Standalone Mode: agentic-api dials the base guide's EPP Service by DNS.
    applied = [d for d in yaml.safe_load_all((tmp_path / "stdin-applied.yaml").read_text()) if d]
    api = next(d for d in applied if d["kind"] == "Deployment" and d["metadata"]["name"] == "agentic-api")
    assert api["spec"]["template"]["spec"]["containers"][0]["args"] == [
        "--llm-api-base", f"http://{BASE}-epp.{NS}.svc.cluster.local:80",
    ]
    assert not any(c.startswith("kubectl apply") and "manifests/gateway" in c for c in calls)


def test_postgres_storage_class_can_be_overridden(tmp_path: Path) -> None:
    result = _run(tmp_path, POSTGRES_STORAGE_CLASS="premium-rwo")
    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load_all((tmp_path / "out/postgres.yaml").read_text())
    pvc = next(d for d in rendered if d and d["kind"] == "PersistentVolumeClaim")
    assert pvc["spec"]["storageClassName"] == "premium-rwo"


def test_without_the_nightly_priorityclass_none_is_set(tmp_path: Path) -> None:
    result = _run(tmp_path, FAKE_PRIORITYCLASS="0")
    assert result.returncode == 0, result.stderr
    assert "priorityClassName" not in _modelserver(tmp_path)["spec"]["template"]["spec"]


def test_crd_install_gives_up_after_three_attempts(tmp_path: Path) -> None:
    result = _run(tmp_path, FAKE_CRD_APPLY_FAIL="1")
    assert result.returncode != 0
    assert "CRD install failed after 3 attempts" in result.stderr
    assert len([c for c in _calls(tmp_path) if c.startswith("kubectl apply -f https://")]) == 3
    assert not any(c.startswith("helm ") for c in _calls(tmp_path))


def test_validator_targets_the_base_guide_the_deploy_script_installs() -> None:
    pattern = re.compile(r'^BASE_GUIDE_NAME="([^"]+)"$', re.MULTILINE)
    assert pattern.findall(SCRIPT.read_text()) == [BASE]
    assert pattern.findall(VALIDATOR.read_text()) == [BASE]
