"""Tests for helpers/preflight/preflight.py — reading the cluster.

Run from the repo root:

    python -m pytest helpers/preflight/tests/ -v
"""

import builders
import pytest
from builders import preflight


def test_collects_healthy_gke():
    st = builders.state(builders.healthy_gke())
    assert "resource.k8s.io/v1" in st.api_versions
    assert "leaderworkersets.leaderworkerset.x-k8s.io" in st.crds
    assert len(st.nodes) == 5
    gpu0 = next(n for n in st.nodes if n.name == "gpu-0")
    assert gpu0.taints == (preflight.Taint("nvidia.com/gpu", "present", "NoSchedule"),)
    assert gpu0.allocatable["ephemeral-storage"] == 2 * 2**40
    assert len(st.devices) == 64
    dev = next(d for d in st.devices if d.driver == "gpu.nvidia.com")
    assert dev.attributes["driverVersion"] == "570.172.08"
    assert dev.attributes["resource.kubernetes.io/pcieRoot"].startswith("pci0000:")
    assert st.device_classes == {"gpu.nvidia.com": "gpu.nvidia.com", "mrdma.google.com": "mrdma.google.com"}
    assert st.lws == (preflight.LwsController("lws-system", "registry.k8s.io/lws/lws:v0.11.0", ""),)
    assert st.webhooks == frozenset({"vdisaggregatedset.kb.io"})
    assert st.denied == ()


def test_dra_resources_not_queried_without_dra_api():
    runner = builders.FakeRunner(builders.healthy_coreweave())
    st = preflight.collect_state(runner)
    assert st.devices == ()
    assert st.device_classes == {}
    assert ("get", "resourceslices.resource.k8s.io") not in runner.calls


def test_forbidden_list_becomes_none():
    responses = builders.healthy_gke()
    responses[("get", "resourceslices.resource.k8s.io")] = builders.forbidden("resourceslices")
    st = builders.state(responses)
    assert st.devices is None
    assert len(st.denied) == 1 and "resourceslices" in st.denied[0]


def test_other_kubectl_errors_propagate():
    responses = builders.healthy_gke()
    responses[("get", "nodes")] = preflight.KubectlError(("get", "nodes"), 1, "Unable to connect to the server")
    with pytest.raises(preflight.KubectlError):
        builders.state(responses)


def test_device_class_without_driver_selector_maps_to_none():
    responses = builders.healthy_gke()
    responses[("get", "deviceclasses.resource.k8s.io")] = builders.items(
        {"metadata": {"name": "custom"}, "spec": {"selectors": [{"cel": {"expression": "true"}}]}})
    assert builders.state(responses).device_classes == {"custom": None}


def test_per_device_node_name():
    responses = builders.healthy_gke()
    shared = builders.resource_slice("", "gpu.nvidia.com", 2)
    shared["spec"].pop("nodeName")
    for i, device in enumerate(shared["spec"]["devices"]):
        device["nodeName"] = f"gpu-{i}"
    responses[("get", "resourceslices.resource.k8s.io")] = builders.items(shared)
    assert {d.node for d in builders.state(responses).devices} == {"gpu-0", "gpu-1"}


def test_lws_image_without_tag_is_found():
    responses = builders.healthy_gke()
    responses[("get", "deployments", "-A", "-l", "control-plane=controller-manager")] = builders.items(
        builders.lws_deployment("registry.k8s.io/lws/lws"))
    assert builders.state(responses).lws[0].image == "registry.k8s.io/lws/lws"


def test_runner_timeout_becomes_kubectl_error(monkeypatch):
    def slow(*args, **kwargs):
        raise preflight.subprocess.TimeoutExpired(args[0], 120)
    monkeypatch.setattr(preflight.subprocess, "run", slow)
    with pytest.raises(preflight.KubectlError, match="timed out"):
        preflight.Runner().text("version")
