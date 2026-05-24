import json, re, subprocess, sys
from tqdm import tqdm
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Multi-run mode: each base_prefix is a set of inference-perf jobs, one per 
# concurrency level. The script lists GCS for the latest <SUFFIX> per 
# (base_prefix, c) and loads stage_0 reports.
RUNS = [

    ("k8",                        "code-generation-infperf-baseline-det-1-final",          "#1f77b4", "o"),
    ("epp prefix-cache filter + modified token-load 286k",      "code-generation-infperf-epp-penalty286k-modified-tokens-scorer-max-picker-no-exp-det-1-final", "black", "x"),
    ("epp default",            "code-generation-infperf-epp-max-score-det-1-final", "#2ca02c", "x"),
    ("latency-predictor 5s",  "code-generation-infperf-epp-penalty5s-latency-predictor-det-2-final", "#9467bd", "x"),
]

BUCKET = "gs://kaushikmitra-llm-ig-benchmark/workload-catalog-runs"
GMP_PROJECT = "kaushikmitra-gke-dev"
GMP_URL = f"https://monitoring.googleapis.com/v1/projects/{GMP_PROJECT}/location/global/prometheus/api/v1/query"
SCRAPE_INTERVAL = 15
SCRAPE_BUFFER = 2
FAIL_RATE_THRESHOLD = 0.60
FAIL_COUNT_FLOOR = 3
MIN_DISPATCHED = 30


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
    pat = re.compile(rf"({re.escape(base_prefix)}-c(\d+)-(\d+))summary_lifecycle_metrics\.json$")
    latest = {}  # conc -> (suffix, full_prefix)
    for line in listing.splitlines():
        m = pat.search(line)
        if m:
            full_prefix, conc, suffix = m.group(1), int(m.group(2)), int(m.group(3))
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
    """Peak cluster-wide pending depth (sum across replicas)."""
    eval_time, window = _get_eval_window(prefix, duration, offset)
    q = f"sum(max_over_time(vllm:num_requests_waiting[{window}s]))"
    return gmp_query(q, eval_time)


def gmp_running_req_total(prefix, duration, offset=0):
    """Mean cluster-wide concurrent in-flight count."""
    eval_time, window = _get_eval_window(prefix, duration, offset)
    q = f"sum(avg_over_time(vllm:num_requests_running[{window}s]))"
    return gmp_query(q, eval_time)


def gmp_running_req_max_per_endpoint(prefix, duration, offset=0):
    """Peak concurrent in-flight requests on any single pod."""
    eval_time, window = _get_eval_window(prefix, duration, offset)
    q = f"max(max_over_time(vllm:num_requests_running[{window}s]))"
    return gmp_query(q, eval_time)

def gmp_ttft_p50(prefix, duration, offset=0):
    eval_time, window = _get_eval_window(prefix, duration, offset)
    q = f"histogram_quantile(0.50, sum by (le) (rate(vllm:time_to_first_token_seconds_bucket[{window}s])))"
    return gmp_query(q, eval_time)

def gmp_ttft_p90(prefix, duration, offset=0):
    eval_time, window = _get_eval_window(prefix, duration, offset)
    q = f"histogram_quantile(0.90, sum by (le) (rate(vllm:time_to_first_token_seconds_bucket[{window}s])))"
    return gmp_query(q, eval_time)

def gmp_ttft_p95(prefix, duration, offset=0):
    eval_time, window = _get_eval_window(prefix, duration, offset)
    q = f"histogram_quantile(0.95, sum by (le) (rate(vllm:time_to_first_token_seconds_bucket[{window}s])))"
    return gmp_query(q, eval_time)

def gmp_ttft_p99(prefix, duration, offset=0):
    eval_time, window = _get_eval_window(prefix, duration, offset)
    q = f"histogram_quantile(0.99, sum by (le) (rate(vllm:time_to_first_token_seconds_bucket[{window}s])))"
    return gmp_query(q, eval_time)

def gmp_tpot_p50(prefix, duration, offset=0):
    eval_time, window = _get_eval_window(prefix, duration, offset)
    q = f"histogram_quantile(0.50, sum by (le) (rate(vllm:request_time_per_output_token_seconds_bucket[{window}s])))"
    return gmp_query(q, eval_time) * 1000.0

def gmp_tpot_p90(prefix, duration, offset=0):
    eval_time, window = _get_eval_window(prefix, duration, offset)
    q = f"histogram_quantile(0.90, sum by (le) (rate(vllm:request_time_per_output_token_seconds_bucket[{window}s])))"
    return gmp_query(q, eval_time) * 1000.0

def gmp_tpot_p95(prefix, duration, offset=0):
    eval_time, window = _get_eval_window(prefix, duration, offset)
    q = f"histogram_quantile(0.95, sum by (le) (rate(vllm:request_time_per_output_token_seconds_bucket[{window}s])))"
    return gmp_query(q, eval_time) * 1000.0

def gmp_tpot_p99(prefix, duration, offset=0):
    eval_time, window = _get_eval_window(prefix, duration, offset)
    q = f"histogram_quantile(0.99, sum by (le) (rate(vllm:request_time_per_output_token_seconds_bucket[{window}s])))"
    return gmp_query(q, eval_time) * 1000.0

def gmp_e2e_p50(prefix, duration, offset=0):
    eval_time, window = _get_eval_window(prefix, duration, offset)
    q = f"histogram_quantile(0.50, sum by (le) (rate(vllm:e2e_request_latency_seconds_bucket[{window}s])))"
    return gmp_query(q, eval_time)

def gmp_e2e_p90(prefix, duration, offset=0):
    eval_time, window = _get_eval_window(prefix, duration, offset)
    q = f"histogram_quantile(0.90, sum by (le) (rate(vllm:e2e_request_latency_seconds_bucket[{window}s])))"
    return gmp_query(q, eval_time)

def gmp_e2e_p95(prefix, duration, offset=0):
    eval_time, window = _get_eval_window(prefix, duration, offset)
    q = f"histogram_quantile(0.95, sum by (le) (rate(vllm:e2e_request_latency_seconds_bucket[{window}s])))"
    return gmp_query(q, eval_time)

def gmp_e2e_p99(prefix, duration, offset=0):
    eval_time, window = _get_eval_window(prefix, duration, offset)
    q = f"histogram_quantile(0.99, sum by (le) (rate(vllm:e2e_request_latency_seconds_bucket[{window}s])))"
    return gmp_query(q, eval_time)


def gather(label, base_prefix):
    discovered = discover(base_prefix)
    if not discovered:
        # print(f"  [{label}] no reports found for base prefix '{base_prefix}'", file=sys.stderr)
        return []

    rows = []
    for c in tqdm(sorted(discovered), desc=f"Loading {label}", leave=False):
        prefix = discovered[c]
        try:
            life = load(prefix, "lifecycle_metrics", 0)
            try:
                prom = load(prefix, "prometheus_metrics", 0)
            except subprocess.CalledProcessError:
                prom = None
            
            dispatched = life["load_summary"]["count"]
            failures = life["failures"]["count"]
            fail_rate = failures / dispatched if dispatched else 0.0
            qps = life["load_summary"].get("achieved_rate", 0.0)

            if dispatched < MIN_DISPATCHED:
                # print(f"  drop [{label}] c={c}: only {dispatched} dispatched", file=sys.stderr)
                continue
            
            lat = life["successes"]["latency"]
            duration = life["benchmark_time_seconds"]
            
            row = {
                "conc": c,
                "achieved_rate": qps,
                "success_rate": (1.0 - fail_rate) * 100,
                "ttft_p50":  lat["time_to_first_token"]["median"],
                "ttft_p90":  lat["time_to_first_token"]["p90"],
                "ttft_p95":  lat["time_to_first_token"].get("p95", 0.0),
                "ttft_p99":  lat["time_to_first_token"].get("p99", 0.0),
                "tpot_p50":  lat["time_per_output_token"]["median"] * 1000,
                "tpot_p90":  lat["time_per_output_token"]["p90"] * 1000,
                "tpot_p95":  lat["time_per_output_token"].get("p95", 0.0) * 1000,
                "tpot_p99":  lat["time_per_output_token"].get("p99", 0.0) * 1000,
                "e2e_p50":   lat["request_latency"]["median"],
                "e2e_p90":   lat["request_latency"]["p90"],
                "e2e_p95":   lat["request_latency"].get("p95", 0.0),
                "e2e_p99":   lat["request_latency"].get("p99", 0.0),
                "out_per_sec": life["successes"]["throughput"]["output_tokens_per_sec"],
                "in_per_sec": life["successes"]["throughput"]["input_tokens_per_sec"],
                "vllm_ttft_p50": gmp_ttft_p50(prefix, duration),
                "vllm_ttft_p90": gmp_ttft_p90(prefix, duration),
                "vllm_ttft_p95": gmp_ttft_p95(prefix, duration),
                "vllm_ttft_p99": gmp_ttft_p99(prefix, duration),
                "vllm_tpot_p50": gmp_tpot_p50(prefix, duration),
                "vllm_tpot_p90": gmp_tpot_p90(prefix, duration),
                "vllm_tpot_p95": gmp_tpot_p95(prefix, duration),
                "vllm_tpot_p99": gmp_tpot_p99(prefix, duration),
                "vllm_e2e_p50":  gmp_e2e_p50(prefix, duration),
                "vllm_e2e_p90":  gmp_e2e_p90(prefix, duration),
                "vllm_e2e_p95":  gmp_e2e_p95(prefix, duration),
                "vllm_e2e_p99":  gmp_e2e_p99(prefix, duration),
                "prefix_hit_pct": gmp_prefix_hit_pct(prefix, duration),
                "prompt_tokens_p50": life["successes"]["prompt_len"]["median"],
                "prompt_tokens_p90": life["successes"]["prompt_len"]["p90"],
                "gen_tokens_p50":    life["successes"]["output_len"]["median"],
                "gen_tokens_p90":    life["successes"]["output_len"]["p90"],
            }
            if prom:
                row.update({
                    "kv_cache_mean": prom["successes"]["kv_cache_usage_percentage"]["mean"] * 100,
                    "kv_cache_p90":  prom["successes"]["kv_cache_usage_percentage"]["p90"] * 100,
                    "queue_len_mean": gmp_queue_len(prefix, duration),
                    "running_req_total": gmp_running_req_total(prefix, duration),
                    "running_req_max_per_endpoint": gmp_running_req_max_per_endpoint(prefix, duration),
                    "queue_time_mean": prom["successes"]["request_queue_time"]["mean"],
                    "queue_time_p90":  prom["successes"]["request_queue_time"]["p90"],
                    "queue_time_p99":  prom["successes"]["request_queue_time"]["p99"],
                })
            else:
                row.update({
                    "kv_cache_mean": 0.0, "kv_cache_p90": 0.0, "queue_len_mean": 0.0,
                    "running_req_total": 0.0, "running_req_max_per_endpoint": 0.0,
                    "queue_time_mean": 0.0, "queue_time_p90": 0.0, "queue_time_p99": 0.0,
                })
            rows.append(row)
        except Exception as e:
            print(f"  [{label}] c={c}: {e}", file=sys.stderr)
    return rows


runs = [(label, gather(label, base), color, marker) for label, base, color, marker in tqdm(RUNS, desc="Processing runs")]
runs = [r for r in runs if r[1]]

if not runs:
    sys.exit("no runs with data found")

fig, axes = plt.subplots(10, 3, figsize=(22, 50))
ax = axes.flatten()


def plot_pair(idx, key_lo, key_hi, ylabel, title):
    lo_label = key_lo.rsplit("_", 1)[-1]
    hi_label = key_hi.rsplit("_", 1)[-1] if key_hi else None
    for label, rows, color, marker in runs:
        ax[idx].plot([r["conc"] for r in rows], [r[key_lo] for r in rows], f"{marker}-", label=f"{label} {lo_label}", color=color)
        if key_hi:
            ax[idx].plot([r["conc"] for r in rows], [r[key_hi] for r in rows], f"{marker}--", label=f"{label} {hi_label}", color=color, alpha=0.5)
    ax[idx].set_xlabel("Concurrency")
    ax[idx].set_ylabel(ylabel); ax[idx].set_title(title)
    ax[idx].legend(fontsize=8); ax[idx].grid(alpha=0.3)


def plot_single(idx, key, ylabel, title):
    for label, rows, color, marker in runs:
        ax[idx].plot([r["conc"] for r in rows], [r[key] for r in rows], f"{marker}-", label=label, color=color)
    ax[idx].set_xlabel("Concurrency")
    ax[idx].set_ylabel(ylabel); ax[idx].set_title(title)
    ax[idx].legend(fontsize=8); ax[idx].grid(alpha=0.3)


# vLLM Latency Metrics
plot_single(0, "vllm_ttft_p50", "vLLM TTFT p50 (s)", "vLLM TTFT p50")
plot_single(1, "vllm_ttft_p90", "vLLM TTFT p90 (s)", "vLLM TTFT p90")
plot_single(2, "vllm_ttft_p95", "vLLM TTFT p95 (s)", "vLLM TTFT p95")

plot_single(3, "vllm_tpot_p50", "vLLM TPOT p50 (ms)", "vLLM TPOT p50")
plot_single(4, "vllm_tpot_p90", "vLLM TPOT p90 (ms)", "vLLM TPOT p90")
plot_single(5, "vllm_tpot_p95", "vLLM TPOT p95 (ms)", "vLLM TPOT p95")

plot_single(6, "vllm_e2e_p50", "vLLM E2E p50 (s)", "vLLM E2E Latency p50")
plot_single(7, "vllm_e2e_p90", "vLLM E2E p90 (s)", "vLLM E2E Latency p90")
plot_single(8, "vllm_e2e_p95", "vLLM E2E p95 (s)", "vLLM E2E Latency p95")

# Client Latency Metrics
plot_single(9, "ttft_p50", "Client TTFT p50 (s)", "Client TTFT p50")
plot_single(10, "ttft_p90", "Client TTFT p90 (s)", "Client TTFT p90")
plot_single(11, "ttft_p95", "Client TTFT p95 (s)", "Client TTFT p95")

plot_single(12, "tpot_p50", "Client TPOT p50 (ms)", "Client TPOT p50")
plot_single(13, "tpot_p90", "Client TPOT p90 (ms)", "Client TPOT p90")
plot_single(14, "tpot_p95", "Client TPOT p95 (ms)", "Client TPOT p95")

plot_single(15, "e2e_p50", "Client E2E p50 (s)", "Client E2E Latency p50")
plot_single(16, "e2e_p90", "Client E2E p90 (s)", "Client E2E Latency p90")
plot_single(17, "e2e_p95", "Client E2E p95 (s)", "Client E2E Latency p95")

# Throughput and Load
plot_single(18, "out_per_sec", "output tokens / sec", "Throughput (mean output tokens/s)")
plot_single(19, "in_per_sec", "input tokens / sec", "Throughput (mean input tokens/s)")
plot_single(20, "achieved_rate", "Achieved QPS", "Achieved Rate")

# Efficiency and Queue
plot_pair(21, "kv_cache_mean", "kv_cache_p90", "KV cache usage (%)", "KV Cache Usage (mean/p90)")
plot_single(22, "prefix_hit_pct", "prefix cache hit (%)", "Prefix Cache Hit Rate")
plot_single(23, "queue_len_mean", "queue length (cluster, sum_max)", "Queue Size (vllm:num_requests_waiting)")

# Workload Characteristics
plot_pair(24, "prompt_tokens_p50", "prompt_tokens_p90", "input tokens / request", "Prompt Size (p50/p90)")
plot_pair(25, "gen_tokens_p50", "gen_tokens_p90", "output tokens / request", "Output Size (p50/p90)")
plot_single(26, "queue_time_mean", "queue wait mean (s)", "Request Queue Wait Time")

# System State
plot_single(27, "running_req_total", "total running requests", "Cluster-wide Concurrent In-Flight")
plot_single(28, "running_req_max_per_endpoint", "max running requests / endpoint", "Max Concurrent Requests per Endpoint")
plot_single(29, "success_rate", "Success Rate (%)", "Request Success Rate")

plt.suptitle(" vs ".join(label for label, _, _, _ in RUNS if label in [r[0] for r in runs]) + "   (inference-perf code-generation runs)", fontsize=12)
plt.tight_layout()
import os
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "baseline_vs_epp_codegen_runs.png")
plt.savefig(out, dpi=120, bbox_inches="tight")
print(f"saved: {out}")

print()
header = (
    f"{'conc':>5} | {'Success%':>8} | "
    f"{'vTTFT p50':>10} | {'vTTFT p90':>10} | {'vTTFT p95':>10} | {'vTTFT p99':>10} | "
    f"{'TTFT p50':>10} | {'TTFT p90':>10} | {'TTFT p95':>10} | {'TTFT p99':>10} | "
    f"{'vTPOT p50':>10} | {'vTPOT p90':>10} | {'vTPOT p95':>10} | {'vTPOT p99':>10} | "
    f"{'TPOT p50':>10} | {'TPOT p90':>10} | {'TPOT p95':>10} | {'TPOT p99':>10} | "
    f"{'vE2E p50':>10} | {'vE2E p90':>10} | {'vE2E p95':>10} | {'vE2E p99':>10} | "
    f"{'E2E p50':>10} | {'E2E p90':>10} | {'E2E p95':>10} | {'E2E p99':>10} | "
    f"{'out_t/s':>8} | {'in_t/s':>8} | {'ach_qps':>8} | "
    f"{'kv_mean':>8} | {'prefix%':>8} | {'q_size':>8} | {'q_wait':>8} | "
    f"{'run_tot':>8} | {'run_max':>8}"
)
print(header)
for label, rows, *_ in runs:
    print(f"--- {label} ---")
    for r in rows:
        print(
            f"{r['conc']:5d} | "
            f"{r['success_rate']:7.1f}% | "
            f"{r['vllm_ttft_p50']:9.1f}s | "
            f"{r['vllm_ttft_p90']:9.1f}s | "
            f"{r['vllm_ttft_p95']:9.1f}s | "
            f"{r['vllm_ttft_p99']:9.1f}s | "
            f"{r['ttft_p50']:9.1f}s | "
            f"{r['ttft_p90']:9.1f}s | "
            f"{r['ttft_p95']:9.1f}s | "
            f"{r['ttft_p99']:9.1f}s | "
            f"{r['vllm_tpot_p50']:8.1f}ms | "
            f"{r['vllm_tpot_p90']:8.1f}ms | "
            f"{r['vllm_tpot_p95']:8.1f}ms | "
            f"{r['vllm_tpot_p99']:8.1f}ms | "
            f"{r['tpot_p50']:8.1f}ms | "
            f"{r['tpot_p90']:8.1f}ms | "
            f"{r['tpot_p95']:8.1f}ms | "
            f"{r['tpot_p99']:8.1f}ms | "
            f"{r['vllm_e2e_p50']:9.1f}s | "
            f"{r['vllm_e2e_p90']:9.1f}s | "
            f"{r['vllm_e2e_p95']:9.1f}s | "
            f"{r['vllm_e2e_p99']:9.1f}s | "
            f"{r['e2e_p50']:9.1f}s | "
            f"{r['e2e_p90']:9.1f}s | "
            f"{r['e2e_p95']:9.1f}s | "
            f"{r['e2e_p99']:9.1f}s | "
            f"{r['out_per_sec']:8.1f} | "
            f"{r['in_per_sec']:8.1f} | "
            f"{r['achieved_rate']:8.2f} | "
            f"{r['kv_cache_mean']:7.1f}% | "
            f"{r['prefix_hit_pct']:7.1f}% | "
            f"{r['queue_len_mean']:8.1f} | "
            f"{r['queue_time_mean']:8.1f}s | "
            f"{r['running_req_total']:8.1f} | "
            f"{r['running_req_max_per_endpoint']:8.1f}")
