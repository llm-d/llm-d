#!/usr/bin/env python3
"""Check multi-cluster routing end to end through the hub.

Needs only the Python 3 standard library, plus kubectl (or oc) on PATH to read
each cluster's request counters.

    python3 verify.py --base-url http://127.0.0.1:8000 --scorer load \\
        --cluster a=${NS_A} --cluster b=${NS_B}

A cluster in another kubeconfig context is named NAME=NAMESPACE@CONTEXT, for
example --cluster b=${NS_B}@other-cluster.

[1/3] Waits for the hub to answer GET /v1/models and lists each cluster's Ready
      model-server pods. With --scorer load, also checks that each cluster
      router serves its load metrics to a caller without a token, as the hub is.
      The check runs inside each cluster, so it does not cover the network path
      from the hub to a cluster in another Kubernetes cluster.
[2/3] Sends --requests streamed chat completions through the hub, --concurrency
      at a time, reading every model server's completed-request counter before
      and after, and prints the median and 90th-percentile time to first token
      at this client. --prompt-words N adds about N tokens to every prompt, so
      that prefill, rather than network delay, dominates the time to first token.
[3/3] Passes when every request succeeded and every cluster served some of them,
      then prints how the hub split the traffic next to each cluster's share of
      the model-server replicas. The split is reported, not judged: how closely
      it follows capacity depends on the scorer, on the prompt sizes, on how busy
      the clusters are, and on the hardware behind each replica.

Exit codes: 0 pass, 1 verification failure, 2 usage error, 3 unexpected error.
"""

import argparse
import http.client
import json
import random
import re
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import NamedTuple

# vllm:request_success_total is split across finished_reason labels
# (stop/length/abort/...); summing them gives completed requests.
SUCCESS_TOTAL = re.compile(r"^vllm:request_success_total\{[^}]*\}\s+([0-9.eE+-]+)", re.M)

# The pool averages the load scorer reads from each cluster router.
LOAD_METRICS = ("llm_d_epp_average_kv_cache_utilization", "llm_d_epp_average_queue_size")

# Common words of about one token each, for --prompt-words.
FILLER_WORDS = ("the of and to in is was for on that with as by at from his her an which be this are had not but "
                "were they all one have their has more been would when time who will no if out so said what up its "
                "about into than them can only other new some could these two may first then do any like my now over "
                "such our man me even most made after also did many before must through back years where much your "
                "way well down should because each just those people how too little state good very make world still "
                "own see men work long get here between both life being under never day same another know while last "
                "might us great old year off come since against go came right used take three").split()


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
    """POST a streaming request and read it to the end.

    Returns (status, error text, seconds to the first streamed chunk or None).
    """
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json"}
    )
    started = time.perf_counter()
    first = None
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for line in response:
                if first is None and line.startswith(b"data:"):
                    first = time.perf_counter() - started
            return response.status, "", first
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode(errors="replace")[:300], None
    except (OSError, http.client.HTTPException) as error:
        return 0, f"{type(error).__name__}: {error}", None


class Cluster(NamedTuple):
    name: str
    namespace: str
    kube: list  # kubectl command prefix, with --context when the cluster is in another context
    where: str  # NAMESPACE or NAMESPACE@CONTEXT, for messages


def kubectl(kube, args):
    try:
        result = subprocess.run([*kube, *args], capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        raise RuntimeError(f"{kube[0]} is not on PATH (choose the binary with --kubectl)") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{' '.join(kube)} {' '.join(args)} timed out after 60s") from None
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"{' '.join(kube)} {' '.join(args)} failed")
    return result.stdout


def fetch_in_pod(kube, namespace, pod, url):
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
    output = kubectl(kube, ["exec", "-n", namespace, pod, "--", "python3", "-c", script])
    status, _, body = output.partition("\n")
    if not status.isdigit():
        raise RuntimeError(f"unexpected output from {pod}: {output[:200]}")
    return int(status), body


def ready_pods(kube, namespace, selector):
    """Ready pods matching the selector, mapped to their container restart count."""
    items = json.loads(kubectl(kube, ["get", "pods", "-n", namespace, "-l", selector, "-o", "json"]))["items"]
    pods = {}
    for item in items:
        status = item.get("status", {})
        conditions = {c["type"]: c["status"] for c in status.get("conditions", [])}
        if conditions.get("Ready") == "True":
            pods[item["metadata"]["name"]] = sum(c.get("restartCount", 0) for c in status.get("containerStatuses", []))
    return pods


def check_router_metrics(kube, namespace, pod, service, port):
    """Fail unless the cluster router serves the load metrics to a caller without a token.

    Fetched from inside the cluster with no credentials, as the hub fetches them.
    Going through the API server's Service proxy instead would not catch a router
    that still requires a token.
    """
    ip = kubectl(kube, ["get", "svc", service, "-n", namespace, "-o", "jsonpath={.spec.clusterIP}"]).strip()
    status, body = fetch_in_pod(kube, namespace, pod, f"http://{ip}:{port}/metrics")
    if status in (401, 403):
        fail(f"the router in {namespace} answers /metrics with HTTP {status}, so the hub cannot read its load: "
             f"install it with router/leaf.values.yaml")
    if status != 200:
        fail(f"cannot read the router's /metrics in {namespace} at {ip}:{port}: HTTP {status} {body.strip()[:200]}")
    missing = [name for name in LOAD_METRICS if name not in body]
    if missing:
        fail(f"the router in {namespace} does not publish {', '.join(missing)}, which the load scorer reads")


def completed_requests(kube, namespace, pod, port):
    """Completed requests on one model server, from vLLM's own counter.

    Scraped pod by pod on purpose: scraping through a Service lands on one
    arbitrary replica and would report its count as the whole cluster's.
    """
    try:
        text = kubectl(kube, ["get", "--raw", f"/api/v1/namespaces/{namespace}/pods/{pod}:{port}/proxy/metrics"])
    except RuntimeError as proxy_error:
        # Any API-server pod-proxy failure (commonly: pods/proxy is forbidden
        # where pods/exec is allowed) falls back to reading from inside the pod.
        try:
            status, text = fetch_in_pod(kube, namespace, pod, f"http://localhost:{port}/metrics")
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


def snapshot(clusters, pods, port):
    counters = {}
    for cluster in clusters:
        for pod, restarts in pods[cluster.name].items():
            counters[(cluster.name, pod, restarts)] = completed_requests(cluster.kube, cluster.namespace, pod, port)
    return counters


def per_cluster(before, after):
    totals = {}
    for (cluster, pod, restarts), value in after.items():
        # Keyed by restart count too: a container that restarted during the run
        # reset its counter, so everything it counted since then is new.
        totals[cluster] = totals.get(cluster, 0.0) + value - before.get((cluster, pod, restarts), 0.0)
    return totals


def parse_clusters(parser, specs, kubectl_bin):
    clusters = []
    for spec in specs:
        name, separator, target = spec.partition("=")
        namespace, at, context = target.partition("@")
        if not separator or not name or not namespace or (at and not context):
            parser.error(f"--cluster must be NAME=NAMESPACE or NAME=NAMESPACE@CONTEXT, got {spec!r}")
        kube = [kubectl_bin, "--context", context] if context else [kubectl_bin]
        clusters.append(Cluster(name, namespace, kube, target))
    if len(clusters) < 2:
        parser.error("pass --cluster at least twice")
    return clusters


def main():
    parser = argparse.ArgumentParser(description="Check llm-d multi-cluster routing through the hub.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="the hub's HTTP endpoint")
    parser.add_argument("--cluster", action="append", required=True, metavar="NAME=NAMESPACE[@CONTEXT]",
                        help="a cluster the hub routes to; repeat for each (at least two). Add @CONTEXT for a "
                             "cluster in another kubeconfig context")
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
    parser.add_argument("--prompt-words", type=int, default=0,
                        help="filler words, about one token each, to add to every prompt, so that prefill outweighs "
                             "network delay (default 0: a one-line prompt)")
    parser.add_argument("--wait", type=int, default=30,
                        help="seconds to wait for the hub to answer, for example while a port-forward starts")
    parser.add_argument("--kubectl", default="kubectl", help="kubectl binary (use oc on OpenShift)")
    args = parser.parse_args()
    clusters = parse_clusters(parser, args.cluster, args.kubectl)
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
    for cluster in clusters:
        pods[cluster.name] = ready_pods(cluster.kube, cluster.namespace, args.selector)
        if not pods[cluster.name]:
            fail(f"cluster {cluster.name} ({cluster.where}) has no Ready pods matching {args.selector}")
        print(f"  OK  cluster {cluster.name} ({cluster.where}): {len(pods[cluster.name])} Ready model-server pod(s)")
        if args.scorer == "load":
            check_router_metrics(cluster.kube, cluster.namespace, next(iter(pods[cluster.name])),
                                 args.router_service, args.router_metrics_port)
            print(f"  OK  cluster {cluster.name}: router serves its load metrics without a token")

    print(f"\n[2/3] Sending {args.requests} streamed requests, {args.concurrency} at a time")
    before = snapshot(clusters, pods, args.metrics_port)

    run = uuid.uuid4().hex[:8]

    def send(index):
        # A distinct prompt per request and per run, so prefix caching does not
        # make some requests nearly free and blur the load each cluster reports.
        # The IDs lead the prompt on purpose: moved to the end, every prompt
        # would share a cacheable prefix.
        if args.prompt_words > 0:
            rng = random.Random(index)
            filler = " ".join(rng.choice(FILLER_WORDS) for _ in range(args.prompt_words))
            content = (f"Request {run}-{index}: {filler}. Summarize the text above, then write a short story "
                       f"about the number {index}.")
        else:
            content = f"Request {run}-{index}: write a short story about the number {index}."
        request = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
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
    failed = [(status, error) for status, error, _ in results if status != 200]
    print(f"  {args.requests - len(failed)}/{args.requests} succeeded in {elapsed:.0f}s")
    firsts = sorted(first for status, _, first in results if status == 200 and first is not None)
    if firsts:
        print(f"  time to first token at this client: median {1000 * firsts[len(firsts) // 2]:.0f} ms, "
              f"p90 {1000 * firsts[int(len(firsts) * 0.9)]:.0f} ms")
    if failed:
        for status, error in sorted(set(failed))[:3]:
            print(f"  HTTP {status}: {error[:200]}")
        fail(f"{len(failed)}/{args.requests} requests failed "
             f"(HTTP 0 means no response: the port-forward or the hub is not reachable)")

    pods_after = {c.name: ready_pods(c.kube, c.namespace, args.selector) for c in clusters}
    if pods_after != pods:
        print("  NOTE model-server pods became Ready, stopped being Ready, or restarted during the run; "
              "the split below may be skewed")
    after = snapshot(clusters, pods_after, args.metrics_port)

    print("\n[3/3] How the hub split the traffic")
    served = per_cluster(before, after)
    total = sum(served.values()) or 1
    capacity_total = sum(len(pods[c.name]) for c in clusters)
    print(f"  {'cluster':<10} {'served':>8} {'share':>8} {'replicas':>14}")
    for c in clusters:
        print(f"  {c.name:<10} {served.get(c.name, 0):>8.0f} {100 * served.get(c.name, 0) / total:>7.1f}% "
              f"{100 * len(pods[c.name]) / capacity_total:>13.0f}%")

    idle = [c.name for c in clusters if served.get(c.name, 0) == 0]
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
