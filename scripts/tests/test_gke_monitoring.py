"""Check backend selection and GMP-only deployment/cleanup without coreos CRDs."""

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import guide  # noqa: E402

GUIDES = ["multimodal-serving", "omni-serving", "models/nemotron-3-ultra"]


def load_guide(name):
    return yaml.safe_load((REPO_ROOT / "guides" / name / "guide.yaml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", GUIDES)
def test_gmp_selects_router_values_and_omits_coreos_resources(name):
    data = load_guide(name)
    overrides = {"MONITORING": "true", "MONITORING_BACKEND": "gmp", "INFRA_PROVIDER": "gke"}
    selected = guide.emit_script(data, ["deploy.monitoring_values"], overrides)
    assert "features/monitoring-gke.values.yaml" in selected
    assert "features/monitoring.values.yaml" not in selected
    assert selected.count("export MONITORING_VALUES=") == 1
    for section in ["deploy.monitoring", "cleanup.rest"]:
        commands = guide.emit_script(data, [section], overrides)
        assert "/components/monitoring" not in commands
        assert "${MONITORING_COMPONENT}" not in commands


@pytest.mark.parametrize("name", GUIDES)
@pytest.mark.parametrize("provider", ["base", "gke"])
def test_oss_backend_remains_available_on_every_provider(name, provider):
    data = load_guide(name)
    overrides = {"MONITORING": "true", "INFRA_PROVIDER": provider}
    selected = guide.emit_script(data, ["deploy.monitoring_values"], overrides)
    assert "features/monitoring.values.yaml" in selected
    assert "features/monitoring-gke.values.yaml" not in selected
    assert "kubectl apply" in guide.emit_script(data, ["deploy.monitoring"], overrides)
    assert "kubectl delete" in guide.emit_script(data, ["cleanup.rest"], overrides)


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


def test_sglang_diffusion_has_no_model_server_metrics_install():
    data = load_guide("omni-serving")
    emitted = guide.emit_script(data, ["deploy.monitoring"], {"MONITORING": "true", "MODEL_SERVER": "sglang", "TASK": "text-to-image"})
    assert "kubectl apply" not in emitted
