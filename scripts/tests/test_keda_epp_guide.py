"""KEDA guide variable contract; no cluster commands are executed.

Shell checks require Bash. Render checks additionally require kubectl and GNU
envsubst, and exercise real Kustomize/substitution with apply/delete intercepted.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
GUIDE_DIR = REPO_ROOT / "guides/workload-autoscaling/keda-epp"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import guide  # noqa: E402

REQUIRED = ("NAMESPACE", "MODEL", "TARGET_DEPLOYMENT", "EPP_SERVICE", "INFERENCE_POOL")
OVERLAYS = ("k8s/queue", "k8s/saturation", "ocp/queue", "ocp/saturation")
STEPS = ("prepare",) + tuple(f"apply:{o}" for o in OVERLAYS) + tuple(
    f"cleanup:{o}" for o in OVERLAYS
)
SCALEDOBJECT = "optimized-baseline-keda-epp"
HPA = "keda-hpa-optimized-baseline"
STUBS = r'''
kubectl() {
  printf 'KUBECTL:%s\n' "$*" >&2
  case "$1" in
    kustomize) printf 'mock manifest\n' ;;
    apply|delete) cat ;;
  esac
}
envsubst() { printf 'ENVSUBST\n' >&2; cat; }
'''


@pytest.fixture(scope="module")
def keda():
    return guide.Guide.load(GUIDE_DIR)


@pytest.fixture(scope="module")
def bash():
    executable = shutil.which("bash")
    if not executable:
        pytest.skip("Bash is required for shell execution checks")
    return executable


def variables(overlay):
    platform, signal = overlay.split("/")
    return {"ENV": "existing" if platform == "k8s" else "ocp", "SIGNAL": signal}


def step_script(keda, step, emitted):
    if step == "prepare":
        section = "deploy.prepare"
        command = keda.data["deploy"]["prepare"][0]["run"]
        selection = {}
    else:
        action, overlay = step.split(":")
        selection = variables(overlay)
        if action == "apply":
            group = "apply_" + overlay.replace("/", "_")
            section = "deploy." + group
            command = keda.data["deploy"][group][0]["run"]
        else:
            section = "cleanup"
            command = next(
                s["run"] for s in keda.data["cleanup"]
                if all(value in s["when"][key] for key, value in selection.items())
            )
    return keda.emit([section], variables=selection) if emitted else command


def environment(keda, custom=False):
    env = os.environ.copy()
    for key in REQUIRED:
        value = keda.data["env"]["static"][key]
        env[key] = str(value["default"] if isinstance(value, dict) else value)
    if custom:
        env.update(zip(REQUIRED, (
            "custom-ns", "example/custom-model", "custom-decode", "custom-epp", "custom-pool",
        )))
    env["OVERLAY_ROOT"] = "optimized-baseline"
    return env


def shell(bash, script, env, interactive=False):
    args = [bash, "--noprofile", "--norc", "--noediting"]
    if interactive:
        args.append("-i")
        env = dict(env, PS1="", PS2="")
    return subprocess.run(
        [*args, "-s"],
        input=script,
        cwd=GUIDE_DIR, env=env, capture_output=True, text=True, timeout=60,
    )


@pytest.mark.parametrize("step", STEPS)
@pytest.mark.parametrize("emitted", [False, True], ids=["snippet", "emitted"])
@pytest.mark.parametrize("key", REQUIRED)
@pytest.mark.parametrize("state", ["unset", "empty"])
@pytest.mark.parametrize("interactive", [False, True], ids=["script", "interactive"])
def test_missing_variable_stops_before_any_tool(
    keda, bash, step, emitted, key, state, interactive
):
    env = environment(keda)
    if state == "unset":
        env.pop(key, None)
    else:
        env[key] = ""
    result = shell(bash, STUBS + step_script(keda, step, emitted), env, interactive)
    assert result.returncode != 0
    assert key in result.stderr
    assert "KUBECTL:" not in result.stderr
    assert "ENVSUBST" not in result.stderr


@pytest.mark.parametrize("step", STEPS)
@pytest.mark.parametrize("emitted", [False, True], ids=["snippet", "emitted"])
@pytest.mark.parametrize("custom", [False, True], ids=["defaults", "custom"])
@pytest.mark.parametrize("interactive", [False, True], ids=["script", "interactive"])
def test_valid_variables_reach_only_expected_commands(
    keda, bash, step, emitted, custom, interactive
):
    env = environment(keda, custom)
    result = shell(bash, STUBS + step_script(keda, step, emitted), env, interactive)
    assert result.returncode == 0, result.stderr
    calls = re.findall(r"^KUBECTL:(.*)$", result.stderr, re.MULTILINE)
    if step == "prepare":
        assert calls == [
            f"scale deployment {env['TARGET_DEPLOYMENT']} -n {env['NAMESPACE']} --replicas=1",
            f"rollout status deployment/{env['TARGET_DEPLOYMENT']} -n {env['NAMESPACE']} --timeout=15m",
        ]
    else:
        action, overlay = step.split(":")
        expected = "apply -f -" if action == "apply" else "delete --ignore-not-found=true -f -"
        # Pipeline processes can start in either order.
        assert sorted(calls) == sorted([f"kustomize optimized-baseline/overlays/{overlay}", expected])
        assert result.stdout == "mock manifest\n"


def test_names_are_fixed_not_exported_or_overridable(keda):
    for name in ("SCALEDOBJECT_NAME", "HPA_NAME"):
        assert name not in keda.data["env"]["static"]
        assert name not in (GUIDE_DIR / "guide.yaml").read_text()
        assert name not in (GUIDE_DIR / "README.md").read_text()
        with pytest.raises(guide.GuideError, match=name):
            keda.emit(["env"], variables={name: "misleading-name"})
    verify = keda.emit(["verify.tests"])
    assert f"get scaledobject {SCALEDOBJECT} " in verify
    assert f"scaledobject/{SCALEDOBJECT} " in verify
    hpa_queries = re.findall(r"kubectl get hpa (\S+)", verify)
    assert hpa_queries and set(hpa_queries) == {HPA}


@pytest.mark.parametrize("overlay", OVERLAYS)
def test_fixed_names_match_every_overlay(overlay):
    obj = yaml.safe_load((GUIDE_DIR / "optimized-baseline/overlays" / overlay / "scaledobject.yaml").read_text())
    assert obj["metadata"]["name"] == SCALEDOBJECT
    assert obj["spec"]["advanced"]["horizontalPodAutoscalerConfig"]["name"] == HPA


@pytest.mark.parametrize("overlay", OVERLAYS)
@pytest.mark.parametrize("custom", [False, True], ids=["defaults", "custom"])
def test_real_render_apply_cleanup_and_verify_agree(keda, bash, overlay, custom):
    for executable in ("kubectl", "envsubst"):
        if not shutil.which(executable):
            pytest.skip(f"{executable} is required for real rendering")
    env = environment(keda, custom)
    # Old caller exports must not redirect verification away from the fixed names.
    env.update(SCALEDOBJECT_NAME="ignored-scaler", HPA_NAME="ignored-hpa")
    intercept = r'''
kubectl() {
  case "$1" in
    kustomize) command kubectl "$@" ;;
    apply|delete) cat ;;
    *) echo "Unexpected cluster call: $*" >&2; return 99 ;;
  esac
}
'''
    outputs = []
    for action in ("apply", "cleanup"):
        result = shell(bash, intercept + step_script(keda, f"{action}:{overlay}", True), env)
        assert result.returncode == 0, result.stderr
        assert "${" not in result.stdout
        outputs.append(list(yaml.safe_load_all(result.stdout)))
    assert outputs[0] == outputs[1]
    objects = outputs[0]
    scalers = [o for o in objects if o["kind"] == "ScaledObject"]
    assert len(scalers) == 1
    obj = scalers[0]
    assert obj["metadata"]["name"] == SCALEDOBJECT
    assert obj["spec"]["advanced"]["horizontalPodAutoscalerConfig"]["name"] == HPA
    assert obj["spec"]["scaleTargetRef"]["name"] == env["TARGET_DEPLOYMENT"]
    for resource in objects:
        if resource["kind"] == "ClusterRoleBinding":
            assert resource["metadata"]["name"].endswith("-" + env["NAMESPACE"])
            assert all(s["namespace"] == env["NAMESPACE"] for s in resource["subjects"])
        else:
            assert resource["metadata"]["namespace"] == env["NAMESPACE"]
    queries = [t["metadata"]["query"] for t in obj["spec"]["triggers"]]
    assert queries
    for query in queries:
        assert f'namespace="{env["NAMESPACE"]}"' in query
        if "flow_control_pool_saturation" in query:
            assert f'inference_pool="{env["INFERENCE_POOL"]}"' in query
        else:
            assert f'service="{env["EPP_SERVICE"]}"' in query
            assert f'model_name="{env["MODEL"]}"' in query
