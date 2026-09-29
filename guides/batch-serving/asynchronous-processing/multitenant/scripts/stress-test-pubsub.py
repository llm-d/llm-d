#!/usr/bin/env python3
"""
Stress test script for Multi-Tenant Async Processing using GCP Pub/Sub backend.
Generates multi-tenant traffic (team × tier × model) and drives background
saturation load on the router to populate Prometheus, Grafana, and Cloud Monitoring metrics.

Usage:
  ./scripts/stress-test-pubsub.py
  PROJECT_ID=my-project NAMESPACE=llm-d-async ./scripts/stress-test-pubsub.py
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

DEFAULT_PROJECT = os.environ.get("PROJECT_ID")
if not DEFAULT_PROJECT:
    try:
        out = subprocess.check_output(
            ["gcloud", "config", "get-value", "project"], text=True
        ).strip()
        if out and out != "(unset)":
            DEFAULT_PROJECT = out
    except Exception:
        DEFAULT_PROJECT = None

DEFAULT_NAMESPACE = os.environ.get("NAMESPACE", "llm-d-async")
DEFAULT_MODEL_A = os.environ.get("MODEL_A", "Qwen/Qwen3-8B")
DEFAULT_MODEL_B = os.environ.get("MODEL_B", "Qwen/Qwen3-8B")

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

def publish_message(project_id, topic, team, model_code, model_name, seq_id, ttl=600):
    now = int(time.time())
    dl = now + ttl
    msg_id = f"stress-{team}-{model_code}-{now}-{seq_id:04d}"
    payload = {
        "id": msg_id,
        "created": now,
        "deadline": dl,
        "payload": {
            "model": model_name,
            "prompt": f"Summarize key trends in async multi-tenant queue processing request #{seq_id}",
            "max_tokens": 32
        },
        "metadata": {
            "team": team
        }
    }
    cmd = [
        "gcloud", "pubsub", "topics", "publish", topic,
        "--project", project_id,
        "--attribute", f"team={team}",
        "--message", json.dumps(payload)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.returncode == 0, msg_id

def publish_traffic(project_id, model_a, model_b):
    topics_spec = [
        ("team-premium-a-requests", "premium", "a", model_a, 15),
        ("team-standard-a-requests", "standard", "a", model_a, 10),
        ("team-batch-a-requests", "batch", "a", model_a, 5),
        ("team-premium-b-requests", "premium", "b", model_b, 15),
        ("team-standard-b-requests", "standard", "b", model_b, 10),
        ("team-batch-b-requests", "batch", "b", model_b, 5),
    ]
    print(f"[*] Publishing multi-tenant requests across 6 Pub/Sub topics...")
    tasks = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        for topic, team, m_code, m_name, count in topics_spec:
            for i in range(count):
                tasks.append(executor.submit(
                    publish_message, project_id, topic, team, m_code, m_name, i + 1
                ))
        success = 0
        failed = 0
        for f in as_completed(tasks):
            ok, mid = f.result()
            if ok:
                success += 1
            else:
                failed += 1
    print(f"[*] Finished publishing: {success} succeeded, {failed} failed.")

def main():
    parser = argparse.ArgumentParser(description="Multi-tenant async processor Pub/Sub stress test")
    parser.add_argument("--project", default=DEFAULT_PROJECT, help="GCP Project ID")
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE, help="Kubernetes namespace")
    parser.add_argument("--model-a", default=DEFAULT_MODEL_A, help="Model A name")
    parser.add_argument("--model-b", default=DEFAULT_MODEL_B, help="Model B name")
    parser.add_argument("--duration", type=int, default=60, help="Saturation duration in seconds")
    parser.add_argument("--igw-port", type=int, default=8080, help="Local port for router gateway")
    args = parser.parse_args()

    if not args.project:
        print("[!] Error: GCP Project ID is required.")
        print("    Please provide --project or set the PROJECT_ID environment variable.")
        sys.exit(1)

    print("==========================================================")
    print("Multi-Tenant Async Processor — GCP Pub/Sub Stress Test")
    print(f"Project:    {args.project}")
    print(f"Namespace:  {args.namespace}")
    print(f"Model A:    {args.model_a}")
    print(f"Model B:    {args.model_b}")
    print(f"Duration:   {args.duration}s")
    print("==========================================================")

    pf_proc = start_port_forward(args.namespace, args.igw_port)
    igw_url = f"http://127.0.0.1:{args.igw_port}"

    try:
        with ThreadPoolExecutor(max_workers=2) as exec_main:
            bg_future = exec_main.submit(send_background_load, igw_url, args.model_a, args.duration)
            time.sleep(6)  # allow saturation to reach threshold
            pub_future = exec_main.submit(publish_traffic, args.project, args.model_a, args.model_b)
            pub_future.result()
            bg_future.result()

        print("[*] Load complete. Allowing 15s for queues to settle...")
        time.sleep(15)
        print("[+] Stress test finished successfully.")
    finally:
        if pf_proc:
            pf_proc.terminate()
            pf_proc.wait()
            print("[*] Port-forward closed.")

if __name__ == "__main__":
    main()
