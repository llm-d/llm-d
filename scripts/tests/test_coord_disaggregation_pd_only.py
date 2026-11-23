"""PD-only variant of the Coordinator Disaggregation guide.

The PD-only note in the guide's README selects two copies of configuration that
otherwise live in one place:

- router/coord-disaggregation-pd-only.values.yaml repeats the router's whole
  plugin config, because the rollout screener's requiredRoles sits inside the
  pluginsCustomConfig string and Helm cannot edit inside a string;
- coordinator/components/pd-only/patch-pd-only.yaml repeats the Coordinator's
  whole coordinator.yaml, because it replaces that ConfigMap key.

These tests keep each copy equal to its source except for what PD-only drops,
and check that the coordinator/pd-only and coordinator/pd-only-gke overlays
build the same objects as base and gke with only the pipeline changed.
"""

import copy
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
GUIDE_DIR = REPO_ROOT / "guides/coord-disaggregation"
ROUTER_VALUES = GUIDE_DIR / "router/coord-disaggregation.values.yaml"
PD_ONLY_VALUES = GUIDE_DIR / "router/coord-disaggregation-pd-only.values.yaml"
PLUGINS_KEY = "coordinator-plugins.yaml"
CONFIG_KEY = "coordinator.yaml"
PD_ONLY_STEPS = ["conditional-decode", "prefill", "decode"]
# Each PD-only overlay and the provider overlay it is built from.
OVERLAYS = {"pd-only": "base", "pd-only-gke": "gke"}


def _plugins(values_file: Path) -> dict:
    values = yaml.safe_load(values_file.read_text())
    return yaml.safe_load(values["router"]["epp"]["pluginsCustomConfig"][PLUGINS_KEY])


def _screener(plugins: dict) -> dict:
    [screener] = [p for p in plugins["plugins"] if p["type"] == "disaggregatedset-rollout-screener"]
    return screener


def test_pd_only_router_values_set_only_the_plugin_config():
    """Layered after the main values file, the PD-only file overrides one key and nothing else."""
    values = yaml.safe_load(PD_ONLY_VALUES.read_text())
    assert values == {"router": {"epp": {"pluginsCustomConfig": {PLUGINS_KEY: values["router"]["epp"]["pluginsCustomConfig"][PLUGINS_KEY]}}}}


def test_pd_only_router_plugins_differ_only_in_required_roles():
    main, pd_only = _plugins(ROUTER_VALUES), _plugins(PD_ONLY_VALUES)
    assert _screener(main)["parameters"]["revisionGating"]["requiredRoles"] == ["encode", "prefill", "decode"]
    assert _screener(pd_only)["parameters"]["revisionGating"]["requiredRoles"] == ["prefill", "decode"]
    expected = copy.deepcopy(main)
    _screener(expected)["parameters"]["revisionGating"]["requiredRoles"] = ["prefill", "decode"]
    assert pd_only == expected


def _build(overlay: str) -> list[dict]:
    path = GUIDE_DIR / "coordinator" / overlay
    kubectl, kustomize = shutil.which("kubectl"), shutil.which("kustomize")
    if kubectl:
        cmd = [kubectl, "kustomize", str(path)]
    elif kustomize:
        cmd = [kustomize, "build", str(path)]
    else:
        pytest.skip("kubectl or kustomize is required to build the coordinator overlays")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def _split(docs: list[dict]) -> tuple[dict, list[dict]]:
    """The Coordinator's parsed coordinator.yaml, and every other object."""
    [cm] = [d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "llm-d-coordinator-config"]
    rest = [d for d in docs if d is not cm]
    return yaml.safe_load(cm["data"][CONFIG_KEY]), rest


@pytest.mark.parametrize("overlay", OVERLAYS)
def test_pd_only_overlay_runs_the_pd_only_pipeline(overlay):
    config, _ = _split(_build(overlay))
    pipeline = config["pipeline"]
    assert [s["type"] for s in pipeline["steps"]] == PD_ONLY_STEPS
    assert "ec_connector" not in pipeline


@pytest.mark.parametrize("overlay,source", OVERLAYS.items())
def test_pd_only_overlay_changes_only_the_pipeline(overlay, source):
    """Same objects as the provider overlay; coordinator.yaml equal to it once the
    encode-only pieces are dropped (no ec_connector; replace-media-urls, render and
    encode removed from the steps)."""
    pd_config, pd_rest = _split(_build(overlay))
    src_config, src_rest = _split(_build(source))
    assert pd_rest == src_rest
    expected = copy.deepcopy(src_config)
    del expected["pipeline"]["ec_connector"]
    expected["pipeline"]["steps"] = [s for s in expected["pipeline"]["steps"] if s["type"] in PD_ONLY_STEPS]
    assert pd_config == expected
