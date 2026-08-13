# Flow Control

[![E2E (GKE GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-flow-control-gke-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-flow-control-gke-acc-gpu-vllm-x.yaml)

## Overview

Flow Control enables intelligent request queuing at the llm-d Router level. Traditional load balancing falls short for LLMs because resource consumption varies wildly per request. Shifting queuing to the Router enables:

* **Multi-Tenancy**: Prevent noisy neighbors from starving others and enforce fairness between tenants.
* **No-Regret Scheduling**: Hold requests during peak saturation instead of committing them to a server's local queue where they become stuck.

### How it Works

Incoming requests are classified by a `FlowKey` (Fairness ID + Priority). EPP maintains separate in-memory queues for each flow and dispatches them based on:

1. **Priority**: Servicing highest priority bands first.
2. **Fairness**: Cycling through tenants within a band.
3. **Ordering**: Ordering requests within a flow.

*While Backpressure protects the physical hardware from overload, the Multi-Tenancy policies dictate exactly how that delayed traffic is ordered and distributed among your users.*

The following diagram illustrates the centralized queuing topology:

```mermaid
flowchart TD
    TenantA["Tenant A (Pri: 100)"] --> QA[("Queue: Tenant A<br/>Band: 100")]
    TenantB["Tenant B (Pri: 0)"] --> QB[("Queue: Tenant B<br/>Band: 0")]
    TenantC["Tenant C (Pri: -10)"] --> QC[("Queue: Tenant C<br/>Band: -10")]

    QA --> Disp{Dispatcher}
    QB --> Disp
    QC --> Disp

    Disp --> Pool["Model Server Pool"]
```

## Default Configuration

The following default hardware configuration is inherited from the [Optimized Baseline](../optimized-baseline/README.md):

| Parameter          | Value                                                   |
| ------------------ | ------------------------------------------------------- |
| Model              | [Qwen/Qwen3-32B](https://huggingface.co/Qwen/Qwen3-32B) |
| Replicas           | 8                                                       |
| Tensor Parallelism | 2                                                       |
| GPUs per replica   | 2                                                       |
| Total GPUs         | 16                                                      |

When the `flowControl` feature gate is enabled, the EPP uses the following policies by default. These defaults are explicitly designed to mimic legacy, non-flow-control behavior (Strict FCFS) to ensure a seamless transition for existing workloads.

| Policy Type | Default Plugin | Description |
| :--- | :--- | :--- |
| **Fairness** | `global-strict-fairness-policy` | Ignores flow isolation and serves all requests in a single global order. |
| **Ordering** | `fcfs-ordering-policy` | First-Come, First-Served based on arrival time. |
| **Saturation** | `utilization-detector` | Closed-loop detector reacting to real-time telemetry. |

> [!NOTE]
>
> * Beneath the flow control layer, this guide uses the exact same `prefix-cache-scorer` and `load-aware` routing policies established in the [Optimized Baseline](../optimized-baseline/README.md). Flow control acts as an intelligent ingress layer that holds saturated traffic *before* it passes to the scheduler.
> * While `utilization-detector` is the out-of-the-box system default listed here, production deployments should switch to `concurrency-detector` to avoid telemetry lag risks, as detailed in the [Tuning Guide](tuning.md).

By default, the EPP uses a `global-strict` policy. Because the system is **work-conserving**, it will never artificially throttle traffic if GPUs have spare capacity. However, enforcing strict fairness (like Round-Robin) during periods of saturation constrains the scheduler's ability to pick the globally optimal request for batching or cache reuse, thereby bounding the maximum explorable latency-throughput frontier. The default prioritizes absolute global throughput, while this guide overrides it to prioritize tenant equity.

### Supported Hardware Backends

Flow Control is a software-level scheduling feature at the EPP layer and is entirely hardware-agnostic. It supports all accelerators detailed in the [Optimized Baseline guide](../optimized-baseline/README.md#supported-hardware-backends). Since this guide builds exactly on top of that baseline, we will dynamically deploy the baseline's model servers in the steps below rather than maintaining duplicate configurations.

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
  export GUIDE_NAME="flow-control"
  export NAMESPACE="llm-d-flow-control"
  export MODEL_NAME="Qwen/Qwen3-32B"
  ```

* Install the required CRDs (GAIE InferencePool + llm-d.ai InferenceObjective):

  ```bash
  # GAIE_VERSION provided by ${REPO_ROOT}/guides/env.sh
  if [ "$GAIE_VERSION" = "latest" ]; then
    kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/latest/download/v1-manifests.yaml
  else
    kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GAIE_VERSION}/v1-manifests.yaml
  fi

  # ROUTER_RELEASE_VERSION provided by ${REPO_ROOT}/guides/env.sh
  if [ "$ROUTER_RELEASE_VERSION" = "latest" ]; then
    kubectl apply -f https://github.com/llm-d/llm-d-router/releases/latest/download/manifests.yaml
  else
    kubectl apply -f https://github.com/llm-d/llm-d-router/releases/download/${ROUTER_RELEASE_VERSION}/manifests.yaml
  fi
  ```

* Create a target namespace for the installation:

  ```bash
  kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
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

## Installation Instructions

### 1. Deploy the Router

#### Standalone Mode

This deploys the router with an Envoy sidecar, it doesn't set up a Kubernetes Gateway.

```bash
helm upgrade --install ${GUIDE_NAME} \
    ${ROUTER_STANDALONE_CHART} \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

The router pod's resource requests are set in
[base.values.yaml](../recipes/router/base.values.yaml): 4 vCPU and 8 GiB of memory for
each of the EPP and Envoy containers, so 8 vCPU and 16 GiB per pod. A pod stuck `Pending`
with `Insufficient cpu` needs a node with the pod's full CPU request allocatable.

<details>
<summary><h4>Gateway Mode</h4></summary>

To use a Kubernetes Gateway managed proxy rather than the standalone version, follow these steps instead of applying the previous Helm chart:

1. *Deploy a Kubernetes Gateway* named by following one of [the gateway guides](../../docs/infrastructure/gateway).
2. *Deploy the router and an HTTPRoute* that connects it to the Gateway as follows:

```bash
export PROVIDER_NAME=gke # options: none, gke, agentgateway, istio
helm upgrade --install ${GUIDE_NAME} \
    ${ROUTER_GATEWAY_CHART}  \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/recipes/router/features/httproute-flags.yaml \
    -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
    --set provider.name=${PROVIDER_NAME} \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

</details>

### 2. Deploy the Model Server

Instead of maintaining duplicate hardware configurations, we dynamically render the model server manifests from the `optimized-baseline` guide and inject the `flow-control` guide labels using `sed`.

Deploy the model server (defaulting to NVIDIA GPU / vLLM) by running:

```bash
export INFRA_PROVIDER=base # base | gke
kubectl kustomize ${REPO_ROOT}/guides/optimized-baseline/modelserver/gpu/vllm/${INFRA_PROVIDER}/ \
  | sed "s/optimized-baseline/${GUIDE_NAME}/g" \
  | kubectl apply -n ${NAMESPACE} -f -
```

### 3. Enable monitoring (optional)

If you want monitoring, decide before step 1: the monitoring values create a
ServiceMonitor, whose CRD only exists after the monitoring stack is installed.

* Install the [Monitoring stack](../../docs/operations/observability/setup.md) **before deploying the router**.
* Add `-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml` to the [router installation command](#1-deploy-the-router). If you already installed without it, re-run the same `helm upgrade --install` command with the extra `-f` appended.
* Deploy the monitoring resources for model servers:

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring
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

### 2. Basic Verification

Check EPP logs for feature gate activation:

```bash
# -c epp: in standalone mode kubectl defaults to the Envoy sidecar, whose log never matches
kubectl logs deploy/${GUIDE_NAME}-epp -c epp -n ${NAMESPACE} | grep "Initializing Flow Control layer"
```

Expected: one line, `Initializing Flow Control layer`. No output means the gate is off for
this deployment (the EPP then logs `Flow Control layer is disabled` instead) or the log
format changed; the stronger check is that `llm_d_epp_flow_control_*` series exist on the
metrics endpoint (see [Proof of Queuing](#3-proof-of-queuing)).

### 3. Proof of Queuing

To fully verify that queuing and backpressure are working, you must apply concurrent load; [Use Case 2](#use-case-2-backpressure-management) does that with a burst of concurrent requests. For now, set up the test environment.

**Read `maxConcurrency` from [router/flow-control.values.yaml](./router/flow-control.values.yaml).**
The Use Case 2 load test sizes its burst from `MAX_CONCURRENCY`; a retuned values file
changes the burst with it:

```bash
MAX_CONCURRENCY=$(awk '$1 == "maxConcurrency:" {print $2; n++} END {exit n!=1}' \
    ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml) \
  || echo "expected exactly one maxConcurrency: in the values file" >&2
export MAX_CONCURRENCY
echo "maxConcurrency: ${MAX_CONCURRENCY}"   # expect the integer set in the values file
```

**Grant read access to the EPP metrics endpoint.** The EPP authenticates every metrics
scrape against the Kubernetes API: a TokenReview on the caller's bearer token, then a
SubjectAccessReview on the `/metrics` URL. Give the debug pod's service account
permission to read `/metrics`, and give the EPP's service account the
`system:auth-delegator` role it needs to run the reviews. The chart ships an equivalent
grant only when `router.monitoring.prometheus.enabled` is set, which also creates a
ServiceMonitor and requires the Prometheus Operator CRDs; this guide leaves that flag
off and creates the binding directly:

```bash
kubectl create clusterrole ${GUIDE_NAME}-metrics-reader \
    --verb=get --non-resource-url=/metrics \
    --dry-run=client -o yaml | kubectl apply -f -
kubectl create clusterrolebinding ${GUIDE_NAME}-metrics-reader \
    --clusterrole=${GUIDE_NAME}-metrics-reader \
    --serviceaccount=${NAMESPACE}:default \
    --dry-run=client -o yaml | kubectl apply -f -
kubectl create clusterrolebinding ${GUIDE_NAME}-epp-auth-delegator \
    --clusterrole=system:auth-delegator \
    --serviceaccount=${NAMESPACE}:${GUIDE_NAME}-epp \
    --dry-run=client -o yaml | kubectl apply -f -
```

Without these grants the metrics endpoint returns `401 Unauthorized`, and every
metrics check in this guide reads as empty grep output.

**Open a temporary interactive shell inside the cluster:**

```bash
kubectl run curl-debug --rm -it \
    --image=cfmanteiga/alpine-bash-curl-jq \
    --namespace="$NAMESPACE" \
    --env="IP=$IP" \
    --env="NAMESPACE=$NAMESPACE" \
    --env="GUIDE_NAME=$GUIDE_NAME" \
    --env="MODEL_NAME=$MODEL_NAME" \
    --env="MAX_CONCURRENCY=$MAX_CONCURRENCY" \
    -- /bin/bash
```

**From inside the debug pod, check the metrics.** The pod's automounted service account
token authenticates the scrape:

```bash
TOKEN=$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)
curl -s -o metrics.txt -w "%{http_code}\n" -H "Authorization: Bearer ${TOKEN}" \
  http://${GUIDE_NAME}-epp:9090/metrics   # expect: 200
grep llm_d_epp_flow_control_queue_size metrics.txt
```

Expected: one series per active flow (tenant × priority), all `0` on an idle pool. A
flow's series appears after its first request queues, so a quiet pool may show none.
A `401` means the metrics-access grants above were skipped; a `500` means the EPP's
service account lacks the auth-delegator binding (the EPP log shows a failed
TokenReview).

## Use Cases

### Use Case 1: Multi-Tenancy (Model-as-a-Service)

In this use case, we configure 3 priority tiers (Premium, Standard, Best-Effort) and guarantee fairness between tenants within the same tier.

#### 1. Apply InferenceObjectives

The `helm upgrade --install` command you ran earlier configured the EPP's underlying `EndpointPickerConfig` to map queues to priority bands. However, you must explicitly define these bands in the cluster using `InferenceObjective` resources.

Apply the full definitions (Premium, Standard, Best-Effort) provided in [objectives.yaml](./objectives.yaml) by running:

```bash
kubectl apply -f ${REPO_ROOT}/guides/${GUIDE_NAME}/objectives.yaml -n ${NAMESPACE}
```

The file defines three priority tiers:

* **Premium** (priority 100): Highest priority.
* **Standard** (priority 0): Default priority.
* **Best-Effort** (priority -10): Admitted into queue but subject to strict band limits.

#### 2. Client Integration

Clients must send the appropriate headers to be placed in the correct queues.

**From inside the `curl-debug` pod**, send a completion request with headers:

```bash
curl -X POST http://${IP}/v1/completions \
  -H 'Content-Type: application/json' \
  -H 'x-llm-d-inference-fairness-id: tenant-a' \
  -H 'x-llm-d-inference-objective: premium-traffic' \
  -d "{
    \"model\": \"${MODEL_NAME}\",
    \"prompt\": \"Say hello\"
  }"
```

> [!WARNING]
> **Trust Boundary**: In a production system, allowing end-users to self-assert their tenant ID or traffic priority (`premium-traffic`) is an abuse vector.
>
> **Production Pattern**: Your ingress API Gateway (or an Envoy `ext_authz` filter) should be configured to automatically strip any incoming `x-llm-d-*` headers, plus the deprecated EPP-managed aliases listed in the [EPP HTTP headers reference](../../docs/api-reference/epp-http-headers.md), from external traffic. Gateway API Inference Extension (GAIE) endpoint picker protocol headers such as `x-gateway-destination-endpoint*` are not part of this stripping rule. After stripping, validate the user's API Key or JWT, extract their tier/tenant from the token claims, and securely inject the authoritative `x-llm-d-inference-fairness-id` and `x-llm-d-inference-objective` headers before passing the request to the EPP.

#### 3. Verify the Classification

Still inside the `curl-debug` pod, confirm the request was accounted under the premium
band:

```bash
TOKEN=$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)
curl -s -H "Authorization: Bearer ${TOKEN}" http://${GUIDE_NAME}-epp:9090/metrics \
  | grep 'llm_d_epp_flow_control_request_queue_duration_seconds_count' \
  | grep 'priority="100"'
```

Expected: a series labeled `fairness_id="tenant-a", priority="100"` with a count of at
least 1. If it is absent while a `priority="0"` series grows, the objective headers are
not being honored. Check that `objectives.yaml` is applied and that its `poolRef`
matches the InferencePool in the namespace (`kubectl get inferencepools -n ${NAMESPACE}`).

### Use Case 2: Backpressure Management

Backpressure management protects GPUs from context-thrashing and ensures predictable generation times by holding requests in the EPP when the pool is saturated.

Unlike legacy admission mode which immediately drops negative-priority requests when the pool is full, Flow Control safely buffers them. Load shedding is triggered strictly by memory protection boundaries—meaning a request is only rejected if its specific priority band hits its `maxRequests` limit, OR if the global limits (`maxRequests` or `maxBytes`) are breached.

#### Verification for Use Case 2

To verify backpressure management, you must overwhelm the pool's capacity. Because the system is work-conserving, a single request will dispatch immediately. We will fire a sustained burst of concurrent `curl` requests to trigger saturation.

1. **Create a payload that sustains load** (from inside the `curl-debug` pod). A short
   prompt with the default `max_tokens` completes in under a second and drains before any
   queue is observable, so build one that keeps each request busy for tens of seconds:

    ```bash
    awk 'BEGIN{s=""; for(i=0;i<1500;i++) s=s" " int(rand()*32000); print s}' \
      | jq -Rs "{model: \"${MODEL_NAME}\", prompt: ., max_tokens: 500, ignore_eos: true}" \
      > payload.json
    ```

2. **Fire a burst of Best-Effort requests in the background.** Queuing begins only once
   in-flight requests exceed the pool's dispatch capacity: `maxConcurrency` × ready
   model-server replicas. Scale the pool to a single replica so a shell loop can exceed
   that capacity, and record the current replica count for step 5 to restore. The debug
   pod has no kubectl, so run the scale commands from your host terminal:

    ```bash
    export ORIG_REPLICAS=$(kubectl get deployment -l llm-d.ai/guide=${GUIDE_NAME} \
        -n ${NAMESPACE} -o jsonpath='{.items[0].spec.replicas}')
    kubectl scale deployment -l llm-d.ai/guide=${GUIDE_NAME} -n ${NAMESPACE} --replicas=1
    ```

   Wait for the scale-down to settle. While more than one replica reports ready, dispatch
   capacity stays above `MAX_CONCURRENCY` and the burst drains without queuing:

    ```bash
    until [ "$(kubectl get deployment -l llm-d.ai/guide=${GUIDE_NAME} \
        -n ${NAMESPACE} -o jsonpath='{.items[0].status.readyReplicas}')" = "1" ]; do
      sleep 5
    done
    ```

   Back in the debug pod, confirm a single request succeeds before bursting; a failing
   payload would read as an empty queue in step 3. The request generates 500 tokens and
   takes several seconds on an unloaded pool:

    ```bash
    curl -s -o /dev/null -w "%{http_code}\n" --max-time 600 -X POST \
      -H "Content-Type: application/json" \
      -H "x-llm-d-inference-fairness-id: tenant-b" \
      -H "x-llm-d-inference-objective: best-effort-traffic" \
      -d @payload.json http://${IP}/v1/completions   # expect: 200
    ```

   Then fire the burst: `MAX_CONCURRENCY` requests dispatch immediately and `SURPLUS`
   requests queue. Keep `SURPLUS` below the best-effort band's `maxRequests` limit in
   [router/flow-control.values.yaml](./router/flow-control.values.yaml); past that limit
   the EPP rejects the overflow. The burst must still be running when you poll in
   step 3, so background each request (`&`):

    ```bash
    SURPLUS=20   # requests expected to queue; stay below the band's maxRequests
    BURST=$((MAX_CONCURRENCY + SURPLUS))
    for i in $(seq 1 ${BURST}); do
      curl -s -o /dev/null --max-time 600 -X POST \
        -H "Content-Type: application/json" \
        -H "x-llm-d-inference-fairness-id: tenant-b" \
        -H "x-llm-d-inference-objective: best-effort-traffic" \
        -d @payload.json http://${IP}/v1/completions &
    done
    ```

3. **Observe behavior while the load runs.** The surplus requests buffer in the
   `best-effort` priority band. Poll the queue-size metric for the duration of the load
   and record the peak:

    ```bash
    TOKEN=$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)
    PEAK=0
    for i in $(seq 1 30); do
      Q=$(curl -s -H "Authorization: Bearer ${TOKEN}" http://${GUIDE_NAME}-epp:9090/metrics \
        | awk '/llm_d_epp_flow_control_queue_size.*priority="-10"/ {s+=$2} END {print s+0}')
      [ "$Q" -gt "$PEAK" ] && PEAK=$Q
      sleep 1
    done
    echo "peak best-effort queue depth: ${PEAK} (expected ~${SURPLUS})"
    wait   # let the burst finish before moving on
    ```

    Expect a peak near `SURPLUS`: the burst minus the `MAX_CONCURRENCY` requests that
    dispatched immediately. A peak above 0 confirms the EPP queued the surplus. A peak
    of 0 usually means the metrics scrape failed auth (re-run the metrics check from
    [Proof of Queuing](#3-proof-of-queuing); expect 200), the poll missed the load
    window (the burst finished first; raise `max_tokens`), or the burst never exceeded
    dispatch capacity (more than one replica still ready; re-check the scale-down in
    step 2). Rule out all three causes before raising the concurrency and re-running.

    `wait` returns once the pool works through the burst: about 30 seconds on the
    reference workload's single replica, longer on slower pools. On a pool slow enough
    that queued requests wait past `defaultRequestTTL` (60s in
    [router/flow-control.values.yaml](./router/flow-control.values.yaml)), the EPP
    rejects them. The burst curls discard their responses, so a rejection leaves no
    output; the poll records the peak in the first seconds of the burst, before any TTL
    can expire.

4. **Exit the debug shell** once testing is complete to return to your host terminal:

    ```bash
    exit
    ```

5. **Restore the model server replica count** recorded in step 2 (model server pods take
   several minutes to become ready again):

    ```bash
    kubectl scale deployment -l llm-d.ai/guide=${GUIDE_NAME} -n ${NAMESPACE} --replicas=${ORIG_REPLICAS}
    ```

## Production Tuning: Deriving `maxConcurrency`

> [!IMPORTANT]
> The `maxConcurrency` value shipped in [router/flow-control.values.yaml](./router/flow-control.values.yaml) is empirically tuned **only** for the default reference workload (Qwen3-32B on 16 H100s). If you use a different model, hardware, or have different prompt lengths, you **must** calculate your own `maxConcurrency` to prevent GPU starvation or OOMs.

For detailed instructions on how to derive the optimal `maxConcurrency` for your specific workload, see the [Tuning Guide](tuning.md).

## Benchmarking

This guide uses [`llmdbenchmark`](https://github.com/llm-d/llm-d-benchmark) — the supported standard CLI for llm-d performance benchmarking.

In this example we will demonstrate how to run [`inference-perf`](https://github.com/kubernetes-sigs/inference-perf) with a generic load workload against the stack you just deployed above (standalone or gateway mode). When orchestrating benchmarks via `llmdbenchmark`, the CLI automatically and transparently deploys a harness pod (`llmdbench-harness-launcher`) into your namespace. This pod is central to driving the workload, collecting the results, and tearing itself down when it's finished.

> [!IMPORTANT]
> **For more in-depth explanation and features for benchmarking llm-d guides, see [`helpers/benchmark.md`](../../helpers/benchmark.md).**
>
> The Benchmarking section below contains only the **flow-control-specific commands** needed to drive the stack you just deployed — for everything else (and especially when something goes wrong), start at [`helpers/benchmark.md`](../../helpers/benchmark.md).
>
> For even more details about benchmarking, see the actual repository: [`llm-d-benchmark` on GitHub](https://github.com/llm-d/llm-d-benchmark).

> [!WARNING]
> Benchmarking flow-control's QoS differentiation and fairness behaviors requires a specialized multi-tenant load harness that does NOT yet ship in `llm-d-benchmark` or `inference-perf`. The command below exercises the stack under load and validates the end-to-end path, but it does NOT measure QoS slicing across priority classes or tenant isolation. A dedicated `guide_flow-control_1.yaml` will be added upstream once multi-tenant load shaping is supported.

> [!TIP]
> To run a simpler workload with fewer execution cycles first (useful for validating the path, image pulls, PVC binding, etc. before committing to a real run), pick a generic sample profile such as `shared_prefix_synthetic.yaml` from the catalog in [`helpers/benchmark.md` → Available workload profiles](../../helpers/benchmark.md#available-workload-profiles) and substitute it for the `--workload` flag in the command below.

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

### 3. Run the benchmark profile for Flow Control

Benchmark results are copied to the `workspace` directory that is specified by _you_ (or that is automatically generated when omitted from the cli) on the machine running the CLI. The workspace location is optional — by default the CLI auto-generates a timestamped workspace and prints its full path in the logs during the run. If you'd rather choose where results land, pass `--workspace <YOUR_DIR_HERE>` as a top-level argument of `llmdbenchmark` (before the `run` subcommand):

```bash
llmdbenchmark \
    --spec           guides/flow-control \
    run \
    --endpoint-url   "${ENDPOINT_URL}" \
    --gateway-class  "${GATEWAY_CLASS}" \
    --model          "${MODEL_NAME}" \
    --namespace      "${NAMESPACE}" \
    --harness        inference-perf \
    --workload       random_concurrent.yaml \
    --analyze
```

> [!NOTE]
> The harness pod requests 16 vCPU by default
> ([resource requirements](https://github.com/llm-d/llm-d-benchmark/blob/main/docs/resource_requirements.md)),
> and its `workload-pvc` requires a `ReadWriteMany`-capable StorageClass. If the run stalls on a `Pending` pod or a PVC
> timeout, see [Troubleshooting in `helpers/benchmark.md`](../../helpers/benchmark.md#troubleshooting)
> for the StorageClass override (including a GKE Filestore walkthrough) and the
> [timeout knobs](../../helpers/benchmark.md#timeouts).

## Observability

The Flow Control layer exposes detailed metrics to track queuing dynamics. Please refer to [flow control architecture](../../docs/architecture/core/router/epp/flow-control.md) for more details.

## Cleanup

To remove the deployed components:

```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
kubectl delete -f ${REPO_ROOT}/guides/${GUIDE_NAME}/objectives.yaml -n ${NAMESPACE}
export INFRA_PROVIDER=base # match the value used at deploy time
kubectl kustomize ${REPO_ROOT}/guides/optimized-baseline/modelserver/gpu/vllm/${INFRA_PROVIDER}/ \
  | sed "s/optimized-baseline/${GUIDE_NAME}/g" \
  | kubectl delete -n ${NAMESPACE} -f -
kubectl delete clusterrolebinding ${GUIDE_NAME}-metrics-reader ${GUIDE_NAME}-epp-auth-delegator
kubectl delete clusterrole ${GUIDE_NAME}-metrics-reader
```

If you ran the Benchmarking section, also delete the harness leftovers. The workload PVC
is backed by shared storage that bills until removed. Follow
[Cleaning up harness resources in `helpers/benchmark.md`](../../helpers/benchmark.md#cleaning-up-harness-resources)
with `<namespace>` set to `${NAMESPACE}`.

Afterward `kubectl get all,inferenceobjectives,pvc -n ${NAMESPACE}` returns
`No resources found` (the `llm-d-hf-token` secret remains).

## Further Reading

See [Flow Control architecture](../../docs/architecture/core/router/epp/flow-control.md) for full details of the design.
