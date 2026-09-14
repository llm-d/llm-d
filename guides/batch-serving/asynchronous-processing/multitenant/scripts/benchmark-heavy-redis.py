#!/usr/bin/env python3
"""
Heavy Multi-Tenant Benchmark for Async Processor (Redis SortedSet backend).
Dispatches 600 requests across all 6 tier-priority queues (team × tier × model)
and monitors multi-minute in-process queue draining and upstream completion.
"""

import argparse
import json
import os
import random
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request

NAMESPACE = os.environ.get("NAMESPACE", "llm-d-async")
REDIS_DEPLOY = os.environ.get("REDIS_DEPLOY", "deploy/redis")
MODEL = os.environ.get("MODEL", "Qwen/Qwen3-8B")
PROM_URL = os.environ.get("PROM_URL", "http://localhost:9090")
MONITORING_NAMESPACE = os.environ.get("MONITORING_NAMESPACE", "llm-d-monitoring")
TTL = 3600  # 1 hour deadline

QUEUES_SPEC = [
    ("team-premium-a", "premium", "a", 150),
    ("team-standard-a", "standard", "a", 100),
    ("team-batch-a", "batch", "a", 50),
    ("team-premium-b", "premium", "b", 150),
    ("team-standard-b", "standard", "b", 100),
    ("team-batch-b", "batch", "b", 50),
]

def check_port_open(host="127.0.0.1", port=9090):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex((host, port)) == 0

def start_prom_port_forward(namespace=MONITORING_NAMESPACE, local_port=9090):
    if check_port_open("127.0.0.1", local_port):
        return None
    print(f"[*] Starting background port-forward to svc/llmd-kube-prometheus-stack-prometheus on port {local_port}...")
    proc = subprocess.Popen(
        ["kubectl", "port-forward", "-n", namespace, "svc/llmd-kube-prometheus-stack-prometheus", f"{local_port}:9090"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    time.sleep(2)
    return proc

def clear_existing_results():
    print("[*] Clearing previous results lists...")
    subprocess.run(
        ["kubectl", "-n", NAMESPACE, "exec", REDIS_DEPLOY, "--", "redis-cli", "DEL", "results-a-list", "results-b-list"],
        capture_output=True, text=True
    )

def enqueue_all(model_name=MODEL, max_tokens=80):
    now = int(time.time())
    dl = now + TTL
    run_id = f"{now}-{random.randint(1000, 9999)}"
    total_enqueued = 0

    print(f"[*] Enqueuing 600 multi-tenant requests across 6 queues (max_tokens={max_tokens}, TTL={TTL}s)...")
    for q_name, team, model, count in QUEUES_SPEC:
        zadd_args = []
        for i in range(1, count + 1):
            msg_id = f"bench-{team}-{model}-{run_id}-{i:04d}"
            msg_obj = {
                "internal": {},
                "request_kind": "plain",
                "data": {
                    "id": msg_id,
                    "created": now,
                    "deadline": dl,
                    "payload": {
                        "model": model_name,
                        "prompt": f"Write an informative paragraph explaining distributed asynchronous architectures and queue buffering, request #{i}.",
                        "max_tokens": max_tokens
                    },
                    "metadata": {
                        "team": team
                    }
                }
            }
            zadd_args.extend([str(dl), json.dumps(msg_obj)])

        # Send in chunks of 50 pairs
        for chunk_idx in range(0, len(zadd_args), 100):
            chunk = zadd_args[chunk_idx:chunk_idx + 100]
            cmd = ["kubectl", "-n", NAMESPACE, "exec", "-i", REDIS_DEPLOY, "--", "redis-cli", "ZADD", q_name] + chunk
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode != 0:
                print(f"[!] Error on {q_name}: {res.stderr.strip()}")
        print(f"  [+] {q_name}: Enqueued {count} requests")
        total_enqueued += count

    print(f"[*] Total requests successfully enqueued into Redis: {total_enqueued}\n")
    return total_enqueued

def query_prom(query, prom_url=None):
    if prom_url is None:
        prom_url = PROM_URL
    try:
        url = f"{prom_url}/api/v1/query?query={urllib.parse.quote(query)}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = data.get("data", {}).get("result", [])
            if results:
                return float(results[0]["value"][1])
    except Exception:
        pass
    return 0.0

def monitor_drain(total_enqueued, max_wait_sec=600, prom_url=None):
    print("[*] Monitoring drain progress across Redis, In-Process Queues, and GPU...")
    start_time = time.time()

    while time.time() - start_time < max_wait_sec:
        elapsed = int(time.time() - start_time)

        # Query Redis results
        res_a = subprocess.run(
            ["kubectl", "-n", NAMESPACE, "exec", REDIS_DEPLOY, "--", "redis-cli", "LLEN", "results-a-list"],
            capture_output=True, text=True
        )
        res_b = subprocess.run(
            ["kubectl", "-n", NAMESPACE, "exec", REDIS_DEPLOY, "--", "redis-cli", "LLEN", "results-b-list"],
            capture_output=True, text=True
        )
        try:
            done_a = int(res_a.stdout.strip())
            done_b = int(res_b.stdout.strip())
        except Exception:
            done_a, done_b = 0, 0

        # Query Redis remaining backlog
        q_backlog = 0
        for q_name, _, _, _ in QUEUES_SPEC:
            res = subprocess.run(
                ["kubectl", "-n", NAMESPACE, "exec", REDIS_DEPLOY, "--", "redis-cli", "ZCARD", q_name],
                capture_output=True, text=True
            )
            try:
                q_backlog += int(res.stdout.strip())
            except Exception:
                pass

        total_done = done_a + done_b
        pct = (total_done / total_enqueued) * 100 if total_enqueued > 0 else 0

        # Query Prometheus live gauges
        in_flight = int(query_prom("sum(llm_d_async_async_inflight_requests)", prom_url=prom_url))
        q_depth = int(query_prom("sum(llm_d_async_async_queue_depth)", prom_url=prom_url))

        print(f"[{elapsed:03d}s] Completed: {total_done:3d}/{total_enqueued} ({pct:5.1f}%) | In-Flight: {in_flight:2d} | In-Process Depth: {q_depth:3d} | Redis ZCARD: {q_backlog:3d} | A: {done_a:3d}, B: {done_b:3d}")

        if total_done >= total_enqueued:
            print(f"\n[+] SUCCESS: All {total_enqueued} requests served and completed in {elapsed}s!")
            return True

        time.sleep(10)

    print(f"\n[*] Reached max wait time of {max_wait_sec}s.")
    return False

def main():
    parser = argparse.ArgumentParser(description="Heavy multi-tenant Redis benchmark")
    parser.add_argument("--tokens", type=int, default=80, help="Max completion tokens per request")
    parser.add_argument("--timeout", type=int, default=600, help="Max wait duration in seconds")
    parser.add_argument("--prom-url", type=str, default=PROM_URL, help="Prometheus base URL (default: http://localhost:9090)")
    args = parser.parse_args()

    pf_proc = None
    if "localhost" in args.prom_url or "127.0.0.1" in args.prom_url:
        pf_proc = start_prom_port_forward()

    try:
        clear_existing_results()
        total = enqueue_all(max_tokens=args.tokens)
        monitor_drain(total, max_wait_sec=args.timeout, prom_url=args.prom_url)
    finally:
        if pf_proc:
            pf_proc.terminate()

if __name__ == "__main__":
    main()
