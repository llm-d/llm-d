#!/usr/bin/env python3
"""Computes the GKE Pod Snapshots alerting metrics and compares them against thresholds.

Called by .github/scripts/e2e/e2e-validate-pod-snapshot.sh (the nightly's
`--extra-validate pod-snapshot` step) after guides/pod-snapshot/scripts/nightly-deploy-gke.sh
has brought up Pod 1 (cold start + snapshot) and Pod 2 (restore from the snapshot).

Reads both pods in one pass of kubectl calls (snapshot_timings.load_pods), prints and writes the
per-phase CSV (snapshot_timings.print_report; a failure there is logged and does not skip the
checks), then checks pod state, the PodSnapshot, phase-marker completeness, and the 5 composite
metrics from the GKE Pod Snapshots CI/CD Plan. Every check is evaluated and printed as a PASS/FAIL
table (verify_helpers, shared with the other nightly verifiers); the exit code is 1 if any check
fails and 0 if all pass.

Usage:
  check_thresholds.py --namespace NS --engine vllm|sglang [--selector llm-d.ai/guide=pod-snapshot]
                      [--pods-json pods.json] [--csv results.csv]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
VERIFY_DIR = REPO_ROOT / ".github" / "scripts" / "nightly-e2e-verification"
for p in (str(HERE), str(VERIFY_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import snapshot_timings as st  # noqa: E402
import verify_helpers as v  # noqa: E402

# Thresholds for the composite metrics of the GKE Pod Snapshots CI/CD Plan ("Composite Metrics
# Used for Alerting"). E2E Cold Start and Cold Start use the design doc's initial thresholds
# (from the 94-run baseline). Total Snapshot Overhead comes from 20 runs per engine on the nightly
# cluster (llm-d-scale / llm-d-e2e-pod-snapshot-us-central1, 2026-10-24); the per-engine
# derivation is next to each value. Warm Start is an absolute ceiling rather than the design
# doc's ECS / WSR ratio: ECS is mostly the HF download and model load, so the ratio fails fast
# cold starts and hides slow restores; the ratio is still printed for information.
THRESHOLDS = {
    "vllm": {
        "max_e2e_cold_start_s": 1800.0,    # ECS > 1800 -> fail
        "max_cold_start_s": 350.0,         # CS > 350 -> fail
        "max_snapshot_overhead_s": 65.0,   # TSOT == 0 or > 65 -> fail (p99 60.7s rounded up to a multiple of 5)
        # 30 = the WSR sample's bootstrap p99 upper bound (~27s) rounded up to a multiple of 5,
        # the same rule as TSOT. A restore onto a freshly provisioned node (~2x, e.g. the first
        # run after a node-pool upgrade) exceeds it by design.
        "max_warm_start_s": 30.0,          # WSR == 0 or > 30 -> fail
    },
    "sglang": {
        "max_e2e_cold_start_s": 1800.0,    # ECS > 1800 -> fail
        "max_cold_start_s": 350.0,         # CS > 350 -> fail
        # p99 79.0s, max 80.8s. 90 rather than 85: row 4 now ends at the wrapper's "Restored
        # server_status to ..." line, so it includes the GPU memory resume (4f, a few seconds)
        # that the calibration ended at the checkpoint line, and the GCS upload (4e) is the one
        # phase with run-to-run variance. Revisit after the first weeks of scheduled runs.
        "max_snapshot_overhead_s": 90.0,   # TSOT == 0 or > 90 -> fail
        "max_warm_start_s": 30.0,          # WSR == 0 or > 30 -> fail (same basis as vLLM)
    },
}


def pod_state(pod) -> str:
    """'missing', 'not Ready' or 'Ready', for the presence checks' details."""
    if not (pod and pod.get("created")):
        return "missing"
    return "Ready" if pod.get("ready") else "not Ready"


def pod_label(slot: str, pod) -> str:
    """Formats 'Pod 1 (<pod-name>)' when `pod` has a name so on-callers can match uploaded log files."""
    name = (pod or {}).get("name")
    return f"{slot} ({name})" if name else slot


def missing_lines_detail(markers: list[str], engine: str, label: str) -> str:
    """Formats missing marker keys as the human-readable log lines expected in `label`'s log."""
    quoted = ", ".join(f'"{st.marker_log_line(k, engine)}"' for k in markers)
    return f"no {quoted} {'line' if len(markers) == 1 else 'lines'} in {label}'s log"


def evaluate(p1, p2, engine: str, snap_ready=None) -> list[v.Check]:
    """Returns one verify_helpers.Check per alerting metric, plus the pod state, PodSnapshot and
    phase-marker checks, using the thresholds of `engine` (vllm or sglang). `snap_ready` is the
    time a PodSnapshot reached Ready since Pod 1 was created (snapshot_timings.load_pods), or
    None if none did."""
    t = THRESHOLDS[engine]
    checks: list[v.Check] = []
    p1_label, p2_label = pod_label("Pod 1", p1), pod_label("Pod 2", p2)

    # A pod is only counted once it is Ready: with the Ready transition missing, row 3 collapses
    # to 0 and every composite metric gets easier, so a NotReady pod must not pass.
    p1_state, p2_state = pod_state(p1), pod_state(p2)
    p1_cold = p1_state == "Ready" and not st.is_restored(p1)
    p2_ok = p2_state == "Ready"
    checks.append(v.Check("Pod 1 cold started", p1_cold,
                          f"{p1_label}: Ready, cold start" if p1_cold else
                          f"{p1_label}: restored from an old snapshot" if p1_state == "Ready" else
                          f"{p1_label}: {p1_state}"))
    checks.append(v.Check("Pod 2 present", p2_ok, f"{p2_label}: {p2_state}"))
    # snap_ready is None both when no PodSnapshot reached Ready and when there is no Pod 1 to
    # attribute one to; the Pod 1 check above says which.
    checks.append(v.Check("PodSnapshot Ready", snap_ready is not None,
                          f"Ready {(snap_ready - p1['created']).total_seconds():.0f}s after {p1_label} was created"
                          if snap_ready is not None else f"no PodSnapshot reached Ready since {p1_label} was created"))

    ecs = None
    if p1_cold:
        missing1 = [k for k in st.COLD_MARKERS if k not in p1["log"]]
        missing_breakdown = [k for k in st.COLD_BREAKDOWN_MARKERS if k not in p1["log"]]
        if missing1:
            p1_detail = missing_lines_detail(missing1, engine, p1_label)
        elif missing_breakdown:
            p1_detail = (
                f"all {len(st.COLD_MARKERS)} present in {p1_label}'s log "
                f"(warning: {missing_lines_detail(missing_breakdown, engine, p1_label)})"
            )
        else:
            p1_detail = f"all {len(st.COLD_MARKERS)} present in {p1_label}'s log"
        checks.append(v.Check("Pod 1 phase markers", not missing1, p1_detail))
        if not missing1:
            c = st.compute_cold_metrics(p1)
            ecs, cs, tsot = c["ecs"], c["cs"], c["4"]
            # E2E cold start (ECS = Rows 1 + 2 + 3): pod created -> Ready, excluding snapshot overhead (Row 4).
            checks.append(v.Check("E2E cold start", ecs <= t["max_e2e_cold_start_s"],
                                  f"{ecs:.2f}s <= {t['max_e2e_cold_start_s']:.0f}s"))
            # Cold start without node provisioning (CS = 1b + 1c + 1d + 2): sandbox + image pull + start + engine startup.
            checks.append(v.Check("Cold start", cs <= t["max_cold_start_s"],
                                  f"{cs:.2f}s <= {t['max_cold_start_s']:.0f}s"))
            # Total snapshot overhead (TSOT = Row 4 = 4c + 4e + 4f): GPU release + gVisor checkpoint/upload + GPU resume.
            checks.append(v.Check("Snapshot overhead", 0.0 < tsot <= t["max_snapshot_overhead_s"],
                                  f"0 < {tsot:.2f}s <= {t['max_snapshot_overhead_s']:.0f}s"))

    if p2_ok:
        restored = st.is_restored(p2)
        if restored:
            missing2 = [k for k in st.RESTORE_MARKERS if k not in p2["log"]]
            checks.append(v.Check("Pod 2 phase markers", not missing2,
                                  f"all {len(st.RESTORE_MARKERS)} present in {p2_label}'s log"
                                  if not missing2 else missing_lines_detail(missing2, engine, p2_label)))
            if not missing2:
                wsr = st.compute_restore_metrics(p2)["wsr"]
                # Pod 2 restore time / warm start (WSR = 1b + 1c + 1d + 4e + 4f + 3; excludes 1a node wait).
                # WSR == 0 means the restore timings are missing (every boundary clamped onto the
                # container start), not a 0s restore, so it fails like TSOT == 0. The ECS / WSR
                # ratio is informational: it depends mostly on how fast the weights downloaded.
                ratio = f", {ecs / wsr:.1f}x faster than {p1_label}'s cold start" if ecs and wsr > 0 else ""
                checks.append(v.Check("Pod 2 restore time",
                                      0.0 < wsr <= t["max_warm_start_s"],
                                      f"0 < {wsr:.2f}s <= {t['max_warm_start_s']:.0f}s{ratio}"))
        # A restored pod has no application cold start by construction, so the design doc's
        # "ACS on Pod 2 (Row 2) == 0" is exactly "Pod 2 restored"; Row 2 is only measured to report
        # how long a Pod 2 that did not restore took instead.
        checks.append(v.Check("Pod 2 restored", restored,
                              f"0.00s ({p2_label} restored)" if restored else
                              f"{st.compute_cold_metrics(p2)['2']:.2f}s ({p2_label} did not restore)"))
    return checks


def write_step_summary(engine: str, checks: list[v.Check], p1=None, p2=None) -> None:
    """Appends a markdown table to $GITHUB_STEP_SUMMARY when running in GitHub Actions."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    lines = [f"### Pod Snapshot alerting thresholds ({engine})", ""]
    if p1 or p2:
        lines += [f"- **Pod 1 (cold start):** `{(p1 or {}).get('name') or 'missing'}`",
                  f"- **Pod 2 (restore):** `{(p2 or {}).get('name') or 'missing'}`", ""]
    lines += ["| Status | Check | Detail |", "| --- | --- | --- |"]
    lines += [f"| {'PASS' if c.passed else 'FAIL'} | {c.name} | {c.detail} |" for c in checks]
    with open(summary_path, "a") as fh:
        fh.write("\n".join(lines) + "\n\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--selector", default="llm-d.ai/guide=pod-snapshot")
    parser.add_argument("--engine", required=True, choices=tuple(THRESHOLDS), help="vllm or sglang")
    parser.add_argument("--pods-json", default="", help="optional pre-smoke-loop `kubectl get pods -o json` file")
    parser.add_argument("--csv", default="", help="optional path to write the per-phase metrics CSV")
    args = parser.parse_args(argv)

    try:
        p1, p2, snap_ready = st.load_pods(st.Kubectl(args.namespace), args.selector, args.pods_json)
    except RuntimeError as e:
        v.error(f"ERROR: {e}")
        return 1

    # Informational: the per-phase CSV report for on-call and artifact upload. It must never
    # skip the threshold checks below, which decide pass/fail.
    print()
    print("=== Pod snapshot per-phase timing report ===")
    print(f"Pod 1 (cold start): {(p1 or {}).get('name') or 'missing'}   "
          f"Pod 2 (restore): {(p2 or {}).get('name') or 'missing'}")
    try:
        st.print_report(p1, p2, snap_ready, engine=args.engine, csv_path=args.csv)
    except Exception as e:  # noqa: BLE001 - any report bug is logged, not fatal
        v.error(f"Warning: the timing report failed ({type(e).__name__}: {e}); continuing to the threshold checks.")

    checks = evaluate(p1, p2, args.engine, snap_ready)
    print()
    print(f"=== Pod Snapshot alerting thresholds — {args.engine} / {args.namespace} ===")
    print()
    v.print_checks_table(checks)
    print()
    write_step_summary(args.engine, checks, p1=p1, p2=p2)

    failed = [c for c in checks if not c.passed]
    if failed:
        v.error(f"FAIL: {len(failed)}/{len(checks)} check(s) failed")
        return 1
    print(f"PASS: {len(checks)} check(s) passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
