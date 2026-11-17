# Flow control with prefill/decode

Prefill and decode can reach capacity at different times. A burst of long prompts can build a queue at prefill, while long responses can keep decode busy after prefill finishes. Spare capacity in one pool does not remove pressure from the other.

Flow control checks both pools and uses the higher saturation value to decide when queued requests can dispatch. With the default settings, requests wait in the Endpoint Picker when either stage reaches its saturation limit. Configured priorities determine which queued requests dispatch first.

This example enables flow control in the [P/D deployment](README.md). Flow control governs when requests are dispatched; the P/D scheduling profiles select their prefill and decode workers.

## Enable flow control

Edit `router.epp.pluginsCustomConfig.pd-config.yaml` in [`router/pd-disaggregation.values.yaml`](router/pd-disaggregation.values.yaml). If your accelerator values file also defines `pd-config.yaml`, edit that file instead; it is applied last.

Add `featureGates` at the same indentation as `apiVersion` and `kind`:

```yaml
featureGates:
- flowControl
```

To read the metrics below, set `MONITORING=true` and select `MONITORING_VALUES` for your platform in the [router setup](README.md#1-deploy-the-llm-d-router) before installing or upgrading.

For a new deployment, continue with the [P/D guide](README.md). For an existing deployment, upgrade from the same shell with the chart version and values used for the original install, including any extra overrides:

```bash
helm upgrade ${GUIDE_NAME} ${ROUTER_STANDALONE_CHART} \
  -f ${ROUTER_BASE_VALUES} \
  ${MONITORING_VALUES} \
  -f ${ROUTER_VALUES} \
  ${ACCELERATOR_VALUES} \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

Wait for the rollout, then check that the Endpoint Picker enabled flow control:

```bash
kubectl rollout status deploy/${GUIDE_NAME}-epp -n ${NAMESPACE} --timeout=120s && \
kubectl logs deploy/${GUIDE_NAME}-epp -c epp -n ${NAMESPACE} \
  | grep 'Initializing Flow Control layer'
```

The default utilization detector uses a queue-depth threshold of `5` and a KV cache threshold of `0.8`. The default queue timeout is `60s`; a shorter gateway or client timeout can end the request first. See the [detector settings](https://github.com/llm-d/llm-d-router/tree/main/pkg/epp/framework/plugins/flowcontrol/saturationdetector/utilization) to tune these thresholds.

To disable flow control, remove the feature gate and repeat the upgrade.

## Check the stage metrics

Query the metrics below in your configured Prometheus or [GKE Managed Prometheus](../../docs/operations/observability/setup.md).

With the OSS Prometheus / OpenShift monitoring values, you can also read them directly:

```bash
kubectl get --raw "/api/v1/namespaces/${NAMESPACE}/services/${GUIDE_NAME}-epp:9090/proxy/metrics" \
  | grep -E '^llm_d_epp_flow_control_(pool_saturation|stale_endpoints|queue_size)'
```

The GKE monitoring values keep metrics authentication enabled; use the configured collector rather than this direct command.

- `llm_d_epp_flow_control_pool_saturation` has `prefill`, `decode`, and `effective` stage values. The default utilization detector averages endpoint scores within each stage; `effective` is the higher stage value. With the default settings, dispatch pauses at `1.0` or higher.
- `llm_d_epp_flow_control_stale_endpoints` counts endpoints with missing or old metrics. They score `1.0` by default, so check this before treating a high saturation score as overload.
- `llm_d_epp_flow_control_queue_size` counts requests waiting in the Endpoint Picker, separate from the model servers' queues. Per-flow series appear after traffic creates a flow.

Read these metrics from the same Endpoint Picker. Match `pool_saturation` and `queue_size` by `inference_pool`. If a stage is missing, check the pods' `llm-d.ai/role` labels; unlabeled endpoints count as decode.

On a test deployment, compare the same workload with flow control on and off. Watch stage saturation and queue size alongside client latency, completions, errors, and timeouts. Confirm that queued requests resume as saturation falls.

## Prioritize requests (optional)

Install the InferenceObjective CRD from the flow-control guide's [prerequisites](../flow-control/README.md#prerequisites), then adapt its [priority examples](../flow-control/README.md#use-case-1-multi-tenancy-model-as-a-service). Set each objective's `spec.poolRef.name` to this deployment's pool name and create it in `${NAMESPACE}`.

Send the objective's name in the `x-llm-d-inference-objective` request header. For production traffic, follow the linked guide's instructions for assigning these headers at a trusted gateway.
