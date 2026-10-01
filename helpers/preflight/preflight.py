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
