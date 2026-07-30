# GLM-5.2-FP8 on H200

## Overview

This guide deploys [GLM-5.2-FP8](https://huggingface.co/zai-org/GLM-5.2-FP8) (753B MoE) on H200
GPUs using P/D-disaggregated LeaderWorkerSets with NIXL for KV transfer. Prefill runs
DEP8 (TP=1, DP=8) on 1 node; decode runs DEP16 (TP=1, DP=16) across 2 nodes (wide EP).
DeepEP high-throughput all-to-all for prefill, low-latency for decode.

DeepGemm MoE backend, tool calling (`glm47`) and reasoning (`glm45`) parsers.
MTP speculative decoding is on by default (3 tokens).

Tested on CoreWeave (CKS) with InfiniBand networking. This recipe reuses the
[wide-ep-lws guide](../../../README.md) for the router/gateway and shared prerequisites
(namespace, HF token secret, LeaderWorkerSet controller).

## Default Configuration

| Parameter               | Value                                                                              |
| ----------------------- | ---------------------------------------------------------------------------------- |
| Model                   | [zai-org/GLM-5.2-FP8](https://huggingface.co/zai-org/GLM-5.2-FP8)                |
| Accelerator             | NVIDIA H200 (8 GPUs per node)                                                      |
| DP model                | Supervisor (`--data-parallel-multi-port-external-lb`)                              |
| Prefill parallelism     | TP=1, DP=8, EP=8 (DEP8) — 1 node                                                  |
| Decode parallelism      | TP=1, DP=16, EP=16 (DEP16, wide) — 2 nodes                                        |
| All-to-all (prefill)    | `deepep_high_throughput`                                                           |
| All-to-all (decode)     | `deepep_low_latency` (IBGDA + NVSHMEM)                                            |
| MoE backend             | DeepGemm                                                                           |
| KV transfer             | NixlConnector                                                                      |
| KV cache offloading     | Off (opt-in via components)                                                        |
| MTP speculative decoding | On (3 tokens; opt-out via `no-mtp` component)                                     |
| Prefill `gpu-memory-utilization` | 0.92 (single-node) / 0.88 (multi-node)                                   |
| Decode `gpu-memory-utilization`  | 0.95                                                                     |
| Reasoning / tool-call   | glm45 / glm47                                                                     |

### P/D Deployment Options

| Deployment | Prefill                    | Decode                        | Nodes / GPUs |
| ---------- | -------------------------- | ----------------------------- | ------------ |
| `p1w1d1w1` | 1 replica, 1 node, DEP8    | 1 replica, 1 node, DEP8      | 2 / 16       |
| `p1w1d1w2` | 1 replica, 1 node, DEP8    | 1 replica, 2 nodes, DEP16    | 3 / 24       |
| `p1w2d1w2` | 1 replica, 2 nodes, DEP16  | 1 replica, 2 nodes, DEP16    | 4 / 32       |
| `p2w1d1w1` | 2 replicas, 1 node, DEP8   | 1 replica, 1 node, DEP8      | 3 / 24       |
| `p2w1d1w2` | 2 replicas, 1 node, DEP8   | 1 replica, 2 nodes, DEP16    | 4 / 32       |
| `p2w2d1w2` | 2 replicas, 2 nodes, DEP16 | 1 replica, 2 nodes, DEP16    | 6 / 48       |
| `p2w2d2w2` | 2 replicas, 2 nodes, DEP16 | 2 replicas, 2 nodes, DEP16   | 8 / 64       |
| `p3w2d1w2` | 3 replicas, 2 nodes, DEP16 | 1 replica, 2 nodes, DEP16    | 8 / 64       |
| `p3w2d2w2` | 3 replicas, 2 nodes, DEP16 | 2 replicas, 2 nodes, DEP16   | 10 / 80      |

### Supported Hardware Backends

| Backend             | Directory                                                      | Notes                                    |
| ------------------- | -------------------------------------------------------------- | ---------------------------------------- |
| NVIDIA GPU (vLLM)   | `wide-ep-lws/modelserver/gpu/vllm-glm-5.2/`                   | H200, P/D disaggregated                  |

## Components

Add [kustomize Components](https://kubectl.docs.kubernetes.io/guides/config_management/components/)
to a deployment's `kustomization.yaml` under `components:`.

| Component | Targets | Effect |
| --------- | ------- | ------ |
| `no-mtp` | prefill + decode | Disables MTP speculative decoding (`ENABLE_MTP=0`) |
| `offloading-cpu` | prefill only | CPU-only KV cache offloading (`OFFLOADING_MODE=cpu`) |
| `offloading-tiered` | prefill only | CPU + NVMe tiered KV cache offloading (`OFFLOADING_MODE=tiered`) |
| `compute-nan-decode` | decode only | Sets `VLLM_COMPUTE_NANS_IN_LOGITS=1` on decode |
| `compute-nan-prefill` | prefill only | Sets `VLLM_COMPUTE_NANS_IN_LOGITS=1` on prefill |
| `mrv1` | decode only | Sets `VLLM_USE_V2_MODEL_RUNNER=0` on decode |
| `max-model-len-130k` | prefill + decode | Sets `max-model-len` to 130000 |

K8s takes the last duplicate env var, so appended values override the base defaults.

### Benchmark Configurations

Pre-built overlays under `deployments/benchmark/<config>/<topology>/` combine components with
topology patches. Each matches a tested configuration on CoreWeave H200:

| Configuration | Directory | Components |
| ------------- | --------- | ---------- |
| Baseline | `benchmark/baseline/` | `no-mtp` |
| Baseline + Compute NaN Decode | `benchmark/baseline-computenan-decode/` | `no-mtp` + `compute-nan-decode` |
| Baseline + Compute NaN Decode Prefill | `benchmark/baseline-computenan-decode-prefill/` | `no-mtp` + `compute-nan-decode` + `compute-nan-prefill` |
| Baseline + Compute NaN Decode Prefill + MRV1 | `benchmark/baseline-computenan-decode-prefill-mrv1/` | `no-mtp` + `compute-nan-decode` + `compute-nan-prefill` + `mrv1` |
| Baseline + Compute NaN Prefill + MRV1 | `benchmark/baseline-computenan-prefill-mrv1/` | `no-mtp` + `compute-nan-prefill` + `mrv1` |
| MTP + Offloading | `benchmark/mtp-offloading/` | `offloading-tiered` |
| MTP + Offloading (new nightly) | `benchmark/mtp-offloading-newnightly/` | `offloading-tiered` |
| Offloading | `benchmark/offloading/` | `no-mtp` + `offloading-tiered` |
| Full ISL + MTP + Offloading | `benchmark/full-isl-mtp-offloading/` | `offloading-tiered` |

Deploy a benchmark config:

```bash
kubectl apply -n ${NAMESPACE} -k deployments/benchmark/<config>/<topology>
```

### Example: Tiered Offloading + MTP

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../providers/coreweave
components:
  - ../../components/offloading-tiered
  - ../../components/max-model-len-130k
patches:
  - target:
      kind: LeaderWorkerSet
      name: ".*-prefill"
    patch: |-
      - op: replace
        path: /spec/replicas
        value: 1
      - op: replace
        path: /spec/leaderWorkerTemplate/size
        value: 1
  - target:
      kind: LeaderWorkerSet
      name: ".*-decode"
    patch: |-
      - op: replace
        path: /spec/replicas
        value: 1
      - op: replace
        path: /spec/leaderWorkerTemplate/size
        value: 1
```

## Prerequisites

In addition to the [wide-ep-lws prerequisites](../../../README.md#prerequisites):

```bash
export KUBECONFIG=~/.kube/config
export NAMESPACE=<your-namespace>
export MODEL=zai-org/GLM-5.2-FP8
```

## Deploy the Model Server

### P/D Disaggregated

Pick a deployment from the [P/D Deployment Options](#pd-deployment-options) table and apply:

```bash
kubectl apply -n ${NAMESPACE} -k deployments/<deployment>
```

Wait for pods to become ready (model load takes time; the startup probe allows up to 45 minutes):

```bash
kubectl get pods -n ${NAMESPACE} -l llm-d.ai/model=GLM-5.2-FP8 -w
```

## Verification

### 1. Get the IP of the Proxy

```bash
export IP=$(kubectl get service wide-ep-lws-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```

### 2. Send Test Requests

Open a temporary shell inside the cluster:

```bash
kubectl run curl-debug --rm -it \
    --image=cfmanteiga/alpine-bash-curl-jq \
    --env="IP=$IP" \
    --env="NAMESPACE=$NAMESPACE" \
    -- /bin/bash
```

Send a completion request:

```bash
curl -X POST http://${IP}/v1/completions \
    -H 'Content-Type: application/json' \
    -d '{
        "model": "zai-org/GLM-5.2-FP8",
        "prompt": "How are you today?"
    }' | jq
```

## Benchmark Results

Agentic code-generation workload (avg ISL ~55K tokens, bursty arrivals) on CoreWeave H200 with
InfiniBand (inter-node) and NVLink (intra-node). Benchmarked with [aiperf](https://github.com/ai-dynamo/aiperf). Full interactive
dashboard and analysis in the accompanying blog post (link TBD).

### CPU Offloading + MTP (recommended)

| Topology | Concurrency | TTFT p50 (s) | TTFT p99 (s) | ITL p50 (ms) | ITL p99 (ms) | Tok/s/user | Throughput (tok/s) |
| -------- | ----------- | ------------ | ------------ | ------------ | ------------ | ---------- | ------------------ |
| `p1w1d1w1` (2 nodes) | 16 | 2.16 | 17.44 | 16.0 | 40.1 | 62.1 | 729 |
| `p1w1d1w1` (2 nodes) | 64 | 19.05 | 101.65 | 18.1 | 27.6 | 55.6 | 1,336 |
| `p1w1d1w1` (2 nodes) | 128 | 48.12 | 173.69 | 18.5 | 29.1 | 54.3 | 1,418 |
| `p1w2d1w2` (4 nodes) | 16 | 2.10 | 14.59 | 14.7 | 47.2 | 67.1 | 784 |
| `p1w2d1w2` (4 nodes) | 64 | 4.96 | 22.02 | 16.5 | 25.6 | 60.8 | 2,529 |
| `p1w2d1w2` (4 nodes) | 256 | 24.87 | 79.61 | 19.7 | 31.9 | 50.9 | 4,356 |
| `p2w2d1w2` (6 nodes) | 64 | 3.62 | 16.04 | 17.2 | 27.1 | 58.4 | 2,953 |
| `p2w2d1w2` (6 nodes) | 256 | 9.28 | 43.27 | 24.4 | 48.1 | 41.1 | 6,155 |
| `p2w2d1w2` (6 nodes) | 512 | 22.44 | 85.05 | 24.9 | 60.0 | 39.9 | 7,046 |
| `p3w2d1w2` (8 nodes) | 64 | 2.91 | 14.01 | 17.7 | 27.1 | 57.5 | 2,898 |
| `p3w2d1w2` (8 nodes) | 256 | 7.51 | 33.41 | 25.4 | 62.2 | 39.4 | 6,476 |
| `p3w2d1w2` (8 nodes) | 512 | 30.10 | 80.31 | 28.8 | 53.4 | 35.1 | 7,057 |

### Baseline (no MTP, no offloading)

| Topology | Concurrency | TTFT p50 (s) | TTFT p99 (s) | ITL p50 (ms) | ITL p99 (ms) | Tok/s/user | Throughput (tok/s) |
| -------- | ----------- | ------------ | ------------ | ------------ | ------------ | ---------- | ------------------ |
| `p1w1d1w1` (2 nodes) | 16 | 1.15 | 14.68 | 34.0 | 36.1 | 30.2 | 439 |
| `p1w1d1w1` (2 nodes) | 64 | 23.85 | 84.43 | 39.9 | 42.9 | 24.9 | 851 |
| `p1w1d1w1` (2 nodes) | 256 | 122.49 | 256.35 | 43.1 | 45.6 | 23.5 | 1,080 |
| `p1w2d1w2` (4 nodes) | 16 | 1.23 | 13.64 | 33.2 | 40.7 | 29.9 | 454 |
| `p1w2d1w2` (4 nodes) | 64 | 2.32 | 13.89 | 38.1 | 39.3 | 26.4 | 1,492 |
| `p1w2d1w2` (4 nodes) | 256 | 17.13 | 193.81 | 41.4 | 45.6 | 24.3 | 2,714 |
| `p2w2d1w2` (6 nodes) | 64 | 1.68 | 12.57 | 38.5 | 41.4 | 26.0 | 1,532 |
| `p2w2d1w2` (6 nodes) | 256 | 3.74 | 36.33 | 46.4 | 48.7 | 22.2 | 3,549 |

### Key Takeaways

- **MTP doubles per-user decode speed**: ~60 tok/s/user with MTP vs ~26 tok/s/user baseline
  (ITL drops from ~35–45 ms to ~15–20 ms).
- **CPU offloading preserves throughput**: adding CPU offloading to enable longer
  contexts does not meaningfully degrade ITL or per-user throughput.
- **Scaling prefill replicas controls TTFT at load**: `p2w2d1w2` (6 nodes) keeps TTFT p99
  under 20s at c64 vs 100s+ for `p1w1d1w1` — prefill replication absorbs burst arrivals.
- **Aggregate throughput scales with nodes**: 7,000+ tok/s at c512 on 8 nodes
  (`p3w2d1w2`) vs ~1,300 tok/s on 2 nodes.

## Optional Features

### MTP Speculative Decoding

On by default (3 tokens) for both prefill and decode. Disable with the `no-mtp`
component or `ENABLE_MTP=0`. Token count: `MTP_NUM_TOKENS` (default `3`).

### EPP Routing

The GLM-5.2 EPP overrides (`router/glm-5.2-overrides.values.yaml`) add dual prefix-cache
scoring for P/D routing:

- **GPU prefix-cache scorer** (weight 5) — auto-tuned, tracks GPU-resident prefix blocks
- **CPU prefix-cache scorer** (weight 2) — fixed LRU capacity (200k entries per server),
  tracks CPU-offloaded prefix blocks
- **Active-request scorer** (weight 1 prefill, 3 decode) — load balancing

All 8 DP rank ports (8000-8007) are exposed as `targetPorts` for per-rank routing.

### KV Cache Offloading (Prefill)

Off by default. Enable via the `offloading-cpu` or `offloading-tiered` component.

- **`offloading-cpu`** — CPU-only offloading via `OffloadingConnector`. Uses mmap in
  `/dev/shm`. The pod allocates 1500Gi memory and 1500Gi `dshm` to accommodate 8 DP
  ranks' mmap regions. `cpu_bytes_to_use` is per-rank — total CPU KV cache = value x 8.
- **`offloading-tiered`** — CPU + NVMe tiered offloading via `TieringOffloadingSpec`.
  Same CPU tier as above, plus NVMe as a secondary eviction target. Host-path volume at
  `/mnt/local/kv-cache` mounted as `/mnt/nvme-cache`.

Without offloading, `max-model-len` caps at ~108K on H200 (model weights ~122 GiB +
KV cache + DeepGemm warmup + CUDA overhead must fit in 139.80 GiB).

Decode pods do not use offloading (256Gi dshm, 512Gi memory).

### InfiniBand Networking

Both prefill and decode configure IB for multi-node communication:

| Variable                  | Value  | Purpose                                          |
| ------------------------- | ------ | ------------------------------------------------ |
| `NCCL_IB_HCA`            | `ibp`  | Filter IB HCAs for NCCL collectives              |
| `NVSHMEM_HCA_PREFIX`      | `ibp`  | Filter IB HCAs for NVSHMEM (decode low-latency)  |
| `NVSHMEM_REMOTE_TRANSPORT` | `ibgda` | GPUDirect Async for NVSHMEM                     |
| `rdma/ib`                 | `8`    | Request 8 RDMA/IB devices per pod                |

Multi-node deployments (`LWS_GROUP_SIZE > 1`) automatically set `NVSHMEM_SYMMETRIC_SIZE=16G`
and reduce `gpu-memory-utilization` to 0.80 to reserve VRAM for the NVSHMEM heap.

### KV Cache Evictor

`base/kv-cache-evictor.yaml` deploys a DaemonSet that evicts stale KV cache data from NVMe
when utilization exceeds 90%, targeting 70%.

### Monitoring

Node-exporter sidecars on each pod collect InfiniBand, CPU, memory pressure, and network
retransmission metrics. Apply Prometheus scrape configs:

```bash
bash guides/wide-ep-lws/monitoring/apply-scrape-configs.sh ${NAMESPACE}
```

DCGM custom metrics: `base/dcgm-custom-metrics.yaml`.

## Cleanup

```bash
kubectl delete -n ${NAMESPACE} -k deployments/<deployment>
```
