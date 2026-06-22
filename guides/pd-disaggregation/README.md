# P/D Disaggregation

[![E2E (CKS GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-pd-disaggregation-cks-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-pd-disaggregation-cks-acc-gpu-vllm-x.yaml)
[![E2E (GKE GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-pd-disaggregation-gke-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-pd-disaggregation-gke-acc-gpu-vllm-x.yaml)
[![E2E (GKE TPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-pd-disaggregation-gke-acc-tpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-pd-disaggregation-gke-acc-tpu-vllm-x.yaml)
[![E2E (OCP GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-pd-disaggregation-ibm-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-pd-disaggregation-ibm-acc-gpu-vllm-x.yaml)

## Overview

This guide deploys `openai/gpt-oss-120b` with prefill-decode disaggregation, improving throughput per GPU and quality of service. Since disaggregation is natively built into llm-d Router, we can compose features like prefix- and load-aware routing with disaggregated serving. In this example, we will demonstrate a deployment with:

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
| Google TPU          | `modelserver/tpu/v6/vllm/` & `modelserver/tpu/v7/vllm/` | GKE TPU (v6e & v7x), see [TPU Guide](./README.tpu.md) |
| AMD GPU             | `modelserver/amd/vllm/`    | AMD GPU, community contributed                           |
| Intel XPU           | `modelserver/xpu/vllm/`    | Intel Data Center GPU Max 1550+, community contributed   |
| Intel XPU + RDMA    | `modelserver/xpu/vllm-rdma/` | Intel XPU with RDMA via UCX (`ib,rc,ze_copy`), requires RDMA DRA driver |

> [!NOTE]
> Some hardware variants use reduced configurations (fewer replicas, smaller models) to enable CI testing for compatibility and regression checks. These configurations are maintained by their respective hardware vendors and are not guaranteed as production-ready examples. Users deploying on non-default hardware should review and adjust the configurations for their environment.

## Prerequisites

* Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md) to use this guide.
* Checkout llm-d repo:

```bash
export branch="main" # branch, tag, or commit hash
git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout ${branch}
```
* Set the following environment variables:

```bash
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
source ${REPO_ROOT}/guides/env.sh
export GUIDE_NAME="pd-disaggregation"
export NAMESPACE="llm-d-pd-disaggregation"
export MODEL_NAME="openai/gpt-oss-120b"
```
* Install the Gateway API Inference Extension CRDs:

```bash
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GAIE_VERSION}/v1-manifests.yaml
```
* Create a target namespace for the installation

```bash
kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
```

* [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN` matching a valid HuggingFace token](../../helpers/hf-token.md) to pull models.
<!-- llm-d-cicd:skip start -->
  ```bash
  export HF_TOKEN=<your HuggingFace token>
  kubectl create secret generic llm-d-hf-token \
    --from-literal="HF_TOKEN=${HF_TOKEN}" \
    --namespace "${NAMESPACE}" \
    --dry-run=client -o yaml | kubectl apply -f -
  ```
<!-- llm-d-cicd:skip end -->

## Installation Instructions

### 1. Deploy the llm-d Router

#### Standalone Mode

This deploys the llm-d Router with an Envoy sidecar, it doesn't set up a Kubernetes Gateway.

```bash
helm install ${GUIDE_NAME} \
    ${ROUTER_STANDALONE_CHART} \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

<details>
<summary><h4>Gateway Mode</h4></summary>

To employ a Kubernetes Gateway managed proxy instead of the standalone one, then instead of applying the standalone helm chart above, do the following:

1. *Deploy a Kubernetes Gateway*. Follow [the gateway guides](../../docs/infrastructure/gateway) for step by step deployment for a Gateway named `llm-d-inference-gateway`. You only need to create one Gateway for your cluster, all guides can share one Gateway each with a separate HTTPRoute.
2. *Deploy the llm-d Router and an HTTPRoute*. The following deploys the llm-d Router with an HttpRoute that connects it to the Gateway created in the previous step (set `provider.name` to the gateway provider you deployed):

```bash
export PROVIDER_NAME=gke # other na, agentgateway or istio
helm install ${GUIDE_NAME} \
    ${ROUTER_GATEWAY_CHART}  \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/recipes/router/features/httproute-flags.yaml \
    -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
    --set provider.name=${PROVIDER_NAME} \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

</details>

### 2. Deploy the Model Server

Apply the Kustomize overlays for your specific backend (defaulting to NVIDIA GPU / vLLM):

> [!NOTE]
> The Kubernetes ecosystem has not yet standardized on how to expose
> NICs to pods. We provide some pre-configured setups for certain
> Kubernetes providers. You may need to adapt the guides for the
> specifics of your infrastructure provider. The provider specific
> overlays deal with the specifics of each cloud's setup.

```bash
export INFRA_PROVIDER=base # base | coreweave | gke | aws

kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/${INFRA_PROVIDER}
```

<details>
<summary><h4>Deploying with SGLang</h4></summary>

To run the disaggregated deployment with SGLang instead of vLLM, apply the SGLang overlay (available for NVIDIA GPU with `base`, `coreweave`, and `gke` infra providers):

```bash
export INFRA_PROVIDER=base # base | coreweave | gke

kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/sglang/${INFRA_PROVIDER}
```

SGLang-specific notes:

* **Engine flags**: prefill and decode pods launch with `--disaggregation-mode={prefill,decode}` and `--disaggregation-transfer-backend=nixl`. The decode pod's routing-proxy sidecar is configured with `--connector=sglang`.
* **Bootstrap server**: each prefill instance runs a bootstrap server on port `8998` (the default). To use a different port, set `SGLANG_BOOTSTRAP_PORT` on the sidecar and `--disaggregation-bootstrap-port` on the SGLang engine so the two match. P/D peers discover each other through this server rather than vLLM's peer-to-peer negotiation; the KV transfer itself still runs directly over NIXL/RDMA.
* **Operations**: scale up/down, request cancellation, fault tolerance, and rollout behavior differ from vLLM. See [Disaggregated Serving: Operations (SGLang)](../../docs/architecture/advanced/disaggregation/operations-sglang.md).

</details>

> [!NOTE]
> **Feature parity and known limitations (SGLang vs vLLM)**
>
> * Disaggregation lives in the llm-d Router (EPP) and is engine-agnostic, so SGLang P/D composes with the same prefix-cache-aware and load-aware routing as vLLM.
> * SGLang P/D is **validated each release** on NVIDIA GPU but is not yet part of the nightly E2E CI that covers the vLLM path (the badges above).
> * The SGLang P/D overlays are **NVIDIA GPU only** today; the AMD overlay (`modelserver/amd/vllm/`) provides vLLM P/D only.
> * On the NIXL transfer backend, SGLang has no explicit prefill-side free-notification (as vLLM does) and no prefill-side reclaim timeout, so a request cancelled before the decode initiates the transfer can strand KV cache on the prefill until the pod restarts. See the [SGLang operations doc](../../docs/architecture/advanced/disaggregation/operations-sglang.md).

### 3. Enable Monitoring (optional)

> [!NOTE]
> GKE provides [automatic application monitoring](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/configure-automatic-application-monitoring) out of the box. The llm-d [Monitoring stack](../../docs/operations/observability/setup.md) is not required for GKE, but it is available if you prefer to use it.

* Install the [Monitoring stack](../../docs/operations/observability/setup.md).
* Deploy the monitoring resources for this guide.

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring-pd
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
        "model": "openai/gpt-oss-120b",
        "prompt": "How are you today?"
    }' | jq
```

## Benchmarking

This guide uses [`llmdbenchmark`](https://github.com/llm-d/llm-d-benchmark) — the supported standard CLI for llm-d performance benchmarking.

In this example we will demonstrate how to run [`inference-perf`](https://github.com/kubernetes-sigs/inference-perf) with a synthetic random-data workload (typical of variable-length prompts in P/D-disaggregated serving) against the stack you just deployed above (standalone or gateway mode). When orchestrating benchmarks via `llmdbenchmark`, the CLI automatically and transparently deploys a harness pod (`llmdbench-harness-launcher`) into your namespace. This pod is central to driving the workload, collecting the results, and tearing itself down when it's finished.

> [!IMPORTANT]
> **For more in-depth explanation and features for benchmarking llm-d guides, see [`helpers/benchmark.md`](../../helpers/benchmark.md).**
>
> The Benchmarking section below contains only the **pd-disaggregation-specific commands** needed to drive the stack you just deployed — for everything else (and especially when something goes wrong), start at [`helpers/benchmark.md`](../../helpers/benchmark.md).
>
> For even more details about benchmarking, see the actual repository: [`llm-d-benchmark` on GitHub](https://github.com/llm-d/llm-d-benchmark).

> [!TIP]
> The command below runs this guide's **dedicated** benchmark profile, which is intentionally shaped to exercise the prefill-decode disaggregation pattern under realistic load — and accordingly takes longer to complete. To run a simpler workload with fewer execution cycles first (useful for validating the path, image pulls, PVC binding, etc. before committing to a real run), pick a generic sample profile such as `shared_prefix_synthetic.yaml` from the catalog in [`helpers/benchmark.md` → Available workload profiles](../../helpers/benchmark.md#available-workload-profiles) and substitute it for the `--workload` flag in the command below.

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

### 3. Run the benchmark profile for P/D Disaggregation

`guide_pd-disaggregation_1.yaml` is a **dedicated workload profile** shipped with `llm-d-benchmark` specifically for this guide — it reproduces the saturation load used to generate the [graphs at the bottom of this guide](#benchmarking-report) (constant rate=45 with 45 workers and per-worker concurrency=100) and is shaped to highlight the strengths of the prefill-decode disaggregation pattern under load.

Benchmark results are copied to the `workspace` directory that is specified by _you_ (or that is automatically generated when omitted from the cli) on the machine running the CLI. The workspace location is optional — by default the CLI auto-generates a timestamped workspace and prints its full path in the logs during the run. If you'd rather choose where results land, pass `--workspace <YOUR_DIR_HERE>` as a top-level argument of `llmdbenchmark` (before the `run` subcommand):

```bash
llmdbenchmark \
    --spec           guides/pd-disaggregation \
    run \
    --endpoint-url   "${ENDPOINT_URL}" \
    --gateway-class  "${GATEWAY_CLASS}" \
    --model          "openai/gpt-oss-120b" \
    --namespace      "${NAMESPACE}" \
    --harness        inference-perf \
    --workload       guide_pd-disaggregation_1.yaml \
    --analyze
```

A second profile `guide_pd-disaggregation_2.yaml` is also available for low-rate latency characterization (rate=1, num_workers=100) — pass it instead of `guide_pd-disaggregation_1.yaml` for that mode.

> [!NOTE]
> Depending on your `cluster` you may need to extend the default `timeout` values to longer duration, as `bind`, `access` and `wait-timeout` times of `pvcs` and `pods` can be arbitrarily slower on other systems, please utilize `llmdbenchmark run --help` to view the knobs needed to increase those values.

## Cleanup

To remove the deployed components:

```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/${INFRA_PROVIDER}
```

<details>
<summary><h4>Cleanup for SGLang</h4></summary>

If you deployed the SGLang overlay, delete that path instead of the vLLM one:

```bash
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/sglang/${INFRA_PROVIDER}
```

</details>

## Benchmarking Report

The benchmark is running on 16 H200 GPUs (with Infinband on CKS).

There is a report for each stage.

<details>
<summary><b><i>Click</i></b> here to view the report for `rate=45` from the above example</summary>

```yaml
metrics:
  latency:
    inter_token_latency:
      max: 0.3643897734582424
      mean: 0.008325434739626478
      min: 3.7653371691703796e-06
      p0p1: 3.975816071033478e-06
      p1: 4.145316779613495e-06
      p10: 4.616566002368927e-06
      p25: 5.087815225124359e-06
      p5: 4.416331648826599e-06
      p50: 6.280839443206787e-06
      p75: 1.2137927114963531e-05
      p90: 0.03592400047928101
      p95: 0.06747404355555772
      p99: 0.12114070571027777
      p99p9: 0.18705207404308383
      units: s/token
    normalized_time_per_output_token:
      max: 0.04898325727620708
      mean: 0.014364489551937707
      min: 0.0004188831798717112
      p0p1: 0.0004855348222305054
      p1: 0.008621003280209023
      p10: 0.01086499850006588
      p25: 0.011933319070146827
      p5: 0.010361602989319029
      p50: 0.013688608406590488
      p75: 0.015965917295299104
      p90: 0.018797610009301274
      p95: 0.020827560955696416
      p99: 0.02667838998102462
      p99p9: 0.04062934044765229
      units: s/token
    request_latency:
      max: 11.119199401699007
      mean: 3.5384947839587997
      min: 1.5062068477272987
      p0p1: 1.9175463474858552
      p1: 2.3823377661034466
      p10: 2.6774717193096875
      p25: 2.9338933038525283
      p5: 2.5588959713466464
      p50: 3.356982336845249
      p75: 3.916417645290494
      p90: 4.574965833220631
      p95: 5.0852895775344225
      p99: 6.531727972868838
      p99p9: 9.935308576508453
      units: s
    time_per_output_token:
      max: 0.010571206539869309
      mean: 0.008325349373725296
      min: 0.004886588230729103
      p0p1: 0.005544693316236138
      p1: 0.006968683542534709
      p10: 0.007752664919942617
      p25: 0.008032276449785117
      p5: 0.007547358138114214
      p50: 0.008331082850694657
      p75: 0.008618501575663686
      p90: 0.008902709059789777
      p95: 0.009100843822024763
      p99: 0.009630139790810646
      p99p9: 0.010342120162323167
      units: s/token
    time_to_first_token:
      max: 9.166204158216715
      mean: 1.4439210383442265
      min: 0.21261637564748526
      p0p1: 0.25461369096953423
      p1: 0.35444720844738187
      p10: 0.5667089101858437
      p25: 0.8372100500855595
      p5: 0.4620446518063545
      p50: 1.264039859175682
      p75: 1.8248309704940766
      p90: 2.4776970406062904
      p95: 2.9816138751804835
      p99: 4.4258010189700965
      p99p9: 7.718557042311907
      units: s
  requests:
    failures: 0
    input_length:
      max: 5209.0
      mean: 5151.397962962963
      min: 5104.0
      p0p1: 5110.0
      p1: 5118.0
      p10: 5132.0
      p25: 5141.0
      p5: 5126.0
      p50: 5151.0
      p75: 5162.0
      p90: 5171.0
      p95: 5177.0
      p99: 5187.0
      p99p9: 5200.601000000001
      units: count
    output_length:
      max: 5430.0
      mean: 281.0096296296296
      min: 76.0
      p0p1: 190.798
      p1: 224.0
      p10: 240.0
      p25: 243.0
      p5: 237.0
      p50: 246.0
      p75: 248.0
      p90: 249.0
      p95: 250.0
      p99: 253.0
      p99p9: 5415.601000000001
      units: count
    total: 5400
  throughput:
    output_tokens_per_sec: 12236.597879353767
    requests_per_sec: 43.54511941630466
    total_tokens_per_sec: 236554.83733748455
  time:
    duration: 119.97667319700122
scenario:
  load:
    args:
      api:
        headers: null
        streaming: true
        type: completion
      circuit_breakers: null
      data:
        input_distribution:
          max: 5000
          mean: 5000.0
          min: 5000
          std_dev: 0.0
          total_count: 5401
        output_distribution:
          max: 250
          mean: 250.0
          min: 250
          std_dev: 0.0
          total_count: 5401
        path: null
        shared_prefix: null
        trace: null
        type: random
      load:
        circuit_breakers: []
        interval: 1.0
        lora_traffic_split: null
        num_workers: 45
        request_timeout: null
        stages:
        - concurrency_level: null
          duration: 120
          num_requests: null
          rate: 45.0
        sweep: null
        trace: null
        type: constant
        worker_max_concurrency: 100
        worker_max_tcp_connections: 2500
      metrics: null
      report:
        prometheus:
          per_stage: false
          summary: true
        request_lifecycle:
          per_adapter: true
          per_adapter_stage: false
          per_request: false
          per_stage: true
          percentiles:
          - 0.1
          - 1.0
          - 5.0
          - 10.0
          - 25.0
          - 50.0
          - 75.0
          - 90.0
          - 95.0
          - 99.0
          - 99.9
          summary: true
      server:
        api_key: null
        base_url: http://10.16.2.220
        cert_path: null
        ignore_eos: true
        key_path: null
        model_name: openai/gpt-oss-120b
        type: vllm
      storage:
        google_cloud_storage: null
        local_storage:
          path: /requests/inference-perf_1777579326_random_20_1_isl_osl_pd-gpt-oss-120b
          report_file_prefix: null
        simple_storage_service: null
      tokenizer:
        pretrained_model_name_or_path: openai/gpt-oss-120b
        token: null
        trust_remote_code: null
    metadata:
      stage: 0
    name: inference-perf
  model:
    name: unknown
version: '0.1'
```

</details>

## Comparing llm-d P/D disaggregation to a k8s service

The following scripts run the same benchmark against a standard deployment and service running `openai/gpt-oss-120b`.

#### Run Baseline (Aggregated)

* Deploy (16 replicas of TP=1, with a standard k8s service)

```bash
kubectl apply -n ${NAMESPACE} -f ${REPO_ROOT}/guides/pd-disaggregation/baseline/manifest.yaml
```

* Benchmark (using the same workload profile as the main run, but pointed at the baseline service rather than the EPP):

```bash
export ENDPOINT_URL="http://$(kubectl get service baseline -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')"

llmdbenchmark \
    --spec           guides/pd-disaggregation \
    run \
    --endpoint-url   "${ENDPOINT_URL}" \
    --gateway-class  "${GATEWAY_CLASS}" \
    --model          "openai/gpt-oss-120b" \
    --namespace      "${NAMESPACE}" \
    --harness        inference-perf \
    --workload       guide_pd-disaggregation_1.yaml \
    --workspace      ./results-baseline \
    --analyze
```

(Drives the same `guide_pd-disaggregation_1.yaml` workload — rate=45 for 120s, 45 workers — against the aggregated baseline so the two result sets are directly comparable.)

For this workload (20:1 ISL:OSL, 45 QPS), llm-d disaggregation improved mean and P90 request latency by ~50%!

| Metric                   | aggregated | llm-d        | Δ% |
| :----------------------- | :--------- | :----------- | :------- |
| **E2E Latency (Mean)**   | **6.7s**   | **3.5s**     | **-47%** |
| **E2E Latency (P95)**    | **10.2s**  | **5.08**     | **-50%** |
| ITL (Mean)               | 25ms       | 8ms          | -67%     |
| ITL (P95)                | 197ms      | 67ms         | -66%     |
| TTFT (Mean)              | 532ms      | 1400ms       | +170%    |
| TTFT (P95)               | 1574ms     | 2471ms       | +57%     |

> [!NOTE]
> In aggregated setup, vLLM allocates all GPU resources to
> processing prefills as they arrive. TTFT is elevated in the
> disaggregated setup because less resources are allocated to
> processing prefills.
