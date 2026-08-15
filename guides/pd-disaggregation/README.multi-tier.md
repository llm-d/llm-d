# P/D Disaggregation with Multi Tier KV Cache

> **Experimental:** P/D Multi Tier KV cache support is experimental. Validate
> correctness, resource sizing, and performance on the target topology before
> using it for production traffic.

This variant extends the [P/D disaggregation guide](./README.md) with vLLM
Multi Tier KV-cache offloading. NIXL remains responsible for the
prefill-to-decode transfer. On each prefiller, `MultiConnector` composes:

- `NixlConnector` for the normal P/D handoff.
- `OffloadingConnector` for HBM to CPU cache retention.

| Configuration | P/D transport | Prefix-cache tiers |
| --- | --- | --- |
| Plain P/D | NIXL | HBM |
| PD Multi Tier | NIXL | HBM, CPU |

## When to use Multi Tier

Multi Tier is most useful when prompts reuse prefixes and the active prefix
working set is larger than GPU KV-cache capacity but fits in the configured CPU
tier.

Multi Tier is unlikely to improve workloads with little prefix reuse. It also
adds host-memory copies, so measure the complete throughput and latency
distribution before enabling it for such workloads.

## Architecture

The default Multi Tier overlay configures only the prefill workers with CPU
offloading. Decode workers continue to use `NixlConnector` for the P/D transfer:

```text
request -> router -> prefill HBM
                         |
                         v
                   CPU primary tier
                         |
                         +---- NIXL P/D handoff ----> decoder
```

## Prerequisites

- Complete the [main guide prerequisites](./README.md#prerequisites), including
  RDMA networking and the `llm-d-hf-token` Secret.
- At least 16 GPUs for the guide topology: 8 TP=1 prefill instances and 2 TP=4
  decode instances.
- vLLM v0.27.1 or later. The overlays pin v0.27.1.
- Enough host memory for the configured tier. The example reserves 100 GiB per
  prefiller.
- The same vLLM `--block-size` and router
  `tokenProcessorConfig.blockSizeTokens`. This guide uses 128 tokens.
- The same `PYTHONHASHSEED` on every engine pod. The overlay sets `0`.

## Installation

Set the environment variables and create the namespace and Hugging Face Secret
as described by the [main guide](./README.md#checkout-repo--setups).

```bash
export GUIDE_NAME="pd-disaggregation"
export NAMESPACE="llm-d-pd-disaggregation"
export INFRA_PROVIDER="base" # base | coreweave
```

### 1. Deploy the router

Multi Tier uses precise prefix-cache events so CPU-resident prefixes continue
to influence placement after their HBM copies are evicted.

```bash
helm install ${GUIDE_NAME} \
  ${ROUTER_STANDALONE_CHART} \
  -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
  -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
  -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/pd-multi-tier.values.yaml \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

Deploy the Service that exposes vLLM's renderer endpoint to the router:

```bash
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/${GUIDE_NAME}/render/multi-tier
```

### 2. Deploy PD Multi Tier

```bash
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/multi-tier-${INFRA_PROVIDER}
```

The CoreWeave overlay adds the `rdma/ib` resource and container capabilities
required by the NIXL transport.

### Data-parallel KV-event sockets

Precise routing requires one ZMQ publisher for every data-parallel engine.
Each engine in the same pod must bind a different port. The router's
`podDiscoveryConfig.socketPort` is the first local port in that range.

For a multi-pod DP deployment, vLLM adds the global DP rank to the port in
`kv-events-config.endpoint`. Subtract the pod's first global rank so every pod
publishes on the same local port range. For example, DP=8 per pod and router
`socketPort: 5557` use:

```bash
START_RANK=$((LWS_WORKER_INDEX * DP_SIZE_LOCAL))
EVENT_PORT_BASE=5557
ROUTER_API_PORT_BASE=8000
KV_EVENTS_BASE=$((EVENT_PORT_BASE - START_RANK))

KV_EVENTS_ARGS="--kv-events-config {\"enable_kv_cache_events\":true,\"publisher\":\"zmq\",\"endpoint\":\"tcp://*:${KV_EVENTS_BASE}\",\"topic\":\"kv@${POD_IP}:${ROUTER_API_PORT_BASE}@${MODEL}\"}"
```

Ranks 0-7 therefore bind `5557-5564` on every pod. The topic's endpoint port
is the router-visible API base. With
`--data-parallel-multi-port-external-lb`, the topics identify endpoints
`8000-8007` in this example. A routing proxy can expose that range even when
the corresponding vLLM ports use a different base, such as `8200-8207`.
Binding every rank directly to `tcp://*:5557` causes a port collision.
Omitting the `START_RANK` adjustment on the second pod instead publishes on
`5565-5572`, which does not match router discovery.

Before benchmarking, verify all eight listeners on every model-server pod and
all eight established router subscriptions. A correct listener check prints
the same range for each pod:

```bash
kubectl exec -n ${NAMESPACE} ${POD} -c modelserver -- python3 -c '
ports = []
for line in open("/proc/net/tcp"):
    fields = line.split()
    if len(fields) > 3 and fields[3] == "0A":
        port = int(fields[1].split(":")[1], 16)
        if 5557 <= port <= 5564:
            ports.append(port)
print(sorted(ports))
'
```

Also reject startup logs containing `Address already in use` or a ZMQ bind
error. Listener checks alone are insufficient: the router must have one
established connection to every rank before traffic starts.

Check the router metrics endpoint before sending traffic:

```bash
kubectl port-forward -n ${NAMESPACE} deploy/${GUIDE_NAME}-epp 9090:9090

curl -s localhost:9090/metrics \
  | grep -E 'llm_d_router_epp_kv_cache_events_active_subscribers|llm_d_epp_ready_endpoints'
```

Both values must equal the number of routable DP endpoints. The measured
DP=8 prefill plus DP=8 decode topology reported 16 active subscribers and 16
ready endpoints, with listeners `5557-5564` reachable on both pods.

## Verification

Wait for all model-server pods:

```bash
kubectl get pods -n ${NAMESPACE} -l llm-d.ai/guide=pd-disaggregation -w
```

Confirm that each prefiller created its CPU tier:

```bash
kubectl logs -n ${NAMESPACE} \
  -l llm-d.ai/role=prefill -c modelserver --prefix \
  | grep -E 'CPUOffloadingManager|SharedOffloadRegion|CPU KV'
```

Inspect the offload metrics after sending traffic:

```bash
kubectl exec -n ${NAMESPACE} \
  $(kubectl get pod -n ${NAMESPACE} -l llm-d.ai/role=prefill -o name | head -1) \
  -c modelserver -- curl -s localhost:8000/metrics \
  | grep -E 'kv_offload_(total_bytes|cpu_allocation|tiering)'
```

`vllm:kv_offload_total_bytes_total{transfer_type="GPU_to_CPU"}` counts data
retained in local CPU memory, and `transfer_type="CPU_to_GPU"` counts data
restored from it. A non-zero restore delta during the benchmark window is the
mechanism check for CPU Multi Tier.

## CPU sizing

`cpu_bytes_to_use` is the capacity of one `OffloadingConnector` instance. The
guide's TP=1 pods have one instance, so 100 GiB means 100 GiB per prefiller.
With data parallelism, each local DP engine creates its own connector: DP=8 and
`cpu_bytes_to_use: 107374182400` therefore reserve up to 800 GiB per pod. Size
the pod and node for the multiplied capacity, not just the YAML value. If the
CPU tier is smaller than the reusable working set, older blocks are evicted and
must be recomputed.

Size the pod memory limit for the CPU tier plus vLLM's normal host-memory use,
and leave node-level headroom for concurrent prefiller pods. Monitor
`vllm:kv_offload_allocation_failure_total` and CPU-cache usage under the target
concurrency; a nominal cache capacity is not sufficient if active transfers pin
most of the available blocks.

Size from measured token capacity and the target reusable working set. On the
measured H200 topology, each TP=1 prefiller exposed 1,630,790 HBM KV tokens, or
13,046,320 tokens across eight prefills. The eviction benchmark below uses an
approximate 17,280,000-token unique prefill working set. Its 4,233,680-token
excess over aggregate HBM, plus transfer and concurrency headroom, fits in the
configured 800 GiB aggregate CPU tier. Re-run the capacity check after changing
the model, tensor parallelism, block size, or GPU memory utilization.

## Benchmarking

Use the main guide's dedicated `guide_pd-disaggregation_1.yaml` profile for a
like-for-like comparison:

```bash
llmdbenchmark \
  --spec guides/pd-disaggregation \
  run \
  --endpoint-url "${ENDPOINT_URL}" \
  --gateway-class epponly \
  --model "openai/gpt-oss-120b" \
  --namespace "${NAMESPACE}" \
  --harness inference-perf \
  --workload guide_pd-disaggregation_1.yaml \
  --monitoring \
  --analyze
```

The profile sends random 5,000-token prompts with 250 output tokens at 45 QPS
for 120 seconds. It is the guide's saturation test, but it intentionally has
little prefix reuse. It measures the steady-state cost of CPU offloading; it is
not a best-case cache-reuse workload. See the
[Multi Tier benchmark report](./benchmark-results/vllm-gpt-oss-120b-h200-multi-tier.md)
for the controlled NIXL and CPU Multi Tier comparison.

The
[GLM-5.2-FP8 DP benchmark](./benchmark-results/vllm-glm-5.2-fp8-h200-dp-multi-tier.md)
compares precise NIXL P/D with precise CPU Multi Tier on a DP=8 prefill plus
DP=8 decode topology. In its single 300-second Weka C32 cut, CPU Multi Tier
improved successful request rate by 6.4%, reduced mean TTFT by 13.6%, and
restored 25.23 GiB from CPU. It does not use a filesystem tier.

For a prefix-reuse comparison, run the document-Q&A profile:

```bash
llmdbenchmark \
  --spec guides/pd-disaggregation \
  run \
  --endpoint-url "${ENDPOINT_URL}" \
  --gateway-class epponly \
  --model "openai/gpt-oss-120b" \
  --namespace "${NAMESPACE}" \
  --harness inference-perf \
  --workload guide_p2p-kv-cache-sharing_1.yaml \
  --monitoring \
  --analyze
```

This profile schedules 1,152 turns across 192 six-turn conversations. Each
conversation has a private 49,152-token document prefix, so reuse occurs
within a conversation rather than through one global shared prefix. Compare
success count and the full TTFT distribution in addition to request rate; the
180-second request timeout makes failures part of the result.

### Eviction-pressure comparison

Use the checked-in eviction profile when the goal is to verify that CPU
retention extends useful prefix capacity beyond HBM:

```bash
llmdbenchmark \
  --spec guides/pd-disaggregation \
  run \
  --endpoint-url "${ENDPOINT_URL}" \
  --gateway-class epponly \
  --model "openai/gpt-oss-120b" \
  --namespace "${NAMESPACE}" \
  --harness inference-perf \
  --workload-file-path \
    "${REPO_ROOT}/guides/pd-disaggregation/benchmark-templates/tiered-eviction.yaml.in" \
  --wait-timeout 7200 \
  --monitoring \
  --analyze
```

The profile schedules 10,800 requests in eight 60-second Poisson stages from
5 to 40 QPS. It uses 1,000 groups with five prompts per group, a 16,000-token
shared prefix, 256 new input tokens, and 256 requested output tokens. It does
not use a filesystem KV tier. The `storage.local_storage` field in the profile
is only the harness result directory and is unrelated to model-server KV
storage. Per-request lifecycle output is disabled to avoid a multi-gigabyte
trace; the overall and per-stage summaries remain enabled.

Use three arms to separate the engine and placement effects: plain NIXL with
CPU backend weight `0.0`, the CPU-offload engine with CPU backend weight `0.0`,
and the same byte-identical CPU-offload engine with CPU backend weight `0.4`.
Restart every engine and the router between arms to clear HBM, CPU-tier, and
precise-index state. Snapshot the CPU-to-GPU counter before and after each CPU
arm. Compare per-stage request rate, TTFT, latency, success count, and full
completion time. A fixed arrival window alone hides the queued-work tail after
saturation.

On the measured Fozzie 16x H200 topology, all three arms completed all 10,800
requests. At the 40-QPS stage, plain NIXL, CPU-blind offload, and CPU-aware
Multi Tier sustained 22.10, 32.62, and 36.04 request/s respectively. Across the
full run, enabling CPU offload reduced P90 request latency from 24.010 to 8.078
seconds. Enabling CPU-aware placement on the same engine reduced it further to
2.859 seconds. The CPU-aware arm logged 3.58 TiB restored from CPU with zero
allocation failures, confirming that the read path was active. These are
single runs with generated seeds, so use the result to demonstrate the
mechanism and repeat it before treating the percentages as a capacity estimate.

On the measured 8x TP=1 prefill and 2x TP=4 decode topology, plain NIXL had the
best document-Q&A success count and mean latency. CPU Multi Tier was effectively
tied with NIXL on the random guide workload and did not improve document Q&A,
but it prevented the saturation collapse in the deliberate eviction-pressure
profile. See the benchmark report for the full tables, exact CPU sizing, and
limitations.

## Cleanup

Delete the overlay that was deployed, the render Service, and the router:

```bash
kubectl delete -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/multi-tier-${INFRA_PROVIDER}
kubectl delete -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/${GUIDE_NAME}/render/multi-tier
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
```
