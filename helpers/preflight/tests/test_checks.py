"""Tests for helpers/preflight/preflight.py — the checks.

Run from the repo root:

    python -m pytest helpers/preflight/tests/ -v
"""

from dataclasses import replace
from pathlib import Path

import builders
from builders import REPO_ROOT, preflight

RENDERS = Path(__file__).resolve().parent / "fixtures" / "render"
CFG = preflight.load_guide_config(REPO_ROOT / "guides" / "wide-ep" / "preflight.yaml")


def req(name="gke"):
    return preflight.parse_render((RENDERS / f"{name}.yaml").read_text())


def statuses(results):
    return [r.status for r in results]


# --- access -----------------------------------------------------------------

def test_access_passes_with_helm():
    assert statuses(preflight.check_access(builders.state(builders.healthy_gke()), True)) == ["PASS"]


def test_access_warns_without_helm_and_on_denied_lists():
    responses = builders.healthy_gke()
    responses[("get", "nodes")] = builders.forbidden("nodes")
    results = preflight.check_access(builders.state(responses), False)
    assert statuses(results) == ["PASS", "WARN", "WARN"]
    assert "helm" in results[1].detail
    assert "nodes" in results[2].detail


# --- APIs and CRDs ----------------------------------------------------------

def test_apis_and_crds_pass_on_healthy_gke():
    assert statuses(preflight.check_apis(req(), CFG, builders.state(builders.healthy_gke()), False)) == ["PASS", "PASS"]


def test_missing_dra_api_fails():
    responses = builders.healthy_gke()
    responses[("api-versions",)] = responses[("api-versions",)].replace("resource.k8s.io/v1\n", "")
    del responses[("get", "resourceslices.resource.k8s.io")]
    del responses[("get", "deviceclasses.resource.k8s.io")]
    results = preflight.check_apis(req(), CFG, builders.state(responses), False)
    assert results[0].status == "FAIL" and "resource.k8s.io/v1" in results[0].detail


def test_missing_crd_from_config_fails():
    responses = builders.healthy_gke()
    responses[("get", "customresourcedefinitions")] = builders.crd_list("disaggregatedsets.disaggregatedset.x-k8s.io")
    results = preflight.check_apis(req(), CFG, builders.state(responses), False)
    assert results[1].status == "FAIL"
    assert "inferencepools.inference.networking.k8s.io" in results[1].detail


def test_gateway_crds_only_with_gateway_mode():
    st = builders.state(builders.healthy_gke())
    assert preflight.check_apis(req(), CFG, st, False)[1].status == "PASS"
    results = preflight.check_apis(req(), CFG, st, True)
    assert results[1].status == "FAIL" and "gateways.gateway.networking.k8s.io" in results[1].detail


def test_unreadable_crds_warn():
    responses = builders.healthy_gke()
    responses[("get", "customresourcedefinitions")] = builders.forbidden("customresourcedefinitions")
    assert preflight.check_apis(req(), CFG, builders.state(responses), False)[1].status == "WARN"


# --- LWS ----------------------------------------------------------------------

def _lws(*deployments):
    responses = builders.healthy_gke()
    responses[("get", "deployments", "-A", "-l", "control-plane=controller-manager")] = builders.items(*deployments)
    return builders.state(responses)


def test_lws_new_enough_passes():
    assert statuses(preflight.check_lws(CFG, _lws(builders.lws_deployment()))) == ["PASS"]


def test_lws_too_old_fails():
    results = preflight.check_lws(CFG, _lws(builders.lws_deployment("registry.k8s.io/lws/lws:v0.10.2")))
    assert results[0].status == "FAIL" and "v0.10.2" in results[0].detail and "v0.11.0" in results[0].detail


def test_lws_non_semver_tag_warns():
    assert statuses(preflight.check_lws(CFG, _lws(builders.lws_deployment("registry.k8s.io/lws/lws:main")))) == ["WARN"]


def test_lws_digest_image_warns():
    image = "registry.k8s.io/lws/lws@sha256:" + "a" * 64
    assert statuses(preflight.check_lws(CFG, _lws(builders.lws_deployment(image)))) == ["WARN"]


def test_lws_tag_with_digest_is_still_a_version():
    image = "registry.k8s.io/lws/lws:v0.11.0@sha256:" + "a" * 64
    assert statuses(preflight.check_lws(CFG, _lws(builders.lws_deployment(image)))) == ["PASS"]


def test_lws_version_label_is_a_fallback():
    dep = builders.lws_deployment("registry.k8s.io/lws/lws:main", labels={"app.kubernetes.io/version": "v0.11.0"})
    assert statuses(preflight.check_lws(CFG, _lws(dep))) == ["PASS"]


def test_lws_absent_warns():
    assert statuses(preflight.check_lws(CFG, _lws())) == ["WARN"]


# --- DisaggregatedSet webhook ------------------------------------------------

def test_webhook_present_passes():
    assert statuses(preflight.check_ds_webhook(builders.state(builders.healthy_gke()))) == ["PASS"]


def test_webhook_absent_warns_with_helm_flag_hint():
    responses = builders.healthy_gke()
    responses[("get", "validatingwebhookconfigurations")] = builders.items(
        builders.webhook_config("vleaderworkerset.kb.io"))
    results = preflight.check_ds_webhook(builders.state(responses))
    assert results[0].status == "WARN" and "enableDisaggregatedSet=true" in results[0].hint
