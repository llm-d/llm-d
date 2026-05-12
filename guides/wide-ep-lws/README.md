# Wide Expert Parallelism

<!--
[![Nightly - Wide EP LWS E2E (OpenShift)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-wide-ep-lws-ocp.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-wide-ep-lws-ocp.yaml)
-->
[![Nightly - Wide EP LWS E2E (CKS)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-wide-ep-lws-cks.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-wide-ep-lws-cks.yaml) [![Nightly - Wide EP LWS E2E (GKE)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-wide-ep-lws-gke.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/nightly-e2e-wide-ep-lws-gke.yaml)

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
  export GAIE_VERSION=v1.5.0
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
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
helm install ${GUIDE_NAME} \
    oci://registry.k8s.io/gateway-api-inference-extension/charts/standalone \
    -f ${REPO_ROOT}/guides/recipes/scheduler/base.values.yaml \
    -f ${REPO_ROOT}/guides/${GUIDE_NAME}/scheduler/${GUIDE_NAME}.values.yaml \
    -n ${NAMESPACE} --version ${GAIE_VERSION}
```

<details>
<summary><h4>Gateway Mode</h4></summary>

To use a Kubernetes Gateway managed proxy rather than the standalone version, follow these steps instead of applying the previous Helm chart:

1. *Deploy a Kubernetes Gateway* by following one of [the gateway guides](../prereq/gateways).
2. *Deploy the llm-d Router and an HTTPRoute* that connects it to the Gateway as follows:

```bash
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))

export PROVIDER_NAME=gke # options: none, gke, agentgateway, istio
helm install ${GUIDE_NAME} \
    oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool  \
    -f ${REPO_ROOT}/guides/recipes/scheduler/base.values.yaml \
    -f ${REPO_ROOT}/guides/recipes/scheduler/features/httproute-flags.yaml \
    -f ${REPO_ROOT}/guides/${GUIDE_NAME}/scheduler/${GUIDE_NAME}.values.yaml \
    --set provider.name=${PROVIDER_NAME} \
    -n ${NAMESPACE} --version ${GAIE_VERSION}
```

</details>


### 2. Deploy the Model Server

Apply the Kustomize overlays for your specific backend:

```bash
export INFRA_PROVIDER=gke # options: gke, coreweave, dgx-cloud-gb200
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

The benchmark launches a pod (`llmdbench-harness-launcher`) that, in this case, uses `inference-perf` with a synthetic random batch workload workload named `2048_concurrent_2k_isl_2k_osl`. For more details, refer to the [benchmark instructions doc](../../helpers/benchmark.md).

### 1. Prepare the Benchmarking Suite

- Download the benchmark script:

```bash
curl -L -O https://raw.githubusercontent.com/llm-d/llm-d-benchmark/main/existing_stack/run_only.sh
chmod u+x run_only.sh
```

### 2. Download the Workload Template

The template is located at `guides/wide-ep-lws/benchmark-templates/guide.yaml`. You can also download it if needed:

```bash
curl -LJO "https://raw.githubusercontent.com/llm-d/llm-d/main/guides/${GUIDE_NAME}/benchmark-templates/2048_concurrent_2k_isl_2k_osl.yaml"
```

### 3. Execute Benchmark

```bash
envsubst < 2048_concurrent_2k_isl_2k_osl.yaml > config.yaml
./run_only.sh -c config.yaml -o ./results
```

## Cleanup

To remove the deployed components:

```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
kubectl delete -n ${NAMESPACE} -k guides/${GUIDE_NAME}/modelserver/<gke|coreweave>
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
