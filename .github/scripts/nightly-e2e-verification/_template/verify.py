#!/usr/bin/env python3
"""Template verifier for a nightly E2E scenario.

Copy this directory:
    cp -r _template <your-scenario>
Then customize the checks list below and, if you need scenario-specific
cluster inspection (kubectl gets, EPP log parsing, etc.), add print()s and
custom v.Check objects — see tiered-prefix-cache/ for a worked example.

The caller workflow points at this script via the `verify_script` input:
    verify_script: .github/scripts/nightly-e2e-verification/<your-scenario>/verify.py

All output is plain text so raw CI logs stay readable.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import verify_helpers as v  # noqa: E402


def main() -> int:
    env = v.workflow_env()

    exp_dirs = v.find_results_dirs(env["workspace"], env["namespace"])
    if not exp_dirs:
        return 1
    results_dir = exp_dirs[0]

    metrics = v.MetricsSummary.load(results_dir)
    if metrics is None:
        return 1

    print(f"=== <your-scenario> — {env['scenario']} / {env['workload'] or '?'} ===")
    print(f"Namespace: {env['namespace']}")
    print()

    # Two flavors of metric check:
    #   check_aggregated(metric, aggregate, op, bound)
    #     -> reads metrics.aggregated[metric][aggregate], threshold-checks it.
    #   check_per_pod(metric, aggregate, op, bound, *, combine=max)
    #     -> pulls metrics.per_pod[pod][metric][aggregate] for every pod,
    #       combines across pods (default: max), threshold-checks the result.
    #
    # Aggregates available: mean, stddev, min, p25, p50, p75, p90, p95, p99, max, count
    # Ops: <=, >=, <, >, ==
    # Additional checks: v.Check(name, passed, detail="...")
    checks = [
        # Smoke check — passes on any run with monitoring enabled.
        metrics.check_aggregated("vllm:num_requests_running", "count", ">", 0),

        # Examples — uncomment and customize:
        # metrics.check_aggregated("vllm:time_to_first_token_seconds", "p99", "<=", 2.0),
        # metrics.check_per_pod("vllm:kv_offload_store_bytes_total", "max", ">", 0.0),
        # v.Check("Custom assertion", passed=True, detail="..."),
    ]

    passed = v.verify_checks(env, metrics, checks)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
