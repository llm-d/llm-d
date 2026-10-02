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
# --- RDMA ---------------------------------------------------------------------


def test_rdma_dra_passes_on_healthy_gke():
    assert statuses(preflight.check_rdma(req(), CFG, builders.state(builders.healthy_gke()))) == ["PASS"]


def test_rdma_missing_device_class_fails():
    responses = builders.healthy_gke()
    responses[("get", "deviceclasses.resource.k8s.io")] = builders.items(builders.device_class("gpu.nvidia.com"))
    results = preflight.check_rdma(req(), CFG, builders.state(responses))
    assert results[0].status == "FAIL" and "mrdma.google.com" in results[0].detail
    assert "docs/infrastructure/providers/gke/README.md" in results[0].hint


def test_rdma_no_nic_devices_published_fails():
    responses = builders.healthy_gke()
    slices = responses[("get", "resourceslices.resource.k8s.io")]["items"]
    responses[("get", "resourceslices.resource.k8s.io")] = builders.items(
        *[s for s in slices if s["spec"]["driver"] != "mrdma.google.com"])
    results = preflight.check_rdma(req(), CFG, builders.state(responses))
    assert results[0].status == "FAIL" and "mrdma.google.com" in results[0].detail


def test_rdma_missing_pcie_root_warns():
    responses = builders.healthy_gke()
    for s in responses[("get", "resourceslices.resource.k8s.io")]["items"]:
        if s["spec"]["driver"] == "mrdma.google.com":
            for d in s["spec"]["devices"]:
                d["attributes"] = {}
    results = preflight.check_rdma(req(), CFG, builders.state(responses))
    assert statuses(results) == ["PASS", "WARN"] and "pcieRoot" in results[1].detail


def test_rdma_unreadable_slices_warn():
    responses = builders.healthy_gke()
    responses[("get", "resourceslices.resource.k8s.io")] = builders.forbidden("resourceslices")
    assert statuses(preflight.check_rdma(req(), CFG, builders.state(responses))) == ["WARN"]


def test_rdma_device_plugin_passes_and_fails():
    st = builders.state(builders.healthy_coreweave())
    assert statuses(preflight.check_rdma(req("coreweave"), CFG, st)) == ["PASS"]
    responses = builders.healthy_coreweave()
    responses[("get", "nodes")] = builders.items(builders.node("gpu-0", gpu=8))
    results = preflight.check_rdma(req("coreweave"), CFG, builders.state(responses))
    assert results[0].status == "FAIL" and "rdma/ib" in results[0].detail


def test_rdma_not_in_manifests_warns():
    results = preflight.check_rdma(req("base"), CFG, builders.state(builders.healthy_coreweave()))
    assert statuses(results) == ["WARN"] and "docs/infrastructure/rdma/README.md" in results[0].hint


# --- GPU driver -----------------------------------------------------------------

def test_driver_below_limit_passes():
    assert statuses(preflight.check_driver(CFG, builders.state(builders.healthy_gke("570.172.08")))) == ["PASS"]


def test_driver_r580_warns_by_default():
    results = preflight.check_driver(CFG, builders.state(builders.healthy_gke("580.65.06")))
    assert results[0].status == "WARN" and "580" in results[0].detail and "gpu-0" in results[0].detail


def test_driver_r580_fails_when_configured():
    results = preflight.check_driver(replace(CFG, driver_severity="fail"),
                                     builders.state(builders.healthy_gke("580.65.06")))
    assert results[0].status == "FAIL"


def test_driver_from_gfd_label_and_gke_annotation():
    responses = builders.healthy_coreweave()
    responses[("get", "nodes")] = builders.items(
        builders.node("a", gpu=8, labels={"nvidia.com/cuda.driver-version.major": "580"}),
        builders.node("b", gpu=8, labels={"nvidia.com/cuda.driver.major": "570"}),
        builders.node("c", gpu=8, annotations={"cloud.google.com/cuda.driver-version.major": "575"}))
    results = preflight.check_driver(CFG, builders.state(responses))
    assert results[0].status == "WARN"
    assert results[0].detail.endswith(": a")  # only node a is on R580; b (570) and c (575) are not


def test_driver_unknown_warns():
    results = preflight.check_driver(CFG, builders.state(builders.healthy_coreweave()))
    assert statuses(results) == ["WARN"] and "not found" in results[0].detail
# --- capacity -------------------------------------------------------------------


def test_capacity_passes_on_healthy_gke():
    results = preflight.check_capacity(req(), builders.state(builders.healthy_gke()))
    assert statuses(results) == ["PASS", "INFO"] and "4/4" in results[0].detail


def test_capacity_passes_on_healthy_coreweave():
    results = preflight.check_capacity(req("coreweave"), builders.state(builders.healthy_coreweave()))
    assert results[0].status == "PASS"


def test_capacity_small_boot_disk_fails_and_names_the_resource():
    responses = builders.healthy_gke()
    nodes = responses[("get", "nodes")]["items"]
    for n in nodes:
        n["status"]["allocatable"]["ephemeral-storage"] = "500Gi"
    results = preflight.check_capacity(req(), builders.state(responses))
    assert results[0].status == "FAIL"
    assert "0/4" in results[0].detail and "ephemeral-storage" in results[0].detail


def test_capacity_counts_dra_devices():
    responses = builders.healthy_gke()
    slices = responses[("get", "resourceslices.resource.k8s.io")]["items"]
    responses[("get", "resourceslices.resource.k8s.io")] = builders.items(
        *[s for s in slices if s["spec"]["nodeName"] != "gpu-3"])
    results = preflight.check_capacity(req(), builders.state(responses))
    assert results[0].status == "FAIL" and "3/4" in results[0].detail


def test_capacity_excludes_untolerated_and_cordoned_nodes():
    responses = builders.healthy_gke()
    nodes = responses[("get", "nodes")]["items"]
    nodes[0]["spec"]["taints"].append({"key": "dedicated", "value": "batch", "effect": "NoSchedule"})
    nodes[1]["spec"]["unschedulable"] = True
    results = preflight.check_capacity(req(), builders.state(responses))
    assert results[0].status == "FAIL" and "2/4" in results[0].detail
    assert "gpu-0" in results[0].detail and "gpu-1" in results[0].detail


def test_capacity_warns_when_nodes_unreadable():
    responses = builders.healthy_gke()
    responses[("get", "nodes")] = builders.forbidden("nodes")
    assert preflight.check_capacity(req(), builders.state(responses))[0].status == "WARN"


def test_capacity_fails_on_dra_cluster_without_gpu_devices():
    # Big CPU-only nodes and no DRA devices must not pass: GPU demand can't vanish.
    responses = builders.healthy_gke()
    responses[("get", "nodes")] = builders.items(
        *[builders.node(n, cpu="190", memory="1800Gi", eph="2Ti") for n in builders.GPU_NODES])
    responses[("get", "resourceslices.resource.k8s.io")] = builders.items()
    responses[("get", "deviceclasses.resource.k8s.io")] = builders.items()
    results = preflight.check_capacity(req(), builders.state(responses))
    assert results[0].status == "FAIL" and "0/4" in results[0].detail
    assert "gpu.nvidia.com devices 0 < 8" in results[0].detail
    assert "decode" in results[0].detail or "prefill" in results[0].detail


def test_capacity_warns_when_device_classes_unreadable():
    responses = builders.healthy_gke()
    responses[("get", "deviceclasses.resource.k8s.io")] = builders.forbidden("deviceclasses")
    assert preflight.check_capacity(req(), builders.state(responses))[0].status == "WARN"


def test_capacity_warns_when_device_class_names_no_driver():
    responses = builders.healthy_gke()
    responses[("get", "deviceclasses.resource.k8s.io")] = builders.items(
        {"metadata": {"name": "gpu.nvidia.com"}, "spec": {"selectors": [{"cel": {"expression": "true"}}]}},
        builders.device_class("mrdma.google.com"))
    results = preflight.check_capacity(req(), builders.state(responses))
    assert results[0].status == "WARN" and "gpu.nvidia.com" in results[0].detail


# --- router node ------------------------------------------------------------------

def test_router_fits_on_cpu_node():
    results = preflight.check_router(CFG, builders.state(builders.healthy_gke()), False)
    assert results[0].status == "PASS" and "cpu-0" in results[0].detail


def test_router_ignores_dra_gpu_nodes():
    responses = builders.healthy_gke()
    nodes = responses[("get", "nodes")]["items"]
    responses[("get", "nodes")] = builders.items(
        *[n for n in nodes if n["metadata"]["name"] != "cpu-0"],
        builders.node("cpu-small", cpu="3920m", memory="12Gi"))
    for n in responses[("get", "nodes")]["items"]:
        n["spec"]["taints"] = []  # even untainted, DRA GPU nodes are not router candidates
    results = preflight.check_router(CFG, builders.state(responses), False)
    assert results[0].status == "FAIL" and "cpu-small" in results[0].detail


def test_router_warns_when_dra_slices_unreadable():
    responses = builders.healthy_gke()
    responses[("get", "resourceslices.resource.k8s.io")] = builders.forbidden("resourceslices")
    assert preflight.check_router(CFG, builders.state(responses), False)[0].status == "WARN"


def test_router_fails_when_only_tainted_cpu_nodes():
    responses = builders.healthy_coreweave()
    nodes = responses[("get", "nodes")]["items"]
    for n in nodes:
        n["spec"]["taints"] = [{"key": "dedicated", "value": "infra", "effect": "NoSchedule"}]
    assert preflight.check_router(CFG, builders.state(responses), False)[0].status == "FAIL"


def test_router_gateway_mode_needs_less():
    responses = builders.healthy_gke()
    nodes = responses[("get", "nodes")]["items"]
    responses[("get", "nodes")] = builders.items(
        *[n for n in nodes if n["metadata"]["name"] != "cpu-0"],
        builders.node("cpu-6", cpu="5800m", memory="20Gi"))
    st = builders.state(responses)
    assert preflight.check_router(CFG, st, False)[0].status == "FAIL"
    assert preflight.check_router(CFG, st, True)[0].status == "PASS"


# --- notes and run_checks ----------------------------------------------------------

def test_render_notes_become_warnings():
    r = preflight.Requirements(frozenset(), (), "none", ("claim template 'x' is not in the rendered manifests",))
    assert statuses(preflight.check_notes(r)) == ["WARN", "INFO"]


def test_run_checks_on_healthy_gke_has_no_fail():
    results = preflight.run_checks(req(), CFG, builders.state(builders.healthy_gke()), False, True)
    assert "FAIL" not in statuses(results)
    assert "decode" in " ".join(r.detail for r in results if r.status == "INFO")


# --- review fixes -----------------------------------------------------------------

def test_rdma_via_unresolved_claim_is_not_reported_as_absent():
    r = preflight.Requirements(frozenset(), (), "none", (), ("compute-domain-channel",))
    results = preflight.check_rdma(r, CFG, builders.state(builders.healthy_coreweave()))
    assert results[0].status == "WARN"
    assert "compute-domain-channel" in results[0].detail
    assert "requests no RDMA" not in results[0].detail


def test_capacity_names_every_shortage_on_a_node():
    responses = builders.healthy_coreweave()
    responses[("get", "nodes")] = builders.items(builders.node("cpu-big", cpu="64", memory="600Gi", eph="100Gi"))
    detail = preflight.check_capacity(req("coreweave"), builders.state(responses))[0].detail
    assert "ephemeral-storage 100Gi < 1Ti" in detail and "nvidia.com/gpu 0 < 8" in detail


def test_capacity_pass_without_requests_reads_naturally():
    smoke = preflight.Requirements(frozenset(), (preflight.PodReq("decode leader", {}, (), {}),), "none", ())
    detail = preflight.check_capacity(smoke, builders.state(builders.healthy_coreweave()))[0].detail
    assert detail == "1/1 pods fit (no resource requests)"
