#!/usr/bin/env python3
"""
Stress test script for Multi-Tenant Async Processing using Redis SortedSet backend.
Generates multi-tenant traffic (team × tier × model) and drives background
saturation load on the router to test quota classification, priority lanes,
and saturation back-off.

Usage:
  ./scripts/stress-test-redis.py
  NAMESPACE=llm-d-async ./scripts/stress-test-redis.py
"""

import argparse
import json
import os
import random
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

DEFAULT_NAMESPACE = os.environ.get("NAMESPACE", "llm-d-async")
DEFAULT_MODEL_A = os.environ.get("MODEL_A", "Qwen/Qwen3-8B")
DEFAULT_MODEL_B = os.environ.get("MODEL_B", "Qwen/Qwen3-8B")
DEFAULT_REDIS = os.environ.get("REDIS_DEPLOY", "deploy/redis")

def check_port_open(host="127.0.0.1", port=8080):
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex((host, port)) == 0

def start_port_forward(namespace, local_port=8080):
    if check_port_open("127.0.0.1", local_port):
        print(f"[*] Port {local_port} is already open, using existing endpoint.")
        return None
    print(f"[*] Starting port-forward to svc/llm-d-router-epp on port {local_port}...")
    proc = subprocess.Popen(
        ["kubectl", "port-forward", "-n", namespace, "svc/llm-d-router-epp", f"{local_port}:80"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    time.sleep(3)
    return proc

def send_background_load(igw_url, model_name, duration_sec=60, concurrency=6):
    print(f"[*] Starting background saturation load ({concurrency} concurrent workers for {duration_sec}s)...")
    start_time = time.time()
    req_count = 0

    def single_req():
        payload = json.dumps({
            "model": model_name,
            "prompt": "Write an extensive scientific treatise on distributed asynchronous message systems with high token generation.",
            "max_tokens": 160
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{igw_url}/v1/completions",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                resp.read()
                return True
        except Exception:
            return False

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = []
        while time.time() - start_time < duration_sec:
            while len(futures) < concurrency and (time.time() - start_time < duration_sec):
                futures.append(executor.submit(single_req))
            done = [f for f in futures if f.done()]
            for f in done:
                futures.remove(f)
                req_count += 1
            time.sleep(0.2)
        for f in as_completed(futures):
            req_count += 1

    print(f"[*] Background saturation load finished. Completed {req_count} completions.")

def publish_redis_queue(namespace, redis_deploy, queue_name, team, model_code, model_name, count, ttl=300):
    now = int(time.time())
    dl = now + ttl
    run_id = f"{now}-{random.randint(1000, 9999)}"
    
    # Build ZADD args in batches of 50
    batch_size = 50
    for chunk_start in range(1, count + 1, batch_size):
        chunk_end = min(chunk_start + batch_size, count + 1)
        zadd_args = []
        for i in range(chunk_start, chunk_end):
            msg_id = f"{team}-{model_code}-{run_id}-{i:04d}"
            msg_obj = {
                "internal": {},
                "request_kind": "plain",
                "data": {
                    "id": msg_id,
                    "created": now,
                    "deadline": dl,
                    "payload": {
                        "model": model_name,
                        "prompt": f"Summarize key trends in async multi-tenant queue processing request #{i}",
                        "max_tokens": 32
                    },
                    "metadata": {
                        "team": team
                    }
                }
            }
            zadd_args.extend([str(dl), json.dumps(msg_obj)])

        cmd = ["kubectl", "-n", namespace, "exec", "-i", redis_deploy, "--", "redis-cli", "ZADD", queue_name] + zadd_args
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            print(f"[!] Error publishing to {queue_name}: {res.stderr.strip()}")
            return False

    return True

def publish_traffic(namespace, redis_deploy, model_a, model_b):
    queues_spec = [
        ("team-premium-a", "premium", "a", model_a, 40),
        ("team-standard-a", "standard", "a", model_a, 25),
        ("team-batch-a", "batch", "a", model_a, 15),
        ("team-premium-b", "premium", "b", model_b, 40),
        ("team-standard-b", "standard", "b", model_b, 25),
        ("team-batch-b", "batch", "b", model_b, 15),
    ]
    print(f"[*] Publishing multi-tenant requests across 6 Redis SortedSet queues...")
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            executor.submit(
                publish_redis_queue, namespace, redis_deploy, q_name, team, m_code, m_name, count
            ): q_name for q_name, team, m_code, m_name, count in queues_spec
        }
        for f in as_completed(futures):
            q_name = futures[f]
            try:
                ok = f.result()
                if ok:
                    print(f"  [+] Enqueued messages into {q_name}")
            except Exception as e:
                print(f"  [!] Failed {q_name}: {e}")

def get_redis_stats(namespace, redis_deploy):
    print("\n[*] Checking Redis Quota Counters & Results:")
    try:
        keys_to_check = [
            "quota:a:team:premium", "quota:a:team:standard", "quota:a:team:batch",
            "quota:b:team:premium", "quota:b:team:standard", "quota:b:team:batch"
        ]
        for k in keys_to_check:
            res = subprocess.run(
                ["kubectl", "-n", namespace, "exec", redis_deploy, "--", "redis-cli", "GET", k],
                capture_output=True, text=True
            )
            val = res.stdout.strip() or "(nil)"
            print(f"  - {k}: {val}")

        for res_key in ["results-a-list", "results-b-list"]:
            res = subprocess.run(
                ["kubectl", "-n", namespace, "exec", redis_deploy, "--", "redis-cli", "LLEN", res_key],
                capture_output=True, text=True
            )
            length = res.stdout.strip() or "0"
            print(f"  - {res_key} length: {length}")
    except Exception as e:
        print(f"[!] Error querying Redis stats: {e}")

def main():
    parser = argparse.ArgumentParser(description="Multi-tenant async processor Redis stress test")
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE, help="Kubernetes namespace")
    parser.add_argument("--model-a", default=DEFAULT_MODEL_A, help="Model A name")
    parser.add_argument("--model-b", default=DEFAULT_MODEL_B, help="Model B name")
    parser.add_argument("--redis-deploy", default=DEFAULT_REDIS, help="Redis deployment name (default: deploy/redis)")
    parser.add_argument("--duration", type=int, default=60, help="Saturation duration in seconds")
    parser.add_argument("--igw-port", type=int, default=8080, help="Local port for router gateway")
    args = parser.parse_args()

    print("==========================================================")
    print("Multi-Tenant Async Processor — Redis SortedSet Stress Test")
    print(f"Namespace:    {args.namespace}")
    print(f"Redis Deploy: {args.redis_deploy}")
    print(f"Model A:      {args.model_a}")
    print(f"Model B:      {args.model_b}")
    print(f"Duration:     {args.duration}s")
    print("==========================================================")

    pf_proc = start_port_forward(args.namespace, args.igw_port)
    igw_url = f"http://127.0.0.1:{args.igw_port}"

    try:
        with ThreadPoolExecutor(max_workers=2) as exec_main:
            bg_future = exec_main.submit(send_background_load, igw_url, args.model_a, args.duration)
            time.sleep(6)  # allow saturation to reach threshold
            pub_future = exec_main.submit(publish_traffic, args.namespace, args.redis_deploy, args.model_a, args.model_b)
            pub_future.result()
            bg_future.result()

        print("[*] Load complete. Allowing 15s for queues to drain...")
        time.sleep(15)
        get_redis_stats(args.namespace, args.redis_deploy)
        print("\n[+] Redis stress test finished successfully.")
    finally:
        if pf_proc:
            pf_proc.terminate()
            pf_proc.wait()
            print("[*] Port-forward closed.")

if __name__ == "__main__":
    main()
