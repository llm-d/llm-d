# Flow control with prefill/decode

This walkthrough configures [flow control](../flow-control/README.md) to prioritize traffic and manage load across the prefill and decode pools using a hybrid request/token detector. Request counts alone can understate pressure from large prompts; uncached prompt-token counts alone can understate pressure from long-running decode requests.
The hybrid detector evaluates both signals, and the Endpoint Picker uses the higher of the prefill and decode saturation scores to control dispatch. [Request priorities](#3-configure-request-priorities) determine which queued requests receive dispatch opportunities first.

The steps below add hybrid detection to the [P/D deployment](README.md), configure priorities, and verify saturation and queueing under both prefill-heavy and decode-heavy load.

## Prerequisites

Complete the [P/D guide prerequisites and environment setup](README.md#prerequisites). The deployment commands below use the same environment variables, chart version, and model servers.

## Installation Instructions

### 1. Configure the Router

The [example router values](router/flow-control.values.yaml) enable flow control with a hybrid request/token detector and the shared P/D routing configuration. Copy the values file to configure your deployment:

```bash
cp ${REPO_ROOT}/guides/pd-disaggregation/router/flow-control.values.yaml ./pd-flow-control.values.yaml
export FLOW_CONTROL_VALUES="$(pwd)/pd-flow-control.values.yaml"
```

If your accelerator or deployment customizes `pd-config.yaml`, use the last values file that defines `pd-config.yaml` as the starting file instead. Within `router.epp.pluginsCustomConfig.pd-config.yaml`, copy `featureGates`, `flowControl`, and the `pd-hybrid` plugin from the example, and set `addEstimatedOutputTokens: false` on the existing `inflight-load-producer`. This preserves the accelerator's routing parameters; Helm replaces the complete `pd-config.yaml` string rather than merging individual plugins.

In your local file, replace both placeholders with positive integer limits measured for your model, hardware, and workload:

| Parameter | Placeholder | Per-endpoint limit |
| --- | --- | --- |
| `maxConcurrency` | `<REQUEST_LIMIT>` | In-flight requests |
| `maxTokenConcurrency` | `<TOKEN_LIMIT>` | Uncached prompt tokens |

The [P/D detector comparison](https://github.com/alexagriffith/flow-control-benchmarks/tree/5297d328d37062a7ced5debe351d7293d10f34db/benchmark-data/rhaii-3.5-flow-control/pd-flow-control) used `64` requests and `80000` tokens for GPT-OSS 20B with one prefill pod and one decode pod on two H100s. Those values are benchmark-specific; measure limits for this guide's model and topology before deploying.

`addEstimatedOutputTokens: false` excludes output-token estimates from token accounting. The producer counts uncached prompt tokens, while decode request counters remain active until completion. For streaming responses, prompt-token counters are released at the first response chunk; non-streaming responses retain prompt-token accounting until completion. Both limits apply to every endpoint in this example; saturation is calculated separately for the prefill and decode pools. Calibrate with the cache behavior and response mode expected in production.

The configuration loader adds the detector's endpoint filter to both scheduling profiles. The existing prefill and decode scorers still select workers from the eligible endpoints. The detector's default filter can fall back to all candidates when every candidate exceeds the limits; see the [concurrency detector's filtering behavior](https://github.com/llm-d/llm-d-router/tree/main/pkg/epp/framework/plugins/flowcontrol/saturationdetector/concurrency#role-in-scheduling-the-traffic-shaper).

### 2. Deploy the Router

To collect the stage metrics, set `MONITORING=true` and select `MONITORING_VALUES` for your platform in the [router setup](README.md#1-deploy-the-llm-d-router) before installing or upgrading.

For a new deployment, follow the [P/D installation instructions](README.md#installation-instructions), adding `-f ${FLOW_CONTROL_VALUES}` after `${ACCELERATOR_VALUES}` in the router Helm command. Then deploy and verify the model servers as described in the P/D guide.

For an existing standalone router deployment, use the original chart version, values files, and `--set` overrides, with the edited flow-control file last:

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

### 3. Configure Request Priorities

To assign different priorities, install the InferenceObjective CRD using the P/D deployment's `ROUTER_RELEASE_URL`:

```bash
kubectl apply -f https://github.com/llm-d/llm-d-router/${ROUTER_RELEASE_URL}/manifests.yaml
```

Copy the [example objectives](../flow-control/objectives.yaml) to a file you can edit:

```bash
cp ${REPO_ROOT}/guides/flow-control/objectives.yaml ./pd-objectives.yaml
```

In each objective, set `spec.poolRef.name` to the value of `${GUIDE_NAME}`. Choose the objective names and `spec.priority` values for your deployment. Higher priority values receive dispatch opportunities first. Requests without a matching objective use priority `0` by default. Apply the edited file in the P/D namespace:

```bash
kubectl apply -f ./pd-objectives.yaml -n ${NAMESPACE}
```

Send the objective's name in the `x-llm-d-inference-objective` request header. For production traffic, follow the flow-control guide's [client integration instructions](../flow-control/README.md#2-client-integration) to assign these headers at a trusted gateway.

After sending requests with the objective header, compare the following counter in Prometheus before and after the requests. Replace `<pool-name>` with the value of `${GUIDE_NAME}` and `<priority>` with the objective's priority:

```promql
sum(llm_d_epp_flow_control_request_queue_duration_seconds_count{inference_pool="<pool-name>", priority="<priority>"})
```

An increase confirms that requests were classified at the expected priority. If the expected series does not increase, check the header, objective name, namespace, and `spec.poolRef.name`. Classification alone does not demonstrate priority ordering under contention.

## Verification

First complete the [P/D verification](README.md#verification) to confirm that requests use both pools and transfer KV data. Then query the metrics below in your configured Prometheus or [GKE Managed Prometheus](../../docs/operations/observability/setup.md).

With the OSS Prometheus / OpenShift monitoring values, you can also read the metrics directly:

```bash
kubectl get --raw "/api/v1/namespaces/${NAMESPACE}/services/${GUIDE_NAME}-epp:9090/proxy/metrics" \
  | grep -E '^llm_d_epp_(flow_control_(pool_saturation|queue_size)|inflight_(requests|tokens))'
```

The GKE monitoring values keep metrics authentication enabled; use the configured collector for GKE.

- `llm_d_epp_flow_control_pool_saturation` exposes `prefill`, `decode`, and `effective` stage values. For each endpoint, the hybrid detector takes the higher of the request-count ratio and token-count ratio, then averages endpoint scores within each stage. Pending dispatch reservations can raise the stage score before in-flight counters update. The `effective` score is the higher stage value. With the default usage-limit policy, dispatch pauses at `1.0` or above for every priority.
- `llm_d_epp_flow_control_queue_size` counts requests waiting in the Endpoint Picker, separate from model-server queues. Per-flow series appear after traffic creates a flow.

The `llm_d_epp_inflight_requests` and `llm_d_epp_inflight_tokens` metrics expose the two detector inputs. For each endpoint and namespace, sum the series with `producer_name="inflight-load-producer"` across `fairness_id` and `priority`. Divide request counts by your `maxConcurrency` and token counts by your `maxTokenConcurrency` to compare the two ratios. Use `endpoint_name` and the pod role labels to identify the prefill and decode endpoints.

Read metrics from the same Endpoint Picker. Match `pool_saturation` and `queue_size` by `inference_pool`. If a stage is missing, check the pods' `llm-d.ai/role` labels; unlabeled endpoints count as decode.

On a test deployment, exercise both traffic shapes with representative inputs and output lengths:

| Traffic shape | What to check |
| --- | --- |
| Large prompts with short responses | Compare prefill saturation with in-flight token load and prefill model-server metrics. Confirm that the token limit detects pressure that request count alone would miss. |
| Shorter prompts with long responses | Compare decode saturation with active requests and decode model-server metrics. Confirm that the request limit detects pressure that uncached prompt-token count alone would miss. |

For each shape, compare the same workload with the feature gate enabled and disabled while keeping the hybrid detector and limits unchanged. Use non-negative priorities for this comparison to avoid the legacy rejection of saturated negative-priority traffic. Record actual request lengths, client latency, completions, errors, and timeouts alongside stage saturation and queue size. If saturation never reaches the dispatch ceiling, the run has not tested saturation-based queueing.

To check priority ordering, send requests for multiple objectives while requests are queued. Confirm classification with the priority counter above, then compare dispatch outcomes for the configured priorities. A light-load test with no queue contention cannot demonstrate priority ordering.

As saturation falls, confirm that queued requests dispatch and complete. A smaller queue can also mean requests timed out. Repeat with streaming and non-streaming requests, comparing stage accounting with model-server metrics.

For different limits on the two pools, use separate detector instances and assign the detectors with the [`max-saturation-detector` `stages` map](https://github.com/llm-d/llm-d-router/tree/main/pkg/epp/framework/plugins/flowcontrol/saturationdetector/composite). A composite detector also requires the child endpoint filters in the corresponding scheduling profiles. Priority-based capacity reserve is configured separately through a [usage-limit policy](https://github.com/llm-d/llm-d-router/tree/main/pkg/epp/framework/plugins/flowcontrol/usagelimits).
