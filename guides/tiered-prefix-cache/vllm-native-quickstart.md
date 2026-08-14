# Tiered Prefix Cache — vLLM Native Offloading Quickstart

This is the **recommended default configuration** for Tiered Prefix Cache: vLLM's native `OffloadingConnector`, offloading evicted KV-cache blocks to CPU RAM and, optionally, to a shared filesystem. It requires no extra components beyond the model server and is low-overhead enough to enable in almost any deployment.

For the concepts, tier tradeoffs, and architecture, see the [Tiered Prefix Cache well-lit path](../../docs/well-lit-paths/foundations/tiered-prefix-cache.md). Need LMCache, MooncakeStore, SGLang HiCache, or a TPU/XPU deployment instead? See the [full guide](./README.md#choosing-a-path).

## Default Configuration

| Parameter              | Value                                                   |
| ---------------------- | ------------------------------------------------------- |
| Model                  | [Qwen/Qwen3-32B](https://huggingface.co/Qwen/Qwen3-32B) |
| GPUs per replica (TP)  | 2                                                        |
| GPU Accelerator        | NVIDIA H100                                             |
| CPU Cache Offload Size | 100 GB                                                  |

> [!IMPORTANT]
> **Serving models with mismatched attention head dimensions (e.g. Gemma 4) under KV offloading:**
> enabling the vLLM native `OffloadingConnector` disables vLLM's Hybrid KV Cache Manager (HMA). Most
> models still run fine because their attention layers share one KV spec and collapse into a single
> unified group — this includes sliding-window + full-attention models (e.g `gpt-oss-120b`) and Mamba +
> attention hybrids ( e.g `Nemotron`, whose attention layers are uniform and whose SSM state uses a separate
> cache). **Gemma 4 does not:** its sliding-window and full-attention layers use *different* head
> dimensions, so their KV specs cannot be unified and the server fails to start. To serve
> such a model, add`--no-disable-hybrid-kv-cache-manager` to the vLLM args to keep HMA enabled.

---

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
  export NAMESPACE=llm-d-tiered-prefix-cache
  ```

* Install the Gateway API Inference Extension CRDs:

  ```bash
  kubectl apply -f "https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GAIE_VERSION}/v1-manifests.yaml"
  ```

* Create a target namespace for the installation:

  ```bash
  kubectl create namespace ${NAMESPACE}
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

---

## Installation Instructions

### 1. Deploy the llm-d Router

#### Standalone Mode

```bash
helm install tiered-prefix-cache \
    ${ROUTER_STANDALONE_CHART} \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/tiered-prefix-cache/router/tiered-prefix-cache-cpu.values.yaml \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

<details>
<summary><h4>Gateway Mode</h4></summary>

1. *Deploy a Kubernetes Gateway* by following one of [the gateway guides](../../docs/infrastructure/gateway).
2. *Deploy the llm-d Router and an HTTPRoute*:

```bash
export PROVIDER_NAME=gke # options: none, gke, agentgateway, istio
helm install tiered-prefix-cache \
    ${ROUTER_GATEWAY_CHART} \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/tiered-prefix-cache/router/tiered-prefix-cache-cpu.values.yaml \
    --set provider.name=${PROVIDER_NAME} \
    --set httpRoute.create=true \
    --set httpRoute.inferenceGatewayName=llm-d-inference-gateway \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

</details>

> [!NOTE]
> To enable tiered prefix caching, the llm-d EPP is configured with two prefix-cache scorers: one for the accelerator (GPU/TPU) cache and one for the CPU cache.
> LRU capacity for the CPU cache must be configured manually (`lruCapacityPerServer`) because vLLM does not currently emit CPU block metrics.

---

### 2. Deploy the Model Server

Deploy **one** of the two variants below. `INFRA_PROVIDER` selects a `base` overlay or a provider-specific one (for example `gke`).

#### vLLM native — CPU RAM

```bash
export MODEL_SERVER=vllm # vllm
export CONNECTOR=native  # native
export VARIANT=cpu       # cpu | fs
export INFRA_PROVIDER=base  # base | gke
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/tiered-prefix-cache/modelserver/gpu/vllm/native/cpu/${INFRA_PROVIDER}/
```

#### vLLM native — CPU RAM + Filesystem

This variant adds a shared filesystem tier using vLLM's native multi-tier offloading. It requires a ReadWriteMany PVC mounted at `/mnt/files-storage`.

First, provision the PVC. See [Storage Backends](./README.md#storage-backends) in the full guide to configure a `StorageClass` for your environment.

```bash
export STORAGE_CLASS="" # cluster default if empty; or e.g. "lustre" / "efs-sc"
envsubst < ${REPO_ROOT}/guides/tiered-prefix-cache/manifests/pvc.yaml | kubectl apply -n ${NAMESPACE} -f -
```

Then deploy the model server:

```bash
export INFRA_PROVIDER=base  # base | gke
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/tiered-prefix-cache/modelserver/gpu/vllm/native/fs/${INFRA_PROVIDER}/
```

---

### 3. (Optional) Enable monitoring

* Install the [Monitoring stack](../../docs/operations/observability/setup.md).
* To enable Prometheus monitoring on the llm-d router, add `-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml` during the [router installation step](#1-deploy-the-llm-d-router).
* Deploy the monitoring resources for model servers:

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring
```

---

## Verification

### 1. Check the PVC (filesystem variant only)

```bash
kubectl get pvc -n ${NAMESPACE}
```

Output should show the PVC as `Bound`.

### 2. Get the IP of the Proxy

**Standalone Mode**

```bash
export IP=$(kubectl get service tiered-prefix-cache-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```

<details>
<summary><b>Gateway Mode</b></summary>

```bash
export IP=$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')
```

</details>

### 3. Send Test Requests

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

### 4. Verify KV cache is offloaded to storage (filesystem variant)

```bash
# Long prompt (~3K tokens) to trigger offload
PROMPT=$(printf 'Story: '; for i in $(seq 1 800); do printf 'alice met bob and they walked together. '; done)
jq -n --arg prompt "$PROMPT" '{"model":"Qwen/Qwen3-32B", "prompt":$prompt, "max_tokens":3, "temperature":0}' | \
curl -s http://${IP}/v1/completions \
  -H 'Content-Type: application/json' \
  -d @- | jq
```

```bash
# Check the shared PVC for written blocks
POD=$(kubectl get pod -n ${NAMESPACE} -l llm-d.ai/role=decode -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n ${NAMESPACE} ${POD} -- du -sh /mnt/files-storage/kv-cache
kubectl exec -n ${NAMESPACE} ${POD} -- find /mnt/files-storage/kv-cache -maxdepth 5
```

Expected output: `du -sh` shows hundreds of MB to several GB, and `find` lists a path like
`/mnt/files-storage/<model>_<hash>_r0/<block-config>/<tp-config>/...`.

If you have monitoring set up, confirm via `vllm:kv_offload_total_bytes` in the metrics explorer.

---

## Cleanup

```bash
helm uninstall tiered-prefix-cache -n ${NAMESPACE}
```

```bash
export CONNECTOR=native            # native
export VARIANT=cpu                 # cpu | fs
export INFRA_PROVIDER=base         # base | gke
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/tiered-prefix-cache/modelserver/gpu/vllm/${CONNECTOR}/${VARIANT}/${INFRA_PROVIDER} --ignore-not-found
```

```bash
kubectl delete -f ${REPO_ROOT}/guides/tiered-prefix-cache/manifests/pvc.yaml -n ${NAMESPACE} --ignore-not-found  # if a PVC was created
```
<!-- llm-d-cicd:skip start -->
```bash
kubectl delete namespace ${NAMESPACE}
```
<!-- llm-d-cicd:skip end -->

---

## Next Steps

* Run a benchmark against the stack you just deployed — see the full guide's [Benchmarking](./README.md#benchmarking) section.
* Need a different offloading implementation or platform? See [Choosing a Path](./README.md#choosing-a-path) in the full guide (LMCache, MooncakeStore, SGLang HiCache, TPU, Intel XPU).
