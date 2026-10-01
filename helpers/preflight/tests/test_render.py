"""Tests for helpers/preflight/preflight.py — render parsing and primitives.

Run from the repo root:

    python -m pytest helpers/preflight/tests/ -v
"""

import sys
from decimal import Decimal
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "helpers" / "preflight"))
import preflight  # noqa: E402


@pytest.mark.parametrize(
    "raw, expected",
    [
        (32, Decimal(32)),
        ("8", Decimal(8)),
        ("8000m", Decimal(8)),
        ("15890m", Decimal("15.89")),
        ("512Gi", Decimal(512 * 2**30)),
        ("1Ti", Decimal(2**40)),
        ("100M", Decimal(100 * 10**6)),
        ("1e3", Decimal(1000)),
    ],
)
def test_parse_quantity(raw, expected):
    assert preflight.parse_quantity(raw) == expected


@pytest.mark.parametrize("raw", ["lots", "1.2.3", "1 Gi"])
def test_parse_quantity_rejects_garbage(raw):
    with pytest.raises(ValueError):
        preflight.parse_quantity(raw)


@pytest.mark.parametrize(
    "name, value, expected",
    [
        ("memory", Decimal(512 * 2**30), "512Gi"),
        ("ephemeral-storage", Decimal(2**40), "1Ti"),
        ("memory", Decimal(57 * 2**30 + 2**29), "57.5Gi"),
        ("cpu", Decimal("15.89"), "15.89"),
        ("nvidia.com/gpu", Decimal(8), "8"),
    ],
)
def test_format_quantity(name, value, expected):
    assert preflight.format_quantity(name, value) == expected


GPU_TAINT = preflight.Taint("nvidia.com/gpu", "present", "NoSchedule")


def test_exists_toleration_matches_key():
    tol = preflight.Toleration("nvidia.com/gpu", "Exists", "", "NoSchedule")
    assert preflight.tolerates([tol], GPU_TAINT)


def test_toleration_effect_must_match():
    tol = preflight.Toleration("nvidia.com/gpu", "Exists", "", "NoExecute")
    assert not preflight.tolerates([tol], GPU_TAINT)


def test_equal_toleration_needs_value():
    assert preflight.tolerates([preflight.Toleration("nvidia.com/gpu", "Equal", "present", "")], GPU_TAINT)
    assert not preflight.tolerates([preflight.Toleration("nvidia.com/gpu", "Equal", "other", "")], GPU_TAINT)


def test_empty_key_exists_tolerates_everything():
    assert preflight.tolerates([preflight.Toleration("", "Exists", "", "")], GPU_TAINT)


def test_no_tolerations():
    assert not preflight.tolerates([], GPU_TAINT)
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _render(name):
    return preflight.parse_render((FIXTURES / "render" / f"{name}.yaml").read_text())


def test_gke_overlay_is_dra_with_four_pods():
    req = _render("gke")
    assert req.rdma_mode == "dra"
    assert len(req.pods) == 4
    assert req.apis == frozenset({"disaggregatedset.x-k8s.io/v1", "resource.k8s.io/v1"})
    pod = req.pods[0]
    assert pod.devices == {"gpu.nvidia.com": 8, "mrdma.google.com": 8}
    assert "nvidia.com/gpu" not in pod.requests
    assert pod.requests["cpu"] == 32
    assert pod.requests["memory"] == 512 * 2**30
    assert pod.requests["ephemeral-storage"] == 2**40
    assert preflight.Toleration("nvidia.com/gpu", "Exists", "", "NoSchedule") in pod.tolerations
    assert req.notes == ()


def test_coreweave_overlay_is_device_plugin():
    req = _render("coreweave")
    assert req.rdma_mode == "device-plugin"
    assert req.pods[0].requests["rdma/ib"] == 1
    assert req.pods[0].requests["nvidia.com/gpu"] == 8
    assert req.pods[0].devices == {}


@pytest.mark.parametrize("name", ["base", "dgx-cloud-gb200"])
def test_overlays_without_rdma(name):
    req = _render(name)
    assert req.rdma_mode == "none"
    assert len(req.pods) == 4
    assert req.pods[0].tolerations == ()


def _ds(pod_spec, size=2, replicas=1, extra_docs=""):
    return f"""
apiVersion: disaggregatedset.x-k8s.io/v1
kind: DisaggregatedSet
metadata: {{name: t}}
spec:
  roles:
  - name: decode
    spec:
      replicas: {replicas}
      leaderWorkerTemplate:
        size: {size}
        workerTemplate:
          spec: {pod_spec}
{extra_docs}"""


def test_overlay_without_disaggregatedset_is_unsupported():
    text = "apiVersion: leaderworkerset.x-k8s.io/v1\nkind: LeaderWorkerSet\nmetadata: {name: x}\n"
    with pytest.raises(preflight.PreflightError, match="unsupported overlay"):
        preflight.parse_render(text)


def test_limits_count_when_requests_absent():
    spec = "{containers: [{name: vllm, resources: {limits: {cpu: '4', memory: 8Gi}}}]}"
    pod = preflight.parse_render(_ds(spec)).pods[0]
    assert pod.requests == {"cpu": 4, "memory": 8 * 2**30}


def test_replicas_times_size_pods_and_leader_template():
    spec = "{containers: [{name: w, resources: {requests: {cpu: '1'}}}]}"
    text = _ds(spec, size=3, replicas=2).replace(
        "        workerTemplate:",
        "        leaderTemplate:\n"
        "          spec: {containers: [{name: l, resources: {requests: {cpu: '2'}}}]}\n"
        "        workerTemplate:",
    )
    pods = preflight.parse_render(text).pods
    assert len(pods) == 6
    assert sorted(p.requests["cpu"] for p in pods) == [1, 1, 1, 1, 2, 2]


def test_unresolved_claim_template_is_a_note_not_a_crash():
    spec = ("{containers: [{name: vllm}], "
            "resourceClaims: [{name: c, resourceClaimTemplateName: wide-ep-compute-domain}]}")
    req = preflight.parse_render(_ds(spec))
    assert req.pods[0].devices == {}
    assert any("wide-ep-compute-domain" in n for n in req.notes)


def test_first_available_request_is_a_note():
    rct = """---
apiVersion: resource.k8s.io/v1
kind: ResourceClaimTemplate
metadata: {name: t}
spec:
  spec:
    devices:
      requests:
      - name: gpu
        firstAvailable: [{name: a, deviceClassName: gpu.nvidia.com}]
"""
    spec = "{containers: [{name: vllm}], resourceClaims: [{name: c, resourceClaimTemplateName: t}]}"
    req = preflight.parse_render(_ds(spec, extra_docs=rct))
    assert req.pods[0].devices == {}
    assert any("firstAvailable" in n for n in req.notes)


def test_sidecar_init_container_counts_toward_requests():
    # The decode role has a routing-proxy sidecar (initContainer with restartPolicy: Always);
    # Kubernetes adds sidecar requests to the pod's, plain init containers are not summed.
    spec = ("{containers: [{name: vllm, resources: {requests: {cpu: '4'}}}], "
            "initContainers: [{name: proxy, restartPolicy: Always, resources: {requests: {cpu: '2'}}}, "
            "{name: init, resources: {requests: {cpu: '100'}}}]}")
    assert preflight.parse_render(_ds(spec)).pods[0].requests["cpu"] == 6


def test_claim_template_without_devices_is_unsupported():
    rct = """---
apiVersion: resource.k8s.io/v1
kind: ResourceClaimTemplate
metadata: {name: t}
spec: {spec: {}}
"""
    spec = "{containers: [{name: vllm}], resourceClaims: [{name: c, resourceClaimTemplateName: t}]}"
    with pytest.raises(preflight.PreflightError, match="ResourceClaimTemplate 't'"):
        preflight.parse_render(_ds(spec, extra_docs=rct))
