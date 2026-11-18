#!/usr/bin/env python3
"""Post-benchmark verifier for tiered-prefix-cache.

The scenario tiers vLLM's KV cache to a second layer so hot prefixes survive
GPU eviction. The guide (guides/tiered-prefix-cache/README.md) exercises two variants:

  - Storage offloading (VARIANT=fs): tier lives on a RWX PVC mounted at
    OFFLOAD_MOUNT. This verifier asserts the PVC is Bound and that
    kv-cache files landed on it — the same manual "Verify KV cache is
    offloaded to storage" step from the guide's README §4.
  - CPU offloading (VARIANT=cpu): tier lives in host RAM only. No PVC-side
    checks apply; only the metric-based checks below run.

The mode is selected via `LLMDBENCH_CICD_OFFLOADING_TARGET` at the job level
in reusable-ci-nightly-benchmark.yaml (`fs` / `cpu`).

Metric checks (both variants):
  - vllm:kv_offload_store_bytes_total.max  > 0 : GPU wrote at least once into
    the offload tier during the run (tier is being populated).
  - (not tested currently) vllm:kv_offload_load_bytes_total.max   > 0 : the
    offload tier served at least one read back to GPU (tier is being *used*,
    not just filled).
  Both are `check_per_pod` with the default `combine=max`, so "any pod hit
  it" is enough — we don't require every replica to have been exercised.

XPU LMCache MP pods are identified by both `llm-d.ai/accelerator-variant=xpu`
and `llm-d.ai/lmcache-mode=mp`.
Their offload counters are read from the node-local cache servers on port
8080; filesystem checks inspect those servers' MP directory on the PVC.
These checks prove stores, not retrieval after HBM eviction.

All output is plain text (no markdown) so raw CI logs stay readable.
"""
from __future__ import annotations

import math
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import verify_helpers as v  # noqa: E402


OFFLOAD_MOUNT = "/mnt/files-storage"
KV_CACHE_DIR = f"{OFFLOAD_MOUNT}/kv-cache"
MP_KV_CACHE_DIR = f"{KV_CACHE_DIR}/mp"
XPU_MP_CACHE_SELECTOR = (
    "app.kubernetes.io/name=lmcache,"
    "app.kubernetes.io/instance=tiered-prefix-cache-xpu"
)


def is_storage_mode() -> bool:
    """True when the job was launched with storage offloading selected.

    `LLMDBENCH_CICD_OFFLOADING_TARGET` is a job-level env in
    reusable-ci-nightly-benchmark.yaml: `fs` = storage (PVC-backed),
    `cpu` = RAM-only.
    """
    return os.environ.get("LLMDBENCH_CICD_OFFLOADING_TARGET", "").lower() == "fs"


def is_xpu_lmcache_mp(namespace: str, pod: str) -> bool:
    """Limit XPU cache-server checks to XPU MP clients using pod labels."""
    return v.kubectl([
        "get", "pod", pod, "-n", namespace, "-o",
        r'jsonpath={.metadata.labels.llm-d\.ai/accelerator-variant}{" "}'
        r'{.metadata.labels.llm-d\.ai/lmcache-mode}',
    ]).split() == ["xpu", "mp"]


def prometheus_counter(text: str, name: str) -> float | None:
    """Sum a counter's label series; None means it has not emitted a sample."""
    pattern = re.compile(rf"^{re.escape(name)}(?:\{{.*?\}})?\s+(\S+)")
    samples = []
    for line in text.splitlines():
        match = pattern.match(line)
        if match:
            value = float(match[1])
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"invalid counter sample: {line}")
            samples.append(value)
    return sum(samples) if samples else None


def check_lmcache_mp_stores(
    namespace: str, pods: list[str], storage_mode: bool,
) -> list[v.Check]:
    """Require successful MP stores on at least one cache server."""
    if not pods:
        return [v.Check("LMCache MP servers", False, "no cache-server pods found")]
    snapshots = {
        pod: v.kubectl([
            "get", "--raw",
            f"/api/v1/namespaces/{namespace}/pods/{pod}:8080/proxy/metrics",
        ])
        for pod in pods
    }
    names = ["lmcache_mp_l1_write_chunks_total"]
    if storage_mode:
        names.append("lmcache_mp_l2_store_completed_objects_chunks_total")
    checks = []
    for name in names:
        total = 0.0
        errors = []
        for pod, text in snapshots.items():
            if not text:
                errors.append(f"{pod}: no metrics")
                continue
            try:
                total += prometheus_counter(text, name) or 0.0
            except ValueError as exc:
                errors.append(f"{pod}: {exc}")
        checks.append(v.Check(
            name=f"{name} (all MP servers)",
            passed=total > 0 and not errors,
            detail=f"stored chunks={total:g}" + (f"; {'; '.join(errors)}" if errors else ""),
        ))
    return checks


# ---------------------------------------------------------------------------
# PVC checks (storage-offloading variant only)
# ---------------------------------------------------------------------------

def find_offload_pvc(namespace: str, pod: str) -> str | None:
    """Return the PVC name that backs OFFLOAD_MOUNT on `pod`, or None if the
    pod has no volume mounted there.

    Two-step jsonpath because volumeMounts and volumes are separate lists:
      1. Find the *volume name* mounted at OFFLOAD_MOUNT inside any container.
      2. Look up that volume in `.spec.volumes` and read its
         `persistentVolumeClaim.claimName`.
    """
    vols = v.kubectl([
        "get", "pod", pod, "-n", namespace, "-o",
        f'jsonpath={{.spec.containers[*].volumeMounts[?(@.mountPath=="{OFFLOAD_MOUNT}")].name}}',
    ]).split()
    if not vols:
        return None
    claim = v.kubectl([
        "get", "pod", pod, "-n", namespace, "-o",
        f'jsonpath={{.spec.volumes[?(@.name=="{vols[0]}")].persistentVolumeClaim.claimName}}',
    ]).strip()
    return claim or None


def check_pvc_is_bound(namespace: str, pod: str) -> v.Check:
    """Assert the PVC backing OFFLOAD_MOUNT on `pod` has phase=Bound.

    Only the offload PVC — we don't care about model-download or metrics
    PVCs that may share the namespace. Fails if:
      - No volume is mounted at OFFLOAD_MOUNT (misconfigured pod spec), or
      - The PVC exists but isn't Bound.
    """
    claim = find_offload_pvc(namespace, pod)
    if not claim:
        return v.Check(
            name="Offload PVC bound",
            passed=False,
            detail=f"no PVC mounted at {OFFLOAD_MOUNT} on {pod}",
        )
    phase = v.kubectl([
        "get", "pvc", claim, "-n", namespace, "-o", "jsonpath={.status.phase}",
    ]).strip()
    return v.Check(
        name="Offload PVC bound",
        passed=phase == "Bound",
        detail=f"pvc/{claim} phase={phase or '<none>'}",
    )


def check_pvc_has_data(
    namespace: str, pod: str, cache_dir: str = KV_CACHE_DIR,
) -> v.Check:
    """Assert kv-cache blocks are actually on the PVC by counting files
    under KV_CACHE_DIR from inside the pod, and report their total size.

    Mirrors the guide's storage-offload verification step.

    Distinguishes three failure modes so the CI log points at the right
    thing: kubectl exec failed, the mount is missing on the pod, or the
    directory exists but is empty.
    """
    name = "PVC has KV cache data"
    lines = v.kubectl(
        ["exec", "-n", namespace, pod, "--", "sh", "-c",
         f"[ -d {cache_dir} ] || {{ echo MISSING; exit 0; }}; "
         f"find {cache_dir} -type f 2>/dev/null | wc -l; "
         f"du -sh {cache_dir} 2>/dev/null | awk '{{print $1}}'"],
        timeout=30,
    ).splitlines()

    if not lines:
        return v.Check(name=name, passed=False,
                       detail=f"kubectl exec failed on {pod}")
    if lines == ["MISSING"]:
        return v.Check(name=name, passed=False,
                       detail=f"{cache_dir} does not exist on {pod}")
    try:
        n = int(lines[0])
    except (ValueError, IndexError):
        return v.Check(name=name, passed=False,
                       detail=f"unparseable exec output on {pod}: {lines!r}")
    size = lines[1] if len(lines) > 1 else "?"
    return v.Check(
        name=name,
        passed=n > 0,
        detail=f"{n} files ({size}) at {cache_dir} on {pod}",
    )


# ---------------------------------------------------------------------------

def main() -> int:
    """Entry point: load metrics, run PVC + metric checks, print report.

    Exit code: 0 if every check passed, 1 otherwise (or on setup failures
    like a missing namespace / kubectl / results dir).
    """
    env = v.workflow_env()
    if not env["namespace"]:
        v.error("LLMDBENCH_CICD_NS not set")
        return 1

    namespace = env["namespace"]

    if not shutil.which("kubectl"):
        v.error("kubectl not on PATH")
        return 1

    exp_dirs = v.find_results_dirs(env["workspace"], env["namespace"])
    if not exp_dirs:
        return 1

    # Assume single experiment dir for this workflow
    results_dir = exp_dirs[0]

    metrics = v.MetricsSummary.load(results_dir)
    if metrics is None:
        return 1

    print(f"=== tiered-prefix-cache — {env['scenario']} / {env['workload'] or '?'} ===")
    print(f"Namespace: {env['namespace']}")
    print()

    storage_mode = is_storage_mode()
    target = os.environ.get("LLMDBENCH_CICD_OFFLOADING_TARGET", "<unset>")
    print(f"Offloading target (LLMDBENCH_CICD_OFFLOADING_TARGET): {target}  "
          f"- {'STORAGE' if storage_mode else 'CPU'} offloading")
    print()

    checks: list[v.Check] = []

    pods = v.get_model_pods(namespace)
    if not pods:
        v.error(f"No model pods found in namespace {namespace}")
        return 1
    if is_xpu_lmcache_mp(namespace, pods[0]):
        cache_pods = [
            name.removeprefix("pod/")
            for name in v.kubectl([
                "get", "pod", "-n", namespace, "-l", XPU_MP_CACHE_SELECTOR, "-o", "name",
            ]).split()
        ]
        checks.extend(check_lmcache_mp_stores(namespace, cache_pods, storage_mode))
        if storage_mode and cache_pods:
            checks.append(check_pvc_is_bound(namespace, cache_pods[0]))
            checks.append(check_pvc_has_data(namespace, cache_pods[0], MP_KV_CACHE_DIR))
    else:
        # Other overlays retain their model-pod PVC and vLLM metric checks.
        if storage_mode:
            checks.append(check_pvc_is_bound(namespace, pods[0]))
            checks.append(check_pvc_has_data(namespace, pods[0]))

        vllm_version = v.get_vllm_version(namespace, pods[0])
        if vllm_version is None:
            v.error(f"Assuming vLLM < 0.24.0. (failed to get vllm version from pod {pods[0]})")
            old_metrics = True
            version_str = "<unknown>"
        else:
            old_metrics = vllm_version < (0, 24, 0)
            version_str = ".".join(str(x) for x in vllm_version)
        checks.append(v.Check(
            name=f"vLLM version - {version_str}, kv-cache metrics (new/old)",
            passed=True,
            detail=f"{'old' if old_metrics else 'new'}",
        ))
        if old_metrics:
            checks.append(metrics.check_per_pod("vllm:kv_offload_total_bytes_total", "max", ">", 0.0))
        else:
            checks.append(metrics.check_per_pod("vllm:kv_offload_store_bytes_total", "max", ">", 0.0))
            # Retrieval still needs an HBM eviction workload.

    # Smoke check.
    checks.append(metrics.check_aggregated("vllm:num_requests_running", "count", ">", 0))

    passed = v.verify_checks(env, metrics, checks)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
