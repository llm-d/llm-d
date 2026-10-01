#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["pyyaml"]
# ///
"""Read-only preflight check for llm-d guides.

Renders a guide's model-server overlay with `kubectl kustomize`, derives what
its pods will request, reads the cluster with `kubectl get`, and reports
PASS / WARN / FAIL / INFO per prerequisite before anything is deployed. It
never changes the cluster.

    python3 helpers/preflight/preflight.py guides/wide-ep \\
        --overlay modelserver/gpu/vllm-deepseek-r1-0528/gke

Exit codes: 0 no FAIL, 1 at least one FAIL, 2 usage or environment error.
Guide-specific requirements that can't be read from the manifests live in
`<guide>/preflight.yaml`. See helpers/preflight/README.md.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    sys.stderr.write("preflight: PyYAML is required: python3 -m pip install pyyaml\n")
    sys.exit(2)


class PreflightError(Exception):
    """A usage or environment problem, reported with exit code 2."""


# ---------------------------------------------------------------------------
# Quantities and scheduling primitives
# ---------------------------------------------------------------------------

_SUFFIXES = {
    "Ki": Decimal(2**10), "Mi": Decimal(2**20), "Gi": Decimal(2**30),
    "Ti": Decimal(2**40), "Pi": Decimal(2**50), "Ei": Decimal(2**60),
    "n": Decimal("1e-9"), "u": Decimal("1e-6"), "m": Decimal("1e-3"),
    "k": Decimal(10**3), "M": Decimal(10**6), "G": Decimal(10**9),
    "T": Decimal(10**12), "P": Decimal(10**15), "E": Decimal(10**18),
}
_QUANTITY = re.compile(
    r"^([+-]?[0-9.]+)([eE][+-]?[0-9]+)?(Ki|Mi|Gi|Ti|Pi|Ei|n|u|m|k|M|G|T|P|E)?$"
)
_BINARY_UNITS = (("Ti", 2**40), ("Gi", 2**30), ("Mi", 2**20), ("Ki", 2**10))
_BYTE_RESOURCES = ("memory", "ephemeral-storage")


def parse_quantity(value: Any) -> Decimal:
    """Parse a Kubernetes resource quantity ("8000m", "512Gi", 32) into a Decimal."""
    match = _QUANTITY.match(str(value).strip())
    if not match:
        raise ValueError(f"not a Kubernetes quantity: {value!r}")
    number, exponent, suffix = match.groups()
    try:
        result = Decimal(number + (exponent or ""))
    except InvalidOperation as exc:
        raise ValueError(f"not a Kubernetes quantity: {value!r}") from exc
    return result * _SUFFIXES[suffix] if suffix else result


def format_quantity(name: str, value: Decimal) -> str:
    """Render a quantity for humans: binary units for bytes, plain numbers otherwise."""
    if name in _BYTE_RESOURCES:
        for suffix, factor in _BINARY_UNITS:
            if value >= factor:
                return f"{(value / factor).quantize(Decimal('0.1')).normalize():f}{suffix}"
    return f"{value.normalize():f}"


@dataclass(frozen=True)
class Toleration:
    key: str = ""
    operator: str = "Equal"
    value: str = ""
    effect: str = ""


@dataclass(frozen=True)
class Taint:
    key: str
    value: str
    effect: str


def tolerates(tolerations: Iterable[Toleration], taint: Taint) -> bool:
    """Whether any toleration matches the taint, following Kubernetes semantics."""
    for tol in tolerations:
        if tol.effect and tol.effect != taint.effect:
            continue
        if tol.operator == "Exists" and tol.key in ("", taint.key):
            return True
        if tol.operator != "Exists" and tol.key == taint.key and tol.value == taint.value:
            return True
    return False
# ---------------------------------------------------------------------------
# Requirements derived from the rendered overlay
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PodReq:
    role: str
    requests: Mapping[str, Decimal]
    tolerations: tuple[Toleration, ...]
    devices: Mapping[str, int]  # DRA device class -> devices per pod


@dataclass(frozen=True)
class Requirements:
    apis: frozenset[str]  # group/version of every rendered object outside the core API
    pods: tuple[PodReq, ...]
    rdma_mode: str  # "dra", "device-plugin" or "none"
    notes: tuple[str, ...]  # parts of the render that can't be checked


def parse_render(text: str) -> Requirements:
    """Turn `kubectl kustomize` output into what the model-server pods will request."""
    docs = [d for d in yaml.safe_load_all(text) if isinstance(d, dict)]
    sets = [d for d in docs if d.get("kind") == "DisaggregatedSet"]
    if not sets:
        raise PreflightError("unsupported overlay: no DisaggregatedSet in the rendered manifests")
    templates = {d["metadata"]["name"]: d for d in docs if d.get("kind") == "ResourceClaimTemplate"}
    notes: list[str] = []
    pods = [pod for ds in sets for role in ds["spec"]["roles"] for pod in _role_pods(role, templates, notes)]
    return Requirements(
        apis=frozenset(d["apiVersion"] for d in docs if "/" in d.get("apiVersion", "")),
        pods=tuple(pods),
        rdma_mode=_rdma_mode(pods),
        notes=tuple(dict.fromkeys(notes)),
    )


def _role_pods(role: Mapping, templates: Mapping, notes: list[str]) -> list[PodReq]:
    spec = role["spec"]
    lwt = spec["leaderWorkerTemplate"]
    size = int(lwt.get("size", 1))
    worker = lwt["workerTemplate"]
    name = role.get("name", "role")
    leader = _pod_req(f"{name} leader", lwt.get("leaderTemplate", worker), templates, notes)
    group = [leader] + [_pod_req(f"{name} worker", worker, templates, notes)] * (size - 1)
    return group * int(spec.get("replicas", 1))


def _pod_req(role: str, template: Mapping, templates: Mapping, notes: list[str]) -> PodReq:
    spec = template.get("spec") or {}
    requests: dict[str, Decimal] = {}
    # Sidecars (init containers with restartPolicy: Always) run alongside the main containers.
    sidecars = [c for c in spec.get("initContainers") or [] if c.get("restartPolicy") == "Always"]
    for container in [*(spec.get("containers") or []), *sidecars]:
        resources = container.get("resources") or {}
        # Kubernetes defaults a missing request to the limit.
        merged = {**(resources.get("limits") or {}), **(resources.get("requests") or {})}
        for name, quantity in merged.items():
            if quantity is not None:
                requests[name] = requests.get(name, Decimal(0)) + parse_quantity(quantity)
    tolerations = tuple(
        Toleration(t.get("key", ""), t.get("operator", "Equal"), str(t.get("value", "")), t.get("effect", ""))
        for t in spec.get("tolerations") or []
    )
    return PodReq(role, requests, tolerations, _claimed_devices(spec, templates, notes))


def _claimed_devices(spec: Mapping, templates: Mapping, notes: list[str]) -> dict[str, int]:
    devices: dict[str, int] = {}
    for claim in spec.get("resourceClaims") or []:
        name = claim.get("resourceClaimTemplateName")
        if name not in templates:
            notes.append(f"resource claim {claim.get('name')!r} uses "
                         f"{name or 'a pre-created claim'!r}, which is not in the rendered "
                         "manifests; its devices are not checked")
            continue
        for request in _template_requests(name, templates[name]):
            exactly = request.get("exactly")
            if exactly is None:
                form = "firstAvailable" if "firstAvailable" in request else "an unknown form"
                notes.append(f"claim template {name!r} request {request.get('name')!r} "
                             f"uses {form}; not checked")
                continue
            device_class = exactly["deviceClassName"]
            devices[device_class] = devices.get(device_class, 0) + int(exactly.get("count", 1))
    return devices


def _template_requests(name: str, template: Mapping) -> list[Mapping]:
    try:
        return template["spec"]["spec"]["devices"]["requests"]
    except (KeyError, TypeError) as exc:
        raise PreflightError(f"unsupported ResourceClaimTemplate {name!r}: "
                             "no spec.spec.devices.requests") from exc


def _rdma_mode(pods: Iterable[PodReq]) -> str:
    pods = list(pods)
    if any(p.devices for p in pods):
        return "dra"
    if any(name.startswith("rdma/") for p in pods for name in p.requests):
        return "device-plugin"
    return "none"
# ---------------------------------------------------------------------------
# Guide requirements that can't be derived (<guide>/preflight.yaml)
# ---------------------------------------------------------------------------

_SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")


def parse_semver(text: str) -> tuple[int, int, int] | None:
    match = _SEMVER.match(text or "")
    if not match:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


@dataclass(frozen=True)
class RouterSize:
    cpu: Decimal
    memory: Decimal


@dataclass(frozen=True)
class GuideConfig:
    crds: tuple[str, ...]
    lws_min_version: tuple[int, int, int]
    driver_max_major_exclusive: int
    driver_severity: str  # "warn" or "fail"
    router_standalone: RouterSize
    router_gateway: RouterSize
    docs: Mapping[str, str]


def load_guide_config(path: Path) -> GuideConfig:
    if not path.is_file():
        raise PreflightError(f"{path}: not found; this guide has no preflight requirements yet")
    data = yaml.safe_load(path.read_text())
    try:
        severity = data["gpuDriver"].get("severity", "warn")
        if severity not in ("warn", "fail"):
            raise PreflightError(f"{path}: gpuDriver.severity must be 'warn' or 'fail', got {severity!r}")
        min_version = parse_semver(str(data["lws"]["minVersion"]))
        if min_version is None:
            raise ValueError(f"lws.minVersion is not a version: {data['lws']['minVersion']!r}")
        return GuideConfig(
            crds=tuple(data.get("crds") or ()),
            lws_min_version=min_version,
            driver_max_major_exclusive=int(data["gpuDriver"]["maxMajorExclusive"]),
            driver_severity=severity,
            router_standalone=_router_size(data["router"]["standalone"]),
            router_gateway=_router_size(data["router"]["gateway"]),
            docs=dict(data.get("docs") or {}),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PreflightError(f"{path}: invalid preflight config: {exc!r}") from exc


def _router_size(data: Mapping) -> RouterSize:
    return RouterSize(parse_quantity(data["cpu"]), parse_quantity(data["memory"]))
# ---------------------------------------------------------------------------
# Reading the cluster (read-only kubectl)
# ---------------------------------------------------------------------------


class KubectlError(Exception):
    def __init__(self, args: tuple[str, ...], returncode: int, stderr: str):
        super().__init__(f"kubectl {' '.join(args)}: {stderr or f'exit {returncode}'}")
        self.stderr = stderr

    @property
    def forbidden(self) -> bool:
        return "forbidden" in self.stderr.lower()

    @property
    def unknown_resource(self) -> bool:
        return "doesn't have a resource type" in self.stderr


class Runner:
    """The only code that runs kubectl. Tests replace it with a fake."""

    def __init__(self, context: str | None = None, kubectl: str = "kubectl"):
        self._base = [kubectl, *(["--context", context] if context else [])]

    def text(self, *args: str) -> str:
        try:
            proc = subprocess.run([*self._base, *args], capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired as exc:
            raise KubectlError(args, -1, f"timed out after {exc.timeout}s") from exc
        if proc.returncode != 0:
            raise KubectlError(args, proc.returncode, proc.stderr.strip())
        return proc.stdout

    def json(self, *args: str) -> dict:
        return json.loads(self.text(*args, "-o", "json"))


@dataclass(frozen=True)
class Node:
    name: str
    allocatable: Mapping[str, Decimal]
    taints: tuple[Taint, ...]
    unschedulable: bool
    labels: Mapping[str, str]
    annotations: Mapping[str, str]


@dataclass(frozen=True)
class Device:
    driver: str
    node: str
    attributes: Mapping[str, Any]  # attribute name -> unwrapped value


@dataclass(frozen=True)
class LwsController:
    namespace: str
    image: str
    version_label: str  # app.kubernetes.io/version, if the install sets it


@dataclass(frozen=True)
class ClusterState:
    """What preflight read. None means the list was denied or not served."""

    api_versions: frozenset[str]
    crds: frozenset[str] | None
    nodes: tuple[Node, ...] | None
    devices: tuple[Device, ...] | None
    device_classes: Mapping[str, str | None] | None  # class -> driver from its CEL selector
    lws: tuple[LwsController, ...] | None
    webhooks: frozenset[str] | None
    denied: tuple[str, ...]


DRA_API = "resource.k8s.io/v1"
_DRIVER_SELECTOR = re.compile(r"""device\.driver\s*==\s*['"]([^'"]+)['"]""")
_LWS_IMAGE = re.compile(r"(^|/)lws(?=[:@]|$)")


def collect_state(runner: Runner) -> ClusterState:
    api_versions = frozenset(runner.text("api-versions").split())
    denied: list[str] = []

    def items(*args: str) -> list | None:
        try:
            return runner.json("get", *args)["items"]
        except KubectlError as exc:
            if not (exc.forbidden or exc.unknown_resource):
                raise
            denied.append(f"{args[0]} ({exc.stderr.splitlines()[-1] if exc.stderr else 'denied'})")
            return None

    dra = DRA_API in api_versions
    crds = items("customresourcedefinitions")
    nodes = items("nodes")
    slices = items("resourceslices.resource.k8s.io") if dra else []
    classes = items("deviceclasses.resource.k8s.io") if dra else []
    deployments = items("deployments", "-A", "-l", "control-plane=controller-manager")
    webhooks = items("validatingwebhookconfigurations")
    return ClusterState(
        api_versions=api_versions,
        crds=None if crds is None else frozenset(c["metadata"]["name"] for c in crds),
        nodes=None if nodes is None else tuple(_node(n) for n in nodes),
        devices=None if slices is None else tuple(d for s in slices for d in _devices(s)),
        device_classes=None if classes is None else {c["metadata"]["name"]: _class_driver(c) for c in classes},
        lws=None if deployments is None else tuple(_lws_controllers(deployments)),
        webhooks=None if webhooks is None else frozenset(
            w["name"] for cfg in webhooks for w in cfg.get("webhooks") or []),
        denied=tuple(denied),
    )


def _node(obj: Mapping) -> Node:
    meta, spec, status = obj["metadata"], obj.get("spec") or {}, obj.get("status") or {}
    return Node(
        name=meta["name"],
        allocatable={k: parse_quantity(v) for k, v in (status.get("allocatable") or {}).items()},
        taints=tuple(Taint(t["key"], t.get("value", ""), t["effect"]) for t in spec.get("taints") or []),
        unschedulable=bool(spec.get("unschedulable")),
        labels=meta.get("labels") or {},
        annotations=meta.get("annotations") or {},
    )


def _devices(obj: Mapping) -> Iterator[Device]:
    spec = obj.get("spec") or {}
    for device in spec.get("devices") or []:
        attributes = {name: next(iter(value.values()), None)
                      for name, value in (device.get("attributes") or {}).items()
                      if isinstance(value, dict)}
        # With perDeviceNodeSelection the node is set on each device instead of the slice.
        node = device.get("nodeName") or spec.get("nodeName") or ""
        yield Device(spec["driver"], node, attributes)


def _class_driver(obj: Mapping) -> str | None:
    for selector in (obj.get("spec") or {}).get("selectors") or []:
        match = _DRIVER_SELECTOR.search((selector.get("cel") or {}).get("expression", ""))
        if match:
            return match.group(1)
    return None


def _lws_controllers(deployments: Iterable[Mapping]) -> Iterator[LwsController]:
    for deployment in deployments:
        meta = deployment["metadata"]
        for container in deployment["spec"]["template"]["spec"].get("containers") or []:
            image = container.get("image", "")
            if _LWS_IMAGE.search(image):
                yield LwsController(meta.get("namespace", ""), image,
                                    (meta.get("labels") or {}).get("app.kubernetes.io/version", ""))
# ---------------------------------------------------------------------------
# Checks: pure functions over Requirements, GuideConfig and ClusterState
# ---------------------------------------------------------------------------

PASS, WARN, FAIL, INFO = "PASS", "WARN", "FAIL", "INFO"
GATEWAY_CRDS = ("gateways.gateway.networking.k8s.io", "httproutes.gateway.networking.k8s.io")
DS_WEBHOOK = "vdisaggregatedset.kb.io"


@dataclass(frozen=True)
class Result:
    status: str
    check: str
    detail: str
    hint: str = ""


def _see(cfg: GuideConfig, key: str) -> str:
    return f"see {cfg.docs[key]}" if key in cfg.docs else ""


def check_access(state: ClusterState, helm_found: bool) -> list[Result]:
    results = [Result(PASS, "cluster access", "kubectl reached the API server")]
    if not helm_found:
        results.append(Result(WARN, "client tools", "helm not found on PATH",
                              "the guide installs the router with helm"))
    results += [Result(WARN, "permissions", f"could not list {item}", "checks that need it are reported as WARN")
                for item in state.denied]
    return results


def check_apis(req: Requirements, cfg: GuideConfig, state: ClusterState, gateway_mode: bool) -> list[Result]:
    missing_apis = sorted(req.apis - state.api_versions)
    results = [
        Result(FAIL, "APIs", f"not served: {', '.join(missing_apis)}", _see(cfg, "prerequisites"))
        if missing_apis else Result(PASS, "APIs", f"served: {', '.join(sorted(req.apis))}")
    ]
    wanted = cfg.crds + (GATEWAY_CRDS if gateway_mode else ())
    if state.crds is None:
        results.append(Result(WARN, "CRDs", "cannot list CRDs; not checked"))
    else:
        missing = [c for c in wanted if c not in state.crds]
        results.append(Result(FAIL, "CRDs", f"missing: {', '.join(missing)}", _see(cfg, "prerequisites"))
                       if missing else Result(PASS, "CRDs", f"present: {', '.join(wanted)}"))
    return results


def _fmt_version(version: tuple[int, int, int]) -> str:
    return "v" + ".".join(str(part) for part in version)


def _image_tag(image: str) -> str:
    last = image.rsplit("/", 1)[-1].split("@", 1)[0]
    return last.split(":", 1)[1] if ":" in last else ""


def check_lws(cfg: GuideConfig, state: ClusterState) -> list[Result]:
    want = _fmt_version(cfg.lws_min_version)
    hint = _see(cfg, "prerequisites")
    if state.lws is None:
        return [Result(WARN, "LWS controller", "cannot list deployments; not checked")]
    if not state.lws:
        return [Result(WARN, "LWS controller", "no Deployment with label control-plane=controller-manager "
                       f"running an lws image; need {want} or newer", hint)]
    results = []
    for ctrl in state.lws:
        where = f"{ctrl.namespace}/{ctrl.image}"
        version = parse_semver(_image_tag(ctrl.image)) or parse_semver(ctrl.version_label)
        if version is None:
            results.append(Result(WARN, "LWS controller", f"{where}: version unknown; need {want} or newer", hint))
        elif version < cfg.lws_min_version:
            results.append(Result(FAIL, "LWS controller",
                                  f"{where}: {_fmt_version(version)} is older than {want}", hint))
        else:
            results.append(Result(PASS, "LWS controller", f"{where}: {_fmt_version(version)}"))
    return results


def check_ds_webhook(state: ClusterState) -> list[Result]:
    if state.webhooks is None:
        return [Result(WARN, "DisaggregatedSet webhook", "cannot list validating webhooks; not checked")]
    if DS_WEBHOOK in state.webhooks:
        return [Result(PASS, "DisaggregatedSet webhook", f"{DS_WEBHOOK} registered")]
    return [Result(WARN, "DisaggregatedSet webhook",
                   f"{DS_WEBHOOK} not registered; the controller still runs, but invalid DisaggregatedSets "
                   "are not rejected",
                   "installing LWS with helm: --set enableDisaggregatedSet=true")]
PCIE_ROOT = "resource.kubernetes.io/pcieRoot"
NVIDIA_DRA_DRIVER = "gpu.nvidia.com"
_DRIVER_SOURCES = (
    ("labels", "nvidia.com/cuda.driver-version.major"),  # GPU feature discovery
    ("labels", "nvidia.com/cuda.driver.major"),  # GPU feature discovery, deprecated
    ("annotations", "cloud.google.com/cuda.driver-version.major"),  # GKE device plugin
)
_HARD_EFFECTS = ("NoSchedule", "NoExecute")


def gpu_nodes(state: ClusterState) -> frozenset[str]:
    """Nodes with GPUs, whether exposed by a device plugin or by the NVIDIA DRA driver."""
    plugin = {n.name for n in state.nodes or () if n.allocatable.get("nvidia.com/gpu", 0) > 0}
    dra = {d.node for d in state.devices or () if d.driver == NVIDIA_DRA_DRIVER}
    return frozenset(plugin | dra)


def check_rdma(req: Requirements, cfg: GuideConfig, state: ClusterState) -> list[Result]:
    if req.rdma_mode == "none":
        return [Result(WARN, "RDMA", "the overlay requests no RDMA resource, so RDMA can't be verified "
                       "from the manifests", _see(cfg, "rdma"))]
    if req.rdma_mode == "device-plugin":
        return _check_rdma_device_plugin(req, cfg, state)
    return _check_rdma_dra(req, cfg, state)


def _check_rdma_device_plugin(req: Requirements, cfg: GuideConfig, state: ClusterState) -> list[Result]:
    if state.nodes is None:
        return [Result(WARN, "RDMA", "cannot list nodes; not checked")]
    resources = sorted({name for p in req.pods for name in p.requests if name.startswith("rdma/")})
    missing = [r for r in resources if not any(n.allocatable.get(r, 0) > 0 for n in state.nodes)]
    if missing:
        return [Result(FAIL, "RDMA", f"no node has allocatable {', '.join(missing)}", _see(cfg, "rdma"))]
    return [Result(PASS, "RDMA", f"{', '.join(resources)} allocatable on the cluster")]


def _check_rdma_dra(req: Requirements, cfg: GuideConfig, state: ClusterState) -> list[Result]:
    hint = _see(cfg, "gkeDra")
    if state.device_classes is None or state.devices is None:
        return [Result(WARN, "RDMA", "cannot list DeviceClasses or ResourceSlices; not checked", hint)]
    classes = sorted({c for p in req.pods for c in p.devices})
    missing = [c for c in classes if c not in state.device_classes]
    if missing:
        return [Result(FAIL, "RDMA", f"DeviceClass missing: {', '.join(missing)}", hint)]
    drivers = {c: state.device_classes[c] for c in classes if state.device_classes[c]}
    published = {d.driver for d in state.devices}
    absent = [c for c, driver in drivers.items() if driver not in published]
    if absent:
        return [Result(FAIL, "RDMA", f"no ResourceSlice publishes devices for {', '.join(absent)}", hint)]
    results = [Result(PASS, "RDMA", f"DeviceClasses {', '.join(classes)} present and devices published")]
    unmapped = [c for c in classes if c not in drivers]
    if unmapped:
        results.append(Result(WARN, "RDMA", f"cannot tell which driver serves {', '.join(unmapped)}; "
                              "its devices are not counted"))
    without_root = sorted({d.node for d in state.devices
                           if d.driver in drivers.values() and PCIE_ROOT not in d.attributes})
    if len(drivers) > 1 and without_root:
        results.append(Result(WARN, "RDMA", f"devices on {', '.join(without_root)} have no {PCIE_ROOT}; "
                              "GPU/NIC pairing by PCIe root can't be confirmed", hint))
    return results


def _driver_major(raw: Any) -> int | None:
    match = re.match(r"^\s*v?(\d+)", str(raw)) if raw not in (None, "") else None
    return int(match.group(1)) if match else None


def _driver_majors(state: ClusterState) -> dict[str, int]:
    found: dict[str, int] = {}
    for device in state.devices or ():
        major = _driver_major(device.attributes.get("driverVersion")) if device.driver == NVIDIA_DRA_DRIVER else None
        if major is not None:
            found.setdefault(device.node, major)
    for node in state.nodes or ():
        for where, key in _DRIVER_SOURCES:
            major = _driver_major(getattr(node, where).get(key))
            if node.name not in found and major is not None:
                found[node.name] = major
    return found


def check_driver(cfg: GuideConfig, state: ClusterState) -> list[Result]:
    limit = cfg.driver_max_major_exclusive
    hint = _see(cfg, "gpuDriver")
    majors = _driver_majors(state)
    if not majors:
        return [Result(WARN, "GPU driver", "driver version not found (ResourceSlice driverVersion, "
                       "GPU feature discovery labels, GKE annotations)", hint)]
    too_new = sorted(name for name, major in majors.items() if major >= limit)
    if not too_new:
        return [Result(PASS, "GPU driver", f"major {', '.join(map(str, sorted(set(majors.values()))))} "
                       f"on {len(majors)} node(s), below {limit}")]
    status = FAIL if cfg.driver_severity == "fail" else WARN
    return [Result(status, "GPU driver", f"R{limit} or newer on: {', '.join(too_new)}", hint)]
def _schedulable(node: Node, tolerations: Iterable[Toleration]) -> bool:
    tolerations = tuple(tolerations)
    return not node.unschedulable and all(
        tolerates(tolerations, t) for t in node.taints if t.effect in _HARD_EFFECTS)


def _node_capacity(node: Node, state: ClusterState) -> dict[str, Decimal]:
    capacity = dict(node.allocatable)
    for device in state.devices or ():
        if device.node == node.name:
            key = f"{device.driver} devices"
            capacity[key] = capacity.get(key, Decimal(0)) + 1
    return capacity


def _pod_demand(pod: PodReq, state: ClusterState) -> dict[str, Decimal]:
    """Per-pod demand keyed like _node_capacity. A DeviceClass whose driver is unknown
    is keyed by its own name, so a pod that needs it is never placed silently."""
    demand = {name: qty for name, qty in pod.requests.items() if qty > 0}
    for device_class, count in pod.devices.items():
        driver = (state.device_classes or {}).get(device_class) or device_class
        key = f"{driver} devices"
        demand[key] = demand.get(key, Decimal(0)) + count
    return demand


def _shortage(capacity: Mapping[str, Decimal], demand: Mapping[str, Decimal]) -> str | None:
    for name, qty in demand.items():
        have = capacity.get(name, Decimal(0))
        if have < qty:
            return f"{name} {format_quantity(name, have)} < {format_quantity(name, qty)}"
    return None


def _describe(demand: Mapping[str, Decimal]) -> str:
    return ", ".join(f"{format_quantity(name, qty)} {name}" for name, qty in sorted(demand.items()))


def check_capacity(req: Requirements, state: ClusterState) -> list[Result]:
    name = "model-server capacity"
    note = Result(INFO, name, "checked against allocatable; pods already running are not subtracted")
    dra_unreadable = req.rdma_mode == "dra" and (state.devices is None or state.device_classes is None)
    if state.nodes is None or dra_unreadable:
        return [Result(WARN, name, "cannot list nodes, ResourceSlices or DeviceClasses; not checked"), note]
    classes = state.device_classes or {}
    unmapped = sorted({c for p in req.pods for c in p.devices if c in classes and not classes[c]})
    if unmapped:
        return [Result(WARN, name, f"cannot tell which driver serves DeviceClass {', '.join(unmapped)}; "
                       "device counts not checked"), note]
    return [_place_pods(req, state), note]


def _place_pods(req: Requirements, state: ClusterState) -> Result:
    """First-fit placement of every model-server pod onto schedulable nodes."""
    free = {n.name: _node_capacity(n, state) for n in state.nodes}
    excluded: set[str] = set()
    placed, unplaced = 0, None
    for pod in req.pods:
        demand = _pod_demand(pod, state)
        target = None
        for node in state.nodes:
            if not _schedulable(node, pod.tolerations):
                excluded.add(node.name)
            elif target is None and _shortage(free[node.name], demand) is None:
                target = node.name
        if target is None:
            unplaced = unplaced or (pod, demand)
            continue
        free = {**free, target: {k: v - demand.get(k, Decimal(0)) for k, v in free[target].items()}}
        placed += 1
    total = len(req.pods)
    per_pod = _describe(_pod_demand(req.pods[0], state)) if req.pods else "nothing"
    if unplaced is None:
        return Result(PASS, "model-server capacity", f"{placed}/{total} pods fit ({per_pod} each)")
    pod, demand = unplaced
    reasons = [f"{n.name}: {_shortage(free[n.name], demand)}" for n in state.nodes
               if n.name not in excluded and _shortage(free[n.name], demand)][:6]
    detail = f"{placed}/{total} pods fit; first that doesn't: {pod.role}, needs {_describe(demand)}"
    if reasons:
        detail += "; short: " + "; ".join(reasons)
    if excluded:
        detail += f"; not schedulable for these pods (taint or cordon): {', '.join(sorted(excluded))}"
    return Result(FAIL, "model-server capacity", detail)


def check_router(cfg: GuideConfig, state: ClusterState, gateway_mode: bool) -> list[Result]:
    size = cfg.router_gateway if gateway_mode else cfg.router_standalone
    want = f"{format_quantity('cpu', size.cpu)} CPU and {format_quantity('memory', size.memory)} memory"
    if state.nodes is None:
        return [Result(WARN, "router node", "cannot list nodes; not checked")]
    if DRA_API in state.api_versions and state.devices is None:
        # DRA GPU nodes have no nvidia.com/gpu in allocatable; without ResourceSlices
        # they can't be told apart from CPU nodes.
        return [Result(WARN, "router node", "cannot list ResourceSlices, so GPU nodes can't be "
                       "excluded; not checked")]
    gpus = gpu_nodes(state)
    candidates = [n for n in state.nodes if n.name not in gpus and not n.unschedulable
                  and not any(t.effect in _HARD_EFFECTS for t in n.taints)]
    for node in candidates:
        if node.allocatable.get("cpu", 0) >= size.cpu and node.allocatable.get("memory", 0) >= size.memory:
            return [Result(PASS, "router node", f"{node.name} fits the router ({want})")]
    detail = f"no untainted non-GPU node with {want} allocatable"
    if candidates:
        biggest = max(candidates, key=lambda n: n.allocatable.get("cpu", 0))
        cpu = format_quantity("cpu", biggest.allocatable.get("cpu", Decimal(0)))
        memory = format_quantity("memory", biggest.allocatable.get("memory", Decimal(0)))
        detail += f"; largest is {biggest.name} with {cpu} CPU, {memory} memory"
    return [Result(FAIL, "router node", detail, "router requests come from guides/recipes/router/base.values.yaml")]


def check_notes(req: Requirements) -> list[Result]:
    return [Result(WARN, "manifests", note) for note in req.notes] + [
        Result(INFO, "decode memory", "out-of-memory at decode depends on runtime settings and is not checked")]


def run_checks(req: Requirements, cfg: GuideConfig, state: ClusterState,
               gateway_mode: bool, helm_found: bool) -> list[Result]:
    return [
        *check_access(state, helm_found),
        *check_apis(req, cfg, state, gateway_mode),
        *check_lws(cfg, state),
        *check_ds_webhook(state),
        *check_rdma(req, cfg, state),
        *check_driver(cfg, state),
        *check_capacity(req, state),
        *check_router(cfg, state, gateway_mode),
        *check_notes(req),
    ]
