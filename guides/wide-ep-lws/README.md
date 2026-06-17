# Wide Expert Parallelism

[![E2E (CKS GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-wide-ep-lws-cks-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-wide-ep-lws-cks-acc-gpu-vllm-x.yaml)
[![E2E (GKE GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-wide-ep-lws-gke-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-wide-ep-lws-gke-acc-gpu-vllm-x.yaml)
[![E2E (OCP GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-wide-ep-lws-ibm-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-wide-ep-lws-ibm-acc-gpu-vllm-x.yaml)

This deployment uses **DP-aware scheduling**, where instead of letting vLLM automatically handle data parallelism internally, we explicitly launch separate vLLM server instances for each data parallel rank with a separate port for each rank. This enables the EPP to schedule requests directly to specific DP ranks, improving KV cache routing efficiency.

## Discussion

vLLM supports multiple "modes" for DP load balancing, including:

- **internal**, where vLLM manages DP-balancedness across all ranks. vLLM exposes a single API server endpoint and spreads load between ranks

![alt text](images/internal-lb.png)

- **external**, where an external router manages DP-balancedness. Each DP-rank exposes an API server endpoint and the external LB balances between these endpoints

![alt text](images/external-lb.png)

vLLM also has a **hybrid** mode, where a single API server is exposed PER-NODE. An external LB balances BETWEEN nodes and vLLM balances WITHIN a node.

In the context of `llm-d`, we want to use **external** load-balancing, so that the `llm-d` EPP is able to properly schedule requests with prefix-cache awareness, which requires targeting a specific DP-rank rather than a particular node. However, WideEP leverages **DeepEP** for the sparse dispatch/combine operations needed for WideEP. DeepEP uses `cuda_ipc` for intra-node traffic, which cannot cross pod-boundaries so using **one-pod-per-dp-rank** is not an option for WideEP deployments - we need to use **one-pod-per-node**. As a result, we have primarily been using vLLM's **hybrid** DP-load balancing mode - meaning `llm-d`'s EPP is unable to schedule onto specific ranks (only can schedule at the node level), meaning that prefix-cache aware routing features from EPP have been incompatible with WideEP deployments.

### Multi-Port Solution

To overcome this challenge, we instead launch 8 vLLM DP instances (each with a separate API endpoint) within a pod that has 8 visible GPUs (all the GPUs on a node). As a result, **DeepEP** is able to communicate over `cuda_ipc` within the node. Then, we configure the Gateway and InferencePool with **multi-port** support. The Gateway and EPP view each vLLM pod as a collection of 8 separate API endpoints and schedules onto each one of these endpoints directly.

We can, therefore, compose the WideEP deployment with the existing scorers (for example, `prefix-cache-scorer` and `active-request-scorer`) to balance load across the ranks and handle complex multi-turn request patterns.

### Process Management

This deployment uses vLLM's built-in DP Supervisor (`--data-parallel-multi-port-external-lb`), which manages the lifecycle of all DP rank processes within a pod. The supervisor automatically assigns `CUDA_VISIBLE_DEVICES` and `--data-parallel-rank` to each child process, aggregates health checks across all children via a single supervisor endpoint, and handles graceful shutdown.

## Overview

This guide demonstrates how to deploy DeepSeek-R1-0528 using vLLM's P/D disaggregation support with NIXL in a wide expert parallel pattern with LeaderWorkerSets with DP-aware scheduling. This guide has been validated on:

* a 32xH200 cluster with InfiniBand networking (CoreWeave)
* a 32xB200 cluster with InfiniBand networking (CoreWeave)
* a 32xH200 cluster on GKE with RoCE networking
* Istio 1.29.2 (required for multi-port support in gateway mode)

## Default Configuration

| Parameter                | Value                                                   |
| ------------------------ | ------------------------------------------------------- |
| Model                    | [DeepSeek-R1-0528](https://huggingface.co/deepseek-ai/DeepSeek-R1-0528) |
| Prefill Data Parallelism | 16                                                      |
| Decode Data Parallelism  | 16                                                      |
| Total GPUs               | 32                                                      |

### Tested Hardware Backends

This guide includes configurations for the following accelerators:

| Backend             | Directory                  | Notes                                      |
| ------------------- | -------------------------- | ------------------------------------------ |
| NVIDIA GPU (GKE)    | `modelserver/gpu/vllm/gke/`         | GKE deployment (H200)                      |
| NVIDIA GPU (CoreWeave)| `modelserver/gpu/vllm/coreweave/`   | CoreWeave deployment                     |
| NVIDIA GPU (DGX Cloud GB200)| `modelserver/gpu/vllm/dgx-cloud-gb200/` | DGX Cloud deployment             |

> [!NOTE]
> The pods leveraging inter-node EP must be deployed in a cluster environment with full mesh
> network connectivity. The DeepEP backend used in WideEP requires All-to-All RDMA
> connectivity. Every NIC on a host must be able to communicate with every NIC on all other
> hosts. Networks restricted to communicating only between matching NIC IDs (rail-only
> connectivity) will fail.

## Hardware Requirements

This guide requires 32 Nvidia H200 or B200 GPUs and InfiniBand or RoCE RDMA networking. Check `modelserver/gpu/vllm/base/decode.yaml` and `modelserver/gpu/vllm/base/prefill.yaml` for detailed resource requirements.

## Prerequisites

* Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md) to use this guide.
* Checkout llm-d repo:

  ```bash
  export branch="main" # branch, tag, or commit hash
  git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout ${branch}
  ```
* Set the following environment variables:

  ```bash
  export GAIE_VERSION=v1.5.0
  export ROUTER_CHART_VERSION=v0
  export GUIDE_NAME="wide-ep-lws"
  export NAMESPACE=llm-d-wide-ep
  export MODEL=deepseek-ai/DeepSeek-R1-0528
  export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
  ```
* Install the Gateway API Inference Extension CRDs:

  ```bash
  kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GAIE_VERSION}/v1-manifests.yaml
  ```
* You have deployed the [LeaderWorkerSet controller](https://lws.sigs.k8s.io/docs/installation/)
* Create a target namespace for the installation:

  ```bash
  kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
  ```
* [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN` matching a valid HuggingFace token](../../helpers/hf-token.md) to pull models.

## Installation Instructions

### 1. Deploy the llm-d Router

#### Standalone Mode

This deploys the llm-d Router with an Envoy sidecar, it doesn't set up a Kubernetes Gateway.

```bash
helm install ${GUIDE_NAME} \
    oci://ghcr.io/llm-d/charts/llm-d-router-standalone-dev \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

<details>
<summary><h4>Gateway Mode</h4></summary>

To use a Kubernetes Gateway managed proxy rather than the standalone version, follow these steps instead of applying the previous Helm chart:

1. *Deploy a Kubernetes Gateway* by following one of [the gateway guides](../../docs/infrastructure/gateway).
2. *Deploy the llm-d Router and an HTTPRoute* that connects it to the Gateway as follows:

```bash
export PROVIDER_NAME=gke # options: none, gke, agentgateway, istio
helm install ${GUIDE_NAME} \
    oci://ghcr.io/llm-d/charts/llm-d-router-gateway-dev  \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/recipes/router/features/httproute-flags.yaml \
    -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
    --set provider.name=${PROVIDER_NAME} \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

</details>

### 2. Deploy the Model Server

Apply the Kustomize overlays for your specific backend:

```bash
export INFRA_PROVIDER=gke # options: gke, coreweave, dgx-cloud-gb200
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/${INFRA_PROVIDER}
```

### 3. (Optional) Enable Monitoring

> [!NOTE]
> GKE provides [automatic application monitoring](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/configure-automatic-application-monitoring) out of the box. The llm-d [Monitoring stack](../../docs/operations/observability/setup.md) is not required for GKE, but it is available if you prefer to use it.

* Install the [Monitoring stack](../../docs/operations/observability/setup.md).
* Deploy the monitoring resources for this guide.

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring-pd
```

### 4. (Optional) Topology Aware Scheduling (TAS)

For information on how to use topology aware scheduling using Kueue, see [LWS + TAS user guide](https://lws.sigs.k8s.io/docs/examples/tas/). To deploy the guide with TAS enabled, use the following command:

```bash
# H200 on GKE
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/topology-aware/gke
# B200 on GKE
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/topology-aware/gke-a4
```

## Verification

### 1. Get the IP of the Proxy

**Standalone Mode**

```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```

<details>
<summary> <b>Gateway Mode</b> </summary>

```bash
export IP=$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')
```

</details>

### 2. Send Test Requests

**Open a temporary interactive shell inside the cluster:**

```bash
kubectl run curl-debug --rm -it \
    --image=cfmanteiga/alpine-bash-curl-jq \
    --namespace="$NAMESPACE" \
    --env="IP=$IP" \
    --env="NAMESPACE=$NAMESPACE" \
    -- /bin/bash
```

**Send a completion request:**

```bash
curl -X POST http://${IP}/v1/completions \
    -H 'Content-Type: application/json' \
    -d '{
        "model": "deepseek-ai/DeepSeek-R1-0528",
        "prompt": "How are you today?"
    }' | jq
```

## Troubleshooting

### Pod Startup Ordering

With 4 pods (2 decode, 2 prefill) requiring inter-node DP coordination, staggered startup can cause cascade failures. If one pod starts significantly before the others, it may timeout waiting for peers and exit cleanly (exit code 0), which triggers the DPSupervisor to shut down all children, causing the other pods to also exit.

If pods are stuck in a restart loop, delete all model server pods at once so they restart simultaneously:
```bash
kubectl delete pods -l llm-d.ai/guide=wide-ep-lws
```

### NCCL Shared Memory

With 8 DP rank processes per pod sharing the `/dev/shm` volume (default 2Gi), NCCL may report "No available shared memory broadcast block" during initialization. This is typically a warning — NCCL falls back to a slower communication path. If pods hang during startup, increase `dshm` `sizeLimit` in the base manifests (e.g., to 16Gi).

## Benchmarking

This guide uses [`llmdbenchmark`](https://github.com/llm-d/llm-d-benchmark) — the supported standard CLI for llm-d performance benchmarking.

In this example we will demonstrate how to run [`inference-perf`](https://github.com/kubernetes-sigs/inference-perf) with a high-concurrency random-data workload designed to saturate the wide-expert-parallel topology, against the stack you just deployed above (standalone or gateway mode). When orchestrating benchmarks via `llmdbenchmark`, the CLI automatically and transparently deploys a harness pod (`llmdbench-harness-launcher`) into your namespace. This pod is central to driving the workload, collecting the results, and tearing itself down when it's finished.

> [!IMPORTANT]
> **For more in-depth explanation and features for benchmarking llm-d guides, see [`helpers/benchmark.md`](../../helpers/benchmark.md).**
>
> The Benchmarking section below contains only the **wide-ep-lws-specific commands** needed to drive the stack you just deployed — for everything else (and especially when something goes wrong), start at [`helpers/benchmark.md`](../../helpers/benchmark.md).
>
> For even more details about benchmarking, see the actual repository: [`llm-d-benchmark` on GitHub](https://github.com/llm-d/llm-d-benchmark).

> [!TIP]
> The command below runs this guide's **dedicated** benchmark profile, which is intentionally shaped to fully saturate the wide-expert-parallel topology — and accordingly takes longer to complete. To run a simpler workload with fewer execution cycles first (useful for validating the path, image pulls, PVC binding, etc. before committing to a real run), pick a generic sample profile such as `shared_prefix_synthetic.yaml` from the catalog in [`helpers/benchmark.md` → Available workload profiles](../../helpers/benchmark.md#available-workload-profiles) and substitute it for the `--workload` flag in the command below.

### 1. Install the `llmdbenchmark` CLI

Automatically clone the benchmark repository into `./llm-d-benchmark/` and create a virtualenv at `./llm-d-benchmark/.venv/` containing dependencies and its installation:

```bash
curl -sSL https://raw.githubusercontent.com/llm-d/llm-d-benchmark/main/install.sh | bash
```

Activate the `venv` and enter the repository directory - both are required: the `venv` puts `llmdbenchmark` on your PATH, and the repository directory contains the `workload/profiles/` and `config/specification/` files that orchestrate the benchmark:

```bash
cd llm-d-benchmark
source .venv/bin/activate
llmdbenchmark --version
```

> [!NOTE]
> Subsequent `llmdbenchmark` commands in this section assume you are inside the `llm-d-benchmark` repo directory with the `venv` activated. If you open a new shell, re-run the two commands above.

### 2. Resolve the endpoint of the stack you just deployed

Set two variables so the rest of the section is topology-agnostic: the endpoint URL and the gateway class. The gateway class tells the CLI which deployment topology the cluster is actually running, without this, the CLI re-renders against the benchmark scenario's default values.

**Standalone Mode** (the default in this guide — no Kubernetes Gateway, EPP pod with an Envoy sidecar):

```bash
export ENDPOINT_URL="http://$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')"
export GATEWAY_CLASS=epponly # standalone mode
```

<details>
<summary> <b>Gateway Mode</b> </summary>

```bash
export ENDPOINT_URL="http://$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')"

# Match whichever provider you used when deploying the gateway (e.g. istio, agentgateway, gke).
export GATEWAY_CLASS=istio
```

</details>

### 3. Run the benchmark profile for Wide Expert Parallelism

`guide_wide-ep-lws_1.yaml` is a **dedicated workload profile** shipped with `llm-d-benchmark` specifically for this guide — it reproduces the saturation load used to generate the [graphs at the bottom of this guide](#benchmarking-results) (concurrent load with `concurrency_level=2048` and `num_requests=8192`) and is shaped to highlight the strengths of wide expert parallelism by fully saturating the topology.

Benchmark results are copied to the `workspace` directory that is specified by _you_ (or that is automatically generated when omitted from the cli) on the machine running the CLI. The workspace location is optional — by default the CLI auto-generates a timestamped workspace and prints its full path in the logs during the run. If you'd rather choose where results land, pass `--workspace <YOUR_DIR_HERE>` as a top-level argument of `llmdbenchmark` (before the `run` subcommand):

```bash
llmdbenchmark \
    --spec           guides/wide-ep-lws \
    run \
    --endpoint-url   "${ENDPOINT_URL}" \
    --gateway-class  "${GATEWAY_CLASS}" \
    --model          "deepseek-ai/DeepSeek-R1-0528" \
    --namespace      "${NAMESPACE}" \
    --harness        inference-perf \
    --workload       guide_wide-ep-lws_1.yaml \
    --analyze
```

> [!NOTE]
> Depending on your `cluster` you may need to extend the default `timeout` values to longer duration, as `bind`, `access` and `wait-timeout` times of `pvcs` and `pods` can be arbitrarily slower on other systems, please utilize `llmdbenchmark run --help` to view the knobs needed to increase those values.

## Cleanup

To remove the deployed components:

```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/<gke|coreweave>
```

## Benchmarking Results

### CKS (4x H200, 32 GPUs, InfiniBand)

Benchmark: `2048_concurrent_2k_isl_2k_osl` (2048 concurrent requests, 2K input / 2K output tokens)

| Metric | Hybrid-LB | DP Supervisor | Change |
|---|---|---|---|
| Output tokens/s | 22,125 | 27,853 | +26% |
| Input tokens/s | 22,586 | 29,329 | +30% |
| Total tokens/s | 44,711 | 57,183 | +28% |
| Requests/s | 11.03 | 14.33 | +30% |

~1,741 output tokens/s per decode GPU (16 decode GPUs), compared to ~1,383 with hybrid-lb.

### GKE (4x H200, 32 GPUs, RoCE)

Benchmark: `2048_concurrent_2k_isl_2k_osl` (2048 concurrent requests, 2K input / 2K output tokens)

| Metric | DP Supervisor |
|---|---|
| Output tokens/s | 23,246 |
| Input tokens/s | 25,106 |
| Total tokens/s | 48,352 |
| Requests/s | 12.27 |

~1,453 output tokens/s per decode GPU (16 decode GPUs). ~17% lower than CKS due to RoCE vs InfiniBand latency.
