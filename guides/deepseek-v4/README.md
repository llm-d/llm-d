# DeepSeek-V4-Pro on GB200

## Overview

This guide deploys [DeepSeek-V4-Pro](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro) on NVIDIA
GB200 NVL72 with vLLM prefill/decode disaggregation (NIXL KV transfer) in a wide
expert-parallel pattern, managed as a single `DisaggregatedSet` (a pair of LeaderWorkerSets).
Wide-EP spans multiple nodes over GB200's cross-node NVLink (MNNVL) fabric, provisioned through
an NVIDIA DRA `ComputeDomain`.

It composes the [wide expert parallelism](../../docs/well-lit-paths/foundations/wide-expert-parallelism.md)
and [P/D disaggregation](../../docs/well-lit-paths/foundations/pd-disaggregation.md) foundations
with P/D-aware, prefix-cache-aware routing, and ships a range of prefill : decode operating
points, from a 16-GPU low-latency layout up to a 56-GPU high-throughput layout.

These manifests were tested on Oracle Cloud Infrastructure (OCI, `BM.GPU.GB200.4` nodes). Storage
and DRA may need to be adapted to your environment.

## Default Configuration

| Parameter          | Value                                                                                  |
| ------------------ | -------------------------------------------------------------------------------------- |
| Model              | [deepseek-ai/DeepSeek-V4-Pro](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro)      |
| Accelerator        | NVIDIA GB200 NVL72 (4 GPUs per node), cross-node NVLink via `ComputeDomain`            |
| Serving topology   | P/D disaggregated `DisaggregatedSet`; prefill DEP8, decode TP=8, DEP8 or DEP16 (see [Deployments](#2-deploy-the-model-server)) |
| DP model           | Hybrid load balancing (`--data-parallel-hybrid-lb`), expert parallelism enabled        |
| MoE backend        | `deep_gemm_mega_moe`                                                                   |
| KV transfer        | NixlConnector                                                                          |
| KV cache           | FP8, block size 256                                                                    |
| Max model length   | 9280 tokens                                                                            |
| Model storage      | Node-local NVMe (`hostPath` `/mnt/numa0/hf-cache`, ~850 GB model)                      |

### Supported Hardware Backends

| Backend           | Directory                | Notes                                                    |
| ----------------- | ------------------------ | -------------------------------------------------------- |
| NVIDIA GPU (vLLM) | `modelserver/gpu/vllm/`  | GB200 NVL72 on OCI (`providers/oci`), P/D disaggregated  |

## Prerequisites

- Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md) to use this guide.
- Set the following environment variables:

  ```bash
  export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
  source ${REPO_ROOT}/guides/env.sh
  export GUIDE_NAME="deepseek-v4"
  export NAMESPACE=llm-d-deepseek-v4
  export MODEL=deepseek-ai/DeepSeek-V4-Pro
  ```

- Install the Gateway API Inference Extension CRDs:

  ```bash
  # GAIE_URL is automatically calculated from GAIE_VERSION at ${REPO_ROOT}/guides/env.sh
  kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/${GAIE_URL}/v1-manifests.yaml
  ```

- Deploy the [LeaderWorkerSet controller](https://lws.sigs.k8s.io/docs/installation/) `v0.11.1`
  or newer. When installing with Helm, pass `--set enableDisaggregatedSet=true` to enable the
  `DisaggregatedSet` validating webhook and RBAC used by the model server.
- Install the **NVIDIA DRA driver for GPUs**. The cross-node NVLink fabric is provisioned
  through a [`ComputeDomain`](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/dra-cds.html#computedomains-multi-node-nvlink-simplified)
  resource, so the cluster must have the NVIDIA DRA driver installed and the `ComputeDomain`
  CRD present:

  ```bash
  kubectl get crd computedomains.resource.nvidia.com
  ```

  If this returns `NotFound`, install the [NVIDIA DRA driver](https://github.com/NVIDIA/k8s-dra-driver-gpu)
  before continuing. The guide ships its own `ComputeDomain` CR
  (`modelserver/gpu/vllm/providers/oci/compute-domain.yaml`); applying a deployment creates it
  and the workers claim a channel from it, so no manual fabric setup is needed.
- Create a target namespace for the installation:

  ```bash
  kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
  ```

- [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN` matching a valid HuggingFace token](../../helpers/hf-token.md) to pull models.

## Installation Instructions

### 1. Deploy the llm-d Router

#### Standalone Mode

This deploys the llm-d Router with an Envoy sidecar, it doesn't set up a Kubernetes Gateway.
The router values ([`deepseek-v4.values.yaml`](router/deepseek-v4.values.yaml)) run separate
`prefill` and `decode` scheduling profiles (prefix-cache, queue and active-request scoring for
prefill; active-request scoring for decode).

```bash
helm install ${GUIDE_NAME} \
    ${ROUTER_STANDALONE_CHART} \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

<details>
<summary><b>Gateway Mode</b></summary>

To use a Kubernetes Gateway managed proxy rather than the standalone version, follow these steps instead of applying the previous Helm chart:

1. *Deploy a Kubernetes Gateway* by following one of [the gateway guides](../../docs/infrastructure/gateway).
2. *Deploy the llm-d Router and an HTTPRoute* that connects it to the Gateway as follows:

```bash
export PROVIDER_NAME=gke # options: none, gke, agentgateway, istio
helm install ${GUIDE_NAME} \
    ${ROUTER_GATEWAY_CHART} \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/recipes/router/features/httproute-flags.yaml \
    -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
    --set provider.name=${PROVIDER_NAME} \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

</details>

### 2. Deploy the Model Server

Each deployment is a different prefill : decode operating point. Pick one and apply it:

| Deployment | Layout | Nodes / GPUs |
|---|---|---|
| `oci-low-latency` | 1 prefill (DEP8) : 1 decode (TP=8) | 4 / 16 |
| `oci-low-latency-scaled` | 1 prefill (DEP8) : 4 decode (TP=8) | 10 / 40 |
| `oci-mid-curve` | 1 prefill : 1 decode (DEP8 each) | 4 / 16 |
| `oci-balanced` | 2 prefill (DEP8) : 1 decode (DEP16) | 8 / 32 |
| `oci-high-tpt` | 2 prefill : 1 decode (DEP8 each) | 6 / 24 |
| `oci-high-tpt-dep16` | 3 prefill (DEP8) : 1 decode (DEP16) | 10 / 40 |
| `oci-max-tpt` | 3 prefill : 1 decode (DEP8 each) | 8 / 32 |
| `oci-ultra-tpt` | 4 prefill (DEP8) : 1 decode (DEP16) | 12 / 48 |
| `oci-3p2d-dep8-dep16-flashinfer` | 3 prefill (DEP8) : 2 decode (DEP16), flashinfer | 14 / 56 |

```bash
export DEPLOYMENT=oci-balanced # any deployment from the table above
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/deployments/${DEPLOYMENT}
```

The deployments compose the `providers/oci` overlay (node-local NVMe model cache and the
`ComputeDomain` claim) with a topology component from `modelserver/gpu/vllm/components/`.
Wait for the pods to become ready (the ~850 GB model takes a while to download and load):

```bash
kubectl get pods -n ${NAMESPACE} -l llm-d.ai/model=DeepSeek-V4-Pro -w
```

### 3. (Optional) Enable Monitoring

- Install the [Monitoring stack](../../docs/operations/observability/setup.md).
- To enable Prometheus monitoring on the llm-d router, add `-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml` during the [router installation step](#1-deploy-the-llm-d-router).
- Deploy the monitoring resources for model servers. With DP-aware scheduling, each DP rank is
  available at `podip:port`, where each port is `rank0`-`rank7`; this overlay scrapes each
  rank's port:

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/monitoring
```

## Verification

### 1. Get the IP of the Proxy

#### Standalone Mode

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
    --env="MODEL=$MODEL" \
    -- /bin/bash
```

**Send a completion request:**

```bash
curl -X POST http://${IP}/v1/completions \
    -H 'Content-Type: application/json' \
    -d "{
        \"model\": \"${MODEL}\",
        \"prompt\": \"How are you today?\"
    }" | jq
```

## Benchmarking

Benchmark profiles and results for this guide are not yet published.

## Cleanup

To remove the deployed components:

```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
# If you enabled monitoring (Step 3), remove the monitoring overlay first.
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/monitoring
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/deployments/${DEPLOYMENT}
```
