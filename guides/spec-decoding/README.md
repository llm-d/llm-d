# Speculative Decoding

## Overview

This guide deploys a target model together with a
[speculators](https://github.com/vllm-project/speculators)-trained **drafter** and serves them
with llm-d. See the [speculative decoding foundation](../../docs/well-lit-paths/foundations/spec-decoding.md)
for the conceptual background. Speculative decoding is a **lossless** latency optimization: the small drafter
proposes several tokens ahead and the target (verifier) checks them in a single forward pass, so
accepted tokens skip decode steps. Every accepted token is exactly what the target would have
produced on its own — output quality is unchanged.

Speculative decoding is a property of **how vLLM is launched** — it is orthogonal to routing.
This guide therefore reuses the [Optimized Baseline](../optimized-baseline/README.md) EPP/router
configuration unchanged; the only spec-decoding-specific file is the model server's
`patch-vllm.yaml`, which adds `--speculative-config`.

> [!IMPORTANT]
> Speculative decoding wins in the **memory-bound, low-QPS regime** — vLLM's docs note it reduces
> "inter-token latency under medium-to-low QPS, memory-bound workloads." It trades spare compute
> for lower latency, so the benefit is largest at low concurrency and **erodes (aggregate
> throughput can regress) as load rises and the verifier becomes compute-bound.** Benchmark on
> your own traffic and find the crossover before enabling it in production. See
> [Benchmarking](#benchmarking).

## Configuration

| Parameter               | Default                                                                 |
| ----------------------- | ----------------------------------------------------------------------- |
| Target (verifier) model | [Qwen/Qwen3-32B](https://huggingface.co/Qwen/Qwen3-32B)               |
| Drafter (speculator)    | [RedHatAI/Qwen3-32B-speculator.eagle3](https://huggingface.co/RedHatAI/Qwen3-32B-speculator.eagle3) |
| Method                  | `eagle3`                                                                |
| `num_speculative_tokens`| `3`                                                                     |
| Replicas                | 1                                                                       |
| Tensor Parallelism      | 2                                                                       |
| GPUs per replica        | 2                                                                       |

This guide currently targets **NVIDIA GPU** only. The manifests are tested on H200; adapt
resource requests for other GPU SKUs.

Token acceptance rates are workload-dependent: code and math workloads see ~**2.5–3.0** accepted
tokens per step with EAGLE-3 on Qwen3-32B; chat/summarization workloads see lower rates (~**2.1–2.3**).
To try a different speculator, swap the drafter and `method`/`num_speculative_tokens` in
[`modelserver/gpu/vllm/eagle3/patch-vllm.yaml`](./modelserver/gpu/vllm/eagle3/patch-vllm.yaml).

> [!NOTE]
> **Choosing a speculator:** This guide uses EAGLE-3, which is in mainline vLLM. Alternative
> methods include DFlash and DSpark — see the
> [RedHatAI speculators collection](https://huggingface.co/collections/RedHatAI/speculator-models-68c39684ac2649111619f068)
> for compatible drafters and their published acceptance rates.

## Prerequisites

- The [proper client tools installed](../../helpers/client-setup/README.md).
- A Kubernetes cluster with NVIDIA GPU scheduling.
- Checkout llm-d and set environment variables:

  ```bash
  export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
  source ${REPO_ROOT}/guides/env.sh
  export GUIDE_NAME="spec-decoding"
  export NAMESPACE=llm-d-spec-decoding
  ```

- Install the Gateway API Inference Extension CRDs:

  ```bash
  kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GAIE_VERSION}/v1-manifests.yaml
  ```

- Create the namespace and the HuggingFace token secret:

  ```bash
  kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
  ```
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

### 1. Deploy the llm-d Router (Standalone Mode)

```bash
helm install ${GUIDE_NAME} \
    ${ROUTER_STANDALONE_CHART} \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

### 2. Deploy the Model Server (target + EAGLE-3 drafter)

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/eagle3/
```

### 3. (Optional but recommended) Enable monitoring

The acceptance metrics reported below come from vLLM's Prometheus counters, so enable monitoring
if you intend to benchmark:

- Install the [Monitoring stack](../../docs/operations/observability/setup.md).
- Add `-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml` to the router
  install above, and deploy the model-server monitoring resources:

  ```bash
  kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring
  ```

## Verification

### 1. Get the endpoint IP

```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```

### 2. Send a test request

```bash
kubectl run curl-debug --rm -it \
    --image=cfmanteiga/alpine-bash-curl-jq \
    --namespace="$NAMESPACE" --env="IP=$IP" -- /bin/bash

curl -X POST http://${IP}/v1/completions \
    -H 'Content-Type: application/json' \
    -d '{"model": "Qwen/Qwen3-32B", "prompt": "Write a Python function to reverse a linked list."}' | jq
```

### 3. Confirm speculative decoding is active

Check the model server's spec-decode Prometheus metrics:

```bash
POD=$(kubectl get pod -n ${NAMESPACE} -l llm-d.ai/guide=spec-decoding -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n ${NAMESPACE} ${POD} -c modelserver -- \
  sh -c 'curl -s localhost:8000/metrics | grep vllm:spec_decode'
```

You should see `vllm:spec_decode_num_accepted_tokens_total`,
`vllm:spec_decode_num_draft_tokens_total`, and `vllm:spec_decode_num_drafts` climbing.

## Benchmarking

The point of this guide is not "does it serve" — it is "**does spec decoding help, and until what
load?**" That is a two-arm experiment run **through the deployed llm-d stack** with
[`llmdbenchmark`](../../helpers/benchmark.md), not raw vLLM.

**Arms** (identical hardware and router/EPP values; only the model-server overlay differs):

| Arm | Model server overlay | Spec decoding |
| --- | -------------------- | ------------- |
| A (baseline) | `optimized-baseline/modelserver/gpu/vllm/base` (Qwen3-32B, replicas 1, TP 2) | off |
| B (spec) | `spec-decoding/modelserver/gpu/vllm/eagle3` | on |

**Metrics** (all captured by the harness per [`helpers/benchmark.md`](../../helpers/benchmark.md)),
plus acceptance from the Prometheus counters above:

- **ITL / TPOT** — the headline win (accepted drafts skip decode steps).
- **Token acceptance length** = `spec_decode_num_accepted_tokens_total / spec_decode_num_drafts + 1`
  (per-position from `..._per_pos`). The quality signal that explains the ITL win.
- **TTFT** — expected unchanged (prefill metric); report to prove no regression.
- **Aggregate throughput** — watch for the **crossover** where the win erodes.

**Run** the benchmark — a low-QPS-emphasis ladder that sweeps the crossover region:

```bash
export ENDPOINT_URL="http://$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')"
export GATEWAY_CLASS=epponly

llmdbenchmark \
    --spec          guides/spec-decoding \
    run \
    --endpoint-url  "${ENDPOINT_URL}" \
    --gateway-class "${GATEWAY_CLASS}" \
    --model         "Qwen/Qwen3-32B" \
    --namespace     "${NAMESPACE}" \
    --harness       inference-perf \
    --workload      guide_spec-decoding_1.yaml \
    --analyze
```

Repeat against Arm A (baseline) and overlay the results. See the
[benchmark report](./benchmark-results/vllm-qwen3-32b-eagle3/README.md) for the full
comparison (TPOT-vs-QPS, throughput-vs-QPS, crossover analysis).

## Cleanup

```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/eagle3
kubectl delete namespace ${NAMESPACE}
```
