# P/D Disaggregation

[![Nightly - PD Disaggregation E2E (CKS)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-pd-disaggregation-cks.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-pd-disaggregation-cks.yaml)

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
        "model": "openai/gpt-oss-120b",
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

There is a report for each stage.

<details>
<summary><b><i>Click</i></b> here to view the report for `rate=45` from the above example</summary>

```yaml
results:
  request_performance:
    aggregate:
      latency:
        inter_token_latency:
          max: 0.4451564671471715
          mean: 0.012416639243213254
          min: 3.7653371691703796e-06
          p0p1: 3.986060619354248e-06
          p1: 4.145316779613495e-06
          p10: 4.607252776622772e-06
          p25: 5.087815225124359e-06
          p5: 4.407018423080444e-06
          p50: 6.339512765407562e-06
          p75: 1.2618489563465118e-05
          p90: 0.05138330096378929
          p95: 0.09810644732788208
          p99: 0.18161227625794713
          p99p9: 0.27771183433011765
          units: s/token
        normalized_time_per_output_token:
          max: 0.03838522714795545
          mean: 0.020235644857381253
          min: 0.0005658950262332029
          p0p1: 0.0007098747353253647
          p1: 0.012089349899865027
          p10: 0.01583037307424489
          p25: 0.017349339720976215
          p5: 0.01502842840457876
          p50: 0.019552195101299324
          p75: 0.022555936899546252
          p90: 0.026128762050623703
          p95: 0.028386090987422907
          p99: 0.03241212966789392
          p99p9: 0.037228032483879306
          units: s/token
        request_latency:
          max: 9.41744049731642
          mean: 4.984505030536093
          min: 2.383338357321918
          p0p1: 2.710443040263839
          p1: 3.341698997169733
          p10: 3.893676984217018
          p25: 4.25998754077591
          p5: 3.7291941775474697
          p50: 4.78889629477635
          p75: 5.524961187737063
          p90: 6.3686291925609115
          p95: 6.938895106548443
          p99: 7.916496822880583
          p99p9: 9.114266873544222
          units: s
        time_per_output_token:
          max: 0.015028722692281008
          mean: 0.012416639243213252
          min: 0.007651337441056966
          p0p1: 0.008365667614415288
          p1: 0.009969908600263297
          p10: 0.011210729649662972
          p25: 0.011854297185316682
          p5: 0.010919190391525625
          p50: 0.012558143949136139
          p75: 0.013048471543006599
          p90: 0.013401017013192178
          p95: 0.013635492861457169
          p99: 0.014096900728456678
          p99p9: 0.014626184518687435
          units: s/token
        time_to_first_token:
          max: 6.2327788500115275
          mean: 1.8665386269100148
          min: 0.2328754412010312
          p0p1: 0.31802471200656146
          p1: 0.43291855927556755
          p10: 0.7812989133410155
          p25: 1.1359100888948888
          p5: 0.6199993264395743
          p50: 1.673543413169682
          p75: 2.400195718742907
          p90: 3.2472441403195265
          p95: 3.767739224107936
          p99: 4.7533523408230405
          p99p9: 6.065774005791176
          units: s
      requests:
        failures: 0
        input_length:
          max: 5234.0
          mean: 5151.556481481482
          min: 5106.0
          p0p1: 5108.0
          p1: 5116.0
          p10: 5132.0
          p25: 5141.0
          p5: 5127.0
          p50: 5151.0
          p75: 5162.0
          p90: 5171.0
          p95: 5177.0
          p99: 5188.01
          p99p9: 5199.0
          units: count
        output_length:
          max: 5440.0
          mean: 282.91425925925927
          min: 145.0
          p0p1: 201.0
          p1: 226.0
          p10: 240.0
          p25: 243.0
          p5: 237.0
          p50: 246.0
          p75: 248.0
          p90: 249.0
          p95: 250.0
          p99: 253.0
          p99p9: 5405.803000000002
          units: count
        total: 5400
      throughput:
        output_token_rate:
          mean: 12140.475129786893
          units: tokens/s
        request_rate:
          mean: 42.912206551814364
          units: queries/s
        total_token_rate:
          mean: 233205.13092645828
          units: tokens/s
run:
  cid: 84d64299-c166-584e-b27f-d7951cca928b
  eid: 1b4db7eb-4057-5ddf-91e0-36dec72071f5
  time: {}
  uid: 5342f28e-b732-450b-81fb-fa7e2be55c89
  user: namespace=rob-dev
scenario:
  load:
    metadata:
      cfg_id: 74234e98afe7498fb5daf1f36ac2d78acc339464f950703b8c019892f982b90b
      schema_version: 0.0.1
    native:
      args: {}
    standardized:
      input_seq_len:
        distribution: gaussian
        max: 5234
        min: 5106
        value: 5151.556481481482
      output_seq_len:
        distribution: gaussian
        max: 5440
        min: 145
        value: 282.91425925925927
      parallelism: 1
      rate_qps: 45.0
      source: unknown
      stage: 2
      tool: inference-perf
      tool_version: ''
version: '0.2'
```

</details>


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
