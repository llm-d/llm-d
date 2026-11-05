"""Regression coverage for the KEDA EPP README copy-paste commands."""

import os
from pathlib import Path
import subprocess

import pytest
import yaml

GUIDE = Path(__file__).resolve().parents[2] / "guides/workload-autoscaling/keda-epp/guide.yaml"
DATA = yaml.safe_load(GUIDE.read_text())
STEPS = [key for key in DATA["deploy"] if key.startswith("apply_") and key.endswith(("_queue", "_saturation"))]
REQUIRED = ["NAMESPACE", "MODEL", "TARGET_DEPLOYMENT", "EPP_SERVICE", "INFERENCE_POOL", "SCALEDOBJECT_NAME", "HPA_NAME"]


@pytest.mark.parametrize("step", STEPS)
@pytest.mark.parametrize("missing", REQUIRED + [None])
def test_interactive_apply_guard(tmp_path, step, missing):
    # Stub both pipeline commands: this test must never contact a cluster.
    calls = tmp_path / "calls"
    env = {**os.environ, **{key: "test-value" for key in REQUIRED}, "CALLS": str(calls)}
    if missing:
        env.pop(missing, None)
    command = DATA["deploy"][step][0]["run"]
    script = """kubectl() { echo "$*" >> "$CALLS"; }
envsubst() { cat; }
""" + command + "\necho SHELL_SURVIVED\nexit\n"
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-i"], input=script,
        text=True, capture_output=True, env=env, timeout=10,
    )
    assert "SHELL_SURVIVED" in result.stdout
    if missing:
        assert missing in result.stderr
        assert not calls.exists(), result.stderr
    else:
        assert "apply -f -" in calls.read_text()


@pytest.mark.parametrize("platform", ["ocp", "amd"])
def test_nightly_renders_scaledobject(tmp_path, platform):
    """Run the deploy script through real kustomize, without a cluster."""
    import shutil

    kubectl = shutil.which("kubectl")
    if not kubectl:
        pytest.skip("kubectl is required for the kustomize render regression")
    root = GUIDE.parents[3]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    output = tmp_path / "overlay"
    rendered = tmp_path / "rendered.yaml"
    stub = bin_dir / "kubectl"
    stub.write_text('''#!/usr/bin/env bash
case "$1 $2" in
  'kustomize '*) exec "$REAL_KUBECTL" "$@" ;;
  'apply -k') "$REAL_KUBECTL" kustomize "$3" > "$CAPTURE_RENDERED"; exit 79 ;;
  'get svc') echo service/test-epp ;;
  *) cat >/dev/null </dev/null ;;
esac
''')
    stub.chmod(0o755)
    helm = bin_dir / "helm"
    helm.write_text("#!/usr/bin/env bash\nexit 0\n")
    helm.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "REAL_KUBECTL": kubectl, "CAPTURE_RENDERED": str(rendered),
           "OUTPUT_DIR": str(output), "NAMESPACE": "nightly-test",
           "EPP_SERVICE": "test-epp", "MODEL_NAME": "test/model"}
    result = subprocess.run(
        ["bash", str(root / f"guides/workload-autoscaling/scripts/nightly-deploy-{platform}-keda-epp.sh")],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 79, result.stdout + result.stderr
    text = rendered.read_text()
    assert "${" not in text
    obj = next(x for x in yaml.safe_load_all(text) if x and x.get("kind") == "ScaledObject")
    assert obj["metadata"]["name"] == "optimized-baseline-keda-epp"
    spec = obj["spec"]
    assert spec["advanced"]["horizontalPodAutoscalerConfig"]["name"] == "optimized-baseline-keda-epp-hpa"
    assert spec["scaleTargetRef"]["name"] == ("optimized-baseline-nvidia-gpu-vllm-decode" if platform == "ocp" else "optimized-baseline-rocm-vllm-decode")
    assert spec["maxReplicaCount"] == 2
    assert spec["advanced"]["horizontalPodAutoscalerConfig"]["behavior"]["scaleDown"]["stabilizationWindowSeconds"] == 60
    if platform == "amd":
        assert len(spec["triggers"]) == 2
        for trigger, metric in zip(spec["triggers"], ["llm_d_epp_flow_control_queue_size", "llm_d_epp_request_running"]):
            assert trigger["metadata"]["query"] == f'sum({metric}{{namespace="nightly-test",service="test-epp",model_name="test/model"}})'
            assert "${" not in trigger["metadata"]["serverAddress"]
        assert spec["advanced"]["horizontalPodAutoscalerConfig"]["behavior"]["scaleUp"]["stabilizationWindowSeconds"] == 0
        return
    assert len(spec["triggers"]) == 1
    assert spec["triggers"][0]["metadata"]["query"] == (
        'sum(llm_d_epp_inflight_requests{namespace="nightly-test",producer_name="inflight-load-producer"})'
    )
    assert spec["advanced"]["horizontalPodAutoscalerConfig"]["behavior"]["scaleUp"]["stabilizationWindowSeconds"] == 60
