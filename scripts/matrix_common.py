#!/usr/bin/env python3
"""Shared helpers for the testing matrices in release/README.md.

Both sync-nightly-matrix.py (a live mirror of main) and sync-release-matrix.py
(a frozen per-release snapshot) build the same guide x provider grid out of the
``nightly-e2e-*.yaml`` workflow files. This module holds the pieces they share:
workflow discovery, the guide/provider configuration, and sentinel I/O.
"""

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
README_PATH = REPO_ROOT / "release" / "README.md"

# ---------------------------------------------------------------------------
# Badge sources
# ---------------------------------------------------------------------------

REPO_URL = "https://github.com/llm-d/llm-d"
BADGE_BASE = f"{REPO_URL}/actions/workflows"
SHIELDS_ENDPOINT = "https://img.shields.io/endpoint?url=https://llm-d.github.io/llm-d/badges"

# The matrix a run feeds. "nightly" is the live mirror of main; anything else is a
# release branch name (e.g. "release-0.9"), which is also what the e2e workflows
# check out.
NIGHTLY_MATRIX_TYPE = "nightly"


def badge_endpoint(badge_name: str, matrix_type: str = NIGHTLY_MATRIX_TYPE) -> str:
    """Shields endpoint URL for a badge, suffixed by matrix_type.

    ``nightly`` carries no suffix, so its badge files keep the names they have
    always had. This mirrors reusable-update-badge.yaml in llm-d-infra, which
    only appends ``_{matrix_type}`` when matrix_type is set and not "nightly" —
    the two must agree or the matrices point at files nobody writes.
    """
    suffix = "" if matrix_type in ("", NIGHTLY_MATRIX_TYPE) else f"_{matrix_type}"
    return f"{SHIELDS_ENDPOINT}/{badge_name}{suffix}.json"

# ---------------------------------------------------------------------------
# Table structure
# ---------------------------------------------------------------------------

PROVIDERS = ["ibm", "cks", "gke", "amd", "intel"]
PROVIDER_LABELS = {"ibm": "IBM", "cks": "CKS", "gke": "GKE", "amd": "AMD", "intel": "Intel"}

ACCELERATOR_LABELS = {
    "gpu": "GPU",
    "tpu": "TPU",
    "rocm": "ROCm",
    "xpu": "XPU",
}

ENGINE_LABELS = {
    "vllm": "vLLM",
    "sglang": "SGLang",
    "trtllm": "TRTLLM",
}

# Connectors that need to appear in the badge label because engine+accelerator
# alone does not tell two lanes in the same cell apart (the ROCm P/D lanes are
# both vLLM on ROCm and differ only by transport). Deliberately an allow-list:
# connectors like "native"/"lmcache" already get their own GUIDES row, so adding
# them here would change labels that are correct today.
CONNECTOR_LABELS = {
    "moriio": "MORI",
    "nixl": "NIXL",
}


def accel_engine_label(engine: str, accelerator: str, connector: str = "") -> str:
    """Render a badge label like "vLLM GPU" / "SGLang GPU" / "vLLM ROCm NIXL".

    Combining the engine with the accelerator keeps multiple same-accelerator
    engines within one cell distinguishable, and is shared so the nightly and
    release matrices label badges identically. The connector is appended only for
    the values in CONNECTOR_LABELS, where engine+accelerator would otherwise
    collide.
    """
    engine_label = ENGINE_LABELS.get(engine, engine.upper())
    accelerator_label = ACCELERATOR_LABELS.get(accelerator, accelerator.upper())
    label = f"{engine_label} {accelerator_label}"
    connector_label = CONNECTOR_LABELS.get(connector)
    return f"{label} {connector_label}" if connector_label else label

# (display_name, guide_path, workflow_slugs, connector_filter)
# workflow_slugs: a string or tuple of strings to match parsed guide slugs.
# connector_filter: None matches any connector; a string matches only that variant.
GUIDES = [
    ("Optimized Baseline", "../guides/optimized-baseline/README.md", "optimized-baseline", None),
    ("Precise Prefix Cache Routing", "../guides/precise-prefix-cache-routing/README.md", ("precise-prefix-cache-routing", "precise-prefix-cache"), None),
    ("P/D Disaggregation", "../guides/pd-disaggregation/README.md", "pd-disaggregation", None),
    ("Wide Expert Parallelism", "../guides/wide-ep-lws/README.md", "wide-ep-lws", None),
    ("Tiered Prefix Cache (CPU Offloading)", "../guides/tiered-prefix-cache/README.md", "tiered-prefix-cache", "native"),
    ("Tiered Prefix Cache (LMCache)", "../guides/tiered-prefix-cache/README.md", "tiered-prefix-cache", "lmcache"),
    ("Predicted Latency-Based Routing", "../guides/predicted-latency-routing/README.md", "predicted-latency-routing", None),
    ("Flow Control", "../guides/flow-control/README.md", "flow-control", None),
    ("Workload Autoscaling (WVA)", "../guides/workload-autoscaling/README.md", "workload-autoscaling", None),
    ("Fast Model Actuation (FMA)", "../guides/fast-model-actuation/README.md", "fast-model-actuation", None),
    ("Fast Model Actuation + KEDA Autoscaling", "../guides/fast-model-actuation-keda/README.md", "fast-model-actuation-keda", None),
]

# ---------------------------------------------------------------------------
# Workflow filename convention:
#   nightly-e2e-{guide_slug}-{provider}[-{env}...]-{offload_dest}-{accelerator}-{engine}-{connector}.yaml
#
# Some providers add extra segments between the provider and offload_dest (for
# example the AMD ROCm runs use "-amd-ci-acc-..."), so the trailing four fields
# are what identify the run; anything in between is environment detail.
# ---------------------------------------------------------------------------

WORKFLOW_PREFIX = "nightly-e2e-"


def _extract_badge_name(path: Path) -> str | None:
    """Extract the badge_name value from a workflow YAML file."""
    content = path.read_text(encoding="utf-8")
    m = re.search(r"badge_name:\s*(\S+)", content)
    return m.group(1) if m else None


def _parse_workflow_stem(stem: str) -> tuple[str, str, str, str, str, str] | None:
    """Parse a workflow stem into its components.

    Returns (guide_slug, provider, offload_dest, accelerator, engine, connector)
    or None if parsing fails. Extra segments between the provider and
    offload_dest are ignored, so the trailing four fields are what matter.
    """
    for provider in PROVIDERS:
        marker = f"-{provider}-"
        idx = stem.find(marker)
        if idx == -1:
            continue

        guide_slug = stem[:idx]
        suffix = stem[idx + len(marker):]
        parts = suffix.split("-")
        if len(parts) < 4:
            continue

        offload_dest, accelerator, engine, connector = parts[-4:]
        return (guide_slug, provider, offload_dest, accelerator, engine, connector)

    return None


def discover_workflows() -> dict[tuple[str, str, str], list[tuple[str, str, str, str]]]:
    """Scan the workflows directory and return a mapping.

    Returns:
        dict keyed by (guide_slug, provider, connector) -> sorted list of
        (accelerator, filename, badge_name, engine, connector) tuples.
    """
    result: dict[tuple[str, str, str], list[tuple[str, str, str, str, str]]] = {}

    for path in sorted(WORKFLOWS_DIR.glob(f"{WORKFLOW_PREFIX}*.yaml")):
        filename = path.name
        stem = filename.removeprefix(WORKFLOW_PREFIX).removesuffix(".yaml")

        parsed = _parse_workflow_stem(stem)
        if parsed is None:
            continue

        badge_name = _extract_badge_name(path)
        if badge_name is None:
            continue

        guide_slug, provider, _offload_dest, accelerator, engine, connector = parsed
        key = (guide_slug, provider, connector)
        # engine and connector are appended last so the existing
        # (accelerator, filename) sort order — and thus badge order within a
        # cell — is unaffected.
        result.setdefault(key, []).append(
            (accelerator, filename, badge_name, engine, connector)
        )

    for entries in result.values():
        entries.sort()

    return result


def iter_provider_entries(workflows, guide_slugs, provider, connector_filter):
    """Yield (accelerator, filename, badge_name, engine, connector) per cell.

    Encapsulates the connector-filter matching so both matrices resolve cells
    identically.
    """
    if isinstance(guide_slugs, str):
        guide_slugs = (guide_slugs,)

    if connector_filter is not None:
        for slug in guide_slugs:
            key = (slug, provider, connector_filter)
            yield from workflows.get(key, [])
    else:
        for key, entries in workflows.items():
            if key[0] in guide_slugs and key[1] == provider:
                yield from entries


# ---------------------------------------------------------------------------
# README manipulation
# ---------------------------------------------------------------------------


def read_readme() -> str:
    return README_PATH.read_text(encoding="utf-8")


def extract_matrix(content: str, start: str, end: str) -> str | None:
    pattern = re.compile(
        re.escape(start) + r"\n(.*?)\n" + re.escape(end),
        re.DOTALL,
    )
    m = pattern.search(content)
    return m.group(1) if m else None


def replace_matrix(content: str, new_body: str, start: str, end: str) -> str:
    pattern = re.compile(
        re.escape(start) + r"\n.*?\n" + re.escape(end),
        re.DOTALL,
    )
    # Use a function replacement so backslashes/group refs in new_body stay literal.
    return pattern.sub(lambda _: f"{start}\n{new_body}\n{end}", content)
