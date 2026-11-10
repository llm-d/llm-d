#!/usr/bin/env python3
"""Validate tiered prefix caching for every model pod, on any accelerator.

The runner discovers and monitors pods, then streams this script into each
modelserver container. The internal pod probe uses localhost for requests and
metrics. Checks follow the deployed engine and connector: native vLLM stores,
external connector lookups, SGLang HiCache stores, or LMCache stores/retrievals.
"""

import argparse
import json
import math
import os
import re
import shlex
import signal
import subprocess
import sys
import time
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener


def runtime_config(container, model_server, connector):
    """Read the deployed arguments without importing an engine on the runner."""
    declared = container.get("command", []) + container.get("args", [])
    arguments = []
    for index, argument in enumerate(declared):
        # Preserve ordinary argv entries, especially quoted JSON configs.
        # Shell launchers may forward argv or wrap a second shell script;
        # inspect both their script and their arguments without evaluating it.
        if ((index and declared[index - 1] in ("-c", "-lc"))
                or "exec vllm serve" in argument):
            arguments.extend(shlex.split(argument.replace("\\\n", ""), comments=True))
        else:
            arguments.append(argument)

    def option(name, default=None):
        for index, argument in enumerate(arguments):
            if argument.startswith(name + "="):
                return argument.partition("=")[2]
            if argument == name and index + 1 < len(arguments):
                return arguments[index + 1]
        return default

    if model_server == "sglang":
        if connector != "native":
            raise RuntimeError(f"SGLang does not support CONNECTOR={connector} in this guide")
        if "--enable-hierarchical-cache" not in arguments:
            raise RuntimeError("SGLang hierarchical caching is disabled")
        return {
            "chunk_size": int(option("--page-size", 64)),
            "storage_backend": option("--hicache-storage-backend"),
        }

    transfer = json.loads(option("--kv-transfer-config", "{}"))
    name = transfer.get("kv_connector", "")
    expected = {
        "native": "Offload",
        "lmcache-connector": "LMCache",
        "mooncake-store": "MooncakeStore",
    }[connector]
    if expected not in name or transfer.get("kv_role") != "kv_both":
        raise RuntimeError(f"Deployed KV connector does not match {connector}: {transfer!r}")
    extra = transfer.get("kv_connector_extra_config", {})
    storage_paths = [tier["root_dir"] for tier in extra.get("secondary_tiers", [])
                     if tier.get("type") == "fs" and tier.get("root_dir")]
    if extra.get("raiden_storage_path"):
        storage_paths.append(extra["raiden_storage_path"])
    return {
        "kv_connector": name,
        "chunk_size": max(int(option("--block-size", 64)), int(extra.get("block_size", 64))),
        "storage_paths": storage_paths,
    }


def verify_pod(model, invocation_id, deadline_arg, model_server, connector, variant, runtime_arg):
    deadline = time.monotonic() + (float(deadline_arg) - time.time())
    runtime = json.loads(runtime_arg)

    def require(condition, message):
        if not condition:
            raise RuntimeError(message)

    def remaining():
        seconds = deadline - time.monotonic()
        require(seconds > 0, "Tiered prefix cache validation deadline exceeded")
        return seconds

    def interrupted(signum, frame):
        raise RuntimeError(f"Tiered prefix cache validation interrupted (signal {signum})")

    for signum in (signal.SIGALRM, signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, interrupted)
    signal.setitimer(signal.ITIMER_REAL, remaining())

    if connector == "lmcache-connector":
        # Only this adapter needs LMCache installed. Resolve its actual config,
        # including file/environment overrides, inside the model container.
        from lmcache.integration.vllm.utils import lmcache_get_or_create_config

        config = lmcache_get_or_create_config()
        chunk_size = config.chunk_size
        if variant == "cpu":
            require(config.local_cpu, "LMCache local CPU tier is disabled")
        else:
            require(config.local_disk, "LMCache filesystem tier is disabled")
    else:
        chunk_size = runtime["chunk_size"]
        if model_server == "sglang" and variant == "fs":
            require(runtime["storage_backend"] == "file",
                    "SGLang filesystem storage backend is disabled")
        elif connector == "native" and variant == "fs":
            require(runtime["storage_paths"], "Native filesystem tier is disabled")
        elif connector == "mooncake-store":
            config_path = os.environ.get("MOONCAKE_CONFIG_PATH")
            require(config_path, "MooncakeStore configuration path is missing")
            config = json.loads(Path(config_path).read_text(encoding="utf-8"))
            require(config.get("mode") == ("embedded" if variant == "cpu" else "standalone-store")
                    and config.get("enable_offload") is (variant == "fs"),
                    f"MooncakeStore configuration does not match tier {variant}: {config!r}")
    require(type(chunk_size) is int and chunk_size > 0,
            f"Invalid effective cache chunk/page size: {chunk_size!r}")
    max_tokens = 16
    opener = build_opener(ProxyHandler({}))

    def http(path, payload=None, timeout=10):
        request = Request(
            "http://127.0.0.1:8000" + path,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with opener.open(request, timeout=min(timeout, remaining())) as response:
                require(200 <= response.status < 300,
                        f"{path}: unsuccessful HTTP status {response.status}")
                body = response.read(8 * 1024 * 1024 + 1)
        except HTTPError as error:
            detail = error.read(4096).decode(errors="replace")
            raise RuntimeError(f"{path}: HTTP {error.code}: {detail}") from error
        require(len(body) <= 8 * 1024 * 1024, f"{path}: response is too large")
        remaining()
        return body.decode()

    def http_json(path, payload=None, timeout=10):
        def invalid_constant(value):
            raise ValueError(f"Non-finite JSON constant: {value}")
        try:
            value = json.loads(http(path, payload, timeout),
                               parse_constant=invalid_constant)
        except (ValueError, UnicodeError) as error:
            raise RuntimeError(f"{path}: invalid JSON response") from error
        require(isinstance(value, dict), f"{path}: expected a JSON object")
        require("error" not in value, f"{path}: application error: {value.get('error')}")
        return value

    models = http_json("/v1/models").get("data")
    require(isinstance(models, list) and len(models) == 1
            and isinstance(models[0], dict) and models[0].get("id") == model,
            f"Served model does not match {model!r}: {models!r}")

    # Tokenize the actual completion prompt, rather than estimating from
    # characters. Keep at least four complete chunks, and use the server's
    # effective context limit before attempting inference. Native connectors
    # and HiCache use their deployed block/page size; LMCache uses its loader.
    target_tokens = max(1024, 4 * chunk_size)
    passage = (
        "Evicted KV-cache blocks move to CPU RAM and are loaded back "
        "instead of recomputed. "
    )
    repetitions = 64
    while True:
        prompt = (
            f"Unique KV transfer validation {invocation_id}. "
            + passage * repetitions
            + "\nWhat is six multiplied by seven? Answer with the number only.\nAnswer:"
        )
        tokenized = http_json("/tokenize", {
            "model": model, "prompt": prompt, "add_special_tokens": True,
        })
        prompt_tokens = tokenized.get("count")
        tokens = tokenized.get("tokens")
        context_limit = tokenized.get("max_model_len")
        require(type(prompt_tokens) is int and prompt_tokens > 0
                and isinstance(tokens, list) and len(tokens) == prompt_tokens
                and all(type(token) is int and token >= 0 for token in tokens),
                f"Invalid tokenization response: {tokenized!r}")
        require(type(context_limit) is int and context_limit > max_tokens,
                f"Invalid model context limit: {context_limit!r}")
        require(prompt_tokens + max_tokens <= context_limit,
                f"Validation prompt ({prompt_tokens} tokens) plus generation "
                f"does not fit the {context_limit}-token context")
        if prompt_tokens >= target_tokens:
            break
        repetitions *= 2

    stored_name = "lmcache:num_stored_tokens_total"
    store_count_name = "lmcache:time_to_store_count"
    hit_name = "lmcache:num_hit_tokens_total"
    resident_name = "vllm:prefix_cache_hits_total"
    success_name = "vllm:request_success_total"
    native_store_name = "vllm:kv_offload_store_bytes_total"
    legacy_store_name = "vllm:kv_offload_total_bytes_total"
    query_name = "vllm:external_prefix_cache_queries_total"
    host_name = "sglang:hicache_host_used_tokens"
    backup_name = "sglang:hicache_backup_tokens_total"
    optional_names = set()
    gauge_names = set()
    if connector == "lmcache-connector":
        metric_names = {stored_name, store_count_name, hit_name, resident_name, success_name}
        check = "LMCache storage and retrieval"
    elif model_server == "sglang":
        success_name = "sglang:num_requests_total"
        metric_names = {host_name, backup_name, success_name}
        # Some SGLang versions expose host occupancy without a backup counter.
        # Request counter children may also be created on the first request.
        optional_names = {backup_name, success_name}
        gauge_names = {host_name}
        check = "HiCache host storage"
    elif runtime["kv_connector"] == "OffloadingConnector":
        metric_names = {native_store_name, legacy_store_name, success_name}
        optional_names = {native_store_name, legacy_store_name}
        check = "native offload storage"
    else:
        # TPU native and MooncakeStore connectors report standard vLLM
        # connector lookups, rather than OffloadingConnector store bytes.
        metric_names = {query_name, success_name}
        check = "external connector lookups"
    sample_pattern = re.compile(
        r'([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})?\s+(\S+)(?:\s+[0-9]+)?'
    )
    label_pattern = re.compile(
        r'\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*("(?:[^"\\]|\\.)*")\s*(?:,|$)'
    )
    previous_series = {}

    def metrics(require_store_count=False):
        series = {name: {} for name in metric_names}
        for line in http("/metrics").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name_match = re.match(r'[a-zA-Z_:][a-zA-Z0-9_:]*', line)
            if not name_match:
                continue
            name = name_match[0]
            if name.startswith("sglang_"):
                name = "sglang:" + name[len("sglang_"):]
            if name not in metric_names:
                continue
            match = sample_pattern.fullmatch(line)
            require(match is not None, f"Malformed metric sample: {line!r}")
            _, label_text, raw_value = match.groups()
            labels = {}
            position = 0
            while position < len(label_text or ""):
                label = label_pattern.match(label_text, position)
                require(label is not None, f"Malformed metric labels: {line!r}")
                key, value = label.groups()
                require(key not in labels, f"Duplicate metric label: {line!r}")
                labels[key] = json.loads(value)
                position = label.end()
            require(re.fullmatch(
                r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?',
                raw_value,
            ) is not None, f"Malformed metric value: {line!r}")
            try:
                value = Decimal(raw_value)
            except InvalidOperation as error:
                raise RuntimeError(f"Malformed metric value: {line!r}") from error
            require(math.isfinite(float(raw_value)) and value.is_finite() and value >= 0
                    and value == value.to_integral_value(),
                    f"Invalid counter value: {line!r}")
            if any(labels[key] != model for key in ("model_name", "served_model_name")
                   if key in labels):
                continue
            if name.startswith("lmcache:") and labels.get("role", "worker") != "worker":
                continue
            if name == legacy_store_name and labels.get("transfer_type") != "GPU_to_CPU":
                continue
            if name == "vllm:request_success_total" and labels.get("finished_reason") not in ("stop", "length"):
                continue
            identity = tuple(sorted(labels.items()))
            require(identity not in series[name], f"Duplicate metric sample: {line!r}")
            series[name][identity] = int(value)

        for name in metric_names:
            # LMCache creates histogram children only on observe(). An
            # absent initial store count represents no observations; it
            # must appear with a positive delta after the cold request.
            require(series[name] or name in optional_names
                    or (name == store_count_name and not require_store_count),
                    f"Missing required metric: {name}")
            for identity, old_value in previous_series.get(name, {}).items():
                require(identity in series[name],
                        f"Metric series disappeared: {name} {identity!r}")
                require(name in gauge_names or series[name][identity] >= old_value,
                        f"Counter rollback: {name} {identity!r}")
        previous_series.clear()
        previous_series.update(series)
        return {name: sum(values.values()) for name, values in series.items()}

    def completion(salt=None):
        payload = {
            "model": model, "prompt": prompt, "max_tokens": max_tokens,
            "temperature": 0, "top_p": 1, "seed": 123, "n": 1,
            "stream": False, "add_special_tokens": True,
        }
        if salt is not None:
            payload["cache_salt"] = salt
        response = http_json("/v1/completions", payload, timeout=90)
        choices = response.get("choices")
        require(isinstance(choices, list) and len(choices) == 1
                and isinstance(choices[0], dict),
                f"Invalid completion choices: {response!r}")
        text = choices[0].get("text")
        require(isinstance(text, str) and text.strip(),
                f"Empty completion: {response!r}")
        require(choices[0].get("finish_reason") in ("stop", "length"),
                f"Unsuccessful completion: {response!r}")
        usage = response.get("usage")
        require(isinstance(usage, dict) and usage.get("prompt_tokens") == prompt_tokens
                and type(usage.get("completion_tokens")) is int
                and 0 < usage["completion_tokens"] <= max_tokens,
                f"Completion usage does not match the tokenized prompt: {usage!r}")
        return text

    def wait_for(stage, predicate, require_store_count=False):
        poll_deadline = time.monotonic() + min(30, remaining())
        while True:
            current = metrics(require_store_count)
            require(time.monotonic() < poll_deadline,
                    f"Timed out waiting for {stage}: {current!r}")
            if predicate(current):
                return current
            time.sleep(min(1, remaining(), max(0, poll_deadline - time.monotonic())))

    baseline = metrics()
    native_metric = None
    if check == "native offload storage":
        native_metric = next((name for name in (native_store_name, legacy_store_name)
                              if previous_series[name]), None)
        require(native_metric is not None, "Missing native offload store counter")
    print(f"Checking {model_server}/{connector}/{variant}: {check}", flush=True)
    print(f"Cold inference: tokens={prompt_tokens}, chunk_size={chunk_size}", flush=True)
    cold_text = completion(invocation_id + "-cold" if connector == "lmcache-connector" else None)

    def stored_successfully(current):
        if current[success_name] <= baseline[success_name]:
            return False
        if connector == "lmcache-connector":
            return (current[stored_name] - baseline[stored_name] >= chunk_size
                    and current[store_count_name] > baseline[store_count_name])
        if model_server == "sglang":
            return (current[host_name] > 0
                    and (current[backup_name] > baseline[backup_name]
                         or current[host_name] - baseline[host_name] >= chunk_size))
        if native_metric:
            return current[native_metric] > baseline[native_metric]
        return current[query_name] > baseline[query_name]

    stored = wait_for(
        check, stored_successfully,
    )
    if connector == "lmcache-connector":
        require(stored[hit_name] == baseline[hit_name],
                "Unique cold prompt unexpectedly retrieved existing LMCache tokens")

    # A fresh baseline prevents the cold request's observations from
    # satisfying warm assertions. Only LMCache uses different salts: its
    # token-derived keys survive changes to vLLM's resident block hashes.
    lmcache = connector == "lmcache-connector"
    retrieval_baseline = metrics(require_store_count=lmcache)
    print("Warm inference with identical tokens"
          + (" and a distinct cache_salt" if lmcache else ""), flush=True)
    warm_text = completion(invocation_id + "-warm" if lmcache else None)
    require(cold_text == warm_text,
            f"Cold/warm output mismatch: cold={cold_text!r}, warm={warm_text!r}")
    retrieved = wait_for(
        "published warm request statistics" + (" and completed LMCache retrieval" if lmcache else ""),
        lambda current: current[success_name] > retrieval_baseline[success_name]
            and (not lmcache or current[hit_name] > retrieval_baseline[hit_name]),
        require_store_count=lmcache,
    )
    evidence = {
        "validated": True,
        "invocation_id": invocation_id,
        "model": model,
        "model_server": model_server,
        "connector": connector,
        "variant": variant,
        "check": check,
        "retrieval_checked": lmcache,
        "chunk_size": chunk_size,
        "prompt_tokens": prompt_tokens,
        "cold_metric_deltas": {name: stored[name] - baseline[name] for name in metric_names},
        "warm_metric_deltas": {name: retrieved[name] - retrieval_baseline[name] for name in metric_names},
        "output": warm_text,
        "outputs_equal": True,
    }
    if lmcache:
        resident_hits = retrieved[resident_name] - retrieval_baseline[resident_name]
        require(resident_hits == 0,
                f"Warm request reused {resident_hits} resident prefix-cache tokens")
        evidence.update({
            "stored_token_delta": stored[stored_name] - baseline[stored_name],
            "completed_store_delta": stored[store_count_name] - baseline[store_count_name],
            "completed_retrieval_token_delta": retrieved[hit_name] - retrieval_baseline[hit_name],
            "warm_resident_prefix_hit_delta": resident_hits,
            "cold_salt": invocation_id + "-cold",
            "warm_salt": invocation_id + "-warm",
        })
    print("LLMD_TIERED_PREFIX_CACHE_EVIDENCE=" + json.dumps(evidence), flush=True)


def verify_pods(namespace, model, model_server, connector, variant):
    guide = "tiered-prefix-cache"
    deadline = float(os.environ.get("LLMD_TIERED_PREFIX_CACHE_DEADLINE", time.time() + 270))
    if not math.isfinite(deadline):
        raise RuntimeError("Invalid tiered prefix cache validation deadline")
    deadline_monotonic = time.monotonic() + (deadline - time.time())

    def remaining():
        seconds = deadline_monotonic - time.monotonic()
        if seconds <= 0:
            raise RuntimeError("Tiered prefix cache validation deadline exceeded")
        return seconds

    def interrupted(signum, frame):
        raise RuntimeError(f"Tiered prefix cache validation interrupted (signal {signum})")

    for signum in (signal.SIGALRM, signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, interrupted)
    signal.setitimer(signal.ITIMER_REAL, remaining())

    # Stream this verifier to each pod; only the LMCache probe imports LMCache.
    pod_program = Path(__file__).read_text(encoding="utf-8")
    checked_pvcs = set()

    def kubectl(*arguments, program=None, timeout=10):
        result = subprocess.run(
            ["kubectl", "-n", namespace, *arguments],
            input=program, text=True, capture_output=True,
            timeout=min(timeout, remaining()),
        )
        if program is not None and result.stdout:
            print(result.stdout, end="", flush=True)
        if result.returncode:
            raise RuntimeError(
                f"kubectl {' '.join(arguments)} failed ({result.returncode}): "
                f"{result.stderr or result.stdout}"
            )
        remaining()
        return result.stdout

    def snapshot():
        response = json.loads(kubectl(
            "get", "pods", "-l", f"llm-d.ai/guide={guide}",
            "-o", "json",
        ))
        pods = {}
        for pod in response["items"]:
            metadata = pod["metadata"]
            labels = metadata.get("labels", {})
            worker_index = labels.get("leaderworkerset.sigs.k8s.io/worker-index")
            if worker_index is not None and worker_index != "0":
                continue
            containers = [
                container for container in pod["spec"]["containers"]
                if container["name"] in ("modelserver", "modelserver-leader")
            ]
            if not containers:
                if (labels.get("llm-d.ai/role") in ("decode", "prefill")
                        or labels.get("role") == "leader"):
                    raise RuntimeError(f"{metadata['name']}: missing modelserver container")
                continue  # Router/helper pods do not run a modelserver.
            name = metadata["name"]
            if labels.get("llm-d.ai/engine-type", model_server) != model_server:
                raise RuntimeError(f"{name}: model-server engine does not match {model_server}")
            if len(containers) != 1 or metadata.get("deletionTimestamp"):
                raise RuntimeError(f"{name}: ambiguous or terminating model pod")
            status = pod.get("status", {})
            ready = any(condition["type"] == "Ready" and condition["status"] == "True"
                        for condition in status.get("conditions", []))
            container_status = next((
                item for item in status.get("containerStatuses", [])
                if item["name"] == containers[0]["name"]
            ), {})
            running = container_status.get("state", {}).get("running", {})
            if (status.get("phase") != "Running" or not ready
                    or not container_status.get("ready") or not running
                    or not container_status.get("containerID")
                    or type(container_status.get("restartCount")) is not int):
                raise RuntimeError(f"{name}: model pod is not healthy: {status!r}")
            container = containers[0]
            if variant == "fs" and connector != "mooncake-store":
                mount = next((item for item in container.get("volumeMounts", [])
                              if item["mountPath"] == "/mnt/files-storage"), {})
                volume = next((item for item in pod["spec"].get("volumes", [])
                               if item["name"] == mount.get("name")), {})
                claim = volume.get("persistentVolumeClaim", {}).get("claimName")
                if not claim:
                    raise RuntimeError(f"{name}: filesystem tier has no PVC at /mnt/files-storage")
                if claim not in checked_pvcs:
                    phase = kubectl("get", "pvc", claim, "-o", "jsonpath={.status.phase}").strip()
                    if phase != "Bound":
                        raise RuntimeError(f"{name}: offload PVC {claim} is not Bound: {phase!r}")
                    checked_pvcs.add(claim)
            pods[name] = {
                "identity": (
                    metadata["uid"], container_status["containerID"],
                    container_status["restartCount"], running["startedAt"],
                ),
                "runtime": runtime_config(container, model_server, connector),
                "container": container["name"],
            }
        if not pods:
            raise RuntimeError(f"No eligible model-server pods for {guide} in {namespace}")
        return pods

    initial = snapshot()

    def unchanged():
        current = snapshot()
        if current != initial:
            raise RuntimeError(
                f"Model pods were replaced, restarted or changed during validation: "
                f"before={initial!r}, after={current!r}"
            )

    for pod_name in sorted(initial):
        unchanged()
        invocation_id = str(uuid.uuid4())
        print(f"== Tiered prefix cache gate: {pod_name}", flush=True)
        output = kubectl(
            "exec", "-i", pod_name, "-c", initial[pod_name]["container"],
            "--", "python3", "-", "--pod-probe", model, invocation_id, str(deadline),
            model_server, connector, variant, json.dumps(initial[pod_name]["runtime"]),
            program=pod_program, timeout=remaining(),
        )
        reports = [
            json.loads(line.partition("=")[2]) for line in output.splitlines()
            if line.startswith("LLMD_TIERED_PREFIX_CACHE_EVIDENCE=")
        ]
        if (len(reports) != 1 or reports[0].get("validated") is not True
                or reports[0].get("invocation_id") != invocation_id):
            raise RuntimeError(f"{pod_name}: missing completed tiered prefix cache evidence")
        unchanged()
    unchanged()
    print(f"All {len(initial)} model-server pods passed the tiered prefix cache gate")


def main():
    # This mode runs only inside the selected modelserver container. Keeping
    # its imports inside verify_pod avoids engine dependencies on the runner.
    if sys.argv[1:2] == ["--pod-probe"]:
        if len(sys.argv) != 9:
            raise RuntimeError("The internal pod probe requires model, invocation ID, deadline, "
                               "engine, connector, tier and runtime configuration")
        verify_pod(*sys.argv[2:])
        return

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-n", "--namespace", required=True)
    parser.add_argument("-m", "--model", required=True)
    parser.add_argument("--model-server", choices=("vllm", "sglang"),
                        default=os.environ.get("MODEL_SERVER", "vllm"))
    parser.add_argument("--connector", choices=("native", "lmcache-connector", "mooncake-store"),
                        default=os.environ.get("CONNECTOR", "native"))
    parser.add_argument("--variant", choices=("cpu", "fs"),
                        default=os.environ.get("VARIANT", "cpu"))
    args = parser.parse_args()
    if not args.namespace or not args.model:
        parser.error("namespace and model must be nonempty")
    if args.model_server == "sglang" and args.connector != "native":
        parser.error("SGLang supports only CONNECTOR=native in this guide")
    verify_pods(args.namespace, args.model, args.model_server, args.connector, args.variant)


if __name__ == "__main__":
    main()
