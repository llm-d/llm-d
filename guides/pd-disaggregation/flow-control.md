# Flow control with prefill/decode

This walkthrough configures [flow control](../flow-control/README.md) to prioritize traffic and manage load across the prefill and decode pools using a hybrid request/token detector. Request counts alone can understate pressure from large prompts; uncached prompt-token counts alone can understate pressure from long-running decode requests.
The hybrid detector evaluates both signals, and the Endpoint Picker uses the higher of the prefill and decode saturation scores to control dispatch. [Request priorities](#4-configure-request-priorities) determine which queued requests receive dispatch opportunities first.

The steps below add hybrid detection to the [P/D deployment](README.md), configure priorities, and verify saturation and queueing under both prefill-heavy and decode-heavy load.

## Prerequisites

Deploy and verify the [P/D guide](README.md) with `MONITORING=true` and the `MONITORING_VALUES` for your platform from the [router setup](README.md#1-deploy-the-llm-d-router). The steps below use that deployment to measure limits before enabling flow control. Keep the same environment variables, chart version, and model servers for the upgrade.

## Installation Instructions

### 1. Measure Limits

Measure `maxConcurrency` (in-flight requests) and `maxTokenConcurrency` (estimated uncached prompt tokens) per endpoint. The same pair of limits applies to endpoints in both pools.
The default [token producer](https://github.com/llm-d/llm-d-router/tree/main/pkg/epp/framework/plugins/requesthandling/dataproducer/tokenizer) estimates text-token counts from prompt bytes, using four bytes per estimated token. Calibrate `maxTokenConcurrency` against `llm_d_epp_inflight_tokens`; model-server prompt-token counts can differ from the Endpoint Picker's estimate.

Before enabling the overlay, collect the [metrics below](#verification) from the P/D deployment with monitoring enabled. Increase concurrency with representative large prompts and record the per-endpoint token estimates while measuring time to first token (TTFT). Repeat with long responses, recording per-endpoint request counts and inter-token latency. Choose candidate limits from load levels that meet your latency targets, then validate both limits together using the verification steps. Keep the tokenizer configuration, cache behavior, and response mode consistent between calibration and validation.

The published [P/D hybrid recipe](https://github.com/alexagriffith/flow-control-benchmarks/blob/5297d328d37062a7ced5debe351d7293d10f34db/benchmark-data/rhaii-3.5-flow-control/pd-flow-control/configuration/selected-recipe.yaml) used `64` requests and `80000` estimated tokens for GPT-OSS 20B with one prefill pod and one decode pod on two H100s. Those values are specific to that benchmark; measure limits for this guide's model and topology before deploying.

### 2. Configure the Router

The [example router values](router/flow-control.values.yaml) add hybrid request/token detection to the shared P/D routing configuration. The overlay replaces only `pd-config.yaml`; the P/D deployment supplies the service, pod selection, and replica settings. Copy the values file to configure your deployment:

```bash
cp ${REPO_ROOT}/guides/pd-disaggregation/router/flow-control.values.yaml ./pd-flow-control.values.yaml
export FLOW_CONTROL_VALUES="$(pwd)/pd-flow-control.values.yaml"
```

If your deployment uses [`tpu-v7.values.yaml`](router/tpu-v7.values.yaml), an [`npu.rbln.values.yaml`](router/npu.rbln.values.yaml) revision that defines `pd-config.yaml`, or another custom router configuration, copy that values file to `./pd-flow-control.values.yaml` instead.
Within `router.epp.pluginsCustomConfig.pd-config.yaml`, add the example's `featureGates`, `flowControl`, and `pd-hybrid` plugin. Keep `addEstimatedOutputTokens` at the default `false`. Helm replaces the complete `pd-config.yaml` string, so the local copy must include your accelerator's routing parameters.

In your local file, replace both placeholders with positive integer limits measured for your model, hardware, and workload:

| Parameter | Placeholder | Per-endpoint limit |
| --- | --- | --- |
| `maxConcurrency` | `<REQUEST_LIMIT>` | In-flight requests |
| `maxTokenConcurrency` | `<TOKEN_LIMIT>` | Estimated uncached prompt tokens reported by the Endpoint Picker |

The configuration loader adds the detector's endpoint filter to both scheduling profiles. The existing prefill and decode scorers still select workers from the eligible endpoints. The detector's default filter can fall back to all candidates when every candidate exceeds the limits; see the [concurrency detector's filtering behavior](https://github.com/llm-d/llm-d-router/tree/main/pkg/epp/framework/plugins/flowcontrol/saturationdetector/concurrency#role-in-scheduling-the-traffic-shaper).

### 3. Deploy the Router

Install the InferenceObjective CRD before upgrading the router. The Endpoint Picker discovers this API at startup and needs the objective watcher to resolve request priorities. Use the P/D deployment's `ROUTER_RELEASE_URL`:

```bash
kubectl apply -f https://github.com/llm-d/llm-d-router/${ROUTER_RELEASE_URL}/manifests.yaml
kubectl wait --for=condition=Established crd/inferenceobjectives.llm-d.ai --timeout=60s
```

Upgrade the standalone router using the original chart version, values files, and `--set` overrides, with the edited flow-control file last:

```bash
helm upgrade ${GUIDE_NAME} ${ROUTER_STANDALONE_CHART} \
  -f ${ROUTER_BASE_VALUES} \
  ${MONITORING_VALUES} \
  -f ${ROUTER_VALUES} \
  ${ACCELERATOR_VALUES} \
  -f ${FLOW_CONTROL_VALUES} \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

Wait for the rollout, then check that the Endpoint Picker enabled flow control:

```bash
kubectl rollout status deploy/${GUIDE_NAME}-epp -n ${NAMESPACE} --timeout=120s && \
kubectl logs deploy/${GUIDE_NAME}-epp -c epp -n ${NAMESPACE} \
  | grep 'Initializing Flow Control layer'
```

The default queue timeout is `60s`. A request that exceeds the queue timeout returns HTTP `429` when the pool has endpoints, or `503` when no endpoints are available. A shorter gateway or client timeout can end the request first.

To stop queueing in the Endpoint Picker, remove the feature gate and repeat the upgrade. The hybrid detector still filters endpoints, and legacy admission can reject negative-priority requests with HTTP `429` when saturated. Remove `-f ${FLOW_CONTROL_VALUES}` and repeat the upgrade with the original values files to undo the entire configuration change.

### 4. Configure Request Priorities

Copy the [example objectives](../flow-control/objectives.yaml) to a file you can edit:

```bash
cp ${REPO_ROOT}/guides/flow-control/objectives.yaml ./pd-objectives.yaml
```

In each objective, set `spec.poolRef.name` to the value of `${GUIDE_NAME}` and update the guide and model labels for your deployment. Choose the objective names and `spec.priority` values for your deployment. Higher priority values receive dispatch opportunities first. Requests without a matching objective use priority `0` by default. Apply the edited file in the P/D namespace:

```bash
kubectl apply -f ./pd-objectives.yaml -n ${NAMESPACE}
```

Send the objective's name in the `x-llm-d-inference-objective` request header. For production traffic, follow the flow-control guide's [client integration instructions](../flow-control/README.md#2-client-integration) to assign these headers at a trusted gateway.

On a test deployment, use a non-default priority with no other traffic at that priority. Compare the following counter in Prometheus before and after sending requests with the objective header.
Select the Endpoint Picker's namespace and pod using your collector's target labels (`namespace` and `pod` below). Replace `<pool-name>` with `${GUIDE_NAME}`, `<priority>` with the objective's priority, and the target placeholders with the deployment's values. In a collector spanning clusters, also select the deployment's cluster. Repeat the check if the Endpoint Picker pod restarts.

```promql
sum(llm_d_epp_flow_control_request_queue_duration_seconds_count{namespace="<namespace>", pod="<endpoint-picker-pod>", inference_pool="<pool-name>", priority="<priority>"})
```

An increase confirms that requests were classified at the expected priority. If the expected series does not increase, check the header, objective name, namespace, and `spec.poolRef.name`. Classification alone does not demonstrate priority ordering under contention.

## Verification

First complete the [P/D verification](README.md#verification) to confirm that requests use both pools and transfer KV data. Query the metrics below in your configured Prometheus or [GKE Managed Prometheus](../../docs/operations/observability/setup.md). The in-flight request and token metrics are available before enabling flow control; the flow-control metrics appear after enabling the feature gate.

With the OSS Prometheus / OpenShift monitoring values, you can also read the metrics directly:

```bash
kubectl get --raw "/api/v1/namespaces/${NAMESPACE}/services/${GUIDE_NAME}-epp:9090/proxy/metrics" \
  | grep -E '^llm_d_epp_(flow_control_(pool_saturation|queue_size)|inflight_(requests|tokens))'
```

The GKE monitoring values keep metrics authentication enabled; use the configured collector for GKE.

- `llm_d_epp_flow_control_pool_saturation` exposes `prefill`, `decode`, and `effective` stage values. For each endpoint, the hybrid detector takes the higher of the request-count ratio and token-count ratio, then averages endpoint scores within each stage. Pending request reservations can raise the stage score before in-flight counters update. Tokens are not reserved, so bursts of large prompts can push the token ratio above `1.0`.
  The `effective` score is the higher stage value. With the default usage-limit policy, dispatch pauses at `1.0` or above for every priority.
- `llm_d_epp_flow_control_queue_size` counts requests waiting in the Endpoint Picker, separate from model-server queues. Per-flow series appear after traffic creates a flow.

The `llm_d_epp_inflight_requests` and `llm_d_epp_inflight_tokens` metrics expose the two detector inputs. For each endpoint and namespace, sum the series with `producer_name="inflight-load-producer"` across `fairness_id` and `priority`. Divide request counts by your `maxConcurrency` and token counts by your `maxTokenConcurrency` to compare the two ratios. Match each `endpoint_name` to a pod in the same namespace, then read the pod's `llm-d.ai/role` label to identify the stage.

With `addEstimatedOutputTokens: false`, both selected endpoints carry estimated uncached prompt tokens until the first response chunk. The producer then releases the prefill request count and both endpoints' token counts; the decode request count remains until completion. Non-streaming requests retain both endpoints' request and token counts until completion, so prefill saturation can remain elevated during decode. Interpret stage scores alongside model-server activity rather than treating stage scores as independent measurements of GPU utilization.

Read metrics from the same Endpoint Picker. Match `pool_saturation` and `queue_size` by `inference_pool`. If a stage is missing, check the pods' `llm-d.ai/role` labels; unlabeled endpoints count as decode.

On a test deployment, exercise both traffic shapes with representative inputs and output lengths:

| Traffic shape | What to check |
| --- | --- |
| Large prompts with short responses | Compare prefill saturation with the Endpoint Picker's in-flight token estimate and check prefill model-server activity. Confirm that the token limit detects pressure that request count alone would miss. |
| Shorter prompts with long responses | Compare decode saturation with active requests and decode model-server metrics. Confirm that the request limit detects pressure that uncached prompt-token count alone would miss. |

For each shape, compare the same workload with the feature gate enabled and disabled while keeping the hybrid detector and limits unchanged. Pin the chart version and `router.epp.image.tag`, and confirm that both runs use the same image digest; the default `v0` chart and `main` image are moving references.
Use non-negative priorities for this comparison to avoid the legacy rejection of saturated negative-priority traffic. Record actual request lengths, client latency, completions, errors, and timeouts alongside stage saturation and queue size. If saturation never reaches the dispatch ceiling, the run has not tested saturation-based queueing.

To check priority ordering, send requests for multiple objectives while requests are queued. Confirm classification with the priority counter above, then compare dispatch outcomes for the configured priorities. A light-load test with no queue contention cannot demonstrate priority ordering.

As saturation falls, confirm that queued requests dispatch and complete. A smaller queue can also mean requests timed out. If the deployment accepts both streaming and non-streaming requests, calibrate and validate both modes, comparing stage accounting with model-server metrics.

For different limits on the two pools, use separate detector instances and assign the detectors with the [`max-saturation-detector` `stages` map](https://github.com/llm-d/llm-d-router/tree/main/pkg/epp/framework/plugins/flowcontrol/saturationdetector/composite). A composite detector also requires the child endpoint filters in the corresponding scheduling profiles. Priority-based capacity reserve is configured separately through a [usage-limit policy](https://github.com/llm-d/llm-d-router/tree/main/pkg/epp/framework/plugins/flowcontrol/usagelimits).
