#!/usr/bin/env python3
"""Exercise SGLang prefix affinity through the router, including native /generate.

Run against an otherwise idle, two-replica guide deployment. Each API gets a
unique cold prefix followed by a related request, after speculative entries
expire. A pass needs an EPP cache prediction AND a cache hit on the same backend;
successful inference or a model-server cache hit alone cannot prove routing.
Only Python's standard library and kubectl are required.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time
from urllib.request import Request, urlopen
import uuid


APIS = ("/generate", "/v1/chat/completions", "/v1/completions")
GUIDE = "precise-prefix-cache-routing"


def samples(text: str, name: str) -> list[tuple[dict[str, str], float]]:
    """Read one Prometheus family without including similarly prefixed names."""
    result = []
    for line in text.splitlines():
        match = re.match(r"^" + re.escape(name) + r"(?:\{(.*?)\})?\s+(\S+)", line)
        if match:
            labels = dict(re.findall(r'(\w+)="((?:\\.|[^"\\])*)"', match[1] or ""))
            value = float(match[2])
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"invalid {name} counter: {match[2]}")
            result.append((labels, value))
    return result


def total(text: str, name: str, **labels: str) -> float:
    return sum(value for actual, value in samples(text, name)
               if all(actual.get(key) == value for key, value in labels.items()))


def cached_tokens(text: str) -> float:
    values = samples(text, "sglang:cached_tokens_total")
    # Newer SGLang exports a per-source breakdown plus total. Do not sum both.
    for source in ("device", "total", None):
        selected = [value for labels, value in values if labels.get("cache_source") == source]
        if selected:
            return sum(selected)
    return 0


@dataclass
class Snapshot:
    router: str
    backends: dict[str, str]


def request_deltas(before: Snapshot, after: Snapshot) -> dict[str, float]:
    if before.backends.keys() != after.backends.keys():
        raise AssertionError("backend membership changed during the probe")
    deltas = {pod: total(after.backends[pod], "sglang:num_requests_total")
              - total(before.backends[pod], "sglang:num_requests_total") for pod in before.backends}
    if any(value < 0 for value in deltas.values()):
        raise AssertionError("backend request counter reset during the probe")
    return deltas


def warm_backend(before: Snapshot, after: Snapshot) -> str:
    active = [pod for pod, count in request_deltas(before, after).items() if count > 0]
    if len(active) != 1:
        raise AssertionError(f"cold request must reach exactly one backend, got {active}")
    return active[0]


def check_followup(before: Snapshot, after: Snapshot, owner: str) -> dict:
    deltas = request_deltas(before, after)
    if deltas.get(owner, 0) <= 0 or any(count > 0 for pod, count in deltas.items() if pod != owner):
        raise AssertionError(f"follow-up missed cache-warm backend {owner}: {deltas}")
    predicted = total(after.router, "llm_d_epp_prefix_predicted_cached_tokens_sum") - total(
        before.router, "llm_d_epp_prefix_predicted_cached_tokens_sum")
    hits = cached_tokens(after.backends[owner]) - cached_tokens(before.backends[owner])
    rendered = total(after.router, "llm_d_epp_token_producer_render_duration_seconds_count", result="success") - total(
        before.router, "llm_d_epp_token_producer_render_duration_seconds_count", result="success")
    failed = total(after.router, "llm_d_epp_token_producer_render_failures_total") - total(
        before.router, "llm_d_epp_token_producer_render_failures_total")
    if predicted < 64 or hits < 64:
        raise AssertionError(f"no full cached block: EPP predicted {predicted}, backend reused {hits} tokens")
    if rendered < 1 or failed != 0:
        raise AssertionError(f"router render delta: {rendered} successes, {failed} failures")
    return {"warm_backend": owner, "predicted_cached_tokens": predicted, "reused_tokens": hits}


def payload(api: str, model: str, prompt: str) -> dict:
    if api == "/generate":
        return {"model": model, "text": prompt,
                "sampling_params": {"max_new_tokens": 8, "temperature": 0}}
    common = {"model": model, "max_tokens": 8, "temperature": 0}
    if api == "/v1/chat/completions":
        return {**common, "messages": [{"role": "user", "content": prompt}]}
    if api == "/v1/completions":
        return {**common, "prompt": prompt}
    raise ValueError(f"unsupported API: {api}")


def validate_response(api: str, body: dict) -> None:
    if not isinstance(body, dict) or "error" in body:
        raise AssertionError(f"{api}: inference error: {body}")
    if api == "/generate":
        valid = isinstance(body.get("text"), str) and bool(body["text"])
        usage = body.get("meta_info", {})
    else:
        choices = body.get("choices", [])
        choice = choices[0] if choices else {}
        if api == "/v1/chat/completions":
            message = choice.get("message", {})
            valid = bool(message.get("content") or message.get("reasoning_content"))
        else:
            valid = isinstance(choice.get("text"), str) and bool(choice["text"])
        usage = body.get("usage", {})
    if not valid or usage.get("prompt_tokens", 0) < 64 or usage.get("completion_tokens", 0) <= 0:
        raise AssertionError(f"{api}: missing generated output or token usage: {body}")


def read(url: str, data: dict | None = None, headers: dict | None = None) -> str:
    request = Request(url, data=json.dumps(data).encode() if data is not None else None,
                      headers={"Content-Type": "application/json", **(headers or {})})
    # urlopen raises on non-2xx; an API error must never count as a passing probe.
    with urlopen(request, timeout=120) as response:
        return response.read().decode("utf-8")


@contextmanager
def port_forward(namespace: str, resource: str, port: int):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        local_port = reservation.getsockname()[1]
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(["kubectl", "port-forward", "-n", namespace,
                                    "--address=127.0.0.1", resource, f"{local_port}:{port}"],
                                   stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    with socket.create_connection(("127.0.0.1", local_port), timeout=1):
                        break
                except OSError:
                    time.sleep(0.2)
            else:
                log.seek(0)
                raise RuntimeError(f"port-forward failed for {resource}: {log.read().decode(errors='replace')}")
            yield f"http://127.0.0.1:{local_port}"
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


class Cluster:
    def __init__(self, request_url: str, metrics_url: str, backends: dict[str, str], token: str = ""):
        self.request_url, self.metrics_url, self.backends = request_url, metrics_url, backends
        self.metrics_headers = {"Authorization": f"Bearer {token}"} if token else {}

    def snapshot(self) -> Snapshot:
        return Snapshot(read(self.metrics_url + "/metrics", headers=self.metrics_headers),
                        {pod: read(url + "/metrics") for pod, url in self.backends.items()})

    def send(self, api: str, model: str, prompt: str) -> None:
        validate_response(api, json.loads(read(self.request_url + api, payload(api, model, prompt))))


def await_metrics(cluster: Cluster, check, timeout: float = 20):
    deadline = time.monotonic() + timeout
    while True:
        try:
            return check(cluster.snapshot())
        except AssertionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)


def require_ready_backends(snapshot: Snapshot) -> None:
    if total(snapshot.router, "llm_d_epp_ready_endpoints") != 2:
        raise AssertionError("waiting for the EPP to discover both Ready backends")


def verify(cluster: Cluster, model: str) -> None:
    for api in APIS:
        # A different prefix per API prevents earlier probes hiding a routing bug.
        prefix = f"CI {uuid.uuid4().hex}: " + (
            "The llm-d router routes shared prefixes using backend KV-cache events. " * 128)
        before = cluster.snapshot()
        cluster.send(api, model, prefix + "Question one: what is llm-d?")
        owner = await_metrics(cluster, lambda after: warm_backend(before, after))
        # The speculative index TTL is 2s. Only backend KV events should keep
        # the cold request's blocks visible to the next scheduling decision.
        time.sleep(3)
        before = cluster.snapshot()
        cluster.send(api, model, prefix + "Question two: what does the router do?")
        result = await_metrics(cluster, lambda after: check_followup(before, after, owner))
        print(json.dumps({"endpoint": api, **result}), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--model", default="Qwen/Qwen3-32B")
    parser.add_argument("--metrics-token-file", type=Path, help="Bearer token for authenticated EPP metrics")
    args = parser.parse_args()
    pods = json.loads(subprocess.check_output([
        "kubectl", "get", "pods", "-n", args.namespace,
        "-l", f"llm-d.ai/guide={GUIDE},llm-d.ai/engine-type=sglang", "-o", "json"]))["items"]
    if len(pods) != 2 or any(pod["metadata"].get("deletionTimestamp") or not any(
            condition["type"] == "Ready" and condition["status"] == "True"
            for condition in pod.get("status", {}).get("conditions", [])) for pod in pods):
        raise RuntimeError("the probe requires exactly two Ready SGLang backend pods")
    with ExitStack() as stack:
        forward = lambda resource, port: stack.enter_context(port_forward(args.namespace, resource, port))
        requests = forward(f"service/{GUIDE}-epp", 80)
        metrics = forward(f"service/{GUIDE}-epp", 9090)
        backends = {pod["metadata"]["name"]: forward(f"pod/{pod['metadata']['name']}", 8000) for pod in pods}
        token = args.metrics_token_file.read_text(encoding="utf-8").strip() if args.metrics_token_file else ""
        cluster = Cluster(requests, metrics, backends, token)
        await_metrics(cluster, require_ready_backends, timeout=60)
        verify(cluster, args.model)


if __name__ == "__main__":
    main()
