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
