# [Experimental] Wide Expert Parallelism with Precise Prefix-Cache Routing

## Overview

This variant follows the [Wide Expert Parallelism](README.md) deployment path
and replaces its approximate prefix index with exact KV-event-backed routing.
The EPP routes repeated prefixes to the prefill rank that holds their KV-cache
blocks, subject to modeled prefill load.

This configuration was validated on three 8-GPU H200 nodes with InfiniBand
networking on CoreWeave.

## Default Configuration

| Parameter | Value |
| --- | --- |
| Model | [GLM-5.2-FP8](https://huggingface.co/zai-org/GLM-5.2-FP8) |
| Prefill replicas | 2 nodes, DP8 each |
| Decode replicas | 1 node, DP8 |
| Total GPUs | 24 |
| KV block size | 64 tokens |

## Prerequisites

* Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md).
* Use a Gateway API Inference Extension bundle >= `v1.5.0-rc.2`. Earlier CRD
  generations allow only one `targetPort`, while this deployment exposes all
  eight DP rank ports.
* Use an EPP build with per-rank KV-event attribution
  ([llm-d-router#2233](https://github.com/llm-d/llm-d-router/pull/2233)).
* Deploy the [LeaderWorkerSet controller](https://lws.sigs.k8s.io/docs/installation/).
* Provide three 8-GPU H200 nodes on the same all-to-all RDMA fabric.
* Checkout the llm-d repository:

  ```bash
  export branch="main" # branch, tag, or commit hash
  git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout ${branch}
  ```

* Set the environment variables used by the guide:

  ```bash
  export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
  source ${REPO_ROOT}/guides/env.sh
  export GUIDE_NAME="wide-ep-lws"
  export NAMESPACE="llm-d-wide-ep-precise"
  export MODEL="zai-org/GLM-5.2-FP8"
  export MODEL_SERVER_OVERLAY="modelserver/gpu/vllm-glm-5.2/deployments/p2w1d1w1-precise"
  ```

* Install the Gateway API Inference Extension CRDs:

  ```bash
  kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GAIE_VERSION}/v1-manifests.yaml
  ```

* Create the target namespace:

  ```bash
  kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
  ```

* [Create the `llm-d-hf-token` secret in the target namespace](../../helpers/hf-token.md)
  with the key `HF_TOKEN`.

## Installation Instructions

### 1. Deploy the llm-d Router

#### Standalone Mode

This deploys the llm-d Router with an Envoy sidecar and does not create a
Kubernetes Gateway.

```bash
helm install ${GUIDE_NAME} \
    ${ROUTER_STANDALONE_CHART} \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
    -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/precise-routing.values.yaml \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

<details>
<summary><b>Gateway Mode</b></summary>

To use a Kubernetes Gateway managed proxy, follow these steps instead of the
standalone Helm command:

1. Deploy a Kubernetes Gateway by following one of the
   [gateway guides](../../docs/infrastructure/gateway).
2. Deploy the llm-d Router and HTTPRoute:

```bash
export PROVIDER_NAME=gke # options: none, gke, agentgateway, istio
helm install ${GUIDE_NAME} \
    ${ROUTER_GATEWAY_CHART} \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/recipes/router/features/httproute-flags.yaml \
    -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
    -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/precise-routing.values.yaml \
    --set provider.name=${PROVIDER_NAME} \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

</details>

### 2. Deploy the Model Server

#### Deploy using LeaderWorkerSet

The overlay contains the complete tested modelserver and render-Service
configuration:

```bash
kubectl apply -n ${NAMESPACE} \
    -k ${REPO_ROOT}/guides/${GUIDE_NAME}/${MODEL_SERVER_OVERLAY}
```

### 3. (Optional) Enable Monitoring

* Install the [monitoring stack](../../docs/operations/observability/setup.md).
* Add `-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml`
  to the router installation command.
* Deploy the modelserver monitoring resources:

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
<summary><b>Gateway Mode</b></summary>

```bash
export IP=$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')
```

</details>

### 2. Send a Test Request

Open a temporary shell inside the cluster:

```bash
kubectl run curl-debug --rm -it \
    --image=cfmanteiga/alpine-bash-curl-jq \
    --namespace="${NAMESPACE}" \
    --env="IP=${IP}" \
    --env="MODEL=${MODEL}" \
    -- /bin/bash
```

Send a completion request:

```bash
curl -X POST http://${IP}/v1/completions \
    -H 'Content-Type: application/json' \
    -d "{
        \"model\": \"${MODEL}\",
        \"prompt\": \"How are you today?\",
        \"max_tokens\": 16
    }" | jq
```

### 3. Verify Precise Routing

The render Service must return token IDs:

```bash
kubectl run render-check --rm -i --restart=Never \
  --image=python:3.12-alpine --namespace="${NAMESPACE}" -- \
  python -c '
import json, urllib.request
data = json.dumps({"model": "zai-org/GLM-5.2-FP8", "prompt": "render check", "max_tokens": 1}).encode()
request = urllib.request.Request("http://wide-ep-lws-render:8000/v1/completions/render", data=data, headers={"Content-Type": "application/json"})
with urllib.request.urlopen(request, timeout=10) as response:
    body = json.load(response)
assert isinstance(body, list) and body and body[0].get("token_ids"), body
print(body[0]["token_ids"])
'
```

Each engine pod must have eight established EPP subscriptions on ports
`5557` through `5564`:

```bash
EPP_IP=$(kubectl -n ${NAMESPACE} get endpointslices \
  -l kubernetes.io/service-name=${GUIDE_NAME}-epp \
  -o jsonpath='{.items[0].endpoints[0].addresses[0]}')

for POD in $(kubectl -n ${NAMESPACE} get pods \
  -l llm-d.ai/inference-serving=true -o name); do
  echo "${POD}"
  kubectl -n ${NAMESPACE} exec "${POD}" -c vllm -- python3 -c "
hx=''.join(f'{int(o):02X}' for o in reversed('${EPP_IP}'.split('.')))
print(sum(1 for line in open('/proc/net/tcp')
          if len(line.split()) > 3 and line.split()[3] == '01'
          and line.split()[2].split(':')[0] == hx
          and 5000 <= int(line.split()[1].split(':')[1], 16) < 6000))"
done
```

Send the same long prompt twice. The second request must return to the same
prefill rank, and that rank's `vllm:prefix_cache_hits_total` delta should be
close to the prompt length and aligned to 64-token blocks.

## Benchmarking

This variant uses the same [`inference-perf`](https://github.com/kubernetes-sigs/inference-perf)
workload as the main guide:

```bash
kubectl apply -n ${NAMESPACE} -f ${REPO_ROOT}/guides/${GUIDE_NAME}/inference-perf.yaml
```

## Cleanup

```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
# If monitoring was enabled, remove it before deleting the model servers.
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/monitoring
kubectl delete -n ${NAMESPACE} \
    -k ${REPO_ROOT}/guides/${GUIDE_NAME}/${MODEL_SERVER_OVERLAY}
```
