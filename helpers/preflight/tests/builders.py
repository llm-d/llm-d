"""Fake kubectl and Kubernetes object builders for preflight tests."""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "helpers" / "preflight"))
import preflight  # noqa: E402

GPU_TAINT = {"key": "nvidia.com/gpu", "value": "present", "effect": "NoSchedule"}


class FakeRunner:
    """Stands in for preflight.Runner. Keys are kubectl args without `-o json`."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def _lookup(self, args):
        self.calls.append(args)
        if args not in self.responses:
            raise AssertionError(f"unexpected kubectl call: {args}")
        value = self.responses[args]
        if isinstance(value, Exception):
            raise value
        return value

    def text(self, *args):
        value = self._lookup(args)
        return value if isinstance(value, str) else json.dumps(value)

    def json(self, *args):
        value = self._lookup(args)
        return json.loads(value) if isinstance(value, str) else value


def items(*objs):
    return {"items": list(objs)}


def forbidden(resource):
    return preflight.KubectlError(("get", resource), 1, f'Error from server (Forbidden): {resource} is forbidden')


def node(name, cpu="16", memory="64Gi", eph="100Gi", gpu=None, rdma=None, taints=(),
         labels=None, annotations=None, unschedulable=False):
    allocatable = {"cpu": cpu, "memory": memory, "ephemeral-storage": eph, "pods": "110"}
    if gpu is not None:
        allocatable["nvidia.com/gpu"] = str(gpu)
    if rdma is not None:
        allocatable["rdma/ib"] = str(rdma)
    return {
        "metadata": {"name": name, "labels": labels or {}, "annotations": annotations or {}},
        "spec": {"taints": list(taints), **({"unschedulable": True} if unschedulable else {})},
        "status": {"allocatable": allocatable},
    }


def resource_slice(node_name, driver, count, attributes=None):
    devices = [{"name": f"{driver.split('.')[0]}-{i}", "attributes": attributes(i) if attributes else {}}
               for i in range(count)]
    return {"metadata": {"name": f"{node_name}-{driver}"},
            "spec": {"driver": driver, "nodeName": node_name, "devices": devices}}


def device_class(name, driver=None):
    driver = driver or name
    return {"metadata": {"name": name},
            "spec": {"selectors": [{"cel": {"expression": f"device.driver == '{driver}'"}}]}}


def lws_deployment(image="registry.k8s.io/lws/lws:v0.11.0", namespace="lws-system", labels=None):
    return {"metadata": {"name": "lws-controller-manager", "namespace": namespace, "labels": labels or {}},
            "spec": {"template": {"spec": {"containers": [{"name": "manager", "image": image}]}}}}


def webhook_config(*names):
    return {"metadata": {"name": "lws-validating-webhook-configuration"},
            "webhooks": [{"name": n} for n in names]}


def crd_list(*names):
    return items(*({"metadata": {"name": n}} for n in names))


def gpu_attrs(driver_version="570.172.08"):
    return lambda i: {"resource.kubernetes.io/pcieRoot": {"string": f"pci0000:{i:02x}"},
                      "driverVersion": {"version": driver_version}}


def nic_attrs():
    return lambda i: {"resource.kubernetes.io/pcieRoot": {"string": f"pci0000:{i:02x}"}}


GPU_NODES = [f"gpu-{i}" for i in range(4)]


def healthy_gke(driver_version="570.172.08"):
    """kubectl responses for a GKE DRA cluster that can run wide-ep r1-0528."""
    nodes = [node(n, cpu="190", memory="1800Gi", eph="2Ti", taints=[GPU_TAINT]) for n in GPU_NODES]
    nodes.append(node("cpu-0", cpu="15890m", memory="57Gi"))
    slices = [resource_slice(n, "gpu.nvidia.com", 8, gpu_attrs(driver_version)) for n in GPU_NODES]
    slices += [resource_slice(n, "mrdma.google.com", 8, nic_attrs()) for n in GPU_NODES]
    return {
        ("api-versions",): "apps/v1\ndisaggregatedset.x-k8s.io/v1\nleaderworkerset.x-k8s.io/v1\n"
                           "inference.networking.k8s.io/v1\nresource.k8s.io/v1\nv1\n",
        ("get", "customresourcedefinitions"): crd_list(
            "disaggregatedsets.disaggregatedset.x-k8s.io",
            "leaderworkersets.leaderworkerset.x-k8s.io",
            "inferencepools.inference.networking.k8s.io"),
        ("get", "nodes"): items(*nodes),
        ("get", "resourceslices.resource.k8s.io"): items(*slices),
        ("get", "deviceclasses.resource.k8s.io"): items(device_class("gpu.nvidia.com"),
                                                         device_class("mrdma.google.com")),
        ("get", "deployments", "-A", "-l", "control-plane=controller-manager"): items(lws_deployment()),
        ("get", "validatingwebhookconfigurations"): items(webhook_config("vdisaggregatedset.kb.io")),
    }


def healthy_coreweave():
    nodes = [node(n, cpu="120", memory="2000Gi", eph="3Ti", gpu=8, rdma=1) for n in GPU_NODES]
    nodes.append(node("cpu-0", cpu="32", memory="128Gi"))
    responses = healthy_gke()
    responses[("api-versions",)] = responses[("api-versions",)].replace("resource.k8s.io/v1\n", "")
    responses[("get", "nodes")] = items(*nodes)
    del responses[("get", "resourceslices.resource.k8s.io")]
    del responses[("get", "deviceclasses.resource.k8s.io")]
    return responses


def state(responses):
    return preflight.collect_state(FakeRunner(responses))
