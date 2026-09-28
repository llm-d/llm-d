#!/usr/bin/env python3
"""Check multi-cluster routing end to end through the hub.

Needs only the Python 3 standard library, plus kubectl (or oc) on PATH to read
each cluster's request counters.

    python3 verify.py --base-url http://127.0.0.1:8000 --scorer load \\
        --cluster a=${NS_A} --cluster b=${NS_B}

[1/3] Waits for the hub to answer GET /v1/models and lists each cluster's Ready
      model-server pods. With --scorer load, also checks that each cluster
      router serves its load metrics to a caller without a token, as the hub is.
[2/3] Sends --requests streamed chat completions through the hub, --concurrency
      at a time, reading every model server's completed-request counter before
      and after.
[3/3] Passes when every request succeeded and every cluster served some of them,
      then prints how the hub split the traffic next to each cluster's share of
      the model servers. The split is reported, not judged: how closely it
      follows capacity depends on the scorer and on how busy the clusters are.

Exit codes: 0 pass, 1 verification failure, 2 usage error, 3 unexpected error.
"""

import argparse
import http.client
import json
import re
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# vllm:request_success_total is split across finished_reason labels
# (stop/length/abort/...); summing them gives completed requests.
SUCCESS_TOTAL = re.compile(r"^vllm:request_success_total\{[^}]*\}\s+([0-9.eE+-]+)", re.M)

# The pool averages the load scorer reads from each cluster router.
LOAD_METRICS = ("llm_d_epp_average_kv_cache_utilization", "llm_d_epp_average_queue_size")


def fail(message):
    print(f"FAIL {message}")
    sys.exit(1)


def http_json(url, timeout=10):
    """GET a JSON document. Returns (status, body); status 0 means no HTTP response."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, {"error": error.read().decode(errors="replace")[:300]}
    except (OSError, http.client.HTTPException, json.JSONDecodeError) as error:
        return 0, {"error": f"{type(error).__name__}: {error}"}


def http_stream(url, body, timeout=600):
    """POST a streaming request and read it to the end. Returns (status, error text)."""
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for _ in response:
                pass
            return response.status, ""
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode(errors="replace")[:300]
    except (OSError, http.client.HTTPException) as error:
        return 0, f"{type(error).__name__}: {error}"


def kubectl(kubectl_bin, args):
    try:
        result = subprocess.run([kubectl_bin, *args], capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        raise RuntimeError(f"{kubectl_bin} is not on PATH (choose the binary with --kubectl)") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{kubectl_bin} {' '.join(args)} timed out after 60s") from None
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"{kubectl_bin} {' '.join(args)} failed")
    return result.stdout


def fetch_in_pod(kubectl_bin, namespace, pod, url):
    """GET a URL from inside a pod, with no credentials.

    Returns (status, body); status 0 means no HTTP response and body is the error.
    """
    script = (
        "import urllib.request, urllib.error\n"
        "try:\n"
        f"    response = urllib.request.urlopen({url!r}, timeout=10)\n"
        "    status, body = response.status, response.read().decode()\n"
        "except urllib.error.HTTPError as error:\n"
        "    status, body = error.code, ''\n"
        "except OSError as error:\n"
        "    status, body = 0, str(error)\n"
        "print(status)\n"
        "print(body)\n"
    )
    output = kubectl(kubectl_bin, ["exec", "-n", namespace, pod, "--", "python3", "-c", script])
    status, _, body = output.partition("\n")
    if not status.isdigit():
        raise RuntimeError(f"unexpected output from {pod}: {output[:200]}")
    return int(status), body


def ready_pods(kubectl_bin, namespace, selector):
    """Ready pods matching the selector, mapped to their container restart count."""
    items = json.loads(kubectl(kubectl_bin, ["get", "pods", "-n", namespace, "-l", selector, "-o", "json"]))["items"]
    pods = {}
    for item in items:
        status = item.get("status", {})
        conditions = {c["type"]: c["status"] for c in status.get("conditions", [])}
        if conditions.get("Ready") == "True":
            pods[item["metadata"]["name"]] = sum(c.get("restartCount", 0) for c in status.get("containerStatuses", []))
    return pods


def check_router_metrics(kubectl_bin, namespace, pod, service, port):
    """Fail unless the cluster router serves the load metrics to a caller without a token.

    Fetched from inside the cluster with no credentials, as the hub fetches them.
    Going through the API server's Service proxy instead would not catch a router
    that still requires a token.
    """
    ip = kubectl(kubectl_bin, ["get", "svc", service, "-n", namespace, "-o", "jsonpath={.spec.clusterIP}"]).strip()
    status, body = fetch_in_pod(kubectl_bin, namespace, pod, f"http://{ip}:{port}/metrics")
    if status in (401, 403):
        fail(f"the router in {namespace} answers /metrics with HTTP {status}, so the hub cannot read its load: "
             f"install it with router/leaf.values.yaml")
    if status != 200:
        fail(f"cannot read the router's /metrics in {namespace} at {ip}:{port}: HTTP {status} {body.strip()[:200]}")
    missing = [name for name in LOAD_METRICS if name not in body]
    if missing:
        fail(f"the router in {namespace} does not publish {', '.join(missing)}, which the load scorer reads")


def completed_requests(kubectl_bin, namespace, pod, port):
    """Completed requests on one model server, from vLLM's own counter.

    Scraped pod by pod on purpose: scraping through a Service lands on one
    arbitrary replica and would report its count as the whole cluster's.
    """
    try:
        text = kubectl(kubectl_bin, ["get", "--raw", f"/api/v1/namespaces/{namespace}/pods/{pod}:{port}/proxy/metrics"])
    except RuntimeError as proxy_error:
        # Any API-server pod-proxy failure (commonly: pods/proxy is forbidden
        # where pods/exec is allowed) falls back to reading from inside the pod.
        try:
            status, text = fetch_in_pod(kubectl_bin, namespace, pod, f"http://localhost:{port}/metrics")
            exec_error = "" if status == 200 else f"HTTP {status} {text.strip()[:200]}"
        except RuntimeError as error:
            exec_error = str(error)
        if exec_error:
            fail(f"cannot read {pod}'s metrics on port {port}.\n"
                 f"  pod proxy: {proxy_error}\n  exec fallback: {exec_error}\n"
                 f"  check --metrics-port, and that you may use pods/proxy or pods/exec in {namespace}")
    values = SUCCESS_TOTAL.findall(text)
    if not values:
        fail(f"{pod} publishes no vllm:request_success_total on port {port}; this check counts requests with "
             f"vLLM's counter, so every cluster must run vLLM")
    return sum(float(value) for value in values)


def snapshot(kubectl_bin, clusters, pods, port):
    counters = {}
    for name, namespace in clusters:
        for pod, restarts in pods[name].items():
            counters[(name, pod, restarts)] = completed_requests(kubectl_bin, namespace, pod, port)
    return counters


def per_cluster(before, after):
    totals = {}
    for (cluster, pod, restarts), value in after.items():
        # Keyed by restart count too: a container that restarted during the run
        # reset its counter, so everything it counted since then is new.
        totals[cluster] = totals.get(cluster, 0.0) + value - before.get((cluster, pod, restarts), 0.0)
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
    parser = argparse.ArgumentParser(description="Check llm-d multi-cluster routing through the hub.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="the hub's HTTP endpoint")
    parser.add_argument("--cluster", action="append", required=True, metavar="NAME=NAMESPACE",
                        help="a cluster the hub routes to; repeat for each (at least two)")
    parser.add_argument("--scorer", choices=["load", "latency"], default="load",
                        help="the hub's HUB_SCORER; with load, first check that the hub can read each cluster's load")
    parser.add_argument("--router-service", default="optimized-baseline-epp",
                        help="each cluster router's Service, whose metrics the load scorer reads")
    parser.add_argument("--router-metrics-port", type=int, default=9090, help="the cluster routers' metrics port")
    parser.add_argument("--selector", default="llm-d.ai/guide=optimized-baseline",
                        help="label selector for the model-server pods in each cluster")
    parser.add_argument("--metrics-port", type=int, default=8000, help="the model servers' metrics port")
    parser.add_argument("--model", default="", help="model name (discovered through the hub if omitted)")
    parser.add_argument("--requests", type=int, default=200, help="streamed chat completions to send through the hub")
    parser.add_argument("--concurrency", type=int, default=32,
                        help="requests in flight at once; raise it to load the clusters harder")
    parser.add_argument("--max-tokens", type=int, default=256,
                        help="output tokens per request; raise it for longer, heavier requests")
    parser.add_argument("--wait", type=int, default=30,
                        help="seconds to wait for the hub to answer, for example while a port-forward starts")
    parser.add_argument("--kubectl", default="kubectl", help="kubectl binary (use oc on OpenShift)")
    args = parser.parse_args()
    clusters = parse_clusters(parser, args.cluster)
    base = args.base_url.rstrip("/")
    if not base.startswith(("http://", "https://")):
        parser.error(f"--base-url must start with http:// or https://, got {args.base_url!r}")

    print(f"[1/3] Checking the hub at {base} and the clusters")
    deadline = time.time() + args.wait
    while True:
        status, body = http_json(f"{base}/v1/models")
        if status == 200 and body.get("data"):
            break
        if time.time() >= deadline:
            fail(f"GET /v1/models returned HTTP {status}: {body.get('error', body)} "
                 f"(HTTP 0 means no response: the port-forward or the hub is not reachable)")
        time.sleep(1)
    model = args.model or body["data"][0]["id"]
    print(f"  OK  model {model}")

    pods = {}
    for name, namespace in clusters:
        pods[name] = ready_pods(args.kubectl, namespace, args.selector)
        if not pods[name]:
            fail(f"cluster {name} ({namespace}) has no Ready pods matching {args.selector}")
        print(f"  OK  cluster {name} ({namespace}): {len(pods[name])} Ready model-server pod(s)")
        if args.scorer == "load":
            check_router_metrics(args.kubectl, namespace, next(iter(pods[name])), args.router_service,
                                 args.router_metrics_port)
            print(f"  OK  cluster {name}: router serves its load metrics without a token")

    print(f"\n[2/3] Sending {args.requests} streamed requests, {args.concurrency} at a time")
    before = snapshot(args.kubectl, clusters, pods, args.metrics_port)

    def send(index):
        # A distinct prompt per request, so prefix caching does not make some
        # requests nearly free and blur the load each cluster reports. The index
        # leads the prompt on purpose: moved to the end, every prompt would share
        # a cacheable prefix.
        request = {
            "model": model,
            "messages": [{"role": "user", "content": f"Request {index}: write a short story about the number {index}."}],
            "max_tokens": args.max_tokens,
            # Streamed and read to the end: the latency scorer learns only from
            # streamed responses the hub relays, so a non-streamed request, or a
            # stream closed before its first chunk, teaches it nothing.
            "stream": True,
        }
        return http_stream(f"{base}/v1/chat/completions", request)

    started = time.time()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(pool.map(send, range(args.requests)))
    elapsed = time.time() - started
    failed = [(status, error) for status, error in results if status != 200]
    print(f"  {args.requests - len(failed)}/{args.requests} succeeded in {elapsed:.0f}s")
    if failed:
        for status, error in sorted(set(failed))[:3]:
            print(f"  HTTP {status}: {error[:200]}")
        fail(f"{len(failed)}/{args.requests} requests failed "
             f"(HTTP 0 means no response: the port-forward or the hub is not reachable)")

    pods_after = {name: ready_pods(args.kubectl, namespace, args.selector) for name, namespace in clusters}
    if pods_after != pods:
        print("  NOTE model-server pods became Ready, stopped being Ready, or restarted during the run; "
              "the split below may be skewed")
    after = snapshot(args.kubectl, clusters, pods_after, args.metrics_port)

    print("\n[3/3] How the hub split the traffic")
    served = per_cluster(before, after)
    total = sum(served.values()) or 1
    capacity_total = sum(len(pods[name]) for name, _ in clusters)
    print(f"  {'cluster':<10} {'served':>8} {'share':>8} {'model servers':>14}")
    for name, _ in clusters:
        print(f"  {name:<10} {served.get(name, 0):>8.0f} {100 * served.get(name, 0) / total:>7.1f}% "
              f"{100 * len(pods[name]) / capacity_total:>13.0f}%")

    idle = [name for name, _ in clusters if served.get(name, 0) == 0]
    if idle:
        fail(f"{', '.join(idle)} served no requests, so the hub is not sending traffic to every cluster; compare "
             f"`kubectl get configmap mc-hub-clusters -o yaml` in the hub's namespace with the cluster routers' "
             f"Service IPs")
    if abs(sum(served.values()) - args.requests) > 0.1 * args.requests:
        print(f"  NOTE the model servers counted {sum(served.values()):.0f} completed requests, not {args.requests}: "
              f"other traffic reached them, or model-server pods changed during the run")
    print(f"\nPASS all {args.requests} requests succeeded through the hub and every cluster served some of them.")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        fail(str(error))
    except Exception:
        traceback.print_exc()
        print("ERROR unexpected failure in verify.py")
        sys.exit(3)
