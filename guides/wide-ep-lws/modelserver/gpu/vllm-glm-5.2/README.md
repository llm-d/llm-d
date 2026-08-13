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
| Prefill `gpu-memory-utilization` | 0.935                                                                    |
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


K8s takes the last duplicate env var, so appended values override the base defaults.

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

## Benchmarking

These manifests back the [agentic-serving GLM-5.2 guide](../../../../agentic-serving/glm-5-2-h200.md);
benchmark results, the workload description, and key takeaways are in its
[Benchmark Results](../../../../agentic-serving/glm-5-2-h200.md#benchmark-results) section, with
the full analysis and figures in the
[blog post](https://llm-d.ai/blog/serving-glm-5-2-agentic-workloads-on-llm-d).

### Benchmark Overlays

Pre-built overlays under `deployments/benchmark/<config>/<topology>/` combine components with
topology patches. Each matches a tested configuration on CoreWeave H200:

| Configuration | Directory | Components |
| ------------- | --------- | ---------- |
| Baseline | `benchmark/baseline/` | `no-mtp` |
| MTP + Offloading | `benchmark/mtp-offloading/` | `offloading-tiered` |
| Offloading | `benchmark/offloading/` | `no-mtp` + `offloading-tiered` |
| Full ISL + MTP + Offloading | `benchmark/full-isl-mtp-offloading/` | `offloading-tiered` |

Deploy a benchmark config:

```bash
kubectl apply -n ${NAMESPACE} -k deployments/benchmark/<config>/<topology>
```

Example overlay (`mtp-offloading/p1w1d1w1`):

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../../../providers/coreweave
components:
  - ../../../../components/offloading-tiered
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

### aiperf Command

Every reported number comes from the same [aiperf](https://github.com/ai-dynamo/aiperf) `profile`
invocation, run from inside the cluster against the Kubernetes Gateway Service
(`llm-d-inference-gateway-istio`, from [Gateway Mode](../../../README.md#gateway-mode) with
`PROVIDER_NAME=istio`) and swept across concurrency. The dataset used is the
`semianalysis_cc_traces_weka_with_subagents` aiperf preset, backed by
[`semianalysisai/cc-traces-weka-062126`](https://huggingface.co/datasets/semianalysisai/cc-traces-weka-062126)
on HuggingFace.

```bash
aiperf profile \
    --scenario 'inferencex-agentx-mvp' \
    --url 'http://llm-d-inference-gateway-istio:80/v1' \
    --model 'zai-org/GLM-5.2-FP8' \
    --max-context-length <142000|10000000> \
    --endpoint-type 'chat' \
    --streaming \
    --use-server-token-count \
    --public-dataset 'semianalysis_cc_traces_weka_with_subagents' \
    --concurrency <16|32|64|128|256|512> \
    --random-seed 42 \
    --benchmark-duration 900 \
    --server-metrics 'http://llm-d-inference-gateway-istio:80/metrics' \
    --no-gpu-telemetry \
    --output-artifact-dir <path> \
    --ui 'simple'
```

Only `--max-context-length` and the `--concurrency` sweep differ across the four reported
configurations:

| Configuration | `--max-context-length` | `--concurrency` sweep |
| --------------------------- | ----------------------- | ------------------------- |
| Baseline | `142000` | 16, 32, 64, 128 |
| MTP + Offloading | `142000` | 16, 32, 64, 128 |
| Offloading | `142000` | 16, 32, 64, 128 |
| Full ISL + MTP + Offloading | `10000000` | 16, 32, 64, 128, 256, 512 |

`142000` truncates the trace dataset to requests that fit an EP8 (1-node) prefill deployment.
`10000000` is effectively unbounded and replays full traces (up to ~1M input tokens) — this is
the "Full ISL" config. At concurrency 256/512, some Full ISL runs on smaller topologies exceeded
server capacity (warmup failures) and are excluded from the reported results.

## Optional Features

### MTP Speculative Decoding

On by default (3 tokens) for both prefill and decode. Disable with the `no-mtp`
component or `ENABLE_MTP=0`. Token count: `MTP_NUM_TOKENS` (default `3`).

### EPP Routing

The GLM-5.2 EPP overrides (`router/glm-5.2-overrides.values.yaml`) replace the wide-ep-lws
prefix-cache scorer with dual prefix-cache scoring for P/D routing — include the file after
`wide-ep-lws.values.yaml` when installing the router:

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
NAMESPACE=${NAMESPACE} bash guides/wide-ep-lws/monitoring/apply-scrape-configs.sh
```

DCGM custom metrics: `base/dcgm-custom-metrics.yaml`.

## Cleanup

```bash
kubectl delete -n ${NAMESPACE} -k deployments/<deployment>
```
