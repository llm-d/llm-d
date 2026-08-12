#!/usr/bin/env python3
"""Fetch the KEDA scored-run series from Prometheus, plot the episode, and score it.

Reachability: Prometheus is in-cluster only, so we shell out through
`kubectl exec` on the prometheus-server pod and hit its localhost API.

Panels (one unit each, no dual axes):
  1. Offered load (req/s)
  2. TTFT p90 vs SLO, TPOT p90 vs SLO  (two small-multiple rows)
  3. Replicas: actual (spec) + ready + desired (reconstructed from the formula)
  4. Saturation: raw + smoothed with the 0.40/0.55 hysteresis band

Score: combined SLO attainment from the EPP violation counters over the exact
window, and time-averaged replicas (cost), both cross-checkable by eye.
"""
import json, math, os, subprocess, sys
from datetime import datetime, timezone
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# This script lives in benchmark-template/; run inputs + outputs live in the
# sibling benchmark-results/ (start-time file in, plots out).
DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "benchmark-results"))
NS_MON = "monitoring"
DEPLOY = "optimized-baseline-nvidia-gpu-vllm-decode"
NS_DEPLOY = "llm-d-optimized-baseline"
TTFT_SLO_S, TPOT_SLO_S = 3.0, 0.1
TH_UP, TH_DN, NMIN, NMAX = 0.55, 0.40, 3, 8

# validated palette (dataviz reference instance)
BLUE, AQUA = "#2a78d6", "#1baf7a"
INK, MUTED, SURF = "#0b0b0b", "#52514e", "#fcfcfb"
AMBER, GRIDC = "#d98a1b", "#e7e6e2"


def promql_range(query, start, end, step=15):
    """Run a query_range against the in-cluster Prometheus, return [(ts, val)]."""
    import urllib.parse
    q = urllib.parse.quote(query, safe="")
    url = (f"http://localhost:9090/api/v1/query_range?query={q}"
           f"&start={start}&end={end}&step={step}s")
    out = subprocess.check_output([
        "kubectl", "-n", NS_MON, "exec", "deploy/prometheus-server",
        "-c", "prometheus-server", "--", "sh", "-c", f"wget -qO- '{url}'"])
    res = json.loads(out)["data"]["result"]
    if not res:
        return []
    return [(float(t), float(v) if v not in ("NaN", "+Inf", "-Inf") else float("nan"))
            for t, v in res[0]["values"]]


def promql_scalar(query):
    import urllib.parse
    q = urllib.parse.quote(query, safe="")
    url = f"http://localhost:9090/api/v1/query?query={q}"
    out = subprocess.check_output([
        "kubectl", "-n", NS_MON, "exec", "deploy/prometheus-server",
        "-c", "prometheus-server", "--", "sh", "-c", f"wget -qO- '{url}'"])
    res = json.loads(out)["data"]["result"]
    return float(res[0]["value"][1]) if res else 0.0


def formula(sat, n, r):
    """The ScaledObject control law, clamped to [NMIN, NMAX] (the desired replica count)."""
    if any(math.isnan(x) for x in (sat, n, r)) or n <= 0:
        return float("nan")
    if sat > TH_UP:
        credit = (r / n) if (0 < r < n) else 1.0
        d = n + math.ceil(n * (sat * credit / TH_UP - 1))
    elif sat < TH_DN:
        d = n - math.floor(n * (1 - sat / TH_DN))
    else:
        d = n
    return max(NMIN, min(NMAX, d))


def align(*series):
    """Inner-join a set of [(ts,val)] on timestamp; returns (ts_list, [vals...])."""
    maps = [dict(s) for s in series]
    ts = sorted(set(maps[0]) if maps else [])
    for m in maps[1:]:
        ts = [t for t in ts if t in m]
    cols = [[m[t] for t in ts] for m in maps]
    return ts, cols


def offered_staircase(job, ns, t0):
    """The staged offered-load profile as a step series (minutes-into-episode, rps),
    aligned to the load generator's actual stage-start timestamps from its logs."""
    import re
    stages = [2, 4, 6, 8, 10, 4, 1]      # the scored inference-perf profile (req/s)
    durs = [330, 360, 360, 360, 360, 360, 300]
    try:
        logs = subprocess.check_output(["kubectl", "logs", "-n", ns, f"job/{job}"],
                                       stderr=subprocess.DEVNULL).decode(errors="ignore")
    except Exception:
        return [], []
    starts = {}
    for ln in logs.splitlines():
        m = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+.*Stage (\d) - run started", ln)
        if m:
            ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
            starts[int(m.group(2))] = ts
    if not starts:
        return [], []
    xs, ys = [], []
    for i, rate in enumerate(stages):
        if i in starts:
            xs.append((starts[i] - t0) / 60); ys.append(rate)
    last = max(starts)
    xs.append((starts[last] + durs[last] - t0) / 60); ys.append(stages[last])  # hold last stage
    xs.append((starts[last] + durs[last] - t0) / 60); ys.append(0)             # then load ends
    return xs, ys


def main():
    start_iso = open(f"{DIR}/scored-run-start.txt").read().strip().splitlines()[-1]
    start = int(datetime.fromisoformat(start_iso.replace("Z", "+00:00")).timestamp())
    end = int(sys.argv[1]) if len(sys.argv) > 1 else int(datetime.now(timezone.utc).timestamp())
    print(f"window: {start_iso} .. {datetime.fromtimestamp(end, timezone.utc).isoformat()} "
          f"({(end-start)/60:.1f} min)")

    qps = promql_range("sum(rate(llm_d_epp_request_ttft_seconds_count[1m]))", start, end)
    ttft = promql_range("histogram_quantile(0.9, sum(rate(llm_d_epp_request_ttft_seconds_bucket[1m])) by (le))", start, end)
    ttft_pred = promql_range("histogram_quantile(0.9, sum(rate(llm_d_epp_request_predicted_ttft_seconds_bucket[1m])) by (le))", start, end)
    tpot = promql_range("histogram_quantile(0.9, sum(rate(llm_d_epp_request_streaming_tpot_seconds_bucket[1m])) by (le))", start, end)
    n_s = promql_range(f'max(kube_deployment_status_replicas{{deployment="{DEPLOY}",namespace="{NS_DEPLOY}"}})', start, end)
    r_s = promql_range(f'max(kube_deployment_status_replicas_ready{{deployment="{DEPLOY}",namespace="{NS_DEPLOY}"}})', start, end)
    sat_raw = promql_range("epp:saturation:raw", start, end)
    sat_sm = promql_range("epp:saturation:smoothed", start, end)

    # reconstruct desired from the formula on aligned (sat_sm, n, r)
    ts_d, (sd, nd, rd) = align(sat_sm, n_s, r_s)
    desired = [formula(s, n, r) for s, n, r in zip(sd, nd, rd)]

    t0 = start
    mins = lambda s: [(t - t0) / 60 for t, _ in s]
    vals = lambda s: [v for _, v in s]

    # ---- score ----
    total_req = promql_scalar(f'increase(llm_d_epp_request_ttft_seconds_count[{end-start}s] @ {end})')
    v_ttft = promql_scalar(f'sum(increase(llm_d_epp_request_slo_violation_total{{type="ttft"}}[{end-start}s] @ {end}))')
    v_tpot = promql_scalar(f'sum(increase(llm_d_epp_request_slo_violation_total{{type="tpot"}}[{end-start}s] @ {end}))')
    avg_reps = promql_scalar(f'avg_over_time(max(kube_deployment_status_replicas{{deployment="{DEPLOY}",namespace="{NS_DEPLOY}"}})[{end-start}s:15s] @ {end})')
    # combined attainment: a request fails if it violates either SLO; upper-bound the
    # combined violation fraction by the sum (server-side counters are per-type).
    viol_frac = (v_ttft + v_tpot) / total_req if total_req else float("nan")
    attain = (1 - viol_frac) * 100

    # ---- plot ----
    plt.rcParams.update({"font.size": 9, "axes.edgecolor": MUTED, "axes.labelcolor": INK,
                         "xtick.color": MUTED, "ytick.color": MUTED, "figure.facecolor": SURF,
                         "axes.facecolor": SURF, "axes.grid": True, "grid.color": GRIDC,
                         "axes.spines.top": False, "axes.spines.right": False})
    fig, ax = plt.subplots(4, 1, figsize=(10, 11), sharex=True)

    ox, oy = offered_staircase("ip-scored-keda", NS_DEPLOY, t0)
    if ox:
        ax[0].step(ox, oy, where="post", color=MUTED, lw=1.5, ls="--", label="requested")
    ax[0].plot(mins(qps), vals(qps), color=BLUE, lw=2, label="served")
    ax[0].set_ylabel("load (req/s)")
    ax[0].legend(loc="upper left", frameon=False, ncol=2)

    ax[1].plot(mins(ttft), vals(ttft), color=BLUE, lw=2, label="TTFT p90 actual")
    ax[1].plot(mins(ttft_pred), vals(ttft_pred), color=AQUA, lw=2, label="TTFT p90 predicted (signal)")
    ax[1].axhline(TTFT_SLO_S, color=AMBER, ls="--", lw=1.5, label=f"SLO {TTFT_SLO_S:g}s")
    ax[1].set_ylabel("TTFT p90 (s)")
    ax[1].set_yscale("log")
    ax[1].legend(loc="upper left", frameon=False)

    ax[2].axhline(TPOT_SLO_S, color=AMBER, ls="--", lw=1.5, label=f"SLO {TPOT_SLO_S*1000:g}ms")
    ax[2].plot(mins(tpot), vals(tpot), color=BLUE, lw=2, label="TPOT p90")
    ax[2].set_ylabel("TPOT p90 (s)")
    ax[2].legend(loc="upper left", frameon=False)

    ax[3].plot(mins(n_s), vals(n_s), color=BLUE, lw=2, drawstyle="steps-post", label="provisioned")
    ax[3].plot(mins(r_s), vals(r_s), color=AQUA, lw=2, ls=":", drawstyle="steps-post", label="ready")
    ax[3].set_ylabel("replicas")
    ax[3].set_ylim(NMIN - 0.5, NMAX + 0.5)
    ax[3].legend(loc="upper left", frameon=False, ncol=2)
    ax[3].set_xlabel("minutes into episode")

    # annotate which parameter governs each scaling step (features are spatially
    # separated: up-burst → warmup gap → down-staircase)
    ann = dict(fontsize=8, color=INK, ha="center",
               arrowprops=dict(arrowstyle="->", color=MUTED, lw=1))
    ax[3].annotate("scale-up fires: θ_up = 0.55\nburst Max(100%, 4)/60 s",
                   xy=(15.2, 5.5), xytext=(7.5, 6.6), **ann)
    ax[3].annotate("warmup T_w ≈ 105 s\n(provisioned → ready)",
                   xy=(17.3, 7.3), xytext=(24.5, 6.2), **ann)
    ax[3].annotate("scale-down: θ_dn = 0.40\n1 pod / 120 s, 180 s stabilization",
                   xy=(47, 6), xytext=(34, 4.3), **ann)

    fig.tight_layout()
    fig.savefig(f"{DIR}/scored-run-overview.png", dpi=200, bbox_inches="tight")

    # saturation panel as a separate figure (the control signal itself)
    fig2, a2 = plt.subplots(figsize=(10, 3.2))
    a2.axhspan(TH_DN, TH_UP, color=GRIDC, alpha=0.7, label="hysteresis band")
    a2.plot(mins(sat_raw), vals(sat_raw), color=MUTED, lw=1, alpha=0.7, label="raw")
    a2.plot(mins(sat_sm), vals(sat_sm), color=BLUE, lw=2, label="smoothed")
    a2.axhline(TH_UP, color=AQUA, lw=1); a2.axhline(TH_DN, color=AMBER, lw=1)
    a2.set_ylabel("saturation"); a2.set_xlabel("minutes into episode")
    a2.legend(loc="upper left", frameon=False, ncol=4)
    for sp in ("top", "right"): a2.spines[sp].set_visible(False)
    fig2.tight_layout(); fig2.savefig(f"{DIR}/scored-run-saturation.png", dpi=200, bbox_inches="tight")

    print("\n===== SCORE =====")
    print(f"requests:            {total_req:,.0f}")
    print(f"TTFT violations:     {v_ttft:,.0f}")
    print(f"TPOT violations:     {v_tpot:,.0f}")
    print(f"combined attainment: {attain:.1f}%  (>= this; sum-of-types upper-bounds double-count)")
    print(f"avg replicas:        {avg_reps:.2f}   (cost x2 H100 = {avg_reps*2:.1f} GPU-avg)")
    print(f"\nvs static-8: 100% @ 8")
    print(f"plots: scored-run-overview.png, scored-run-saturation.png")


if __name__ == "__main__":
    main()
