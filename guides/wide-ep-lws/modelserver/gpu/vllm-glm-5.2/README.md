# GLM-5.2-FP8 on H200

## Overview

This guide deploys [GLM-5.2-FP8](https://huggingface.co/zai-org/GLM-5.2-FP8) (753B MoE) on H200
GPUs using LeaderWorkerSets, with two serving topologies:

- **P/D disaggregated** — separate prefill and decode LeaderWorkerSets using NIXL for KV
  transfer. Prefill runs DEP8 (TP=1, DP=8) on 1 node; decode runs DEP16 (TP=1, DP=16) across
  2 nodes (wide EP). DeepEP high-throughput all-to-all for prefill, low-latency for decode.
- **Aggregate** — a single LeaderWorkerSet with TP=8, no disaggregation. Suited for
  high-interactivity workloads.

Both topologies use DeepGemm MoE backend and enable tool calling (`glm47`) and
reasoning (`glm45`) parsers. MTP speculative decoding is on by default (3 tokens).

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
| Parallelism (aggregate) | TP=8                                                                               |
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
| `p2w1d1w1` | 2 replicas, 1 node, DEP8   | 1 replica, 1 node, DEP8      | 3 / 24       |
| `p2w1d1w2` | 2 replicas, 1 node, DEP8   | 1 replica, 2 nodes, DEP16    | 4 / 32       |
| `p3w2d1w2` | 3 replicas, 2 nodes, DEP16 | 1 replica, 2 nodes, DEP16    | 8 / 64       |

### Supported Hardware Backends

| Backend             | Directory                                                      | Notes                                    |
| ------------------- | -------------------------------------------------------------- | ---------------------------------------- |
| NVIDIA GPU (vLLM)   | `wide-ep-lws/modelserver/gpu/vllm-glm-5.2/`                   | H200, P/D disaggregated + aggregate      |

## Components

Add [kustomize Components](https://kubectl.docs.kubernetes.io/guides/config_management/components/)
to a deployment's `kustomization.yaml` under `components:`.

| Component | Targets | Effect |
| --------- | ------- | ------ |
| `no-mtp` | prefill + decode | Disables MTP speculative decoding (`ENABLE_MTP=0`) |
| `offloading-cpu` | prefill only | CPU-only KV cache offloading (`OFFLOADING_MODE=cpu`) |
| `offloading-tiered` | prefill only | CPU + NVMe tiered KV cache offloading (`OFFLOADING_MODE=tiered`) |
| `gpu-mem-prefill-0905` | prefill only | Sets prefill `gpu-memory-utilization` to 0.905 |
| `gpu-mem-prefill-091` | prefill only | Sets prefill `gpu-memory-utilization` to 0.91 |
| `max-model-len-130k` | prefill + decode | Sets `max-model-len` to 130000 |
| `max-model-len-131k` | prefill + decode | Sets `max-model-len` to 131072 |

K8s takes the last duplicate env var, so appended values override the base defaults.

### Benchmark Configurations

Tested combinations (all on CoreWeave H200):

| Configuration | Components | GPU Mem (prefill) | max-model-len |
| ------------- | ---------- | ----------------- | ------------- |
| Baseline | `no-mtp` | 0.92 (default) | 120000 (default) |
| CPU Offloading | `offloading-cpu` + `no-mtp` | 0.92 (default) | 120000 (default) |
| Tiered Offloading | `offloading-tiered` + `no-mtp` + `gpu-mem-prefill-091` + `max-model-len-131k` | 0.91 | 131072 |
| Tiered Offloading + MTP | `offloading-tiered` + `gpu-mem-prefill-0905` + `max-model-len-130k` | 0.905 | 130000 |

### Example: Tiered Offloading + MTP

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../providers/coreweave
components:
  - ../../components/offloading-tiered
  - ../../components/gpu-mem-prefill-0905
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

### Aggregate

```bash
kubectl apply -n ${NAMESPACE} -f base/aggregate.yaml
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

## Benchmarking

Staircase workload profiles for P/D and aggregate topologies are included:

- `bench-stairs-pd.yaml` — ramps request rate across P/D disaggregated deployments
- `bench-stairs-aggregate.yaml` — ramps request rate on the aggregate deployment

See the [agentic-serving guide](../../../../agentic-serving/glm-5-2-h200.md) for the full
benchmarking workflow.

## Benchmark Results

> Under construction — benchmark results will be published here as runs complete.

## Optional Features

### MTP Speculative Decoding

On by default (3 tokens) for both prefill and decode. Disable with the `no-mtp`
component or `ENABLE_MTP=0`. Token count: `MTP_NUM_TOKENS` (default `3`).

### DSpark Speculative Decoding

Set `ENABLE_DSPARK=1` on both prefill and decode. Only activates on multi-node
deployments (`LWS_GROUP_SIZE > 1`). Uses `RedHatAI/GLM-5.2-speculator.dspark` with
7 speculative tokens by default.

When enabled, GPU memory utilization is reduced by 0.07 (decode) / 0.04 (prefill) to fit
the draft model, and `NVSHMEM_QP_DEPTH` is scaled for the expanded dispatch token count.

| Variable            | Default                                   |
| ------------------- | ----------------------------------------- |
| `ENABLE_DSPARK`     | `0`                                       |
| `DSPARK_MODEL`      | `RedHatAI/GLM-5.2-speculator.dspark`      |
| `DSPARK_NUM_TOKENS` | `7`                                       |

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

Hotfixes: `kv_quant_mode` in `MLAAttention.get_kv_cache_spec` (vllm#48379),
`set_` overflow for packed KV caches in the offloading worker.

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

**P/D:**

```bash
kubectl delete -n ${NAMESPACE} -k deployments/<deployment>
```

**Aggregate:**

```bash
kubectl delete -n ${NAMESPACE} -f base/aggregate.yaml
```
