#!/usr/bin/env python3
"""Verifier for the tiered-prefix-cache nightly.

The scenario tiers vLLM's KV cache to a second layer so hot prefixes survive
GPU eviction. The guide (in llm-d-infra/quickstart/) exercises two variants:

  - Storage offloading (VARIANT=fs): tier lives on a RWX PVC mounted at
    OFFLOAD_MOUNT. This verifier asserts the PVC is Bound and that
    kv-cache files landed on it — the same manual "Verify KV cache is
    offloaded to storage" step from the guide's README §4.
  - CPU offloading (VARIANT=cpu): tier lives in host RAM only. No PVC-side
    checks apply; only the metric-based checks below run.

The mode is selected via `LLMDBENCH_CICD_OFFLOADING_TARGET` at the job level
in reusable-ci-nightly-benchmark.yaml (`fs` / `cpu`).

Metric checks (both variants):
  - vllm:kv_offload_store_bytes.max  > 0 : GPU wrote at least once into
    the offload tier during the run (tier is being populated).
  - (not tested currently)vllm:kv_offload_load_bytes.max   > 0 : the offload tier served at
    least one read back to GPU (tier is being *used*, not just filled).
  Both are `check_per_pod` with the default `combine=max`, so "any pod hit
  it" is enough — we don't require every replica to have been exercised.

All output is plain text (no markdown) so raw CI logs stay readable.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import verify_helpers as v  # noqa: E402


OFFLOAD_MOUNT = "/mnt/files-storage"
KV_CACHE_DIR = f"{OFFLOAD_MOUNT}/kv-cache"


def is_storage_mode() -> bool:
    """True when the job was launched with storage offloading selected.

    `LLMDBENCH_CICD_OFFLOADING_TARGET` is a job-level env in
    reusable-ci-nightly-benchmark.yaml: `fs` = storage (PVC-backed),
    `cpu` = RAM-only.
    """
    return os.environ.get("LLMDBENCH_CICD_OFFLOADING_TARGET", "").lower() == "fs"


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


def check_pvc_has_data(namespace: str, pod: str) -> v.Check:
    """Assert kv-cache blocks are actually on the PVC by counting files
    under KV_CACHE_DIR from inside the pod.

    Mirrors the guide's storage-offload verification step.
    """
    count_str = v.kubectl(
        ["exec", "-n", namespace, pod, "--", "sh", "-c",
         f"find {KV_CACHE_DIR} -type f 2>/dev/null | wc -l"],
        timeout=30,
    ).strip()
    try:
        n = int(count_str)
    except ValueError:
        n = 0
    return v.Check(
        name="PVC has KV cache data",
        passed=n > 0,
        detail=f"{n} files at {KV_CACHE_DIR} on {pod}",
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

    exp_dirs = v.find_results_dirs(env["workspace"])
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
    # PVC-side checks only apply in storage mode. The PVC is RWX and shared
    # across every model pod, so `check_pvc_is_bound` runs once against
    # pods[0]. 
    if storage_mode and pods:
        checks.append(check_pvc_is_bound(namespace, pods[0]))
        for pod in pods:
            checks.append(check_pvc_has_data(namespace, pod))

    # Metric checks: both counters should have moved on at least one pod.
    vllm_version = v.get_vllm_version(namespace, pods[0])
    if vllm_version is None:
        v.error(f"Failed to get vllm version from pod {pods[0]}")
        return 1
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
        # For now we dont reach this flow, no eviction from HBM is happening.
        # checks.append(metrics.check_per_pod("vllm:kv_offload_load_bytes_total",  "max", ">", 0.0))

    # Smoke check.
    checks.append(metrics.check_aggregated("vllm:num_requests_running", "count", ">", 0))

    passed = v.verify_checks(env, metrics, checks)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
