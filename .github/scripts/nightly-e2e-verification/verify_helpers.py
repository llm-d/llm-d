"""Helpers for nightly E2E verification scripts.

See `_template/verify.py` for a minimal working scaffold.

Aggregates produced by llm-d-benchmark's process_metrics.py _compute_stats:
  mean, stddev, min, p25, p50, p75, p90, p95, p99, max, count
"""
from __future__ import annotations

import json
import os
import sys
import getpass
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


OPS = {
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
    "<":  lambda a, b: a <  b,
    ">":  lambda a, b: a >  b,
    "==": lambda a, b: a == b,
}


@dataclass
class Check:
    """A single pass/fail assertion with one line of detail.

    Metric-driven checks are built via MetricsSummary.check_aggregated /
    check_per_pod. Custom checks (PVC file counts, kubectl assertions, EPP log
    matches, …) are constructed directly.
    """
    name: str            # human-readable identifier, e.g. "vllm:foo.p99" or "PVC has data"
    passed: bool
    detail: str = ""     # e.g. "1.85 <= 2.00" or "42 files at /mnt/kv-cache"


# ---------------------------------------------------------------------------
# Environment & I/O
# ---------------------------------------------------------------------------

def workflow_env() -> dict:
    """Return useful env vars from the workflow env."""
    return {
        "workspace":  os.environ.get("LLMDBENCH_WORKSPACE", ""),
        "namespace":  os.environ.get("LLMDBENCH_CICD_NS", ""),
        "scenario":   os.environ.get("LLMDBENCH_CICD_SCENARIO", "<unknown>"),
        "workload":   os.environ.get("LLMDBENCH_CICD_WORKLOAD", ""),
        "harness":    os.environ.get("LLMDBENCH_CICD_HARNESS", ""),
        "model":      os.environ.get("LLMDBENCH_CICD_DETECTED_MODEL", ""),
        "run_id":     os.environ.get("GITHUB_RUN_ID", ""),
    }


def error(msg: str) -> None:
    """Print msg to stderr."""
    print(msg, file=sys.stderr)


def find_results_dirs(workspace: str) -> list[Path] | None:
    """Return experiment result subdirs from the most recent `llmdbenchmark run`.

    Only `llmdbenchmark run` invocations produce a `results/` directory whose
    parent is the timestamped `<user>-YYYYMMDD-HHMMSS-mmm` workspace subdir
    llmdbenchmark mints per invocation. Newest by mtime wins.
    """
    if not workspace:
        error("workspace not set (LLMDBENCH_WORKSPACE)")
        return None
    ws = Path(workspace)
    for results in sorted(ws.rglob("results"), key=lambda p: -p.stat().st_mtime):
        if not results.is_dir() and not results.parent.name.startswith(getpass.getuser()):
            continue
        exp_dirs = [c for c in results.iterdir()
                    if c.is_dir() and (c / "run_metadata.yaml").exists()]
        if exp_dirs:
            return exp_dirs
        break  # First time we see a results dir is the correct one; don't keep looking for older ones.
    error(f"No <user>-<ts>/results/<exp>/run_metadata.yaml found under {workspace}")
    return None


def get_vllm_version(namespace: str, pod: str) -> tuple[int, ...] | None:
    out = kubectl(
        ["exec", "-n", namespace, pod, "--", "python3", "-c",
         "import vllm; print(vllm.__version__)"],
        timeout=10,
    ).strip()

    version = tuple(int(x) for x in out.split(".") if x.isdigit())
    return version if version else None   

# ---------------------------------------------------------------------------
# Metrics summary
# ---------------------------------------------------------------------------

class MetricsSummary:
    """Wraps metrics_summary.json.

    Exposes the JSON as two convenience views alongside the raw payload:
      - .aggregated:   {metric_name: {mean, max, p99, ...}}
                       (from _aggregated.metrics in the summary)
      - .per_pod:      {pod_name: {metric_name: {mean, max, ...}}}
                       (from every non-underscored top-level key)
      - .raw:          the entire loaded JSON (escape hatch)

    Threshold checks are produced via check_aggregated (single-value against
    the aggregated view) or check_per_pod (walk per_pod, reduce, threshold).
    """

    def __init__(self, raw: dict) -> None:
        self.raw: dict = raw
        self.aggregated: dict = raw.get("_aggregated", {}).get("metrics", {}) or {}
        self.per_pod: dict = {
            pod: (data.get("metrics") or {})
            for pod, data in raw.items()
            if not pod.startswith("_")
        }

    @classmethod
    def load(cls, results_dir: Path) -> "MetricsSummary | None":
        """Load metrics/processed/metrics_summary.json from the results dir."""
        path = results_dir / "metrics" / "processed" / "metrics_summary.json"
        if not path.exists():
            error(f"{path} missing. Verification expects monitoring to be enabled "
                  f"(--monitoring on both standup and run). Check the harness "
                  f"pod's metrics_collection.log for scrape errors.")
            return None
        with path.open() as f:
            raw = json.load(f)
        info = raw.get("_info", {})
        if info.get("status") == "no_data":
            error(f"metrics_summary.json has no data: {info.get('message')}")
            return None
        return cls(raw)

    @property
    def pod_count(self) -> int:
        return len(self.per_pod)

    @property
    def metric_count(self) -> int:
        return len(self.aggregated)

    # -- Check factories ----------------------------------------------------

    def check_aggregated(self, metric: str, aggregate: str, op: str, bound: float) -> Check:
        """Threshold-check a single value from self.aggregated[metric][aggregate].

        Returns a failing Check (never None) on any failure — invalid op,
        missing metric, or missing aggregate — so callers can always append
        the result without None-guarding.
        """
        if op not in OPS:
            return Check(f"{metric}.{aggregate}", False,
                         detail=f"unknown op '{op}' (use one of {list(OPS)})")
        stats = self.aggregated.get(metric)
        if not stats:
            return Check(f"{metric}.{aggregate}", False,
                         detail="metric not in _aggregated.metrics")
        actual = stats.get(aggregate)
        if actual is None:
            return Check(f"{metric}.{aggregate}", False,
                         detail=f"aggregate '{aggregate}' missing in data.")
        actual = float(actual)
        passed = OPS[op](actual, float(bound))
        return Check(
            name=f"{metric}.{aggregate}",
            passed=passed,
            detail=f"{actual:.4g} {op} {float(bound):.4g}",
        )

    def check_per_pod(
        self,
        metric: str,
        aggregate: str,
        op: str,
        bound: float,
        *,
        combine: Callable[[Iterable[float]], float] = max,
    ) -> Check:
        """Pull `per_pod[pod][metric][aggregate]` for every pod, combine with
        `combine(values)`, threshold-check the combined value.

        Any callable that consumes an iterable of floats and returns one float
        works: max (default), min, sum, statistics.mean, statistics.median, or
        a lambda around functools.reduce for binary combiners.

        Returns a failing Check (rather than raising or returning None) when
        the op is invalid or the metric never appeared on any pod, so callers
        can always append the result to their checks list without special-casing.
        """
        reducer_name = getattr(combine, "__name__", "combine")
        name = f"{metric}.{aggregate} (per-pod {reducer_name})"

        if op not in OPS:
            return Check(name, False,
                         detail=f"unknown op '{op}' (use one of {list(OPS)})")

        values: list[float] = []
        for metrics_by_name in self.per_pod.values():
            stats = metrics_by_name.get(metric)
            if not stats:
                continue
            v = stats.get(aggregate)
            if v is None:
                continue
            values.append(float(v))

        if not values:
            return Check(name, False,
                         detail=f"metric not reported by any pod "
                                f"(per_pod has {len(self.per_pod)} pods)")
        combined = float(combine(values))
        passed = OPS[op](combined, float(bound))
        return Check(
            name=name,
            passed=passed,
            detail=f"{combined:.4g} {op} {float(bound):.4g}",
        )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def print_checks_table(checks: Iterable[Check]) -> None:
    """Print a fixed-width, human-readable checks table to stdout (for CI raw logs)."""
    headers = ["STATUS", "CHECK", "DETAIL (MEAS op TRESH)"]
    rows = [
        ["PASS" if c.passed else "FAIL", c.name, c.detail]
        for c in checks
    ]
    if not rows:
        print("(no checks defined — inspection-only)")
        return
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]

    def fmt_row(cells: list[str]) -> str:
        return "  ".join(cells[i].ljust(widths[i]) for i in range(len(cells))).rstrip()

    print(fmt_row(headers))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print(fmt_row(r))


def verify_checks(env: dict, metrics: MetricsSummary,
                              checks: list[Check]) -> bool:
    """Print the standard report; return True on all-pass, False otherwise."""
    checks = list(checks)
    passed = all(c.passed for c in checks)

    print()
    print(f"=== Verification — {env['scenario']} / {env.get('workload') or '?'} ===")
    print(f"Pods scraped: {metrics.pod_count}   Aggregated metrics: {metrics.metric_count}")
    print()
    print_checks_table(checks)
    print()

    if not passed:
        failed = sum(1 for c in checks if not c.passed)
        print(f"FAIL: {failed}/{len(checks)} check(s) failed", file=sys.stderr)
        return False
    print(f"PASS: {len(checks)} check(s) passed" if checks else "PASS (no checks)")
    return True


# ---------------------------------------------------------------------------
# kubectl helpers
# ---------------------------------------------------------------------------

def kubectl(args: list[str], timeout: int = 30) -> str:
    try:
        r = subprocess.run(["kubectl", *args], capture_output=True, text=True, timeout=timeout)
        return (r.stdout or r.stderr or "").rstrip()
    except subprocess.TimeoutExpired:
        return f"(timed out: kubectl {' '.join(args)})"
    except Exception as e:
        return f"(failed: {e})"


def get_model_pods(namespace: str) -> list[str]:
    """Try modelservice decode/prefill labels first, then standalone."""
    pods = [p for p in kubectl([
        "get", "pod", "-n", namespace,
        "-l", "llm-d.ai/role in (decode,prefill)", "-o", "name",
    ]).split() if p]
    if not pods:
        pods = [p for p in kubectl([
            "get", "pod", "-n", namespace,
            "-l", "llm-d.ai/inferenceServing=true", "-o", "name",
        ]).split() if p]
    return pods

