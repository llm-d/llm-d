# P/D Disaggregation

[![Nightly - PD Disaggregation E2E (CKS)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-pd-disaggregation-cks.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-pd-disaggregation-cks.yaml) [![Nightly - PD Disaggregation E2E (GKE)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-pd-disaggregation-gke.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-pd-disaggregation-gke.yaml)

## Overview

This guide deploys `openai/gpt-oss-120b` with prefill-decode disaggregation, improving throughput per GPU and quality of service. Since disaggregation is natively built into EPP, we can compose features like prefix- and load-aware routing with disaggregated serving. In this example, we will demonstrate a deployment with:

* 8 TP=1 Prefill Instances
* 2 TP=4 Decode Instances

### P/D Best Practices

P/D disaggregation provides more flexibility in navigating the trade-off between throughput and interactivity([ref](https://arxiv.org/html/2506.05508v1)).
In particular, due to the elimination of prefill interference to the decode phase, P/D disaggregation can achieve lower inter token latency (ITL), thus
improving interactivity. For a given ITL goal, P/D disaggregation can benefit overall throughput by:

* Specializing P and D workers for compute-bound vs latency-bound workloads
* Reducing the number of copies of the model (increasing KV cache RAM) with wide parallelism

However, P/D disaggregation is not a target for all workloads. We suggest exploring P/D disaggregation for workloads with:

* Medium-large models (e.g. gpt-oss-120b)
* Longer input sequence lengths (e.g 10k ISL | 1k OSL, not 200 ISL | 200 OSL)
* Sparse MoE architectures with opportunities for wide-ep

As a result, as you tune your P/D deployments, we suggest focusing on the following parameters:

* **Heterogeneous Parallelism**: deploy P workers with less parallelism and more replicas and D workers with more parallelism and fewer replicas
* **xPyD Ratios**: tuning the ratio of P workers to D workers to ensure balance for your ISL|OSL ratio

### Supported Hardware Backends

This guide includes configuration for the following accelerators:

| Backend             | Directory                  | Notes                                                    |
| ------------------- | -------------------------- | -------------------------------------------------------- |
| NVIDIA GPU (vLLM)   | `modelserver/gpu/vllm/`    | vLLM, tested nightly                                     |
| NVIDIA GPU (SGLang) | `modelserver/gpu/sglang/`  | SGLang, validated each release                           |
| Google TPU          | `modelserver/tpu/vllm/`    | GKE TPU, validated each release                          |
| AMD GPU             | `modelserver/amd/vllm/`    | AMD GPU, community contributed                           |
| Intel XPU           | `modelserver/xpu/vllm/`    | Intel Data Center GPU Max 1550+, community contributed   |
| Intel Gaudi (HPU)   | `modelserver/hpu/vllm/`    | Gaudi 1/2/3 with DRA support, community contributed      |

> [!NOTE]
> Some hardware variants use reduced configurations (fewer replicas, smaller models) to enable CI testing for compatibility and regression checks. These configurations are maintained by their respective hardware vendors and are not guaranteed as production-ready examples. Users deploying on non-default hardware should review and adjust the configurations for their environment.

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
    export GUIDE_NAME="pd-disaggregation"
    export NAMESPACE="llm-d-pd-disaggregation"
    export MODEL_NAME="openai/gpt-oss-120b"
  ```
- Install the Gateway API Inference Extension CRDs:

  ```bash
    kubectl apply -k "https://github.com/kubernetes-sigs/gateway-api-inference-extension/config/crd?ref=${GAIE_VERSION}"
  ```
- Create a target namespace for the installation
  ```bash
      kubectl create namespace ${NAMESPACE}
  ```

## Installation Instructions

### 1. Deploy the Inference Scheduler

#### Standalone Mode

This deploys the inference scheduler with an Envoy sidecar, it doesn't set up a Kubernetes Gateway.

```bash
helm install ${GUIDE_NAME} \
    oci://registry.k8s.io/gateway-api-inference-extension/charts/standalone \
    -f guides/recipes/scheduler/base.values.yaml \
    -f guides/${GUIDE_NAME}/scheduler/${GUIDE_NAME}.values.yaml \
    -n ${NAMESPACE} --version ${GAIE_VERSION}
```

<details>
<summary><h4>Gateway Mode</h4></summary>

To employ a Kubernetes Gateway managed proxy instead of the standalone one, then instead of applying the standalone helm chart above, do the following:

1. *Deploy a Kubernetes Gateway*. Follow [the gateway guides](../prereq/gateways) for step by step deployment for a Gateway named `llm-d-inference-gateway`. You only need to create one Gateway for your cluster, all guides can share one Gateway each with a separate HTTPRoute. 
2. *Deploy the Inference Scheduler and HTTPRoute*. The following deploys the inference scheduler with an HttpRoute that connects it to the Gateway created in the previous step (set `provider.name` to the gateway provider you deployed):

```bash
export PROVIDER_NAME=gke # other na, agentgateway or istio
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

Apply the Kustomize overlays for your specific backend (defaulting to NVIDIA GPU / vLLM):

> [!NOTE]
> The Kuberentes ecosystem has not yet standardized on how to expose
> NICs to pods. We provide some pre-configured setups for certain
> Kuberentes providers. You may need to adapt the guides for the
> specifics of your infrastructure provider.

```bash
export INFRA_PROVIDER=base # coreweave, gke

kubectl apply -n ${NAMESPACE} -k guides/${GUIDE_NAME}/modelserver/gpu/vllm/${INFRA_PROVIDER}
```

### 3. Enable Monitoring (optional)

> [!NOTE]
> GKE provides [automatic application monitoring](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/configure-automatic-application-monitoring) out of the box. The llm-d [Monitoring stack](../../docs/monitoring/README.md) is not required for GKE, but it is available if you prefer to use it.

- Install the [Monitoring stack](../../docs/monitoring/README.md).
- Deploy the monitoring resources for this guide.

```bash
kubectl apply -n ${NAMESPACE} -k guides/recipes/modelserver/components/monitoring-pd
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
        "model": "Qwen/Qwen3-32B",
        "prompt": "How are you today?"
    }' | jq
```

## Benchmarking

The benchmark launches a pod (`llmdbench-harness-launcher`) that, in this case, uses `inference-perf` with a synthetic workload named `shared_prefix_synthetic`. This workload runs several stages with different rates. The results will be saved to a local folder by using the `-o` flag of `run_only.sh`. Each experiment is saved under the specified output folder, e.g., `./results/<experiment ID>/inference-perf_<experiment ID>_shared_prefix_synthetic_optimized-baseline_<model name>` folder

For more details, refer to the [benchmark instructions doc](../../helpers/benchmark.md).

### 1. Prepare the Benchmarking Suite

- Download the benchmark script:

  ```bash
  curl -L -O https://raw.githubusercontent.com/llm-d/llm-d-benchmark/main/existing_stack/run_only.sh
  chmod u+x run_only.sh
  ```

- [Create HuggingFace token](../../helpers/hf-token.md)

### 2. Download the Workload Template

```bash
curl -LJO "https://raw.githubusercontent.com/llm-d/llm-d/main/guides/pd-disaggregation/benchmark-templates/20_1_isl_osl.yaml"
```

### 3. Execute Benchmark

```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp  -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```

<details>
<summary> <b>Click here for Gateway Mode</b> </summary>

```bash
export IP=$(kubectl get gateway llm-d-inference-gateway  -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')
```
</details>


```bash
export NAMESPACE=default
envsubst < 20_1_isl_osl.yaml > config.yaml
./run_only.sh -c config.yaml -o ./results
```


## Cleanup

To remove the deployed components:

```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
kubectl delete -n ${NAMESPACE} -k guides/optimized-baseline/modelserver/gpu/vllm/${INFRA_PROVIDER}
```

## Benchmarking Report

The benchmark is running on 16 H200 GPUs (with Infinband on CKS).

### TODO: re-run

## Comparing llm-d P/D disaggregation to a k8s service

The following scripts run the same benchmark against a standard deployment and service running `openai/gpt-oss-120b`.

<details>
<summary><h4>Run Baseline (Aggregated)</h4></summary>

- Deploy (16 replicas of TP=1, with a standard k8s service)
```bash
kubectl apply -n ${NAMESPACE} -f guides/pd-disaggregation/baseline/manifest.yaml
```

- Benchmark (using the same as above)

```bash
export STACK_NAME=baseline
export IP=$(kubectl get service baseline -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
envsubst < 20_1_isl_osl.yaml > config-baseline.yaml
./run_only.sh -c config-baseline.yaml -o ./results-baseline
```

</details>

The following data captures the performance of the last stage conducted at a fixed request rate of **XXX**. We also compare the result with k8s service.

- **Throughput**: Requests/sec **XXX**; Total tokens/sec **XXX%**
- **Latency**: TTFT (mean) **XXX**; E2E request latency (mean) **XXX%**
- **Per-token speed**: Inter-token latency (mean) **XXX%**

| Metric                   | k8s (Mean) | llm-d (Mean) | Δ (llm-d - k8s) | Δ% vs k8s |
| :----------------------- | :--------- | :----------- | :-------------- | :-------- |
| Input tokens/sec         | XXX        | XXX          | XXX             | XXX       |
| Output tokens/sec        | XXX        | XXX          | XXX             | XXX       |
| Total tokens/sec         | XXX        | XXX          | XXX             | XXX       |
| Request latency (s)      | XXX        | XXX          | XXX             | XXX       |
| TTFT (s)                 | XXX        | XXX          | XXX             | XXX       |
| Inter-token latency (ms) | XXX        | XXX          | XXX             | XXX       |
