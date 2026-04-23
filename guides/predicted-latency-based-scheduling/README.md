# Predicted Latency-Based Scheduling

Route each inference request to the model server predicted to serve it fastest — and, optionally, only to a server predicted to meet its TTFT/TPOT SLO.

This path is for operators who want to **adopt** predicted latency-based scheduling in an existing llm-d deployment. For what the component is and how it works internally — the plugin pipeline, the ML model, scaling characteristics, the full metric list — see [architecture/advanced/latency-predictor.md](../../docs/wip-docs-new/architecture/advanced/latency-predictor.md).

## When to Pick This Path

Pick it when:

- Your workload has **high variance in prompt and completion length**, and queue depth alone is a poor proxy for true load.
- Your clients can express **per-request latency SLOs** (interactive vs. batch) and you want the gateway to enforce them.
- Static weight tuning between cache affinity and load has become **fragile** as traffic shifts.

Skip it when your pool is **heterogeneous** — mixed GPU types, model variants, or serving configurations in the same pool will produce inaccurate predictions, because the predictor assumes a single pod shape.

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
    export GUIDE_NAME="predicted-latency-based-scheduling"
    export NAMESPACE=llm-d-predicted-latency
    export MODEL_NAME="Qwen/Qwen3-32B"
  ```
- Install the Gateway API Inference Extension CRDs:

  ```bash
    kubectl apply -k "https://github.com/kubernetes-sigs/gateway-api-inference-extension/config/crd?ref=${GAIE_VERSION}"
  ```
- Create a target namespace for the installation:

  ```bash
    kubectl create namespace ${NAMESPACE}
  ```

## Installation Instructions

### 1. Deploy the Inference Scheduler

Two ready-to-use values files ship with this guide:

| File | When to use |
|---|---|
| [`scheduler/predicted-latency.values.yaml`](./scheduler/predicted-latency.values.yaml) | Default — predictor trains on end-to-end latency. Routing-only, no SLO header support. |
| [`scheduler/predicted-latency-slo.values.yaml`](./scheduler/predicted-latency-slo.values.yaml) | SLO-aware — Assumes `x-slo-ttft-ms` / `x-slo-tpot-ms` are set on requests. Every request must be sent with `"stream": true`. |

Both target model server pods labeled `llm-d.ai/guide=optimized-baseline` since in the next step we will simply reuse the model server manifests from the [optimized-baseline guide](../optimized-baseline).

#### Standalone Mode

This deploys the inference scheduler with an Envoy sidecar, it doesn't set up a Kubernetes Gateway.

```bash
helm install ${GUIDE_NAME} \
    oci://registry.k8s.io/gateway-api-inference-extension/charts/standalone \
    -f guides/${GUIDE_NAME}/scheduler/predicted-latency.values.yaml \
    -n ${NAMESPACE} --version ${GAIE_VERSION}
```

For SLO-aware scheduling, swap the values file: `-f guides/${GUIDE_NAME}/scheduler/predicted-latency-slo.values.yaml`.

<details>
<summary><h4>Gateway Mode</h4></summary>

To use a Kubernetes Gateway managed proxy rather than the standalone version, follow these steps instead of applying the previous Helm chart:

1. *Deploy a Kubernetes Gateway* by following one of [the gateway guides](../prereq/gateways).
2. *Deploy the inference scheduler and an HTTPRoute* that connects it to the Gateway as follows:

```bash
export PROVIDER_NAME=gke # options: none, gke, agentgateway, istio
helm install ${GUIDE_NAME} \
    oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool \
    -f guides/${GUIDE_NAME}/scheduler/predicted-latency.values.yaml \
    --set provider.name=${PROVIDER_NAME} \
    --set experimentalHttpRoute.enabled=true \
    --set experimentalHttpRoute.inferenceGatewayName=llm-d-inference-gateway \
    -n ${NAMESPACE} --version ${GAIE_VERSION}
```

</details>

### 2. Deploy the Model Server

This guide reuses the model server manifests from the optimized-baseline guide (the values files above already select pods labeled `llm-d.ai/guide=optimized-baseline`). Apply the default NVIDIA GPU / vLLM overlay:

```bash
kubectl apply -n ${NAMESPACE} -k guides/optimized-baseline/modelserver/gpu/vllm/
```

For other backends (AMD GPU, Intel XPU, Gaudi, TPU, CPU), see [optimized-baseline → Deploy the Model Server](../optimized-baseline/README.md#2-deploy-the-model-server).

### 3. Enable monitoring (optional)

Follow [optimized-baseline → Enable monitoring](../optimized-baseline/README.md#3-enable-monitoring-optional) — the same steps apply since this guide reuses the same model server manifests.

## Send Requests

Once enabled, latency-based scheduling works on every request — no header changes needed. The proxy picks the endpoint with the lowest predicted latency.

To opt an individual request into SLO-aware routing, add one or both headers:

- `x-slo-ttft-ms` — Time-to-first-token SLO in milliseconds.
- `x-slo-tpot-ms` — Time-per-output-token SLO in milliseconds.

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

### 2. Send a Test Request

**Open a temporary interactive shell inside the cluster:**

```bash
kubectl run curl-debug --rm -it \
    --image=cfmanteiga/alpine-bash-curl-jq \
    --env="IP=$IP" \
    --env="NAMESPACE=$NAMESPACE" \
    --env="MODEL_NAME=$MODEL_NAME" \
    -- /bin/bash
```

**Send a completion request:**

```bash
curl -X POST http://${IP}/v1/completions \
    -H 'Content-Type: application/json' \
    -H 'x-slo-ttft-ms: 200' \
    -H 'x-slo-tpot-ms: 50' \
    -d '{
        "model": "'${MODEL_NAME}'",
        "prompt": "Explain the difference between prefill and decode.",
        "max_tokens": 200,
        "temperature": 0,
        "stream": true,
        "stream_options": {"include_usage": true}
    }'
```

Sheddable requests (priority < 0) are rejected at admission when no endpoint can meet the SLO, rather than routed to a guaranteed miss.

## Verify

Once traffic is flowing, confirm three things in Prometheus (see the [architecture doc](../../docs/wip-docs-new/architecture/advanced/latency-predictor.md#observability) for the metric reference):

1. **Predictions are being produced.** `inference_objective_request_ttft_prediction_duration_seconds` has non-zero samples. If it stays empty, the predictor sidecar is not being called — tail the EPP logs for `predicted-latency-producer` errors.
2. **Predictions track reality.** Compare `inference_objective_request_predicted_ttft_seconds` against `inference_objective_request_ttft_seconds` over a rolling window. A healthy deployment converges to within a few percent after warmup.
3. **SLOs are being honored.** If you're sending SLO-annotated traffic, `inference_objective_request_ttft_slo_violation_total` and `..._tpot_slo_violation_total` should increment only under genuine saturation.

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| Prediction duration metrics empty | Predictor sidecar unreachable — EPP falls back to composite heuristic scoring. Check sidecar readiness and `PREDICTION_SERVER_URL`. |
| Large, persistent drift between predicted and actual TTFT | `streamingMode` mismatch (set to `false` on a streaming workload, or vice versa), or workload drifted outside the training window. |
| High TPOT SLO violation rate at low QPS | `streamingMode: false` — TPOT is not being trained. Flip it to `true` and restart. |
| SLO violations cluster on a few pods during spikes | Scoring strategy is `least`; try `most` for more headroom at the cost of utilization. |
| Prediction-based routing degrades to baseline | Predictor error or sidecar restart — expected fallback, not a failure. Investigate sidecar logs. |

## Related

- [Latency Predictor Architecture](../../docs/wip-docs-new/architecture/advanced/latency-predictor.md) — plugin pipeline, ML model, scaling characteristics, metric reference.
- [llm-d/llm-d-inference-scheduler](https://github.com/llm-d/llm-d-inference-scheduler) — source for the EPP plugins and per-plugin configuration references.
- [llm-d/llm-d-latency-predictor](https://github.com/llm-d/llm-d-latency-predictor) — source for the training and prediction server Python code.
- [Predicted Latency-Based Scheduling for LLMs](https://llm-d.ai/blog/predicted-latency-based-scheduling-for-llms) — design rationale and benchmark results.
