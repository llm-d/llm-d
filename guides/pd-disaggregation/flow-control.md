# Flow control with prefill/decode

Flow control queues requests in the Endpoint Picker when the model servers are saturated. It calculates saturation separately for prefill and decode and uses the higher value for admission.

## Enable flow control

In [`router/pd-disaggregation.values.yaml`](router/pd-disaggregation.values.yaml), add the feature gate inside `router.epp.pluginsCustomConfig.pd-config.yaml`, beside `apiVersion` and `kind`:

```yaml
featureGates:
- flowControl
```

Keep the existing plugins and scheduling profiles. For a new deployment, continue with the [P/D guide](README.md). For an existing deployment, upgrade from the same shell, using the same values as the original install:

```bash
helm upgrade ${GUIDE_NAME} ${ROUTER_STANDALONE_CHART} \
  -f ${ROUTER_BASE_VALUES} \
  ${MONITORING_VALUES} \
  -f ${ROUTER_VALUES} \
  ${ACCELERATOR_VALUES} \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

Confirm that the restarted Endpoint Picker enabled flow control:

```bash
kubectl logs deploy/${GUIDE_NAME}-epp -c epp -n ${NAMESPACE} \
  | grep 'Initializing Flow Control layer'
```

The default utilization detector uses a queue-depth threshold of `5` and a KV cache threshold of `0.8`. The default queue TTL is `60s`; a shorter gateway or client timeout can end the request first. See the [detector settings](https://github.com/llm-d/llm-d-router/tree/main/pkg/epp/framework/plugins/flowcontrol/saturationdetector/utilization) to tune these thresholds.

For multiple priorities, follow the [flow-control guide](../flow-control/README.md), including its InferenceObjective CRD installation. Use this deployment's pool name in the objectives.

To disable flow control, remove the feature gate and repeat the upgrade.

## Check the stage metrics

Enable monitoring with `MONITORING=true` using the [P/D monitoring setup](README.md#observability--troubleshooting), then read the Endpoint Picker's metrics:

```bash
kubectl get --raw "/api/v1/namespaces/${NAMESPACE}/services/${GUIDE_NAME}-epp:9090/proxy/metrics" \
  | grep -E '^llm_d_epp_flow_control_(pool_saturation|stale_endpoints|queue_size)'
```

- `pool_saturation` has `prefill`, `decode`, and `effective` stage values. Each stage averages its endpoint scores; `effective` is the higher stage value. With the default admission policy, dispatch pauses at `1.0` or higher.
- `stale_endpoints` counts endpoints with missing or old metrics. They score `1.0` by default, so check this before treating a high saturation score as overload.
- `queue_size` counts requests waiting in the Endpoint Picker, separate from the model servers' queues. Per-flow series appear after traffic creates a flow.

Compare series from the same `inference_pool` and Endpoint Picker target. If a stage is missing, check the pods' `llm-d.ai/role` labels; unlabeled endpoints count as decode.

On a test deployment, compare the same workload with flow control on and off. Watch stage saturation and queue size alongside client latency, completions, errors, and timeouts. Confirm that queued requests resume as saturation falls.
