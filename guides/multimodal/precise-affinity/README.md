# Multimodal Precise-Affinity Guide

This guide deploys a multimodal vLLM stack with **precise** cache-affinity routing: the llm-d EPP routes each request to the decode pod that already holds the exact multimodal items (image, audio, video) referenced in the request, using the dedicated `mm-embeddings-cache-producer` and `mm-embeddings-cache-scorer` plugins.

Unlike the [multimodal optimized-baseline](../optimized-baseline/README.md) flavor, which scores endpoints from approximate text+image hashes via `prefix-cache-scorer`, this flavor uses real `/render`-derived multimodal item hashes and a per-pod LRU. Routing on identical content is deterministic rather than probabilistic.

---

## Default Configuration

| Parameter          | Value                                                                              |
| ------------------ | ---------------------------------------------------------------------------------- |
| Default Model      | [Qwen/Qwen3-VL-32B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-32B-Instruct)    |
| Replicas           | 4                                                                                  |
| Tensor Parallelism | 2                                                                                  |
| GPUs per replica   | 2                                                                                  |
| Total GPUs         | 8                                                                                  |
| Render placement   | Sidecar in EPP pod                                                                 |

### Supported Hardware Backends

| Backend            | Directory                                | Notes                                              |
| ------------------ | ---------------------------------------- | -------------------------------------------------- |
| NVIDIA GPU         | `modelserver/gpu/vllm/${INFRA_PROVIDER}/`| `INFRA_PROVIDER` options: `base`, `gke`            |

---

## Prerequisites

1. Install the local client tooling using the [client setup guide](../../../helpers/client-setup/README.md).
2. Clone and check out the llm-d repository:
   ```bash
   export branch="main" # branch, tag, or commit hash
   git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout ${branch}
   export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
   ```
3. Set up environment variables:
   ```bash
   export GAIE_VERSION=v1.5.0
   export ROUTER_CHART_VERSION=v0
   export GUIDE_NAME="precise-affinity"
   export NAMESPACE=llm-d-multimodal-precise-affinity
   ```
4. Install the Gateway API Inference Extension CRDs:
   ```bash
   kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GAIE_VERSION}/v1-manifests.yaml
   ```
5. Create the namespace:
   ```bash
   kubectl create namespace ${NAMESPACE}
   ```

---

## Installation Instructions

### 1. Deploy the llm-d Router

#### Standalone Mode

Deploy the llm-d Router in **Standalone Mode** overlaying router custom configurations:
```bash
# Run from the root of the llm-d repo
helm install ${GUIDE_NAME} \
    oci://ghcr.io/llm-d/charts/llm-d-router-standalone-dev \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/multimodal/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

<details>
<summary><h4>Gateway Mode</h4></summary>

To use a Kubernetes Gateway managed proxy rather than the standalone version, follow these steps:

1. _Deploy a Kubernetes Gateway_ by following one of [the gateway guides](../../prereq/gateways).
2. _Deploy the llm-d router and an HTTPRoute_ that connects it to the Gateway as follows:

```bash
export PROVIDER_NAME=gke # options: none, gke, agentgateway, istio
helm install ${GUIDE_NAME} \
    oci://ghcr.io/llm-d/charts/llm-d-router-gateway-dev \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/multimodal/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
    --set provider.name=${PROVIDER_NAME} \
    --set httpRoute.create=true \
    --set httpRoute.inferenceGatewayName=llm-d-inference-gateway \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

</details>

### 2. Deploy the Model Server

Apply the Kustomize overlays for your specific backend (defaulting to NVIDIA GPU / vLLM):

```bash
export INFRA_PROVIDER=base # base | gke
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/multimodal/${GUIDE_NAME}/modelserver/gpu/vllm/${INFRA_PROVIDER}/
```

### 3. (Optional) Enable monitoring

> [!NOTE]
> GKE provides [automatic application monitoring](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/configure-automatic-application-monitoring) out of the box. The llm-d [Monitoring stack](../../docs/monitoring/README.md) is not required for GKE, but it is available if you prefer to use it.

- Install the [Monitoring stack](../../docs/monitoring/README.md).
- Deploy the monitoring resources for this guide:
   ```bash
   kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring
   ```

---

## What This Guide Configures

| Component                              | Role                                                                                                                  |
| -------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| Decode pods                            | Run the multimodal model. The guide deploys 4 replicas; the affinity behavior requires 2 or more to observe.          |
| Endpoint Picker (EPP)                  | Routes requests across the decode pool. Hosts the plugin profile below.                                               |
| `token-producer` (vllm.url mode)       | Calls vLLM `/v1/chat/completions/render` on the loopback render sidecar to produce token IDs and multimodal features. |
| `mm-embeddings-cache-producer`         | Maintains a per-pod LRU keyed on multimodal item hashes. On each request, asynchronously records the chosen pod.      |
| `mm-embeddings-cache-scorer`           | Scores each candidate pod by how many of the request's multimodal items the pod already holds.                        |
| Render endpoint                        | Provides `/v1/chat/completions/render` inside the EPP pod (sidecar). Required for precise affinity.                   |
| `max-score-picker`                     | Picks the highest-scored endpoint. On ties (cold cache, first request) picks at random.                               |

The relevant EPP plugin profile lives at `router/precise-affinity.values.yaml`. The two parameters most likely to need tuning per workload:

- `cacheSizeInMBPerServer` on the producer: the per-pod LRU memory budget. 2048 MiB is a reasonable starting point; raise for high-cardinality workloads.
- `mm-embeddings-cache-scorer` weight in the profile: 10 makes multimodal affinity the primary routing signal. Lower when blending with other scorers; raise to make affinity decisive.

---

## Verification

### 1. Retrieve the Proxy Endpoint IP

#### Standalone Mode
```bash
kubectl port-forward svc/${GUIDE_NAME} -n ${NAMESPACE} 8080:80
export IP=127.0.0.1:8080
```

<details>
<summary><h4>Gateway Mode</h4></summary>

```bash
export IP=$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')
```

</details>

### 2. Send a Multimodal Test Request

Open a debug container within the cluster namespace:
```bash
kubectl run curl-debug --rm -it \
    --image=cfmanteiga/alpine-bash-curl-jq \
    --env="IP=$IP" \
    --env="NAMESPACE=$NAMESPACE" \
    -- /bin/bash
```

Send the same chat completion request twice. Both responses should come from the same decode pod:
```bash
REQ='{
  "model": "Qwen/Qwen3-VL-32B-Instruct",
  "messages": [{
    "role": "user",
    "content": [
      {"type": "text", "text": "What details are present in this photo?"},
      {"type": "image_url", "image_url": {"url": "https://picsum.photos/id/237/640/360"}}
    ]
  }]
}'

curl -si -X POST http://${IP}/v1/chat/completions -H 'Content-Type: application/json' -d "$REQ" | grep -i x-inference-pod
curl -si -X POST http://${IP}/v1/chat/completions -H 'Content-Type: application/json' -d "$REQ" | grep -i x-inference-pod
```

The first request picks a pod at random (cold cache, all endpoints tie at score 0). The second request, with the same image URL, should route to the same pod (score 1.0 on that pod, 0 elsewhere).

### 3. Inspect the Cache-Hit Metric

The producer emits a per-modality, per-pod counter that records cache hits at routing decision time:
```bash
kubectl exec -n ${NAMESPACE} \
    $(kubectl get pods -n ${NAMESPACE} -l app=${GUIDE_NAME} -o name | head -1) \
    -- curl -s localhost:9090/metrics | grep llm_d_router_epp_encoder_cache_hits_total
```

Expected after the two-request sequence above: at least one series with label `pod="<pod-that-served-the-first-request>"` and value `>= 1`. Full label set is `{plugin_type, plugin_name, pod, modality}`; the companion `llm_d_router_epp_encoder_cache_queries_total` carries `{plugin_type, plugin_name, modality}` (no `pod`) and increments on every routing decision regardless of hit.

### 4. Inspect the Routing Trace (optional)

If the [Monitoring stack](../../docs/monitoring/README.md) is enabled, the scorer emits an OpenTelemetry span `llm_d.epp.scorer.prefix_cache` with these attributes:

| Attribute        | Meaning                                                                                |
| ---------------- | -------------------------------------------------------------------------------------- |
| `mm.modality`    | Comma-separated modalities on the request (`image`, `audio`, `video`, or `none`)       |
| `mm.hash_count`  | Number of multimodal items the request carries                                         |
| `mm.hit`         | `true` if at least one item matched in the chosen pod's cache                          |

In a trace explorer (Jaeger, Tempo, console exporter), filter spans where `mm.hit=true` to confirm the affinity path fires on repeated content.

---

## Cleanup

```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/multimodal/${GUIDE_NAME}/modelserver/gpu/vllm/${INFRA_PROVIDER}/
kubectl delete namespace ${NAMESPACE}
```

---

## Troubleshooting

### `llm_d_router_epp_encoder_cache_hits_total` stays at zero on repeated content

Most common cause: the render sidecar is not producing multimodal features.

- Confirm the EPP pod has a `vllm-render` sidecar container in `Running` state.
- Confirm the sidecar's port matches `vllm.url` in `token-producer` config (default `http://localhost:8000`).
- Curl the sidecar from within the EPP pod: `kubectl exec ... -- curl -s http://localhost:8000/health` should return 200.
- Check EPP logs for `tokenization failed: vLLM render returned status NNN`. A `404` means the route is wrong; a `400` means the request body is malformed; a `500` means the render model is misconfigured.

### Cache hits register but routing remains flat across pods

The scorer fires but isn't decisive.

- The `mm-embeddings-cache-scorer` weight is too low relative to other scorers. Raise the weight, or remove competing scorers from the profile.
- A second affinity signal (text-prefix-cache scorer, session-affinity scorer) may be dominating. Confirm by inspecting the trace: if `llm_d.epp.scorer.prefix_cache.score.max` is consistently 1.0 across all requests, the picker is at a hard ceiling and a tiebreak elsewhere is choosing.

### `mm.modality` shows `none` despite a multimodal request

The render endpoint returned no `mm_features`.

- For a text-only model (e.g. `Qwen3-32B` without `-VL`), the render endpoint emits no `mm_features` even when the request carries image blocks. Confirm the served model is multimodal.
- For an older render endpoint, the `input_audio` and `video_url` block types may not be supported. Confirm the render image version supports the modality you are sending.

### Cache hit rate much lower than expected

If the cache hit rate is well below the workload's shared-content fraction, the per-pod LRU is too small for the content cardinality. Raise `cacheSizeInMBPerServer` in `router/precise-affinity.values.yaml`.

---

## Benchmarking Report

> [!NOTE]
> Benchmarks for this guide are produced by the `benchmark-templates/` workload definitions and stored under `benchmark-results/`. The numbers below are placeholders pending the cluster benchmark run.

Benchmark plan:

- Hardware: 8 × H200 across 4 decode replicas (TP=2 per replica)
- Workload: synthetic via inference-perf with a configurable shared-image fraction (10%, 50%, 90%)
- Comparison: precise-affinity (this guide) vs multimodal optimized-baseline (sibling) vs stock Kubernetes Service
- Metrics: throughput (output tokens/sec) at fixed QPS, TTFT (mean and p90), per-pod cache hit rate

When the run lands, this section will mirror the optimized-baseline guide's plot+table format.
