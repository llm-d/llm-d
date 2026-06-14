# Multi-Model Routing

## Overview

This guide deploys the **Inference Payload Processor (IPP)** to enable serving multiple LLMs behind a single Gateway endpoint. IPP extracts the model name from the request body and sets routing headers. HTTPRoutes then match these headers to direct traffic to the appropriate InferencePool.

Use this guide when you need to:

* Serve multiple base models (e.g., Qwen for chatbots, DeepSeek for reasoning)
* Provide a unified API endpoint following the OpenAI specification

For LoRA adapter routing, see [Advanced: LoRA Adapter Routing](#advanced-lora-adapter-routing) after completing the base setup.

For simpler single-model deployments, see the [Optimized Baseline](../optimized-baseline/README.md) guide instead.

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
  export GUIDE_NAME="multi-model-routing"
  export NAMESPACE="llm-d-multi-model"
  ```

* Install the Gateway API Inference Extension CRDs:

  ```bash
  kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GAIE_VERSION}/v1-manifests.yaml
  ```

* Create a target namespace for the installation:

  ```bash
  kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
  ```

* [Create the `llm-d-hf-token` secret in your target namespace](../../helpers/hf-token.md) with a valid HuggingFace token.

* **Multiple InferencePools deployed**, each serving a different base model. Follow the [Optimized Baseline](../optimized-baseline/README.md) guide for each pool, or [Multi-Inference Pool Setup](../workload-autoscaling/README.multi-inference-pool.md) for adding pools to an existing deployment.

  > [!IMPORTANT]
  > When deploying InferencePools for this guide, do **not** use `--set httpRoute.create=true`. This guide's HTTPRoutes (Step 3) handle routing based on model name headers. Pool-level catch-all routes would conflict with header-based routing.

* A Kubernetes Gateway (e.g., Istio, GKE, AgentGateway) deployed in your cluster. See [Gateway Infrastructure](../../docs/infrastructure/gateway/README.md).

## Step 1: Deploy IPP

Clone the IPP repository and deploy using Helm:

```bash
# Clone the IPP repository
git clone https://github.com/llm-d/llm-d-inference-payload-processor.git /tmp/ipp

# Install IPP (for Istio)
helm install ipp /tmp/ipp/config/charts/payload-processor \
    --set provider.name=istio \
    --set inferenceGateway.name=llm-d-inference-gateway \
    --set payloadProcessor.image.tag=v0.1.0-rc.4 \
    -n ${NAMESPACE}
```

> [!NOTE]
> For GKE, use `--set provider.name=gke` instead of `istio`.
> For standalone (no gateway), omit the `provider.name` flag.

Verify IPP is running:

```bash
kubectl get pods -n ${NAMESPACE} -l app=payload-processor
```

## Step 2: Create Model Mapping ConfigMaps

Create ConfigMaps that register each base model with IPP. Review and customize [`manifests/configmaps.yaml`](manifests/configmaps.yaml) for your models, then apply:

```bash
kubectl apply -n ${NAMESPACE} -f ${REPO_ROOT}/guides/multi-model-routing/manifests/configmaps.yaml
```

Each ConfigMap must have the label `inference.llm-d.ai/ipp-managed: "true"` and specify a `baseModel` value matching the model name in API requests.

> [!IMPORTANT]
> All model names must be globally unique across all InferencePools.

## Step 3: Configure HTTPRoutes

Create HTTPRoutes that match on the `X-Gateway-Base-Model-Name` header injected by IPP. Review and customize [`manifests/httproutes.yaml`](manifests/httproutes.yaml) for your setup:

- Update `spec.parentRefs[0].name` to match your Gateway name
- Update `backendRefs[0].name` to match your InferencePool names
- Ensure the header `value` matches the `baseModel` in the corresponding ConfigMap

```bash
kubectl apply -n ${NAMESPACE} -f ${REPO_ROOT}/guides/multi-model-routing/manifests/httproutes.yaml
```

## Step 4: Test the Deployment

Get the Gateway IP and send test requests:

```bash
export GATEWAY_IP=$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')

# Request to Qwen base model
curl -X POST "http://${GATEWAY_IP}/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{"model": "Qwen/Qwen3-32B", "messages": [{"role": "user", "content": "Hello"}]}'

# Request to DeepSeek base model
curl -X POST "http://${GATEWAY_IP}/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{"model": "deepseek/DeepSeek-r1", "messages": [{"role": "user", "content": "Solve this problem"}]}'
```

## Troubleshooting

### Check IPP Logs

```bash
kubectl logs -n ${NAMESPACE} -l app=payload-processor --tail=100
```

### Verify ConfigMap Discovery

IPP should log discovered model mappings at startup. Check that your ConfigMaps have the required label:

```bash
kubectl get configmap -l inference.llm-d.ai/ipp-managed=true -n ${NAMESPACE}
```

### Verify Routing

Check EPP logs to confirm requests reach the correct pool (replace `<pool-name>` with your InferencePool name):

```bash
kubectl logs -n ${NAMESPACE} -l llm-d-router-gateway=<pool-name>-epp --tail=20
```

## Cleanup

```bash
# Remove HTTPRoutes and ConfigMaps
kubectl delete -n ${NAMESPACE} -f ${REPO_ROOT}/guides/multi-model-routing/manifests/httproutes.yaml
kubectl delete -n ${NAMESPACE} -f ${REPO_ROOT}/guides/multi-model-routing/manifests/configmaps.yaml

# Remove IPP
helm uninstall ipp -n ${NAMESPACE}

# Clean up cloned IPP repo
rm -rf /tmp/ipp

# Remove namespace (if no longer needed)
kubectl delete namespace ${NAMESPACE}
```

## Advanced: LoRA Adapter Routing

Once base model routing is working, you can extend ConfigMaps to route LoRA adapter requests to their base model's InferencePool.

Add the `adapters` field to your ConfigMaps listing the LoRA adapter names:

```bash
kubectl apply -n ${NAMESPACE} -f - <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: qwen-model-mapping
  labels:
    inference.llm-d.ai/ipp-managed: "true"
data:
  baseModel: "Qwen/Qwen3-32B"
  adapters: |
    - food-review-1
    - travel-assistant
EOF
```

When a request comes in with `"model": "food-review-1"`, IPP looks up which base model owns that adapter and sets `X-Gateway-Base-Model-Name: Qwen/Qwen3-32B`. The HTTPRoute then routes the request to the Qwen pool.

**Test LoRA routing:**

```bash
curl -X POST "http://${GATEWAY_IP}/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{"model": "food-review-1", "messages": [{"role": "user", "content": "Review this restaurant"}]}'
```

> [!IMPORTANT]
> All adapter names must be globally unique across all ConfigMaps.

## Further Reading

* [Multi-Model Routing Capability](../../docs/well-lit-paths/foundations/multi-model-routing.md) — High-level overview and architecture
* [IPP Architecture](../../docs/architecture/advanced/inference-payload-processing/README.md) — Technical details of the Inference Payload Processor
* [IPP Repository](https://github.com/llm-d/llm-d-inference-payload-processor) — Source code, configuration reference, and plugin documentation
