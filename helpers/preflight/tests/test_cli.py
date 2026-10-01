"""Tests for helpers/preflight/preflight.py — guide config and the CLI.

Run from the repo root:

    python -m pytest helpers/preflight/tests/ -v
"""

import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "helpers" / "preflight"))
import builders  # noqa: E402
import preflight  # noqa: E402

WIDE_EP = REPO_ROOT / "guides" / "wide-ep"


def test_wide_ep_config_loads():
    cfg = preflight.load_guide_config(WIDE_EP / "preflight.yaml")
    assert cfg.lws_min_version == (0, 11, 0)
    assert cfg.driver_max_major_exclusive == 580
    assert cfg.driver_severity == "warn"
    assert cfg.router_standalone == preflight.RouterSize(Decimal(8), Decimal(16 * 2**30))
    assert "leaderworkersets.leaderworkerset.x-k8s.io" in cfg.crds
    assert cfg.docs["rdma"] == "docs/infrastructure/rdma/README.md"


def test_missing_config_is_a_usage_error(tmp_path):
    with pytest.raises(preflight.PreflightError, match="no preflight requirements"):
        preflight.load_guide_config(tmp_path / "preflight.yaml")


def test_bad_severity_is_rejected(tmp_path):
    text = (WIDE_EP / "preflight.yaml").read_text().replace("severity: warn", "severity: loud")
    (tmp_path / "preflight.yaml").write_text(text)
    with pytest.raises(preflight.PreflightError, match="severity"):
        preflight.load_guide_config(tmp_path / "preflight.yaml")


def test_missing_key_is_rejected(tmp_path):
    (tmp_path / "preflight.yaml").write_text("requirements:\n  cluster:\n    crds: []\n")
    with pytest.raises(preflight.PreflightError, match="invalid preflight config"):
        preflight.load_guide_config(tmp_path / "preflight.yaml")


@pytest.mark.parametrize("raw, expected", [("v0.11.0", (0, 11, 0)), ("0.10.2", (0, 10, 2)), ("main", None), ("", None)])
def test_parse_semver(raw, expected):
    assert preflight.parse_semver(raw) == expected


SCRIPT = REPO_ROOT / "helpers" / "preflight" / "preflight.py"
GKE = "modelserver/gpu/vllm-deepseek-r1-0528/gke"
RENDER_GKE = (Path(__file__).resolve().parent / "fixtures" / "render" / "gke.yaml").read_text()


def _which(*present):
    return lambda tool: f"/usr/bin/{tool}" if tool in present else None


def _runner(responses, seen=None):
    def factory(context):
        if seen is not None:
            seen.append(context)
        return builders.FakeRunner(responses)
    return factory


def _responses(**overrides):
    responses = builders.healthy_gke()
    responses[("kustomize", str(WIDE_EP / GKE))] = RENDER_GKE
    responses[("version",)] = "Client Version: v1.35.2\nServer Version: v1.35.1\n"
    responses.update(overrides)
    return responses


def _main(capsys, *args, responses=None, which=None):
    code = preflight.main([str(WIDE_EP), "--overlay", GKE, *args],
                          runner_factory=_runner(responses or _responses()),
                          which=which or _which("kubectl", "helm"))
    return code, capsys.readouterr()


def test_healthy_cluster_exits_0(capsys):
    code, out = _main(capsys)
    assert code == 0
    assert "PASS  model-server capacity" in out.out
    assert "FAIL" not in out.out.replace("0 FAIL", "")


def test_fail_exits_1(capsys):
    responses = _responses()
    responses[("get", "deviceclasses.resource.k8s.io")] = builders.items(builders.device_class("gpu.nvidia.com"))
    code, out = _main(capsys, responses=responses)
    assert code == 1 and "FAIL  RDMA" in out.out and "hint: see docs/infrastructure/providers/gke/README.md" in out.out


def test_json_output(capsys):
    code, out = _main(capsys, "--output", "json")
    data = json.loads(out.out)
    assert code == 0 and {"status", "check", "detail", "hint"} <= set(data[0])


def test_no_kubectl_exits_2(capsys):
    code, out = _main(capsys, which=_which())
    assert code == 2 and "kubectl not found" in out.err


def test_missing_overlay_dir_exits_2(capsys):
    code = preflight.main([str(WIDE_EP), "--overlay", "modelserver/nope"],
                          runner_factory=_runner(_responses()), which=_which("kubectl"))
    assert code == 2 and "not a directory" in capsys.readouterr().err


def test_unreachable_cluster_exits_2(capsys):
    responses = _responses()
    responses[("version",)] = preflight.KubectlError(("version",), 1, "Unable to connect to the server")
    code, out = _main(capsys, responses=responses)
    assert code == 2 and "could not reach the cluster" in out.err


def test_unsupported_overlay_exits_2(capsys):
    responses = _responses()
    responses[("kustomize", str(WIDE_EP / GKE))] = "apiVersion: v1\nkind: ServiceAccount\nmetadata: {name: x}\n"
    code, out = _main(capsys, responses=responses)
    assert code == 2 and "unsupported overlay" in out.err


def test_context_is_passed_to_runner(capsys):
    seen = []
    code = preflight.main([str(WIDE_EP), "--overlay", GKE, "--context", "test"],
                          runner_factory=_runner(_responses(), seen), which=_which("kubectl", "helm"))
    assert code == 0 and seen == ["test"]
    seen.clear()
    preflight.main([str(WIDE_EP), "--overlay", GKE],
                   runner_factory=_runner(_responses(), seen), which=_which("kubectl", "helm"))
    assert seen == [None]


def test_missing_guide_dir_exits_2(capsys, tmp_path):
    code = preflight.main([str(tmp_path / "nope"), "--overlay", GKE],
                          runner_factory=_runner(_responses()), which=_which("kubectl"))
    assert code == 2 and "not a directory" in capsys.readouterr().err


def test_missing_pyyaml_exits_2_with_hint():
    code = ("import sys, runpy; sys.modules['yaml'] = None; sys.argv = ['preflight.py']; "
            f"runpy.run_path({str(SCRIPT)!r}, run_name='__main__')")
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 2 and "pip install pyyaml" in proc.stderr


def test_unparseable_render_exits_2(capsys):
    # vllm-glm-5.2 ships envsubst placeholders; the render can't be read as numbers.
    responses = _responses()
    responses[("kustomize", str(WIDE_EP / GKE))] = RENDER_GKE.replace("size: 2", "size: ${PREFILL_SIZE}", 1)
    code, out = _main(capsys, responses=responses)
    assert code == 2
    assert "cannot read the DisaggregatedSet" in out.err and "PREFILL_SIZE" in out.err
    assert "Traceback" not in out.err


def test_malformed_config_is_a_usage_error(tmp_path):
    (tmp_path / "preflight.yaml").write_text("crds: [unclosed\n")
    with pytest.raises(preflight.PreflightError, match="not valid YAML"):
        preflight.load_guide_config(tmp_path / "preflight.yaml")


def test_requirements_live_under_requirements_cluster(tmp_path):
    # Same container a guide.yaml `requirements:` block would use (see #2636), so moving it is a copy.
    flat = "crds: []\nlws: {minVersion: v0.11.0}\ngpuDriver: {maxMajorExclusive: 580}\n" \
           "router: {standalone: {cpu: '8', memory: 16Gi}, gateway: {cpu: '4', memory: 8Gi}}\n"
    (tmp_path / "preflight.yaml").write_text(flat)
    with pytest.raises(preflight.PreflightError, match="requirements.cluster"):
        preflight.load_guide_config(tmp_path / "preflight.yaml")
