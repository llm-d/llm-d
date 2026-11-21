"""Check backend selection and GMP-only deployment/cleanup without coreos CRDs."""

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import guide  # noqa: E402

GUIDES = ["models/glm-5-2", "models/qwen3-5-397b", "p2p-kv-cache-sharing", "hyperconverged-cephfs"]


def load_guide(name):
    return yaml.safe_load((REPO_ROOT / "guides" / name / "guide.yaml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", GUIDES)
def test_gmp_selects_router_values_and_omits_coreos_resources(name):
    data = load_guide(name)
    overrides = {"MONITORING": "true", "MONITORING_BACKEND": "gmp"}
    selected = guide.emit_script(data, ["deploy.monitoring_values"], overrides)
    assert "features/monitoring-gke.values.yaml" in selected
    assert "features/monitoring.values.yaml" not in selected
    assert selected.count("export MONITORING_VALUES=") == 1
    cleanup = "cleanup.rest" if "rest" in data["cleanup"] else "cleanup.stack"
    for section in ["deploy.monitoring", cleanup]:
        commands = guide.emit_script(data, [section], overrides)
        assert "kubectl apply" not in commands
        assert not any("kubectl delete" in line and "/monitoring" in line for line in commands.splitlines())


@pytest.mark.parametrize("name", GUIDES)
def test_oss_backend_remains_available(name):
    data = load_guide(name)
    overrides = {"MONITORING": "true"}
    selected = guide.emit_script(data, ["deploy.monitoring_values"], overrides)
    assert "features/monitoring.values.yaml" in selected
    assert "features/monitoring-gke.values.yaml" not in selected
    assert "kubectl apply" in guide.emit_script(data, ["deploy.monitoring"], overrides)
    cleanup = "cleanup.rest" if "rest" in data["cleanup"] else "cleanup.stack"
    assert "/monitoring" in guide.emit_script(data, [cleanup], overrides)


@pytest.mark.parametrize("name", GUIDES)
@pytest.mark.parametrize("backend", ["prometheus", "gmp"])
def test_disabled_monitoring_emits_no_install_commands(name, backend):
    data = load_guide(name)
    emitted = guide.emit_script(data, ["deploy.monitoring_values", "deploy.monitoring"], {"MONITORING_BACKEND": backend})
    assert "export MONITORING_VALUES=" not in emitted
    assert "kubectl apply" not in emitted


@pytest.mark.parametrize("name", GUIDES)
def test_ci_retains_oss_backend_and_skips_gmp_readme_commands(name):
    data = load_guide(name)
    emitted = guide.emit_script(data, ["deploy.monitoring_values"], {"MONITORING": "true"}, contexts={"ci"})
    assert "features/monitoring.values.yaml" in emitted
    assert "features/monitoring-gke.values.yaml" not in emitted
    assert data["deploy"]["monitoring_values"]["gke"][0]["skip_in"] == ["ci"]


@pytest.mark.parametrize("backend", ["prometheus", "gmp"])
def test_glm_tpu_does_not_emit_gpu_rank_podmonitors(backend):
    data = load_guide("models/glm-5-2")
    overrides = {"MONITORING": "true", "MONITORING_BACKEND": backend, "ACCELERATOR_TYPE": "tpu", "DEPLOYMENT": "tpu7x-2x2x2-dp16"}
    emitted = guide.emit_script(data, ["deploy.monitoring_values", "deploy.monitoring"], overrides)
    expected = "monitoring-gke.values.yaml" if backend == "gmp" else "monitoring.values.yaml"
    assert expected in emitted
    assert "kubectl apply" not in emitted


def test_hyperconverged_router_and_model_monitoring_are_opt_in():
    data = load_guide("hyperconverged-cephfs")
    emitted = guide.emit_script(data, ["deploy.monitoring_values", "deploy.monitoring"])
    assert "export MONITORING_VALUES=" not in emitted
    assert "kubectl apply" not in emitted


@pytest.mark.parametrize("backend", ["prometheus", "gmp"])
def test_hyperconverged_disabled_monitoring_skips_coreos_cleanup(backend):
    data = load_guide("hyperconverged-cephfs")
    emitted = guide.emit_script(data, ["cleanup.stack"], {"MONITORING_BACKEND": backend})
    assert "/components/monitoring" not in emitted
