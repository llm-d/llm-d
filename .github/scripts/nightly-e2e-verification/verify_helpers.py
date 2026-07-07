"""Helpers for nightly E2E verification scripts.

See `_template/verify.py` for a minimal working scaffold.

Aggregates produced by llm-d-benchmark's process_metrics.py _compute_stats:
  mean, stddev, min, p25, p50, p75, p90, p95, p99, max, count
"""
from __future__ import annotations

import json
import os
import re
import sys
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


def _read_metadata_namespace(meta: Path) -> str | None:
    """Extract the top-level `namespace:` field from run_metadata.yaml.
    Returns None if the file is unreadable or the field is missing.
    run_metadata.yaml is a flat YAML file with a single top-level mapping.
    """
    try:
        text = meta.read_text()
    except OSError:
        return None
    namespace_kvs = [kv for kv in text.splitlines() if kv.strip().startswith("namespace:")]
    if not namespace_kvs:
        return None
    val = namespace_kvs[0].split(":", 1)[1].strip()
    if not val:
        return None
    val = val.strip("'\"")  # Strip a single pair of matching surrounding quotes.
    return val


def find_results_dirs(workspace: str, namespace: str) -> list[Path] | None:
    """Return experiment result subdirs for the current run.

    Matches by namespace (from `run_metadata.yaml`) — concurrent CI jobs must
    use distinct namespaces to avoid K8s collisions, so `namespace` uniquely
    identifies a running job. Sequential runs that reuse the same namespace
    are disambiguated by metadata mtime.

    Only candidates in the canonical `<ws>/<user>-<ts>/results/<exp>/` layout
    are considered.
    All experiments belonging to the winning run are returned (a single
    `llmdbenchmark run` invocation can produce multiple `<exp>/` subdirs).
    """
    ws = Path(workspace)
    matches: list[Path] = []
    for meta in ws.rglob("run_metadata.yaml"):
        if meta.parent.parent.name != "results":
            continue
        if _read_metadata_namespace(meta) == namespace:
            matches.append(meta)
    if not matches:
        error(f"No <user>-<ts>/results/<exp>/run_metadata.yaml with "
              f"namespace={namespace!r} under {workspace}")
        return None
    # Newest metadata mtime wins — its parent's parent is this run's results/ dir.
    newest = max(matches, key=lambda m: m.stat().st_mtime)
    results_root = newest.parent.parent
    return [m.parent for m in matches if m.parent.parent == results_root]


def get_vllm_version(namespace: str, pod: str) -> tuple[int, ...] | None:
    """Return vLLM's version tuple from `pod`, or None if unavailable. 
    For example, '0.24.0rc1' or '0.24.0' will return (0, 24, 0).
    """
    out = kubectl(
        ["exec", "-n", namespace, pod, "--", "python3", "-c",
         "import vllm; print(vllm.__version__)"],
        timeout=10,
    ).strip()
    if not out:
        return None
    parts: list[int] = []
    for seg in out.split("."):
        m = re.match(r"\d+", seg)
        if not m:
            break
        parts.append(int(m.group()))
    return tuple(parts) if parts else None

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
            if not pod.startswith("_") and isinstance(data, dict)
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
            try:
                raw = json.load(f)
            except json.JSONDecodeError as e:
                error(f"{path} is not valid JSON: {e}")
                return None
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
    headers = ["STATUS", "CHECK", "DETAIL (MEAS op THRESH)"]
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
    """Run kubectl and return stdout, or "" if the command failed.

    Failure (non-zero exit, timeout, exec error) is logged to stderr and
    signalled by an empty return string — every caller already treats an
    empty result as "not found / no data", so this collapses the failure
    path uniformly instead of returning stderr text that downstream code
    then parses as if it were pod names, PVC phases, or version strings.
    """
    try:
        r = subprocess.run(["kubectl", *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        error(f"kubectl {' '.join(args)}: timed out after {timeout}s")
        return ""
    except Exception as e:
        error(f"kubectl {' '.join(args)}: {e}")
        return ""
    if r.returncode != 0:
        error(f"kubectl {' '.join(args)}: exit={r.returncode} {r.stderr.rstrip()}")
        return ""
    return r.stdout.rstrip()


def get_model_pods(namespace: str) -> list[str]:
    """Try modelservice decode/prefill labels first, then standalone.

    Returns bare pod names ("gpu-vllm-decode-abc"), not the `pod/name`.
    """
    def _bare(names: list[str]) -> list[str]:
        return [n.removeprefix("pod/") for n in names if n]

    pods = _bare(kubectl([
        "get", "pod", "-n", namespace,
        "-l", "llm-d.ai/role in (decode,prefill)", "-o", "name",
    ]).split())
    if not pods:
        pods = _bare(kubectl([
            "get", "pod", "-n", namespace,
            "-l", "llm-d.ai/inferenceServing=true", "-o", "name",
        ]).split())
    return pods

