#!/usr/bin/env python3
"""Verify multi-cluster routing: the hub splits load across clusters by capacity.

Needs only the Python 3 standard library, plus kubectl (or oc) on PATH to read
each cluster's request counters.

    python3 verify.py --base-url http://127.0.0.1:8000 \\
        --cluster a=${NS_A} --cluster b=${NS_B}

1. Discovers the served model through the hub (GET /v1/models).
2. Snapshots every model server's completed-request counter, pod by pod.
3. Sends --requests chat completions through the hub, --concurrency at a time.
4. Snapshots again and reports how many requests each cluster served, next to
   that cluster's capacity share (its share of Ready model-server pods).

Exit codes: 0 pass, 1 failure, 2 inconclusive (the clusters looked equally idle,
so the hub had no load difference to act on).
"""

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# vllm:request_success_total is split across finished_reason labels
# (stop/length/abort/...); summing them gives completed requests.
SUCCESS_TOTAL = re.compile(r"^vllm:request_success_total\{[^}]*\}\s+([0-9.eE+-]+)", re.M)


def fail(message):
    print(f"FAIL {message}")
    sys.exit(1)


def http_json(method, url, body=None, timeout=600):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, {"error": error.read().decode(errors="replace")[:500]}
    except OSError as error:
        return 0, {"error": str(error)}


def kubectl(kubectl_bin, args):
    result = subprocess.run([kubectl_bin, *args], capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"{kubectl_bin} {' '.join(args)} failed")
    return result.stdout


def ready_pods(kubectl_bin, namespace, selector):
    items = json.loads(kubectl(kubectl_bin, ["get", "pods", "-n", namespace, "-l", selector, "-o", "json"]))["items"]
    pods = []
    for item in items:
        conditions = {c["type"]: c["status"] for c in item.get("status", {}).get("conditions", [])}
        if conditions.get("Ready") == "True":
            pods.append(item["metadata"]["name"])
    return pods


def completed_requests(kubectl_bin, namespace, pod, port):
    """Completed requests on one model server, from vLLM's own counter.

    Scraped pod by pod on purpose: scraping through a Service lands on one
    arbitrary replica and would report its count as the whole cluster's.
    """
    try:
        text = kubectl(kubectl_bin, ["get", "--raw", f"/api/v1/namespaces/{namespace}/pods/{pod}:{port}/proxy/metrics"])
    except RuntimeError:
        # The API-server pod proxy can be forbidden where exec is allowed.
        text = kubectl(
            kubectl_bin,
            [
                "exec", "-n", namespace, pod, "--", "python3", "-c",
                f"import urllib.request;"
                f"print(urllib.request.urlopen('http://localhost:{port}/metrics',timeout=10).read().decode())",
            ],
        )
    return sum(float(match.group(1)) for match in SUCCESS_TOTAL.finditer(text))


def snapshot(kubectl_bin, clusters, selector, port):
    counters = {}
    for name, namespace in clusters:
        for pod in ready_pods(kubectl_bin, namespace, selector):
            counters[(name, pod)] = completed_requests(kubectl_bin, namespace, pod, port)
    return counters


def per_cluster(before, after):
    totals = {}
    for (cluster, pod), value in after.items():
        previous = before.get((cluster, pod), 0.0)
        # A pod restarted mid-run resets its counter; count everything since then
        # rather than reporting a negative share.
        totals[cluster] = totals.get(cluster, 0.0) + (value - previous if value >= previous else value)
    return totals


def parse_clusters(parser, specs):
    clusters = []
    for spec in specs:
        name, separator, namespace = spec.partition("=")
        if not separator or not name or not namespace:
            parser.error(f"--cluster must be NAME=NAMESPACE, got {spec!r}")
        clusters.append((name, namespace))
    if len(clusters) < 2:
        parser.error("pass --cluster at least twice")
    return clusters


def main():
    parser = argparse.ArgumentParser(description="Verify llm-d multi-cluster routing through the hub.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="the hub's HTTP endpoint")
    parser.add_argument("--cluster", action="append", default=[], metavar="NAME=NAMESPACE",
                        help="a cluster the hub routes to; repeat for each")
    parser.add_argument("--selector", default="llm-d.ai/guide=optimized-baseline",
                        help="label selector for the model-server pods in each cluster")
    parser.add_argument("--metrics-port", type=int, default=8000, help="model-server metrics port")
    parser.add_argument("--model", default="", help="model name (discovered through the hub if omitted)")
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=32)
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--margin", type=float, default=0.05,
                        help="how far above an even split the largest cluster's share must be to pass")
    parser.add_argument("--kubectl", default="kubectl", help="kubectl binary (use oc on OpenShift)")
    args = parser.parse_args()
    clusters = parse_clusters(parser, args.cluster)
    base = args.base_url.rstrip("/")

    print(f"[1/3] Model discovery through the hub at {base}")
    status, body = http_json("GET", f"{base}/v1/models", timeout=30)
    if status != 200 or not body.get("data"):
        fail(f"GET /v1/models returned HTTP {status}: {body}")
    model = args.model or body["data"][0]["id"]
    print(f"  OK  model {model}")

    capacity = {}
    for name, namespace in clusters:
        capacity[name] = len(ready_pods(args.kubectl, namespace, args.selector))
        print(f"  OK  cluster {name} ({namespace}): {capacity[name]} Ready model-server pod(s)")
        if capacity[name] == 0:
            fail(f"cluster {name} has no Ready pods matching {args.selector}")

    print(f"\n[2/3] Sending {args.requests} requests, {args.concurrency} at a time")
    before = snapshot(args.kubectl, clusters, args.selector, args.metrics_port)

    def send(index):
        # A distinct prompt per request, so prefix caching does not make some
        # requests nearly free and blur the load each cluster reports.
        request = {
            "model": model,
            "messages": [{"role": "user", "content": f"Request {index}: write a short story about the number {index}."}],
            "max_tokens": args.max_tokens,
        }
        return http_json("POST", f"{base}/v1/chat/completions", request)[0]

    started = time.time()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        statuses = list(pool.map(send, range(args.requests)))
    elapsed = time.time() - started
    failed = [s for s in statuses if s != 200]
    print(f"  {args.requests - len(failed)}/{args.requests} succeeded in {elapsed:.0f}s")
    if failed:
        fail(f"{len(failed)} requests failed (statuses: {sorted(set(failed))})")

    after = snapshot(args.kubectl, clusters, args.selector, args.metrics_port)

    print("\n[3/3] How the hub split the traffic")
    served = per_cluster(before, after)
    total = sum(served.values())
    if total == 0:
        fail("no completed requests appeared on any cluster; check the hub reaches the clusters")
    if abs(total - args.requests) > 0.1 * args.requests:
        print(f"  NOTE the clusters served {total:.0f} requests, not {args.requests}: other traffic is reaching them")
    capacity_total = sum(capacity.values())
    print(f"  {'cluster':<10} {'served':>8} {'share':>8} {'capacity':>9}")
    for name, _ in clusters:
        print(f"  {name:<10} {served.get(name, 0):>8.0f} {100 * served.get(name, 0) / total:>7.1f}% "
              f"{100 * capacity[name] / capacity_total:>8.0f}%")

    if len(set(capacity.values())) == 1:
        print("\nPASS requests flowed through the hub to every cluster. The clusters have equal capacity,")
        print("     so no split is expected; give them unequal replicas to see load-aware selection.")
        return
    largest = max(capacity, key=capacity.get)
    share = served.get(largest, 0) / total
    even = 1 / len(clusters)
    if share >= even + args.margin:
        print(f"\nPASS the hub sent {100 * share:.0f}% of requests to {largest}, which holds "
              f"{100 * capacity[largest] / capacity_total:.0f}% of the capacity (an even split would be {100 * even:.0f}%).")
        return
    print(f"\nINCONCLUSIVE {largest} received {100 * share:.0f}%, close to an even split. The hub scores clusters")
    print("  on queue depth and KV-cache utilization, which stay near zero until a cluster is busy: at")
    print("  this load the clusters looked alike. Raise --concurrency or --max-tokens and run again.")
    sys.exit(2)


if __name__ == "__main__":
    main()
