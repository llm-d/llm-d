#!/usr/bin/env python3
"""Send an exact 102400-token seed and 103424-token follow-up."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import random
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request


MODEL = "zai-org/GLM-5.2-FP8"
INITIAL_TOKENS = 102_400
FOLLOW_UP_TOKENS = 1_024
BACKGROUND_TAIL_TOKENS = 12_800
CACHE_BUST_TOKENS = 64
TOKEN_MIN = 1_000
TOKEN_MAX_EXCLUSIVE = 11_000
INFLIGHT_TOKENS_RE = re.compile(
    r'^llm_d_epp_inflight_tokens\{([^}]*)\}\s+([-+0-9.eE]+)$'
)
LABEL_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)="((?:[^"\\]|\\.)*)"')
REMOTE_CAPACITY_QUEUE_SCRIPT = r'''
import json
import urllib.request

queues = {}
for rank in range(8):
    metrics = urllib.request.urlopen(
        f"http://127.0.0.1:{8000 + rank}/metrics", timeout=10
    ).read().decode()
    value = 0.0
    for line in metrics.splitlines():
        if (
            line.startswith("vllm:num_requests_waiting_by_reason{")
            and 'reason="capacity"' in line
        ):
            value += float(line.rsplit(None, 1)[1])
    queues[str(rank)] = value
print(json.dumps(queues, sort_keys=True))
'''


def seed_prompt(salt: int) -> list[int]:
    """Return a stable payload with one salt-specific leading cache block."""
    payload = random.Random(0x474C4D52)
    tokens = [
        payload.randrange(TOKEN_MIN, TOKEN_MAX_EXCLUSIVE)
        for _ in range(INITIAL_TOKENS)
    ]
    marker = random.Random(salt)
    tokens[:CACHE_BUST_TOKENS] = [
        marker.randrange(TOKEN_MIN, TOKEN_MAX_EXCLUSIVE)
        for _ in range(CACHE_BUST_TOKENS)
    ]
    return tokens


def follow_up_tokens() -> list[int]:
    payload = random.Random(0x474C4D46)
    return [
        payload.randrange(TOKEN_MIN, TOKEN_MAX_EXCLUSIVE)
        for _ in range(FOLLOW_UP_TOKENS)
    ]


def background_tail_tokens(salt: int, index: int) -> list[int]:
    payload = random.Random((salt << 16) ^ index ^ 0x474C4D42)
    return [
        payload.randrange(TOKEN_MIN, TOKEN_MAX_EXCLUSIVE)
        for _ in range(BACKGROUND_TAIL_TOKENS)
    ]


def digest(tokens: list[int]) -> str:
    encoded = json.dumps(tokens, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def request(
    url: str,
    prompt: list[int],
    request_id: str,
    timeout: float,
    session_id: str | None = None,
    start_barrier: threading.Barrier | None = None,
) -> dict[str, object]:
    body = json.dumps(
        {
            "model": MODEL,
            "prompt": prompt,
            "max_tokens": 1,
            "temperature": 0,
            "stream": True,
        },
        separators=(",", ":"),
    ).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Request-Id": request_id,
    }
    if session_id:
        headers["X-Session-Id"] = session_id
    http_request = urllib.request.Request(
        f"{url.rstrip('/')}/v1/completions",
        data=body,
        headers=headers,
        method="POST",
    )
    if start_barrier:
        start_barrier.wait(timeout=60)

    started = time.perf_counter()
    first_token = None
    status = None
    response_body = b""
    try:
        with urllib.request.urlopen(http_request, timeout=timeout) as response:
            status = response.status
            chunks = []
            while True:
                line = response.readline()
                if not line:
                    break
                chunks.append(line)
                if (
                    first_token is None
                    and line.startswith(b"data:")
                    and b"[DONE]" not in line
                ):
                    first_token = time.perf_counter()
            response_body = b"".join(chunks)
    except urllib.error.HTTPError as error:
        status = error.code
        response_body = error.read()
    except Exception as error:  # noqa: BLE001 - transport failures are results.
        return {
            "request_id": request_id,
            "status": status,
            "error": repr(error),
            "seconds": time.perf_counter() - started,
        }

    finished = time.perf_counter()
    result: dict[str, object] = {
        "request_id": request_id,
        "status": status,
        "seconds": finished - started,
        "ttft_seconds": (
            first_token - started if first_token is not None else None
        ),
        "response_bytes": len(response_body),
    }
    if status != 200:
        result["response"] = response_body.decode(errors="replace")[:2_000]
    return result


def endpoint_inflight_tokens(
    metrics_url: str, endpoint_name: str
) -> dict[str, float]:
    """Return gauges for rank endpoints sharing a name prefix."""
    with urllib.request.urlopen(metrics_url, timeout=10) as response:
        metrics = response.read().decode()
    totals: dict[str, float] = {}
    for line in metrics.splitlines():
        match = INFLIGHT_TOKENS_RE.match(line)
        if not match:
            continue
        labels = dict(LABEL_RE.findall(match.group(1)))
        matched_endpoint = labels.get("endpoint_name", "")
        if matched_endpoint.startswith(endpoint_name):
            totals[matched_endpoint] = (
                totals.get(matched_endpoint, 0.0) + float(match.group(2))
            )
    return totals


def capacity_queues(
    kube_context: str,
    namespace: str,
    engine_pod: str,
    endpoint_name: str,
) -> dict[str, float]:
    completed = subprocess.run(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            namespace,
            "exec",
            engine_pod,
            "-c",
            "vllm",
            "--",
            "python3",
            "-c",
            REMOTE_CAPACITY_QUEUE_SCRIPT,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    by_rank = json.loads(completed.stdout)
    return {
        f"{endpoint_name}-rank-{rank}": float(value)
        for rank, value in by_rank.items()
    }


def wait_for_inflight_load(
    metrics_url: str,
    endpoint_name: str,
    minimum_tokens: float,
    minimum_loaded_ranks: int,
    required_capacity_queued_ranks: int,
    kube_context: str,
    namespace: str,
    engine_pod: str,
    timeout: float,
) -> dict[str, object]:
    """Wait for EPP-accounted load and optional real vLLM capacity queues."""
    started = time.perf_counter()
    observations: list[dict[str, object]] = []
    peak_tokens: dict[str, float] = {}
    while time.perf_counter() - started < timeout:
        elapsed = time.perf_counter() - started
        try:
            tokens = endpoint_inflight_tokens(metrics_url, endpoint_name)
            queues = (
                capacity_queues(
                    kube_context, namespace, engine_pod, endpoint_name
                )
                if required_capacity_queued_ranks
                else {}
            )
        except Exception as error:  # noqa: BLE001 - preserve probe evidence.
            observations.append({"seconds": elapsed, "error": repr(error)})
        else:
            for endpoint, value in tokens.items():
                peak_tokens[endpoint] = max(peak_tokens.get(endpoint, 0), value)
            loaded = sorted(
                endpoint
                for endpoint, value in tokens.items()
                if value >= minimum_tokens
            )
            queued = sorted(
                endpoint for endpoint, value in queues.items() if value > 0
            )
            observations.append(
                {
                    "seconds": elapsed,
                    "tokens": tokens,
                    "capacity_queues": queues,
                    "loaded_ranks": loaded,
                    "capacity_queued_ranks": queued,
                }
            )
            if (
                len(loaded) >= minimum_loaded_ranks
                and len(queued) >= required_capacity_queued_ranks
            ):
                return {
                    "reached": True,
                    "minimum_tokens": minimum_tokens,
                    "minimum_loaded_ranks": minimum_loaded_ranks,
                    "required_capacity_queued_ranks": (
                        required_capacity_queued_ranks
                    ),
                    "peak_tokens": peak_tokens,
                    "observations": observations,
                }
        time.sleep(0.5)
    return {
        "reached": False,
        "minimum_tokens": minimum_tokens,
        "minimum_loaded_ranks": minimum_loaded_ranks,
        "required_capacity_queued_ranks": required_capacity_queued_ranks,
        "peak_tokens": peak_tokens,
        "observations": observations,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", choices=("baseline", "candidate"), required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--salt", type=int, required=True)
    parser.add_argument("--cache-wait-seconds", type=float, default=10.0)
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--background-requests", type=int, default=0)
    parser.add_argument("--pool-background-requests", type=int, default=0)
    parser.add_argument("--direct-background-requests-per-rank", type=int, default=0)
    parser.add_argument("--direct-prefill-url-template")
    parser.add_argument("--direct-prefill-port-base", type=int, default=0)
    parser.add_argument("--direct-ranks", type=int, default=8)
    parser.add_argument("--metrics-url")
    parser.add_argument(
        "--loaded-endpoint", default="glm-5-2-prefill-long-0"
    )
    parser.add_argument(
        "--trigger-minimum-inflight-tokens", type=float, default=12_548
    )
    parser.add_argument("--minimum-inflight-tokens", type=float, default=12_548)
    parser.add_argument("--minimum-loaded-ranks", type=int, default=1)
    parser.add_argument("--require-capacity-queued-ranks", type=int, default=0)
    parser.add_argument("--load-wait-timeout", type=float, default=60.0)
    parser.add_argument("--kube-context", default="kermit_US-EAST-01A")
    parser.add_argument("--namespace", default="nilig-agentx-slo")
    parser.add_argument("--engine-pod", default="glm-5-2-prefill-long-0")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    for name in (
        "background_requests",
        "pool_background_requests",
        "direct_background_requests_per_rank",
        "direct_ranks",
        "minimum_loaded_ranks",
        "require_capacity_queued_ranks",
    ):
        if getattr(args, name) < 0:
            raise SystemExit(f"--{name.replace('_', '-')} must be non-negative")
    direct_background = (
        args.direct_background_requests_per_rank * args.direct_ranks
    )
    total_background = (
        args.background_requests
        + args.pool_background_requests
        + direct_background
    )
    if total_background and not args.metrics_url:
        raise SystemExit("--metrics-url is required with background requests")
    if args.pool_background_requests and not args.background_requests:
        raise SystemExit(
            "--pool-background-requests requires a sticky --background-requests trigger"
        )
    if direct_background and not args.background_requests:
        raise SystemExit(
            "--direct-background-requests-per-rank requires a sticky "
            "--background-requests trigger"
        )
    if direct_background and not args.direct_prefill_url_template:
        raise SystemExit(
            "--direct-prefill-url-template is required with direct backgrounds"
        )
    if direct_background and args.pool_background_requests:
        raise SystemExit("direct and pool background modes are mutually exclusive")
    prefix = seed_prompt(args.salt)
    follow_up = prefix + follow_up_tokens()
    stem = f"glm-{args.arm}-smoke-{args.salt}"
    session_id = f"{stem}-session"

    seed = request(
        args.url,
        prefix,
        f"{stem}-seed",
        args.timeout,
        session_id,
    )
    result: dict[str, object] = {
        "schema": 1,
        "mode": "exact-context-migration-smoke",
        "arm": args.arm,
        "model": MODEL,
        "salt": args.salt,
        "cache_bust_tokens": CACHE_BUST_TOKENS,
        "initial_tokens": len(prefix),
        "follow_up_tokens": len(follow_up) - len(prefix),
        "total_follow_up_tokens": len(follow_up),
        "seed_prompt_sha256": digest(prefix),
        "follow_up_prompt_sha256": digest(follow_up),
        "session_id": session_id,
        "seed": seed,
    }
    if seed.get("status") == 200:
        time.sleep(args.cache_wait_seconds)
        if direct_background:
            sticky_prompts = [
                prefix + background_tail_tokens(args.salt, index)
                for index in range(args.background_requests)
            ]
            direct_prompts = []
            for rank in range(args.direct_ranks):
                for index in range(args.direct_background_requests_per_rank):
                    direct_salt = (
                        args.salt
                        + 2_000_000
                        + rank * args.direct_background_requests_per_rank
                        + index
                    )
                    direct_prompts.append(
                        (
                            rank,
                            index,
                            seed_prompt(direct_salt)
                            + background_tail_tokens(direct_salt, index),
                        )
                    )
            result["direct_background_tokens"] = len(direct_prompts[0][2])
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=total_background
            ) as executor:
                direct_start_barrier = threading.Barrier(direct_background)
                direct_futures = [
                    executor.submit(
                        request,
                        args.direct_prefill_url_template.format(
                            rank=rank,
                            port=args.direct_prefill_port_base + rank,
                        ),
                        prompt,
                        f"{stem}-direct-rank-{rank}-{index}",
                        args.timeout,
                        None,
                        direct_start_barrier,
                    )
                    for rank, index, prompt in direct_prompts
                ]
                direct_load_probe = wait_for_inflight_load(
                    args.metrics_url,
                    args.loaded_endpoint,
                    args.minimum_inflight_tokens,
                    0,
                    args.require_capacity_queued_ranks,
                    args.kube_context,
                    args.namespace,
                    args.engine_pod,
                    args.load_wait_timeout,
                )
                result["direct_load_probe"] = direct_load_probe
                sticky_futures: list[concurrent.futures.Future] = []
                if direct_load_probe["reached"]:
                    sticky_futures = [
                        executor.submit(
                            request,
                            args.url,
                            prompt,
                            f"{stem}-background-{index}",
                            args.timeout,
                            f"{stem}-trigger-session-{index}",
                        )
                        for index, prompt in enumerate(sticky_prompts)
                    ]
                    trigger_probe = wait_for_inflight_load(
                        args.metrics_url,
                        args.loaded_endpoint,
                        args.trigger_minimum_inflight_tokens,
                        1,
                        args.require_capacity_queued_ranks,
                        args.kube_context,
                        args.namespace,
                        args.engine_pod,
                        args.load_wait_timeout,
                    )
                    result["trigger_probe"] = trigger_probe
                    if trigger_probe["reached"]:
                        result["follow_up"] = request(
                            args.url,
                            follow_up,
                            f"{stem}-followup",
                            args.timeout,
                            session_id,
                        )
                result["background"] = [
                    future.result() for future in sticky_futures
                ]
                result["direct_background"] = [
                    future.result() for future in direct_futures
                ]
        elif total_background:
            sticky_prompts = [
                prefix + background_tail_tokens(args.salt, index)
                for index in range(args.background_requests)
            ]
            pool_prompts = [
                seed_prompt(args.salt + 1_000_000 + index)
                for index in range(args.pool_background_requests)
            ]
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=total_background
            ) as executor:
                futures = [
                    executor.submit(
                        request,
                        args.url,
                        prompt,
                        f"{stem}-background-{index}",
                        args.timeout,
                        f"{stem}-trigger-session-{index}",
                    )
                    for index, prompt in enumerate(sticky_prompts)
                ]
                trigger_probe = wait_for_inflight_load(
                    args.metrics_url,
                    args.loaded_endpoint,
                    args.trigger_minimum_inflight_tokens,
                    1,
                    0,
                    args.kube_context,
                    args.namespace,
                    args.engine_pod,
                    args.load_wait_timeout,
                )
                result["trigger_probe"] = trigger_probe
                load_probe = trigger_probe
                if trigger_probe["reached"] and pool_prompts:
                    futures.extend(
                        executor.submit(
                            request,
                            args.url,
                            prompt,
                            f"{stem}-pool-background-{index}",
                            args.timeout,
                        )
                        for index, prompt in enumerate(pool_prompts)
                    )
                    load_probe = wait_for_inflight_load(
                        args.metrics_url,
                        args.loaded_endpoint,
                        args.minimum_inflight_tokens,
                        args.minimum_loaded_ranks,
                        args.require_capacity_queued_ranks,
                        args.kube_context,
                        args.namespace,
                        args.engine_pod,
                        args.load_wait_timeout,
                    )
                result["load_probe"] = load_probe
                if load_probe["reached"]:
                    result["follow_up"] = request(
                        args.url,
                        follow_up,
                        f"{stem}-followup",
                        args.timeout,
                        session_id,
                    )
                result["background"] = [future.result() for future in futures]
        else:
            result["follow_up"] = request(
                args.url,
                follow_up,
                f"{stem}-followup",
                args.timeout,
                session_id,
            )

    print(json.dumps(result, indent=2, sort_keys=True))
    statuses = [
        result.get("seed", {}).get("status"),
        result.get("follow_up", {}).get("status"),
    ]
    statuses.extend(item.get("status") for item in result.get("background", []))
    statuses.extend(
        item.get("status") for item in result.get("direct_background", [])
    )
    if any(status != 200 for status in statuses):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
