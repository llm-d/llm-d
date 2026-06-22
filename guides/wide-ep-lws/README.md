# Wide Expert Parallelism

[![E2E (CKS GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-wide-ep-lws-cks-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-wide-ep-lws-cks-acc-gpu-vllm-x.yaml)
[![E2E (GKE GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-wide-ep-lws-gke-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-wide-ep-lws-gke-acc-gpu-vllm-x.yaml)
[![E2E (OCP GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-wide-ep-lws-ibm-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-wide-ep-lws-ibm-acc-gpu-vllm-x.yaml)

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
  export GUIDE_NAME="wide-ep-lws"
  export NAMESPACE=llm-d-wide-ep
  export MODEL=deepseek-ai/DeepSeek-R1-0528
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
    ${ROUTER_STANDALONE_CHART} \
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
    ${ROUTER_GATEWAY_CHART}  \
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

### 3. (Optional) Enable monitoring

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

`guide_wide-ep-lws_1.yaml` is a **dedicated workload profile** shipped with `llm-d-benchmark` specifically for this guide — it reproduces the saturation load used to generate the [graphs at the bottom of this guide](#benchmarking-report) (concurrent load with `concurrency_level=2048` and `num_requests=8192`) and is shaped to highlight the strengths of wide expert parallelism by fully saturating the topology.

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
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/<gke|coreweave>
```

## Benchmarking Report

The benchmark is running on:

* Provider: CKS
* Prefill: 1 instance with EP=16
* Decode: 1 instance with EP=16
* 4 H200 VMs, 32 GPUs, Infiniband

<details>
<summary><b><i>Click</i></b> here to view the report from the above example</summary>

```yaml
results:
  request_performance:
    aggregate:
      latency:
        inter_token_latency:
          max: 30.34121842868626
          mean: 0.07969590251979176
          min: 3.8053840398788452e-06
          p0p1: 3.975816071033478e-06
          p1: 4.106201231479645e-06
          p10: 4.507601261138916e-06
          p25: 5.077570676803589e-06
          p5: 4.325993359088898e-06
          p50: 6.389804184436798e-06
          p75: 1.0115094482898712e-05
          p90: 3.565475344657898e-05
          p95: 0.8909534962382163
          p99: 1.2793979200161996
          p99p9: 1.598953516515907
          units: s/token
        normalized_time_per_output_token:
          max: 22.36453324875661
          mean: 0.11380224349675808
          min: 0.03750446231926297
          p0p1: 0.03859267860123989
          p1: 0.07242633368539624
          p10: 0.07806590183948588
          p25: 0.0794990241915988
          p5: 0.07746114374645986
          p50: 0.08341223422272201
          p75: 0.09037681113958496
          p90: 0.11040518531155001
          p95: 0.11893183671153963
          p99: 0.1280457219757267
          p99p9: 8.673798847227339
          units: s/token
        request_latency:
          max: 259.90107879415154
          mean: 175.831311636725
          min: 137.61615218035877
          p0p1: 144.07184547862877
          p1: 152.20689211042597
          p10: 156.15101961996407
          p25: 158.87230786448345
          p5: 155.0747766970657
          p50: 166.57856354676187
          p75: 180.30430065304972
          p90: 218.8493181052618
          p95: 235.833738672873
          p99: 248.89307169288398
          p99p9: 258.52759975225854
          units: s
        time_per_output_token:
          max: 0.09971424710837197
          mean: 0.07970394011720383
          min: 0.06714119758317436
          p0p1: 0.07046533771248802
          p1: 0.07419887348744633
          p10: 0.07714715711461388
          p25: 0.07882438320307489
          p5: 0.07614581276124897
          p50: 0.07990098050042406
          p75: 0.08073974602562461
          p90: 0.08199862981275519
          p95: 0.08245319460844246
          p99: 0.08341267507742763
          p99p9: 0.0902886399074444
          units: s/token
        time_to_first_token:
          max: 98.8584302579984
          mean: 21.29069098684954
          min: 2.6285605849698186
          p0p1: 2.884328710737638
          p1: 3.419430093830451
          p10: 5.03398488946259
          p25: 6.517769109224901
          p5: 4.3494329891167585
          p50: 11.075260647572577
          p75: 25.52732751844451
          p90: 58.6893984858878
          p95: 73.95478512016125
          p99: 86.97312242283486
          p99p9: 97.31417823833262
          units: s
      requests:
        failures: 0
        input_length:
          max: 2081.0
          mean: 2046.793701171875
          min: 2016.0
          p0p1: 2024.0
          p1: 2028.0
          p10: 2036.0
          p25: 2041.0
          p5: 2033.0
          p50: 2046.0
          p75: 2052.0
          p90: 2058.0
          p95: 2061.0
          p99: 2068.0
          p99p9: 2075.0
          units: count
        output_length:
          max: 4065.0
          mean: 2004.9931640625
          min: 7.0
          p0p1: 23.573
          p1: 1914.82
          p10: 1994.0
          p25: 1999.0
          p5: 1987.0
          p50: 2001.0
          p75: 2001.0
          p90: 2001.0
          p95: 2001.0
          p99: 2003.0
          p99p9: 4056.809000000001
          units: count
        total: 8192
      throughput:
        output_token_rate:
          mean: 22124.879416240507
          units: tokens/s
        request_rate:
          mean: 11.034890199531286
          units: queries/s
        total_token_rate:
          mean: 44711.0231697644
          units: tokens/s
run:
  cid: 84d64299-c166-584e-b27f-d7951cca928b
  eid: 1b4db7eb-4057-5ddf-91e0-36dec72071f5
  time: {}
  uid: 2c9ada2e-362f-4e90-9eba-453b9e0c200d
  user: namespace=rob-dev
scenario:
  load:
    metadata:
      cfg_id: 74234e98afe7498fb5daf1f36ac2d78acc339464f950703b8c019892f982b90b
      schema_version: 0.0.1
    native:
      args: {}
    standardized:
      concurrency: 2048
      input_seq_len:
        distribution: gaussian
        max: 2081
        min: 2016
        value: 2046.793701171875
      output_seq_len:
        distribution: gaussian
        max: 4065
        min: 7
        value: 2004.9931640625
      parallelism: 1
      rate_qps: 8192.0
      source: unknown
      stage: 0
      tool: inference-perf
      tool_version: ''
version: '0.2'
```

</details>

At concurrency 2048 (~128 per decode rank), we observe:

```json
"throughput": {
  "input_tokens_per_sec": 22586.143753523895,
  "output_tokens_per_sec": 22124.879416240507,
  "total_tokens_per_sec": 44711.0231697644,
  "requests_per_sec": 11.034890199531286
}
```

This is ~1350 token/second/decode GPU, peaking at 1600 tokens per second per GPU.

At around 200 requests per decode rank, you can achieve ~2000 TPSG.
