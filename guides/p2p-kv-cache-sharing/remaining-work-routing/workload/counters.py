#!/usr/bin/env python3
"""Snapshot and compare GLM DP-rank vLLM cache counters."""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
import subprocess
import sys
import time
from collections import defaultdict


MODEL_SELECTOR = "llm-d.ai/model=GLM-5.2-FP8"
PORTS = range(8000, 8008)
INTERESTING = (
    "prefix_cache",
    "prompt_tokens",
    "external_prefix",
    "kv_offload",
    "nixl_",
    "num_requests",
)
ACTIVE_REQUEST_METRICS = {
    "vllm:num_requests_running",
    "vllm:num_requests_waiting",
}
REMOTE_SCRAPER = """
import json
import urllib.request

metrics = {}
for port in range(8000, 8008):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=10) as response:
        metrics[str(port)] = response.read().decode()
print(json.dumps(metrics))
"""
LABEL_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)="((?:[^"\\]|\\.)*)"')


def kubectl(context: str, *args: str) -> str:
    result = subprocess.run(
        ["kubectl", "--context", context, *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def ready_engine_pods(namespace: str, context: str) -> list[dict[str, str]]:
    data = json.loads(
        kubectl(
            context,
            "-n",
            namespace,
            "get",
            "pods",
            "-l",
            MODEL_SELECTOR,
            "-o",
            "json",
        )
    )
    pods = []
    for pod in data["items"]:
        labels = pod["metadata"].get("labels", {})
        role = labels.get("llm-d.ai/role")
        if role not in {"prefill", "decode"}:
            continue
        conditions = {
            condition["type"]: condition["status"]
            for condition in pod.get("status", {}).get("conditions", [])
        }
        if conditions.get("Ready") != "True":
            continue
        pods.append(
            {
                "name": pod["metadata"]["name"],
                "role": role,
                "work_range": labels.get("llm-d.ai/prefill-work-range", ""),
            }
        )
    if not pods:
        raise RuntimeError(f"no Ready GLM engine pods found in {namespace}")
    return sorted(pods, key=lambda item: item["name"])


def normalized_series(sample: str) -> tuple[str, str]:
    if "{" not in sample:
        return sample, sample
    name, raw_labels = sample.split("{", 1)
    labels = {
        key: value
        for key, value in LABEL_RE.findall(raw_labels.rsplit("}", 1)[0])
        if key not in {"engine", "model_name"}
    }
    suffix = ",".join(f'{key}="{labels[key]}"' for key in sorted(labels))
    return name, f"{name}{{{suffix}}}" if suffix else name


def parse_metrics(raw_metrics: str) -> dict[str, float]:
    metrics: defaultdict[str, float] = defaultdict(float)
    for line in raw_metrics.splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.rsplit(None, 1)
        if len(fields) != 2:
            continue
        sample, raw_value = fields
        name, series = normalized_series(sample)
        if not name.startswith("vllm:") or not any(
            key in name for key in INTERESTING
        ):
            continue
        if name.endswith(("_bucket", "_created")):
            continue
        try:
            value = float(raw_value)
        except ValueError:
            continue
        if math.isfinite(value):
            metrics[series] += value
    return dict(sorted(metrics.items()))


def scrape_pod(
    namespace: str,
    context: str,
    pod: str,
) -> dict[str, dict[str, float]]:
    raw = kubectl(
        context,
        "-n",
        namespace,
        "exec",
        pod,
        "-c",
        "vllm",
        "--",
        "python3",
        "-c",
        REMOTE_SCRAPER,
    )
    by_port = json.loads(raw)
    missing = [str(port) for port in PORTS if str(port) not in by_port]
    if missing:
        raise RuntimeError(f"{pod} did not return metrics for ports {missing}")
    return {
        port: parse_metrics(by_port[port])
        for port in sorted(by_port, key=int)
    }


def snapshot(namespace: str, context: str, destination: pathlib.Path) -> None:
    pods = ready_engine_pods(namespace, context)
    instances = {
        pod["name"]: {
            "role": pod["role"],
            "work_range": pod["work_range"],
            "ports": scrape_pod(namespace, context, pod["name"]),
        }
        for pod in pods
    }
    totals: defaultdict[str, float] = defaultdict(float)
    for instance in instances.values():
        for metrics in instance["ports"].values():
            for series, value in metrics.items():
                totals[series] += value
    document = {
        "schema": 1,
        "timestamp": time.time(),
        "context": context,
        "namespace": namespace,
        "instances": instances,
        "totals": dict(sorted(totals.items())),
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(
        f"wrote {destination}: {len(instances)} pods, "
        f"{sum(len(item['ports']) for item in instances.values())} ranks"
    )


def active_requests(instances: dict) -> tuple[dict[str, float], dict[str, dict]]:
    totals: defaultdict[str, float] = defaultdict(float)
    by_rank: dict[str, dict] = {}
    for pod_name, instance in instances.items():
        for port, metrics in instance["ports"].items():
            values: defaultdict[str, float] = defaultdict(float)
            for series, value in metrics.items():
                name = series.split("{", 1)[0]
                if name in ACTIVE_REQUEST_METRICS:
                    values[name] += value
                    totals[name] += value
            by_rank[f"{pod_name}:{port}"] = dict(sorted(values.items()))
    return dict(sorted(totals.items())), by_rank


def wait_idle(
    namespace: str,
    context: str,
    destination: pathlib.Path,
    timeout: float,
    interval: float,
    stable_samples: int,
    expected_pods: int,
) -> None:
    started = time.monotonic()
    consecutive = 0
    observations = []
    while time.monotonic() - started < timeout:
        pods = ready_engine_pods(namespace, context)
        instances = {
            pod["name"]: {
                "role": pod["role"],
                "work_range": pod["work_range"],
                "ports": scrape_pod(namespace, context, pod["name"]),
            }
            for pod in pods
        }
        totals, by_rank = active_requests(instances)
        active = sum(totals.values())
        pod_count_ok = expected_pods == 0 or len(instances) == expected_pods
        rank_count = sum(len(instance["ports"]) for instance in instances.values())
        observation = {
            "elapsed_seconds": time.monotonic() - started,
            "pod_count": len(instances),
            "rank_count": rank_count,
            "active_requests": active,
            "totals": totals,
            "by_rank": by_rank,
        }
        observations.append(observation)
        if active == 0 and pod_count_ok:
            consecutive += 1
        else:
            consecutive = 0
        if consecutive >= stable_samples:
            document = {
                "schema": 1,
                "idle": True,
                "stable_samples": stable_samples,
                "expected_pods": expected_pods,
                "observations": observations,
            }
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(document, indent=2, sort_keys=True) + "\n"
            )
            print(
                f"engine fleet idle across {len(instances)} pods and "
                f"{rank_count} ranks for {stable_samples} samples"
            )
            return
        time.sleep(interval)

    document = {
        "schema": 1,
        "idle": False,
        "stable_samples": stable_samples,
        "expected_pods": expected_pods,
        "observations": observations,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    last = observations[-1] if observations else {}
    raise RuntimeError(
        "engine fleet did not become idle: "
        f"last active={last.get('active_requests')}, "
        f"pods={last.get('pod_count')}"
    )


def counter_deltas(before: dict, after: dict) -> dict[str, float]:
    keys = set(before.get("totals", {})) | set(after.get("totals", {}))
    return {
        key: after.get("totals", {}).get(key, 0.0)
        - before.get("totals", {}).get(key, 0.0)
        for key in sorted(keys)
    }


def diff(before_path: pathlib.Path, after_path: pathlib.Path) -> None:
    before = json.loads(before_path.read_text())
    after = json.loads(after_path.read_text())
    output = {
        "schema": 1,
        "before": str(before_path),
        "after": str(after_path),
        "elapsed_seconds": after["timestamp"] - before["timestamp"],
        "deltas": counter_deltas(before, after),
    }
    print(json.dumps(output, indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot_parser = subparsers.add_parser("snapshot")
    snapshot_parser.add_argument("--namespace", default="nilig-agentx-slo")
    snapshot_parser.add_argument("--context", default="kermit_US-EAST-01A")
    snapshot_parser.add_argument("--output", type=pathlib.Path, required=True)

    idle_parser = subparsers.add_parser("wait-idle")
    idle_parser.add_argument("--namespace", default="nilig-agentx-slo")
    idle_parser.add_argument("--context", default="kermit_US-EAST-01A")
    idle_parser.add_argument("--output", type=pathlib.Path, required=True)
    idle_parser.add_argument("--timeout", type=float, default=300)
    idle_parser.add_argument("--interval", type=float, default=5)
    idle_parser.add_argument("--stable-samples", type=int, default=3)
    idle_parser.add_argument("--expected-pods", type=int, default=0)

    diff_parser = subparsers.add_parser("diff")
    diff_parser.add_argument("before", type=pathlib.Path)
    diff_parser.add_argument("after", type=pathlib.Path)
    args = parser.parse_args()

    try:
        if args.command == "snapshot":
            snapshot(args.namespace, args.context, args.output)
        elif args.command == "wait-idle":
            wait_idle(
                args.namespace,
                args.context,
                args.output,
                args.timeout,
                args.interval,
                args.stable_samples,
                args.expected_pods,
            )
        else:
            diff(args.before, args.after)
    except (RuntimeError, subprocess.CalledProcessError, OSError, ValueError) as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
