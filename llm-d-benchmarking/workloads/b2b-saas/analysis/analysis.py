import json, re, subprocess, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Multi-run mode: each base_prefix is a set of inference-perf jobs, one per 
# concurrency level. The script lists GCS for the latest <SUFFIX> per 
# (base_prefix, c) and loads stage_0 reports.
RUNS = [
    ("epp prefix-cache filter + token-load 25k",      "optimised-benchmark-infperf-epp-penalty25k-tokens-scorer-max-picker-no-exp-det-1", "#d62728", "x"),

    ("baseline",                        "optimised-benchmark-infperf-baseline-det-1",          "#1f77b4", "o"),
    ("epp prefix-cache filter + token-load",      "optimised-benchmark-infperf-epp-penalty384k-tokens-scorer-max-picker-no-exp-det-1", "#d62728", "x"),
    ("epp default",            "optimised-benchmark-infperf-epp-max-score-det-1", "#2ca02c", "x"),
    ("latency-predictor",  "optimised-benchmark-infperf-epp-penalty5s-latency-predictor-det-4-safe", "#9467bd", "x"),
]

BUCKET = "gs://kaushikmitra-llm-ig-benchmark/workload-catalog-runs"
GMP_PROJECT = "kaushikmitra-gke-dev"
GMP_URL = f"https://monitoring.googleapis.com/v1/projects/{GMP_PROJECT}/location/global/prometheus/api/v1/query"
SCRAPE_INTERVAL = 15
SCRAPE_BUFFER = 2
FAIL_RATE_THRESHOLD = 0.60
FAIL_COUNT_FLOOR = 3
MIN_DISPATCHED = 30
USE_SERVER_METRICS = False  # Set to True to pull TTFT/TPOT from vLLM prometheus metrics


def discover(base_prefix):
    """Return {conc: full_prefix} for the latest run of each concurrency level."""
    try:
        listing = subprocess.check_output(
            ["gsutil", "ls", f"{BUCKET}/{base_prefix}*summary_lifecycle_metrics.json"],
            stderr=subprocess.DEVNULL,
        ).decode()
    except subprocess.CalledProcessError:
        return {}
    
    # Filenames look like: <base_prefix>-c<CONC>-<SUFFIX>summary_lifecycle_metrics.json
    # OR: <base_prefix>-<SUFFIX>summary_lifecycle_metrics.json (if no -c)
    pat = re.compile(rf"({re.escape(base_prefix)}(?:-c(\d+))?-(\d+))summary_lifecycle_metrics\.json$")
    latest = {}  # conc -> (suffix, full_prefix)
    for line in listing.splitlines():
        m = pat.search(line)
        if m:
            full_prefix, conc_str, suffix = m.group(1), m.group(2), int(m.group(3))
            conc = int(conc_str) if conc_str else 0
            if conc not in latest or suffix > latest[conc][0]:
                latest[conc] = (suffix, full_prefix)
    
    return {conc: prefix for conc, (suffix, prefix) in latest.items()}


def load(prefix, kind, stage=0):
    blob = f"{BUCKET}/{prefix}stage_{stage}_{kind}.json"
    return json.loads(subprocess.check_output(["gsutil", "cat", blob], stderr=subprocess.DEVNULL))


_gmp_token_cache = {"token": None}


def gmp_query(query, eval_time):
    """Run a PromQL query against GMP at eval_time (unix seconds). Returns float or 0.0."""
    if _gmp_token_cache["token"] is None:
        _gmp_token_cache["token"] = subprocess.check_output(
            ["gcloud", "auth", "application-default", "print-access-token"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    import urllib.request, urllib.parse
    params = urllib.parse.urlencode({"query": query, "time": str(int(eval_time))})
    req = urllib.request.Request(
        f"{GMP_URL}?{params}",
        headers={"Authorization": f"Bearer {_gmp_token_cache['token']}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            payload = json.loads(r.read())
        result = payload.get("data", {}).get("result", [])
        if not result:
            return 0.0
        return float(result[0]["value"][1])
    except Exception as e:
        print(f"  gmp_query failed: {e}", file=sys.stderr)
        return 0.0


def _get_eval_window(prefix, duration, offset=0):
    # prefix ends in -<SUFFIX>
    run_start = int(prefix.rsplit("-", 1)[-1])
    stage_start = run_start + int(offset)
    stage_end = stage_start + int(duration)
    eval_time = stage_end + SCRAPE_INTERVAL + SCRAPE_BUFFER
    window = eval_time - stage_start
    return int(eval_time), int(window)


def gmp_prefix_hit_pct(prefix, duration, offset=0):
    """Cluster-wide prefix cache hit % for a specific stage."""
    eval_time, window = _get_eval_window(prefix, duration, offset)
    q = (
        f"100 * sum(increase(vllm:prefix_cache_hits_total[{window}s])) "
        f"/ (sum(increase(vllm:prefix_cache_queries_total[{window}s])) > 0)"
    )
    return gmp_query(q, eval_time)


def gmp_queue_len(prefix, duration, offset=0):
    """Cluster-wide peak queue size (sum_max across pods) for a specific stage."""
    eval_time, window = _get_eval_window(prefix, duration, offset)
    q = f"sum(max_over_time(vllm:num_requests_waiting[{window}s]))"
    return gmp_query(q, eval_time)


def gmp_running_req_total(prefix, duration, offset=0):
    """Cluster-wide mean concurrent in-flight requests (summed across pods)."""
    eval_time, window = _get_eval_window(prefix, duration, offset)
    # Using 15s resolution for the sub-query to match scrape interval
    q = f"avg_over_time(sum(vllm:num_requests_running)[{window}s:15s])"
    return gmp_query(q, eval_time)


def gmp_running_req_max_per_endpoint(prefix, duration, offset=0):
    """Peak concurrent in-flight requests on any single pod."""
    eval_time, window = _get_eval_window(prefix, duration, offset)
    q = f"max(max_over_time(vllm:num_requests_running[{window}s]))"
    return gmp_query(q, eval_time)


def gather(label, base_prefix):
    discovered = discover(base_prefix)
    if not discovered:
        # print(f"  [{label}] no reports found for base prefix '{base_prefix}'", file=sys.stderr)
        return []

    rows = []
    for c in sorted(discovered):
        prefix = discovered[c]
        
        # Iterate through stages until we find no more reports
        stage = 0
        offset = 0
        while True:
            try:
                life = load(prefix, "lifecycle_metrics", stage)
                # Prometheus metrics might be missing for some stages, so we use a fallback
                try:
                    prom = load(prefix, "prometheus_metrics", stage)
                except subprocess.CalledProcessError:
                    prom = None
            except subprocess.CalledProcessError:
                # No more stages found
                break
            
            try:
                dispatched = life["load_summary"]["count"]
                failures = life["failures"]["count"]
                fail_rate = failures / dispatched if dispatched else 0.0
                
                # Use achieved_rate as the x-axis value
                # We store it in 'conc' to reuse the existing plotting functions which expect that field
                qps = life["load_summary"].get("achieved_rate", 0.0)
                
                if dispatched < MIN_DISPATCHED:
                    print(
                        f"  drop [{label}] stage={stage}: only {dispatched} dispatched (< MIN_DISPATCHED={MIN_DISPATCHED})",
                        file=sys.stderr,
                    )
                    duration = life["benchmark_time_seconds"]
                    offset += duration
                    stage += 1
                    continue
                
                lat = life["successes"]["latency"]
                duration = life["benchmark_time_seconds"]

                # Robust metric extraction (handles stream=false where some fields are null)
                # Use server metrics if enabled and available
                if USE_SERVER_METRICS and prom:
                    ttft_obj = prom["successes"].get("time_to_first_token")
                    tpot_obj = prom["successes"].get("time_per_output_token")
                    if not ttft_obj or not tpot_obj:
                         # Fallback if specific keys are missing in prom
                         ttft_obj = lat.get("time_to_first_token") or lat.get("request_latency")
                         tpot_obj = lat.get("time_per_output_token") or lat.get("normalized_time_per_output_token")
                else:
                    ttft_obj = lat.get("time_to_first_token") or lat.get("request_latency")
                    tpot_obj = lat.get("time_per_output_token") or lat.get("normalized_time_per_output_token")
                
                e2e_obj = lat.get("request_latency")
                
                row = {
                    "conc": qps,  # This is the Achieved QPS
                    "success_rate": (1.0 - fail_rate) * 100,
                    "ttft_mean": ttft_obj["mean"] if ttft_obj else 0.0,
                    "ttft_p90":  ttft_obj["p90"] if ttft_obj else 0.0,
                    "tpot_mean": (tpot_obj["mean"] * 1000) if tpot_obj else 0.0,
                    "tpot_p90":  (tpot_obj["p90"] * 1000) if tpot_obj else 0.0,
                    "e2e_mean": e2e_obj["mean"] if e2e_obj else 0.0,
                    "e2e_p90": e2e_obj["p90"] if e2e_obj else 0.0,
                    "out_per_sec": life["successes"]["throughput"]["output_tokens_per_sec"],
                    "in_per_sec": life["successes"]["throughput"]["input_tokens_per_sec"],
                    "prefix_hit_pct": gmp_prefix_hit_pct(prefix, duration, offset),
                    "prompt_tokens_p50": life["successes"]["prompt_len"]["median"],
                    "prompt_tokens_p90": life["successes"]["prompt_len"]["p90"],
                    "gen_tokens_p50":    life["successes"]["output_len"]["median"],
                    "gen_tokens_p90":    life["successes"]["output_len"]["p90"],
                }
                
                if prom:
                    row.update({
                        "kv_cache_mean": prom["successes"]["kv_cache_usage_percentage"]["mean"] * 100,
                        "kv_cache_p99":  prom["successes"]["kv_cache_usage_percentage"]["p99"] * 100,
                        "queue_len_mean": gmp_queue_len(prefix, duration, offset),
                        "running_req_total": gmp_running_req_total(prefix, duration, offset),
                        "running_req_max_per_endpoint": gmp_running_req_max_per_endpoint(prefix, duration, offset),
                        "queue_time_mean": prom["successes"]["request_queue_time"]["mean"],
                        "queue_time_p90":  prom["successes"]["request_queue_time"]["p90"],
                        "queue_time_p99":  prom["successes"]["request_queue_time"]["p99"],
                    })
                else:
                    # Fill with zeros if prometheus metrics are missing
                    row.update({
                        "kv_cache_mean": 0.0, "kv_cache_p99": 0.0, "queue_len_mean": 0.0,
                        "running_req_total": 0.0, "running_req_max_per_endpoint": 0.0,
                        "queue_time_mean": 0.0, "queue_time_p90": 0.0, "queue_time_p99": 0.0,
                    })

                rows.append(row)
                offset += duration
            except Exception as e:
                print(f"  [{label}] stage={stage}: {e}", file=sys.stderr)
            
            stage += 1

        
        print(f"  [{label}] gathered {len(rows)} stages for prefix '{prefix}'", file=sys.stderr)
    return rows


runs = [(label, gather(label, base), color, marker) for label, base, color, marker in RUNS]
runs = [r for r in runs if r[1]]

if not runs:
    sys.exit("no runs with data found")

fig, axes = plt.subplots(5, 3, figsize=(22, 25))
ax = axes.flatten()


def plot_pair(idx, key_lo, key_hi, ylabel, title):
    lo_label = key_lo.rsplit("_", 1)[-1]  # e.g. "mean", "p50"
    hi_label = key_hi.rsplit("_", 1)[-1] if key_hi else None
    for label, rows, color, marker in runs:
        # Sort by x-axis (achieved QPS)
        rows_sorted = sorted(rows, key=lambda r: r["conc"])
        x = [r["conc"] for r in rows_sorted]
        ax[idx].plot(x, [r[key_lo] for r in rows_sorted], f"{marker}-", label=f"{label} {lo_label}", color=color)
        if key_hi:
            ax[idx].plot(x, [r[key_hi] for r in rows_sorted], f"{marker}--", label=f"{label} {hi_label}", color=color, alpha=0.5)
    ax[idx].set_xlabel("Achieved Rate (QPS)")
    ax[idx].set_ylabel(ylabel); ax[idx].set_title(title)
    ax[idx].legend(fontsize=8); ax[idx].grid(alpha=0.3)


def plot_single(idx, key, ylabel, title):
    for label, rows, color, marker in runs:
        # Sort by x-axis (achieved QPS)
        rows_sorted = sorted(rows, key=lambda r: r["conc"])
        x = [r["conc"] for r in rows_sorted]
        ax[idx].plot(x, [r[key] for r in rows_sorted], f"{marker}-", label=label, color=color)
    ax[idx].set_xlabel("Achieved Rate (QPS)")
    ax[idx].set_ylabel(ylabel); ax[idx].set_title(title)
    ax[idx].legend(fontsize=8); ax[idx].grid(alpha=0.3)


#plot_pair(0, "ttft_mean", "ttft_p90", "TTFT (s)", "Time To First Token")
#plot_pair(1, "tpot_mean", "tpot_p90", "TPOT (ms)", "Time Per Output Token")
plot_single(0, "ttft_p90", "TTFT (s)", "Time To First Token p90")
plot_single(1,  "tpot_p90", "TPOT (ms)", "Time Per Output Token p90")
plot_single(2, "out_per_sec", "output tokens / sec", "Throughput (mean output tokens/s)")
plot_pair(3, "kv_cache_mean", "kv_cache_p99", "KV cache usage (%)", "KV Cache Usage")
plot_single(4, "prefix_hit_pct", "prefix cache hit (%)", "Prefix Cache Hit Rate")
plot_single(5, "queue_len_mean", "queue length (cluster, sum_max)",
            "Queue Size (vllm:num_requests_waiting)")
plot_pair(6, "prompt_tokens_p50", "prompt_tokens_p90", "input tokens / request",
          "Prompt Size (loadgen-tokenized)")
plot_pair(7, "gen_tokens_p50", "gen_tokens_p90", "output tokens / request",
          "Output Size (loadgen-tokenized)")
plot_single(8, "queue_time_mean", "queue wait mean (s)",
            "Request Queue Wait Time")
plot_single(9, "running_req_total", "total running requests", "Cluster-wide Concurrent In-Flight")
plot_single(10, "success_rate", "Success Rate (%)", "Request Success Rate")
plot_single(11, "e2e_p90", "E2E Latency (s)", "End-to-End Latency p90")
plot_single(12, "in_per_sec", "input tokens / sec", "Throughput (mean input tokens/s)")
plot_single(13, "running_req_max_per_endpoint", "max running requests / endpoint", "Max Concurrent Requests per Endpoint")

plt.suptitle(" vs ".join(label for label, _, _, _ in RUNS if label in [r[0] for r in runs]) + "   (inference-perf individual runs)", fontsize=12)
plt.tight_layout()
import os
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "baseline_vs_epp_prefix_runs.png")
plt.savefig(out, dpi=120, bbox_inches="tight")
print(f"saved: {out}")

print()
print(f"{'conc':>5} | {'Success%':>8} | {'TTFT mean':>9} {'TTFT p90':>9} | {'TPOT mean':>9} | {'E2E mean':>9} {'E2E p90':>9} | {'out_t/s':>8} | {'in_t/s':>8} | {'kv_mean':>8} | {'prefix%':>8} | {'qsize':>6} | {'prompt_p50':>10} | {'gen_p50':>8}")
for label, rows, *_ in runs:
    print(f"--- {label} ---")
    for r in rows:
        print(
            f"{r['conc']:5.1f} | "
            f"{r['success_rate']:7.1f}% | "
            f"{r['ttft_mean']:7.1f}s  {r['ttft_p90']:7.1f}s | "
            f"{r['tpot_mean']:7.1f}ms | "
            f"{r['e2e_mean']:8.1f}s  {r['e2e_p90']:8.1f}s | "
            f"{r['out_per_sec']:8.1f} | "
            f"{r['in_per_sec']:8.1f} | "
            f"{r['kv_cache_mean']:7.1f}% | "
            f"{r['prefix_hit_pct']:7.2f}% | "
            f"{r['queue_len_mean']:6.1f} | "
            f"{r['prompt_tokens_p50']:10.0f} | "
            f"{r['gen_tokens_p50']:8.0f}"
        )
