# Optimized Baseline

[![Nightly - optimized baseline E2E (OpenShift)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-optimized-baseline-ocp.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-optimized-baseline-ocp.yaml) [![Nightly - optimized baseline E2E (CKS)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-optimized-baseline-cks.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-optimized-baseline-cks.yaml) [![Nightly - optimized baseline E2E (GKE)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-optimized-baseline-gke.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-optimized-baseline-gke.yaml)

## Overview

This guide deploys the recommended out of the box [configuration](https://github.com/llm-d/llm-d-inference-scheduler/blob/main/docs/architecture.md) for most vLLM and SGLang deployments, reducing tail latency and increasing throughput through load-aware and prefix-cache aware balancing.

The optimized-baseline defaults to two main routing criteria:

- **Prefix-cache aware** using the [prefix cache scorer](https://github.com/llm-d/llm-d-inference-scheduler/tree/main/pkg/epp/framework/plugins/scheduling/scorer/prefix), which scores candidate endpoints by estimating prompt prefix cache reuse on each model server.

- **Load-aware** using both the [kv-cache utilization](https://github.com/llm-d/llm-d-inference-scheduler/tree/main/pkg/epp/framework/plugins/scheduling/scorer/kvcacheutilization) and the [queue size](https://github.com/llm-d/llm-d-inference-scheduler/tree/main/pkg/epp/framework/plugins/scheduling/scorer/queuedepth) scorers.

## Default Configuration

| Parameter          | Value                                                   |
| ------------------ | ------------------------------------------------------- |
| Model              | [Qwen/Qwen3-32B](https://huggingface.co/Qwen/Qwen3-32B) |
| Replicas           | 8                                                       |
| Tensor Parallelism | 2                                                       |
| GPUs per replica   | 2                                                       |
| Total GPUs         | 16                                                      |

### Supported Hardware Backends

This guide includes configurations for the following accelerators:

| Backend             | Directory                  | Notes                                      |
| ------------------- | -------------------------- | ------------------------------------------ |
| NVIDIA GPU          | `modelserver/gpu/vllm/`    | Default configuration                      |
| NVIDIA GPU (SGLang) | `modelserver/gpu/sglang/`  | SGLang inference server                    |
| AMD GPU             | `modelserver/amd/vllm/`    | AMD GPU                                    |
| Intel XPU           | `modelserver/xpu/vllm/`    | Intel Data Center GPU Max 1550+            |
| Intel Gaudi (HPU)   | `modelserver/hpu/vllm/`    | Gaudi 1/2/3 with DRA support               |
| Google TPU v6e      | `modelserver/tpu-v6/vllm/` | GKE TPU                                    |
| Google TPU v7       | `modelserver/tpu-v7/vllm/` | GKE TPU                                    |
| CPU                 | `modelserver/cpu/vllm/`    | Intel/AMD, 64 cores + 64GB RAM per replica |

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
    export GUIDE_NAME="optimized-baseline"
    export NAMESPACE=llm-d-optimized-baseline
    export MODEL_NAME="Qwen/Qwen3-32B"
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

### 1. Deploy the llm-d Router

#### Standalone Mode

This deploys the llm-d Router in [Standalone Mode](placeholder-link):

```bash
helm install ${GUIDE_NAME} \
    oci://registry.k8s.io/gateway-api-inference-extension/charts/standalone \
    -f guides/recipes/scheduler/base.values.yaml \
    -f guides/${GUIDE_NAME}/scheduler/${GUIDE_NAME}.values.yaml \
    -n ${NAMESPACE} --version v1.4.0
```

<details>
<summary><h4>Gateway Mode</h4></summary>

To use a Kubernetes Gateway managed proxy rather than the standalone version, follow these steps instead of applying the previous Helm chart:

1. _Deploy a Kubernetes Gateway_ named by following one of [the gateway guides](../prereq/gateways).
2. _Deploy the llm-d router and an HTTPRoute_ that connects it to the Gateway as follows:

```bash
export PROVIDER_NAME=gke # options: none, gke, agentgateway, istio
helm install ${GUIDE_NAME} \
    oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool  \
    -f guides/recipes/scheduler/base.values.yaml \
    -f guides/${GUIDE_NAME}/scheduler/${GUIDE_NAME}.values.yaml \
    --set provider.name=${PROVIDER_NAME} \
    --set experimentalHttpRoute.enabled=true \
    --set experimentalHttpRoute.inferenceGatewayName=llm-d-inference-gateway \
    -n ${NAMESPACE} --version v1.4.0
```

</details>

### 2. Deploy the Model Server

Apply the Kustomize overlays for your specific backend (defaulting to NVIDIA GPU / vLLM):

```bash
kubectl apply -n ${NAMESPACE} -k guides/${GUIDE_NAME}/modelserver/gpu/vllm/
```

<details>
<summary><h4>Other Accelerators</h4></summary>

```bash
# AMD GPU
kubectl apply -n ${NAMESPACE} -k guides/${GUIDE_NAME}/modelserver/amd/vllm/

# Intel XPU
kubectl apply -n ${NAMESPACE} -k guides/${GUIDE_NAME}/modelserver/xpu/vllm/

# Intel Gaudi (HPU)
kubectl apply -n ${NAMESPACE} -k guides/${GUIDE_NAME}/modelserver/hpu/vllm/

# Google TPU v6e
kubectl apply -n ${NAMESPACE} -k guides/${GUIDE_NAME}/modelserver/tpu-v6/vllm/

# Google TPU v7
kubectl apply -n ${NAMESPACE} -k guides/${GUIDE_NAME}/modelserver/tpu-v7/vllm/

# CPU
kubectl apply -n ${NAMESPACE} -k guides/${GUIDE_NAME}/modelserver/cpu/vllm/
```

</details>

<details>
<summary><h4>If you run into NCCL errors on GKE</h4></summary>

Try applying the patch:

```bash
kubectl apply -n ${NAMESPACE} -k guides/${GUIDE_NAME}/modelserver/gpu/gke-patch/vllm/
```

See [gke-patch/README.md](./modelserver/gpu/gke-patch/README.md) for more details.

</details>

### 3. Enable monitoring (optional)

> [!NOTE]
> GKE provides [automatic application monitoring](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/configure-automatic-application-monitoring) out of the box. The llm-d [Monitoring stack](../../docs/monitoring/README.md) is not required for GKE, but it is available if you prefer to use it.

- Install the [Monitoring stack](../../docs/monitoring/README.md).
- Deploy the monitoring resources for this guide.

```bash
kubectl apply -n ${NAMESPACE} -k guides/recipes/modelserver/components/monitoring
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

The benchmark launches a pod (`llmdbench-harness-launcher`) that, in this case, uses `inference-perf` with a shared prefix synthetic workload named `shared_prefix_synthetic`. This workload runs several stages with different rates. The results will be saved to a local folder by using the `-o` flag of `run_only.sh`. Each experiment is saved under the specified output folder, e.g., `./results/<experiment ID>/inference-perf_<experiment ID>_shared_prefix_synthetic_optimized-baseline_<model name>` folder

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
curl -LJO "https://raw.githubusercontent.com/llm-d/llm-d/main/guides/${GUIDE_NAME}/benchmark-templates/shared_prefix.yaml"
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
envsubst < shared_prefix.yaml > config.yaml
./run_only.sh -c config.yaml -o ./results
```

## Cleanup

To remove the deployed components:

```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
kubectl delete  -n ${NAMESPACE} -k guides/${GUIDE_NAME}/modelserver/gpu/vllm/
```

## Benchmarking Report

The benchmark is running on 16 H100 GPUs, distributed across 8 model servers (2 H100s per server with TP=2).

There is a report for each stage.

<details>
<summary><b><i>Click</i></b> here to view the report for `rate=60` from the above example</summary>

```yaml
metrics:
  latency:
    inter_token_latency:
      max: 0.3976375609636307
      mean: 0.06765722222528071
      min: 1.3881013728678226e-05
      p0p1: 1.722399512073025e-05
      p1: 0.00027551683422643626
      p5: 0.02622559448063839
      p10: 0.033432915166486055
      p25: 0.04734217074292246
      p50: 0.07592084849602543
      p75: 0.08339276927290484
      p90: 0.0940622523019556
      p95: 0.09673563879623544
      p99: 0.13096482709748672
      p99p9: 0.18361429275909982
      units: s/token
    normalized_time_per_output_token:
      max: 24.031401686001725
      mean: 0.15119099450472326
      min: 0.029169302775326988
      p0p1: 0.030635711364870543
      p1: 0.03316916608329783
      p5: 0.03686109928604165
      p10: 0.0422473103951594
      p25: 0.06722495797558614
      p50: 0.07227312453111687
      p75: 0.0776502936300094
      p90: 0.08589849215923934
      p95: 0.15161141803650466
      p99: 2.2160512474802
      p99p9: 3.599132445602329
      units: s/token
    request_latency:
      max: 85.97330250998493
      mean: 67.864936218041
      min: 29.08179486700101
      p0p1: 30.597063626140066
      p1: 32.82888973700406
      p5: 36.53580686951754
      p10: 41.68587793367915
      p25: 66.56756829548976
      p50: 71.62742416901165
      p75: 75.53078864999407
      p90: 82.8551616292796
      p95: 85.17766979286971
      p99: 85.8529812369059
      p99p9: 85.96677305092867
      units: s
    time_per_output_token:
      max: 0.08567342651402578
      mean: 0.06765722222528071
      min: 0.028917132598988246
      p0p1: 0.030438513501739303
      p1: 0.03267320581834996
      p5: 0.03637065519659664
      p10: 0.04149165656909463
      p25: 0.06637948430397955
      p50: 0.07139790143899155
      p75: 0.07530937768449075
      p90: 0.08259890788880875
      p95: 0.08494466238816095
      p99: 0.0856393391511339
      p99p9: 0.08567179985522212
      units: s/token
    time_to_first_token:
      max: 0.2749739610007964
      mean: 0.1203408618576747
      min: 0.04670933203306049
      p0p1: 0.05085431289958069
      p1: 0.0542934795509791
      p5: 0.06336988278490026
      p10: 0.07046441090060399
      p25: 0.08575929325888865
      p50: 0.1132554289943073
      p75: 0.1517725815065205
      p90: 0.18095784459728748
      p95: 0.19695026772387791
      p99: 0.22566659807867837
      p99p9: 0.25035182150500235
      units: s
  requests:
    failures: 0
    input_length:
      max: 7668.0
      mean: 7576.364
      min: 7487.0
      p0p1: 7490.992
      p1: 7512.0
      p5: 7531.0
      p10: 7541.9
      p25: 7556.0
      p50: 7577.0
      p75: 7594.0
      p90: 7611.0
      p95: 7624.0
      p99: 7646.0
      p99p9: 7665.006
      units: count
    output_length:
      max: 1999.0
      mean: 941.86
      min: 3.0
      p0p1: 20.0
      p1: 32.99
      p5: 500.2
      p10: 949.9
      p25: 992.0
      p50: 997.0
      p75: 1000.0
      p90: 1000.0
      p95: 1000.0
      p99: 1000.0
      p99p9: 1500.495
      units: count
    total: 1500
  throughput:
    output_tokens_per_sec: 13574.368209884744
    requests_per_sec: 14.41229929064271
    total_tokens_per_sec: 122767.19371273571
  time:
    duration: 24.984177332022227
scenario:
  load:
    args:
      api:
        headers: null
        streaming: true
        type: completion
      circuit_breakers: null
      data:
        input_distribution: null
        output_distribution: null
        path: null
        shared_prefix:
          enable_multi_turn_chat: false
          num_groups: 150
          num_prompts_per_group: 5
          output_len: 1000
          question_len: 1200
          system_prompt_len: 6000
        trace: null
        type: shared_prefix
      load:
        circuit_breakers: []
        interval: 1.0
        num_workers: 224
        request_timeout: null
        stages:
          - concurrency_level: null
            duration: 50
            num_requests: null
            rate: 15.0
          - concurrency_level: null
            duration: 20
            num_requests: null
            rate: 3.0
          - concurrency_level: null
            duration: 20
            num_requests: null
            rate: 10.0
          - concurrency_level: null
            duration: 20
            num_requests: null
            rate: 15.0
          - concurrency_level: null
            duration: 38
            num_requests: null
            rate: 20.0
          - concurrency_level: null
            duration: 34
            num_requests: null
            rate: 22.0
          - concurrency_level: null
            duration: 30
            num_requests: null
            rate: 25.0
          - concurrency_level: null
            duration: 25
            num_requests: null
            rate: 30.0
          - concurrency_level: null
            duration: 21
            num_requests: null
            rate: 35.0
          - concurrency_level: null
            duration: 38
            num_requests: null
            rate: 40.0
          - concurrency_level: null
            duration: 36
            num_requests: null
            rate: 43.0
          - concurrency_level: null
            duration: 33
            num_requests: null
            rate: 46.0
          - concurrency_level: null
            duration: 30
            num_requests: null
            rate: 49.0
          - concurrency_level: null
            duration: 29
            num_requests: null
            rate: 52.0
          - concurrency_level: null
            duration: 27
            num_requests: null
            rate: 55.0
          - concurrency_level: null
            duration: 26
            num_requests: null
            rate: 57.0
          - concurrency_level: null
            duration: 25
            num_requests: null
            rate: 60.0
        sweep: null
        trace: null
        type: poisson
        worker_max_concurrency: 100
        worker_max_tcp_connections: 2500
      metrics: null
      report:
        prometheus:
          per_stage: false
          summary: true
        request_lifecycle:
          per_request: true
          per_stage: true
          summary: true
      server:
        api_key: null
        base_url: http://infra-optimized-baseline-inference-gateway-istio.dpikus-intel-inf.svc.cluster.local:80
        ignore_eos: true
        model_name: Qwen/Qwen3-32B
        type: vllm
      storage:
        google_cloud_storage: null
        local_storage:
          path: /requests/inference-perf_1769435052_Shared_prefix_inf-scheduling-guide-Qwen3-32B
          report_file_prefix: null
        simple_storage_service: null
      tokenizer:
        pretrained_model_name_or_path: Qwen/Qwen3-32B
        token: null
        trust_remote_code: null
    metadata:
      stage: 2
    name: inference-perf
  model:
    name: unknown
version: "0.1"
```

</details>

### Comparing llm-d routing to a simple kubernetes service

The following graphs illustrate the relationship between latency, throughput, and QPS, as generated by the `inference-perf --analyze`. For benchmarking, we compared our results against a standard Kubernetes (k8s) service endpoint that routes traffic directly to vLLM pods.

<img src="./benchmark-results/latency_vs_qps.png" width="900" alt="Throughput vs QPS">
<img src="./benchmark-results/throughput_vs_qps.png" width="450" alt="Throughput vs Latency">

The following data captures the performance of the last stage conducted at a fixed request rate of **60**. We also compare the result with k8s service.

- **Throughput**: Requests/sec **+151.5%**; Total tokens/sec **+151.7%**
- **Latency**: TTFT (mean) **-99.66%**; E2E request latency (mean) **-35.6%**
- **Per-token speed**: Inter-token latency (mean) **-3.9%**

| Metric                   | k8s (Mean) | llm-d (Mean) | Δ (llm-d - k8s) | Δ% vs k8s |
| :----------------------- | :--------- | :----------- | :-------------- | :-------- |
| Requests/sec             | 5.7306     | 14.4123      | +8.6817         | +151.5%   |
| Input tokens/sec         | 43,417.86  | 109,192.83   | +65,774.97      | +151.5%   |
| Output tokens/sec        | 5,362.16   | 13,574.37    | +8,212.21       | +153.2%   |
| Total tokens/sec         | 48,780.02  | 122,767.19   | +73,987.17      | +151.7%   |
| Request latency (s)      | 105.4133   | 67.8649      | -37.5484        | -35.6%    |
| TTFT (s)                 | 34.9145    | 0.1203       | -34.7942        | -99.66%   |
| Inter-token latency (ms) | 70.42      | 67.66        | -2.76           | -3.9%     |
