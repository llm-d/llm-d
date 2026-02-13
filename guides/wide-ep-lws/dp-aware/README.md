# Well-lit Path: Wide Expert Parallelism (EP/DP) with LeaderWorkerSet

## Overview

This guide demonstrates how to deploy DeepSeek-R1-0528 using vLLM's P/D disaggregation support with NIXL in a wide expert parallel pattern with LeaderWorkerSets. This guide has been validated on:

* a 32xH200 cluster with InfiniBand networking
* a 32xH200 cluster on GKE with RoCE networking
* a 32xB200 cluster on GKE with RoCE networking

> WARNING: We are still investigating and optimizing performance for other hardware and networking configurations

In this example, we will demonstrate a deployment of `DeepSeek-R1-0528` with:

* 1 DP=16 Prefill Worker
* 1 DP=16 Decode Worker

## Hardware Requirements

This guide requires 32 Nvidia H200 or B200 GPUs and InfiniBand or RoCE RDMA networking. Check `modelserver/base/decode.yaml` and `modelserver/base/prefill.yaml` for detailed resource requirements.

## Prerequisites

* Have the [proper client tools installed on your local system](../../prereq/client-setup/README.md) to use this guide.
* Ensure your cluster infrastructure is sufficient to [deploy high scale inference](../../prereq/infrastructure/README.md)
  * You must have high speed inter-accelerator networking
  * The pods leveraging inter-node EP must be deployed in a cluster environment with full mesh network connectivity.
    * **_NOTE:_** The DeepEP backend used in WideEP requires All-to-All RDMA connectivity. Every NIC on a host must be able to communicate with every NIC on all other hosts. Networks restricted to communicating only between matching NIC IDs (rail-only connectivity) will fail.
  * You have deployed the [LeaderWorkerSet optional controller](../../prereq/infrastructure/README.md#optional-install-leaderworkerset-for-multi-host-inference)
* Configure and deploy your [Gateway control plane](../../prereq/gateway-provider/README.md).
* Have the [Monitoring stack](../../../docs/monitoring/README.md) installed on your system.
* Create a namespace for installation.

  ```bash
  export NAMESPACE=llm-d-wide-ep # or any other namespace (shorter names recommended)
  kubectl create namespace ${NAMESPACE}
  ```

* [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN` matching a valid HuggingFace token](../../prereq/client-setup/README.md#huggingface-token) to pull models.
* [Choose an llm-d version](../../prereq/client-setup/README.md#llm-d-version)

## Installation

```bash
cd guides/wide-ep-lws/
```

### Deploy Model Servers

GKE and CoreWeave are tested Kubernetes providers for this well-lit path. You can customize the manifests if you run on other Kubernetes providers.

<!-- TABS:START -->

<!-- TAB:GKE (H200):default -->
#### GKE (H200)

```bash
kubectl apply -k ./manifests/modelserver/gke -n ${NAMESPACE}
```

<!-- TAB:GKE (B200) -->
#### GKE (B200)

```bash
# Deploy on GKE for B200 on the a4 instance type to work around a known vLLM memory issue
kubectl apply -k ./manifests/modelserver/gke-a4 -n ${NAMESPACE}
```

<!-- TAB:CoreWeave -->
#### CoreWeave

```bash
kubectl apply -k ./manifests/modelserver/coreweave  -n ${NAMESPACE}
```

<!-- TABS:END -->

### Deploy InferencePool

Select the provider-specific Helm command using the tabs below.

<!-- TABS:START -->

<!-- TAB:GKE:default -->
#### GKE

```bash
helm install llm-d-infpool \
  -n ${NAMESPACE} \
  -f ./manifests/inferencepool.values.yaml \
  --set "provider.name=gke" \
  oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool \
  --version v1.3.0
```

<!-- TAB:Istio -->
#### Istio

```bash
helm install llm-d-infpool \
  -n ${NAMESPACE} \
  -f ./manifests/inferencepool.values.yaml \
  --set "provider.name=istio" \
  oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool \
  --version v1.3.0
```

<!-- TAB:Kgateway -->
#### Kgateway

```bash
helm install llm-d-infpool \
  -n ${NAMESPACE} \
  -f ./manifests/inferencepool.values.yaml \
  oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool \
  --version v1.3.0
```

<!-- TABS:END -->

### Deploy Gateway and HTTPRoute

Deploy the Gateway and HTTPRoute using the [gateway recipe](../../recipes/gateway/README.md).

### Gateway options

To see what gateway options are supported refer to our [gateway provider prereq doc](../../prereq/gateway-provider/README.md#supported-providers). Gateway configurations per provider are tracked in the [gateway-configurations directory](../../prereq/gateway-provider/common-configurations/).

You can also customize your gateway, for more information on how to do that see our [gateway customization docs](../../../docs/customizing-your-gateway.md).

## Tuning Selective PD

As with PD, the `wide-ep-lws` guide supports selective PD. For information on this refer to [this section of the PD docs](../../pd-disaggregation/README.md#tuning-selective-pd).

## Experimental: Data Parallel (DP) Aware Scheduling

This deployment uses **DP-aware scheduling**, where instead of letting vLLM automatically handle data parallelism internally, we explicitly launch separate vLLM server instances for each data parallel rank. This enables the inference scheduler to route requests directly to specific DP ranks, improving KV cache routing efficiency.

### How It Works

**Traditional Data Parallelism:**
- vLLM launches a single server process that internally manages DP=16 across GPUs
- External clients see one endpoint per pod
- vLLM handles internal load balancing across DP ranks

**DP-Aware Scheduling (This Deployment):**
- Each pod explicitly launches 8 separate vLLM server instances (one per GPU)
- Each instance runs on a unique port (8000-8007 for prefill, 8200-8207 for decode)
- Each instance is assigned an explicit DP rank using `--data-parallel-rank`
- The inference scheduler can route to specific DP ranks based on request characteristics

**Key Configuration Changes:**

1. **InferencePool**: Declares 8 target ports (8000-8007) to expose all local DP ranks
2. **Routing Proxy**: Configured with `--data-parallel-size=8` to understand the DP topology
3. **vLLM Containers**: Launch 8 parallel processes per pod with:
   - `CUDA_VISIBLE_DEVICES` pinning each process to a specific GPU (0-7)
   - Unique ports per rank
   - Explicit `--data-parallel-rank` assignment
   - Global `--data-parallel-size=16` (8 ranks × 2 pods)
   - Local `--data-parallel-size-local=8`

**Architecture:**

Each LeaderWorkerSet has 2 pods, each running 8 vLLM instances:
- **Prefill Pod 0**: DP ranks 0-7 on ports 8000-8007
- **Prefill Pod 1**: DP ranks 8-15 on ports 8000-8007
- **Decode Pod 0**: DP ranks 0-7 on ports 8200-8207
- **Decode Pod 1**: DP ranks 8-15 on ports 8200-8207

The scheduler routes to specific ranks using (pod IP, port) tuples, enabling fine-grained request distribution.

For more information on vLLM's data parallelism features, see the [vLLM documentation](https://docs.vllm.ai/en/latest/serving/data_parallel_deployment/#external-load-balancing).

## Verifying the installation

* Firstly, you should be able to list all helm releases installed into your chosen namespace:

```bash
helm list -n ${NAMESPACE}
NAME            NAMESPACE       REVISION    UPDATED                                 STATUS      CHART                       APP VERSION
llm-d-infpool   llm-d-wide-ep   1           2025-08-24 13:14:53.355639 -0700 PDT    deployed    inferencepool-v1.3.0        v0.3.0
```

* Out of the box with this example you should have the following resources (if using Istio):

```bash
kubectl get all -n ${NAMESPACE}
NAME                                                         READY   STATUS    RESTARTS   AGE
pod/infra-wide-ep-inference-gateway-istio-74d5c66c86-h5mfn   1/1     Running   0          2m22s
pod/wide-ep-llm-d-decode-0                                   2/2     Running   0          2m13s
pod/wide-ep-llm-d-decode-0-1                                 2/2     Running   0          2m13s
pod/llm-d-infpool-epp-84dd98f75b-r6lvh                       1/1     Running   0          2m14s
pod/wide-ep-llm-d-prefill-0                                  1/1     Running   0          2m13s
pod/wide-ep-llm-d-prefill-0-1                                1/1     Running   0          2m13s


NAME                                            TYPE           CLUSTER-IP    EXTERNAL-IP   PORT(S)                        AGE
service/infra-wide-ep-inference-gateway-istio   ClusterIP      10.16.1.34    10.16.4.2     15021:30312/TCP,80:33662/TCP   2m22s
service/wide-ep-ip-1e480070                     ClusterIP      None          <none>        54321/TCP                      2d4h
service/wide-ep-llm-d-decode                    ClusterIP      None          <none>        <none>                         2m13s
service/llm-d-infpool-epp                       ClusterIP      10.16.1.137   <none>        9002/TCP                       2d4h
service/wide-ep-llm-d-prefill                   ClusterIP      None          <none>        <none>                         2m13s

NAME                                                    READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/infra-wide-ep-inference-gateway-istio   1/1     1            1           2m22s
deployment.apps/llm-d-infpool-epp                       1/1     1            1           2m14s

NAME                                                               DESIRED   CURRENT   READY   AGE
replicaset.apps/infra-wide-ep-inference-gateway-istio-74d5c66c86   1         1         1       2m22s
replicaset.apps/llm-d-infpool-epp-55bb9857cf                       1         1         1       2m14s

NAME                                                      READY   AGE
statefulset.apps/wide-ep-llm-d-decode     1/1     2m13s
statefulset.apps/wide-ep-llm-d-decode-0   1/1     2m13s
statefulset.apps/wide-ep-llm-d-prefill    1/1     2m13s
statefulset.apps/wide-ep-llm-d-prefill-1  1/1     2m13s
```

**_NOTE:_** This assumes no other guide deployments in your given `${NAMESPACE}` and you have not changed the default release names via the `${RELEASE_NAME}` environment variable.

## Using the stack

For instructions on getting started making inference requests see [our docs](../../../docs/getting-started-inferencing.md)

**_NOTE:_** This example particularly benefits from utilizing stern as described in the [getting-started-inferencing docs](../../../docs/getting-started-inferencing.md#following-logs-for-requests), because while we only have 3 inferencing pods, it has 16 vllm servers or ranks.

**_NOTE:_** Compared to the other examples, this one takes anywhere between 7-10 minutes for the vllm API servers to startup so this might take longer before you can interact with this example.

## Benchmarking

### Overview
We deployed the default wide-ep-lws user guide on GKE (`./manifests/modelserver/gke-a4`).

* Provider: GKE
* Prefill: 1 instance with EP=16
* Decode: 1 instance with EP=16
* 4 `a4-highgpu-8g` VMs, 32 GPUs

We use the [inference-perf](https://github.com/kubernetes-sigs/inference-perf/tree/main) benchmark tool to generate random datasets with 1K input length and 1K output length. This benchmark targets batch use case and we aim to find the maximum throughput by sweeping from lower to higher request rates up to 250 QPS.

### Run Benchmark

1. Deploy the wide-ep-lws stack following the Installation steps above. Once the stack is ready, obtain the gateway IP: 

```bash
export GATEWAY_IP=$(kubectl get gateway/llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')
```

2. Follow the [benchmark guide](../../benchmark/README.md) to deploy the benchmark tool and analyze the benchmark results. Notably, select the corresponding benchmark template:

```
export BENCHMARK_TEMPLATE="${BENCH_TEMPLATE_DIR}"/wide_ep_template.yaml
```

### Results

<img src="throughput_vs_qps.png" width="900" alt="Throughput vs QPS">
<img src="throughput_vs_latency.png" width="300" alt="Throughput vs Latency">

At request rate 250, we achieved the max throughput:

```
"throughput": {
    "input_tokens_per_sec": 51218.79261732335,
    "output_tokens_per_sec": 49783.58426326592,
    "total_tokens_per_sec": 101002.37688058926,
    "requests_per_sec": 50.02468992880545
}
```

This equals to 3200 input tokens/s/GPU and 3100 output tokens/s/GPU.

## Cleanup

To remove the deployment:

```bash
# From examples/wide-ep-lws
helm uninstall llm-d-infpool -n ${NAMESPACE}
kubectl delete -k ./manifests/modelserver/<gke|coreweave> -n ${NAMESPACE}
kubectl delete -k ../../recipes/gateway/<gke-l7-regional-external-managed|istio|kgateway|kgateway-openshift> -n ${NAMESPACE}
```

## Customization

For information on customizing a guide and tips to build your own, see [our docs](../../../docs/customizing-a-guide.md)
