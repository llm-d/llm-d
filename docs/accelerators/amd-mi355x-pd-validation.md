# 2-Node P/D Disaggregation Validation on AMD MI355X

This document records an end-to-end validation that vLLM's NIXL-based P/D KV-transfer mechanism — the same one llm-d's pd-disaggregation guide depends on — is functional across **two AMD Instinct MI355X (gfx950) nodes** connected by RoCE, using the published `ghcr.io/llm-d/llm-d-rocm:v0.6.0` image without modification.

The well-lit-path described in `guides/pd-disaggregation/README.amd.md` requires Kubernetes (the routing-sidecar discovers prefiller endpoints from an `InferencePool` CRD). This document does **not** replace that guide. It uses vLLM's standalone `toy_proxy_server.py` test harness to validate the underlying KV-transfer mechanism without K8s, so that AMD operators can de-risk their cluster bring-up before standing up the full Helm stack.

---

## Hardware

| Property | Value |
|---|---|
| Nodes | 2× 8× AMD Instinct MI355X (gfx950) |
| VRAM | 288 GB / GPU (4608 GB total) |
| Interconnect | RoCE, 8× per-GPU NICs, 0.078 ms RTT, 317 Gb/s single-NIC measured |
| Cluster | Tensorwave (`mia1-p01-g07`, `mia1-p01-g64`) |
| Container | `ghcr.io/llm-d/llm-d-rocm:v0.6.0` (vLLM 0.15.1, ROCm 7.x) |
| Model | `amd/Llama-3.3-70B-Instruct-FP8-KV` |

## Topology

```
client request
    │
    ▼
  toy_proxy_server  (g07, port 9000)
    │
    │  prefill request (HTTP)
    ▼
  PREFILL: vLLM   (g64, TP=4, port 8001)
   ─ kv_role="kv_both", NIXL listening 10.101.64.101:5601
    │
    │  KV cache via NIXL/UCX over RoCE (10.101.64.101 → 10.101.7.101)
    ▼
  DECODE: vLLM    (g07, TP=4, port 8000)
   ─ kv_role="kv_both", NIXL listening 10.101.7.101:5600
    │
    │  decoded tokens (HTTP)
    ▼
  client response
```

## Per-engine launch command

Identical on both nodes except `--port`, `VLLM_NIXL_SIDE_CHANNEL_HOST`, and `VLLM_NIXL_SIDE_CHANNEL_PORT`:

```bash
docker run -d --name llmd-pd-decode \
  --device=/dev/kfd --device=/dev/dri --device=/dev/infiniband \
  --network=host --ipc=host --shm-size=128g \
  --group-add video --cap-add SYS_PTRACE --cap-add IPC_LOCK \
  --security-opt seccomp=unconfined \
  -v /path/to/models:/models:ro \
  -e VLLM_NIXL_SIDE_CHANNEL_HOST=10.101.7.101 \
  -e VLLM_NIXL_SIDE_CHANNEL_PORT=5600 \
  -e UCX_TLS=rc,tcp,sm \
  -e UCX_NET_DEVICES=tw-eth0 \
  ghcr.io/llm-d/llm-d-rocm:v0.6.0 \
    --model /models/Llama-3.3-70B-Instruct-FP8-KV \
    --port 8000 --tensor-parallel-size 4 \
    --gpu-memory-utilization 0.85 --block-size 128 \
    --max-model-len 32000 \
    --kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_both"}' \
    --attention-backend ROCM_ATTN \
    --disable-uvicorn-access-log
```

`UCX_NET_DEVICES=tw-eth0` pins UCX to the named per-GPU RoCE NIC on this Tensorwave node; substitute the appropriate device for your cluster.

## Proxy launch (inside the decode container)

```bash
# vLLM's standalone P/D test harness, not part of llm-d
curl -L -O https://raw.githubusercontent.com/vllm-project/vllm/main/tests/v1/kv_connector/nixl_integration/toy_proxy_server.py
pip install fastapi httpx uvicorn
python3 toy_proxy_server.py \
  --port 9000 --host 0.0.0.0 \
  --prefiller-hosts 10.101.64.101 --prefiller-ports 8001 \
  --decoder-hosts 127.0.0.1 --decoder-ports 8000
```

## Results

### Engine bring-up

Both engines reported `NIXL is available` and `Initializing NIXL Scheduler`. Each engine got a distinct `engine_id` and bound its NIXL side channel to its node's RoCE NIC:

| Side | engine_id | NIXL side channel |
|---|---|---|
| prefill (g64) | `de6a74d7-d752-4ba9-ae18-edadd1bd2991` | `10.101.64.101:5601` |
| decode (g07) | `03868848-655c-4c06-8aa4-12219d9fd376` | `10.101.7.101:5600` |

A single `ucx_utils.cpp:589 memory is detected as host, check that UCX is configured with CUDA support` warning appears on container startup. It is non-fatal — the underlying ROCm-aware path remains used for the GPU-side transfers in the workloads tested.

### Disaggregated inference

```
$ curl http://127.0.0.1:9000/v1/completions \
    -H 'Content-Type: application/json' \
    -d '{"model":"...","prompt":"Hello, my name is Alvin and I am",
         "max_tokens":32,"temperature":0}'
```

Returned (E2E 3.9 s):

> *" a 3rd year student at the University of California, Berkeley, studying Computer Science. I am excited to be a part of the Google Summer of Code program"*

This output matches what the same model produces in single-node mode (#1228 validation), which confirms the KV cache transferred from prefill→decode is semantically correct.

### Sequential reliability test

16 sequential disaggregated requests, 4 different prompt families × 4 each, 64 tokens each, `temperature=0.7` with distinct seeds:

* All 16 requests returned 200 OK
* 1024 generated tokens total in 15.9 s (≈ 64 tok/s aggregate, sequential)
* Sample outputs (3 of 16 shown):

```
req 0:  ' a cat.\nWhy did the cat join a band? Because it wanted to be a purr-cussionist!'
req 7:  ' any study of science and engineering, and this book provides a comprehensive in'
req 15: ' many fields, including physics, engineering, and computer science. The goal of '
```

## What this validates

1. **`ghcr.io/llm-d/llm-d-rocm:v0.6.0` initializes NIXL correctly on MI355X** and binds NIXL side-channel ports to RoCE NICs (`tw-ethN`) without modification.
2. **vLLM's NIXL-based KV transfer works between two MI355X nodes over RoCE.** Both engines report the expected `Registering KV_Caches. use_mla: False, kv_buffer_device: cuda` log line, KV pages are placed on GPU memory, and end-to-end HTTP requests to a P/D-aware proxy return correct text.
3. **The 2-node Tensorwave RoCE topology** (10.101.0.0/16, 8 ACTIVE per-GPU NICs per node, 317 Gb/s single-NIC RDMA bandwidth) is sufficient for llm-d's published P/D recommended config.
4. **The AMD-specific path is not the limiting factor** for bringing up llm-d P/D on MI355X. The remaining work for full validation is operating-environment (Kubernetes + AMD GPU operator + AMD network operator), not in the AMD container or the vLLM AMD backends.

## What this does NOT validate

* **The full llm-d well-lit-path with Helm + routing-sidecar + EPP + InferencePool.** That path requires a Kubernetes cluster with the AMD GPU operator and AMD network operator. This document is intentionally a prerequisite check, not a replacement.
* **Heterogeneous parallelism** (e.g., TP=1 prefiller × 4 replicas + TP=4 decoder, the configuration suggested in `values_amd.yaml`). The toy proxy used here treats both sides as single backends.
* **Performance under concurrency.** The 64 tok/s aggregate above is sequential-only and not a meaningful throughput number — it exists only to demonstrate reliability.
* **Multi-replica routing decisions.** Endpoint picking and sidecar smart batching are not exercised by the toy proxy.

## Reproducer summary

1. Allocate two 8×MI355X nodes on the same RoCE fabric.
2. Confirm RoCE connectivity: `ping <peer>`, `ib_write_bw -d rdma0 -F --report_gbits` between the two nodes.
3. On each node, launch one vLLM container as shown above (different `--port`, different `VLLM_NIXL_SIDE_CHANNEL_*`).
4. Inside the decode container, install `fastapi httpx uvicorn` and run the upstream `toy_proxy_server.py` pointed at both engines.
5. Issue OpenAI-format `/v1/completions` to the proxy port.

## Validation environment

* Tensorwave nodes `mia1-p01-g07` (amd-aim partition) and `mia1-p01-g64` (amd-tw partition)
* `amd/Llama-3.3-70B-Instruct-FP8-KV` weights mounted from a shared filesystem
* Date: April 2026

## Refs

* #1228 — Initial single-node MI355X validation
* This PR (#1233) — Single-node MI355X perf reference + long-context values
* `tests/v1/kv_connector/nixl_integration/toy_proxy_server.py` in `vllm-project/vllm` — upstream test harness used here
