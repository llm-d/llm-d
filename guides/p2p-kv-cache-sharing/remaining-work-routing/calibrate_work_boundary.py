#!/usr/bin/env python3
"""Calibrate the prefill work-range boundary for remaining-work routing.

Derives the long/short split from deployment-measurable quantities only.
No workload knowledge is required; the one policy input is the TTFT penalty
budget the operator is willing to impose on the short class, which the
load gate (`prefix-cache-affinity-filter`) already requires.

Model
-----
A request is "long" when its prefill occupies an engine rank longer than
the declared delay budget:

    boundary_max = peak_prefill_throughput * ttft_penalty            (SLO anchor)

Migration only pays above the pull-vs-recompute crossover, where pulling
the prefix over the P2P tier beats recomputing it:

    pull_time(t)    = pull_floor + t * pull_per_token
    recompute(t)    = t / peak_prefill_throughput
    crossover       = pull_floor / (1/throughput - pull_per_token)   (economic anchor)

The recommended boundary is the crossover rounded up to the next multiple
of the KV block size, clamped into [crossover, boundary_max]. If the band
is empty (crossover exceeds the SLO-equivalent work), pulls cannot pay off
within the declared budget: raise the budget or do not split the pool.

Measured procedure
------------------
1. Peak per-rank prefill throughput: saturating batch of cold fixed-length
   prompts against every DP rank port of ONE prefill engine, aggregate
   prompt tokens / wall time / ranks. Run against the engine's direct
   Service (bypassing the EPP) on an otherwise idle engine.
2. KV bytes per token: delta(kv_offload_store_bytes_total) /
   delta(prompt_tokens_total) across the same run, summed over ranks.
   Requires the CPU offload tier to be enabled (it stores prompt blocks).
3. Pull cost parameters: defaults are measured values from the GLM-5.2
   upstream-tier study (floor 1.25 s, 134 us/token effective, which
   reproduces the independently measured 8.6K-token crossover within 1%).
   Override with values measured on your fabric when available; the pull
   floor dominates, so measure it first (time one seeded cross-engine
   pull and subtract the tail-compute time).

Run from a pod inside the cluster (python3, stdlib only):

    python3 calibrate_work_boundary.py \
        --engine-host glm-5-2-prefill-long-direct \
        --model zai-org/GLM-5.2-FP8 \
        --ranks 8 --ttft-penalty-ms 3500 --block-size 64

Emits the measurement report and a ready-to-paste EPP/label config block.
"""
import argparse
import json
import sys
import threading
import time
import urllib.request

def post_completion(url, model, prompt_tokens, timeout):
    body = json.dumps({"model": model, "prompt": prompt_tokens,
                       "max_tokens": 1, "temperature": 0}).encode()
    req = urllib.request.Request(url + "/v1/completions", data=body,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        r.read()

def scrape(host, port, names):
    out = dict.fromkeys(names, 0.0)
    try:
        m = urllib.request.urlopen(f"http://{host}:{port}/metrics",
                                   timeout=10).read().decode()
    except Exception:
        return None
    for ln in m.splitlines():
        if ln.startswith("#"):
            continue
        for n in names:
            if "vllm:" + n + "{" in ln:
                out[n] += float(ln.rsplit(" ", 1)[1])
    return out

def scrape_all_ranks(host, port_base, ranks, names):
    tot = dict.fromkeys(names, 0.0)
    seen = 0
    for r in range(ranks):
        s = scrape(host, port_base + r, names)
        if s is None:
            continue
        seen += 1
        for n in names:
            tot[n] += s[n]
    return tot, seen

COUNTERS = ["prompt_tokens_total", "kv_offload_store_bytes_total"]

def measure_throughput(args):
    prompt_len = args.probe_prompt_tokens
    per_rank = args.probe_requests_per_rank
    rng_base = 0x43414C  # deterministic, distinct from campaign salts
    before, ranks_seen = scrape_all_ranks(args.engine_host, args.port_base,
                                          args.ranks, COUNTERS)
    if ranks_seen != args.ranks:
        print(f"warning: scraped {ranks_seen}/{args.ranks} rank metric "
              "endpoints; throughput will use the ranks that responded",
              file=sys.stderr)
    errors = []

    def worker(rank, i):
        import random
        rng = random.Random(rng_base + rank * 1000 + i)
        p = [rng.randrange(1000, 11000) for _ in range(prompt_len)]
        url = f"http://{args.engine_host}:{args.port_base + rank}"
        try:
            post_completion(url, args.model, p, args.probe_timeout_s)
        except Exception as e:
            errors.append(f"rank{rank}/{i}: {e}")

    t0 = time.time()
    threads = [threading.Thread(target=worker, args=(r, i))
               for r in range(args.ranks) for i in range(per_rank)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.time() - t0
    after, _ = scrape_all_ranks(args.engine_host, args.port_base,
                                args.ranks, COUNTERS)
    if errors:
        print(f"warning: {len(errors)} probe requests failed "
              f"(first: {errors[0]})", file=sys.stderr)
    tokens = after["prompt_tokens_total"] - before["prompt_tokens_total"]
    stored = (after["kv_offload_store_bytes_total"]
              - before["kv_offload_store_bytes_total"])
    if tokens <= 0:
        sys.exit("no prompt tokens observed; is the engine idle and "
                 "reachable on its rank ports?")
    throughput_per_rank = tokens / wall / max(ranks_seen, 1)
    bytes_per_token = stored / tokens if stored > 0 else None
    return throughput_per_rank, bytes_per_token, tokens, wall

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine-host", required=True,
                    help="direct Service or pod IP of ONE idle prefill engine")
    ap.add_argument("--model", required=True)
    ap.add_argument("--ranks", type=int, default=8)
    ap.add_argument("--port-base", type=int, default=8000)
    ap.add_argument("--ttft-penalty-ms", type=float, required=True,
                    help="SLO: tolerated added TTFT for the short class "
                         "(same value as the load gate's maxTTFTPenaltyMs)")
    ap.add_argument("--block-size", type=int, default=64,
                    help="KV block size tokens (GLM 64, Llama 128)")
    ap.add_argument("--max-context", type=int, default=120000)
    ap.add_argument("--pull-floor-s", type=float, default=1.25,
                    help="fixed cost of one P2P pull (measured default)")
    ap.add_argument("--pull-per-token-us", type=float, default=134.0,
                    help="marginal pull cost per token (measured default)")
    ap.add_argument("--probe-prompt-tokens", type=int, default=16384)
    ap.add_argument("--probe-requests-per-rank", type=int, default=2)
    ap.add_argument("--probe-timeout-s", type=float, default=300.0)
    args = ap.parse_args()

    thr, bpt, tokens, wall = measure_throughput(args)

    per_token_compute_us = 1e6 / thr
    per_token_pull_us = args.pull_per_token_us
    if per_token_pull_us >= per_token_compute_us:
        crossover = None  # pulling never beats recomputing on this setup
    else:
        crossover = args.pull_floor_s * 1e6 / (per_token_compute_us
                                               - per_token_pull_us)
    slo_tokens = thr * args.ttft_penalty_ms / 1000.0

    bs = args.block_size
    print("== measurements")
    print(f"peak prefill throughput: {thr:,.0f} tok/s per rank "
          f"({tokens:,.0f} tokens / {wall:.1f} s / ranks)")
    if bpt:
        print(f"kv bytes per token:      {bpt/1024:,.1f} KiB (from offload "
              "store counters)")
    else:
        print("kv bytes per token:      unavailable (offload tier counters "
              "did not move; enable the CPU tier or ignore)")
    print(f"pull model:              floor {args.pull_floor_s:.2f} s + "
          f"{per_token_pull_us:.0f} us/token (defaults unless overridden)")
    print()
    print("== derived anchors")
    if crossover is None:
        print("pull crossover:          NONE - marginal pull cost exceeds "
              "compute cost; pulls never pay, do not split the pool")
        return
    print(f"pull crossover:          {crossover:,.0f} tokens "
          "(below this, recompute beats pulling)")
    print(f"SLO-equivalent work:     {slo_tokens:,.0f} tokens "
          f"({args.ttft_penalty_ms:.0f} ms at measured throughput)")
    if crossover > slo_tokens:
        print()
        print("EMPTY BAND: the crossover exceeds the SLO-equivalent work.")
        print("Pulls cannot pay off within the declared TTFT budget.")
        print("Raise --ttft-penalty-ms or do not split the prefill pool.")
        return
    boundary = int(-(-crossover // bs) * bs)  # round up to block size
    boundary = min(max(boundary, bs), int(slo_tokens))
    print()
    print("== recommendation")
    print(f"boundary: {boundary:,d} tokens "
          f"(band [{crossover:,.0f}, {slo_tokens:,.0f}], block-aligned)")
    print()
    print("== config to apply")
    print(f"""# short-work prefill class (pod template label)
llm-d.ai/prefill-work-range: "0-{boundary}"
# long-work prefill class
llm-d.ai/prefill-work-range: "{boundary + 1}-{args.max_context}"

# EPP plugin parameters (keep consistent with the labels above)
- type: prefix-cache-affinity-filter
  parameters:
    peakPrefillThroughput: {thr:.0f}
    maxTTFTPenaltyMs: {args.ttft_penalty_ms:.0f}
- type: context-length-aware
  parameters:
    label: llm-d.ai/prefill-work-range
    enableFiltering: false
    reusableTokensProducerName: p2p-cache-source
# note: with minCachedTokenDelta D > 1, routed length is tail + D - 1;
# keep D = 1, or raise the boundary by D per the delta-double-duty rule.""")

main()
