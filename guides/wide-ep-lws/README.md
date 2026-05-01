# Wide Expert Parallelism

[![Nightly - Wide EP LWS E2E (OpenShift)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-wide-ep-lws-ocp.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-wide-ep-lws-ocp.yaml) [![Nightly - Wide EP LWS E2E (CKS)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-wide-ep-lws-cks.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-wide-ep-lws-cks.yaml) [![Nightly - Wide EP LWS E2E (GKE)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-wide-ep-lws-gke.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-wide-ep-lws-gke.yaml)

## Overview

This guide demonstrates how to deploy DeepSeek-R1-0528 using vLLM's P/D disaggregation support with NIXL in a wide expert parallel pattern with LeaderWorkerSets. This guide has been validated on:

* a 32xH200 cluster with InfiniBand networking
* a 32xH200 cluster on GKE with RoCE networking
* a 32xB200 cluster on GKE with RoCE networking

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
| NVIDIA GPU (GKE)    | `modelserver/gke/`         | GKE deployment (H200)                      |
| NVIDIA GPU (GKE A4) | `modelserver/gke-a4/`      | GKE deployment (B200)                      |
| NVIDIA GPU (CoreWeave)| `modelserver/coreweave/`   | CoreWeave deployment                     |

> [!NOTE]
> The pods leveraging inter-node EP must be deployed in a cluster environment with full mesh
> network connectivity. The DeepEP backend used in WideEP requires All-to-All RDMA
> connectivity. Every NIC on a host must be able to communicate with every NIC on all other
> hosts. Networks restricted to communicating only between matching NIC IDs (rail-only
> connectivity) will fail.

## Prerequisites

- Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md) to use this guide.
- Checkout llm-d repo:

  ```bash
  export branch="main" # branch, tag, or commit hash
  git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout ${branch}
  ```
- Set the following environment variables:
  ```bash
  export GAIE_VERSION=v1.4.0
  export GUIDE_NAME="wide-ep-lws"
  export NAMESPACE=llm-d-wide-ep
  export MODEL=deepseek-ai/DeepSeek-R1-0528
  ```
- Install the Gateway API Inference Extension CRDs:

  ```bash
  kubectl apply -k "https://github.com/kubernetes-sigs/gateway-api-inference-extension/config/crd?ref=${GAIE_VERSION}"
  ```
- You have deployed the [LeaderWorkerSet controller](https://lws.sigs.k8s.io/docs/installation/)
- Create a target namespace for the installation:
  ```bash
  kubectl create namespace ${NAMESPACE}
  ```
- [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN` matching a valid HuggingFace token](../../helpers/hf-token.md) to pull models.

## Installation Instructions

### 1. Deploy the llm-d Router

#### Standalone Mode

This deploys the llm-d Router with an Envoy sidecar, it doesn't set up a Kubernetes Gateway.

```bash
helm install ${GUIDE_NAME} \
    oci://registry.k8s.io/gateway-api-inference-extension/charts/standalone \
    -f guides/recipes/scheduler/base.values.yaml \
    -f guides/${GUIDE_NAME}/scheduler/${GUIDE_NAME}.values.yaml \
    -n ${NAMESPACE} --version ${GAIE_VERSION}
```

<details>
<summary><h4>Gateway Mode</h4></summary>

To use a Kubernetes Gateway managed proxy rather than the standalone version, follow these steps instead of applying the previous Helm chart:

1. *Deploy a Kubernetes Gateway* by following one of [the gateway guides](../prereq/gateways).
2. *Deploy the llm-d Router and an HTTPRoute* that connects it to the Gateway as follows:

```bash
export PROVIDER_NAME=gke # options: none, gke, agentgateway, istio
helm install ${GUIDE_NAME} \
    oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool  \
    -f guides/recipes/scheduler/base.values.yaml \
    -f guides/${GUIDE_NAME}/scheduler/${GUIDE_NAME}.values.yaml \
    --set provider.name=${PROVIDER_NAME} \
    --set experimentalHttpRoute.enabled=true \
    --set experimentalHttpRoute.inferenceGatewayName=llm-d-inference-gateway \
    -n ${NAMESPACE} --version ${GAIE_VERSION}
```

</details>


### 2. Deploy the Model Server

Apply the Kustomize overlays for your specific backend:

```bash
export INFRA_PROVIDER=gke # options: gke (H200), gke-a4 (B200), coreweave
kubectl apply -n ${NAMESPACE} -k guides/${GUIDE_NAME}/modelserver/gpu/vllm/${INFRA_PROVIDER}
```


### 3. (Optional) Enable monitoring

> [!NOTE]
> GKE provides [automatic application monitoring](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/configure-automatic-application-monitoring) out of the box. The llm-d [Monitoring stack](../../docs/monitoring/README.md) is not required for GKE, but it is available if you prefer to use it.

- Install the [Monitoring stack](../../docs/monitoring/README.md).
- Deploy the monitoring resources for this guide.

```bash
kubectl apply -n ${NAMESPACE} -k guides/recipes/modelserver/components/monitoring-pd
```

### 4. (Optional) Topology Aware Scheduling (TAS)

For information on how to use topology aware scheduling using Kueue, see [LWS + TAS user guide](https://lws.sigs.k8s.io/docs/examples/tas/). To deploy the guide with TAS enabled, use the following command:

```bash
# H200 on GKE
kubectl apply -n ${NAMESPACE} -k guides/${GUIDE_NAME}/modelserver/gpu/vllm/topology-aware/gke
# B200 on GKE
kubectl apply -n ${NAMESPACE} -k guides/${GUIDE_NAME}/modelserver/gpu/vllm/topology-aware/gke-a4
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

## Benchmarking

The benchmark launches a pod (`llmdbench-harness-launcher`) that uses `inference-perf` with a template workload. The results will be saved to a local folder by using the `-o` flag of `run_only.sh`.

### 1. Prepare the Benchmarking Suite

- Download the benchmark script:

  ```bash
  curl -L -O https://raw.githubusercontent.com/llm-d/llm-d-benchmark/main/existing_stack/run_only.sh
  chmod u+x run_only.sh
  ```

### 2. Download the Workload Template

The template is located at `guides/wide-ep-lws/benchmark-templates/guide.yaml`. You can also download it if needed:

```bash
curl -LJO "https://raw.githubusercontent.com/llm-d/llm-d/main/guides/${GUIDE_NAME}/benchmark-templates/guide.yaml"
```

### 3. Execute Benchmark

```bash
envsubst < guide.yaml > config.yaml
./run_only.sh -c config.yaml -o ./results
```

## Cleanup

To remove the deployed components:

```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
kubectl delete -n ${NAMESPACE} -k guides/${GUIDE_NAME}/modelserver/<gke|coreweave>
```

## Benchmarking Report

We deployed the default wide-ep-lws user guide on GKE (`modelserver/gke-a4`).

* Provider: GKE
* Prefill: 1 instance with EP=16
* Decode: 1 instance with EP=16
* 4 `a4-highgpu-8g` VMs, 32 GPUs

We use the [inference-perf](https://github.com/kubernetes-sigs/inference-perf/tree/main) benchmark tool to generate random datasets with 1K input length and 1K output length. This benchmark targets batch use case and we aim to find the maximum throughput by sweeping from lower to higher request rates up to 250 QPS.

### Results

<img src="./benchmark-results/throughput_vs_qps.png" width="900" alt="Throughput vs QPS">
<img src="./benchmark-results/throughput_vs_latency.png" width="300" alt="Throughput vs Latency">

At request rate 250, we achieved the max throughput:

```json
"throughput": {
    "input_tokens_per_sec": 51218.79261732335,
    "output_tokens_per_sec": 49783.58426326592,
    "total_tokens_per_sec": 101002.37688058926,
    "requests_per_sec": 50.02468992880545
}
```

This equals to 3200 input tokens/s/GPU and 3100 output tokens/s/GPU.

