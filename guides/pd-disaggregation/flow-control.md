# Flow control with prefill/decode

Flow control can queue requests before the Endpoint Picker sends them to the model servers. In a disaggregated deployment, it evaluates prefill and decode separately and uses the higher saturation value for admission.

This example extends the [P/D guide](README.md). It keeps the existing prefill and decode routing profiles and adds flow control. The configuration and checks below still need an end-to-end run on a disaggregated deployment; the P/D guide's nightly results do not validate this combination.

## Enable flow control

Start with the P/D guide's working deployment configuration. In [`router/pd-disaggregation.values.yaml`](router/pd-disaggregation.values.yaml), edit the YAML inside `router.epp.pluginsCustomConfig.pd-config.yaml`:

1. Add the feature gate beside `apiVersion` and `kind`:

   ```yaml
   featureGates:
   - flowControl
   ```

2. Append this detector to the existing `plugins` list. Keep the existing plugins, including `inflight-load-producer`:

   ```yaml
   - type: utilization-detector
     parameters:
       queueDepthThreshold: 5
       kvCacheUtilThreshold: 0.8
   ```

3. Add this block beside `plugins` and `schedulingProfiles`:

   ```yaml
   flowControl:
     defaultRequestTTL: "60s"
     saturationDetector:
       pluginRef: utilization-detector
   ```

These are additions to the existing configuration, not a replacement file. Leave both scheduling profiles intact. Do not layer the standalone flow-control guide's router values over this file: that example uses a different plugin configuration and model-server selector.

The detector thresholds above are example values, not measurements of your model's capacity. The queue threshold refers to the model server's waiting requests, not the Endpoint Picker's queue. The KV cache threshold is a fraction. The detector averages endpoint scores within each stage; the admission signal is the higher of those two stage averages. A stage average can hide a busy individual pod.

Use the P/D guide's deployment procedure with the edited values. Keep its single Endpoint Picker replica for this example. Confirm that the running Endpoint Picker has loaded the new configuration before testing; saving a values file alone does not enable flow control.

The request TTL bounds time in the flow-control queue. A shorter gateway or client timeout can end the request first. Choose timeouts together rather than extending the queue TTL on its own.

This example checks admission for the existing traffic. To compare priorities, also configure priority bands and matching InferenceObjectives using the [flow-control guide](../flow-control/README.md). Use this deployment's pool name; do not copy the standalone example's pool reference unchanged.

## Check which stage is saturated

Use the [P/D monitoring setup](README.md#observability--troubleshooting) to collect the Endpoint Picker's metrics. In Prometheus, select the target for this deployment and inspect:

```promql
llm_d_epp_flow_control_pool_saturation{stage="prefill"}
llm_d_epp_flow_control_pool_saturation{stage="decode"}
llm_d_epp_flow_control_pool_saturation{stage="effective"}
```

Keep the pool and Endpoint Picker target labels when comparing series. Do not sum these values across replicas or pools.

| Signal | What to check |
| --- | --- |
| `prefill` | Saturation calculated from the prefill endpoints. |
| `decode` | Saturation calculated from the decode endpoints. |
| `effective` | The higher stage value, used by the admission policy. |
| `llm_d_epp_flow_control_queue_size` | Requests waiting in the Endpoint Picker, separate from the model servers' queues. Per-flow series appear after traffic creates a flow. |

For example, prefill at `1.2` and decode at `0.4` gives an effective value of `1.2`. That identifies prefill as the limiting stage in this sample. Admission also depends on the configured priority-band ceiling; the score alone does not prove how many requests were held.

Missing series are not zero. Check that flow control loaded, that endpoints have the expected roles, and that the Endpoint Picker can read fresh model-server metrics. The utilization detector treats stale metrics as saturated by default, so a high score can also reflect a metrics problem. See the [detector reference](https://github.com/llm-d/llm-d-router/tree/main/pkg/epp/framework/plugins/flowcontrol/saturationdetector/utilization).

## Validate before relying on it

On a test deployment, compare the same workload with flow control disabled and enabled. Keep the model, replicas, routing profiles, request sizes, and offered load unchanged.

- First confirm that requests complete through both stages at low load.
- Increase load within an agreed test limit. Record all three saturation series, Endpoint Picker queue size, client latency, completions, errors, and timeouts. A successful low-load request does not test admission under saturation.
- Check whether requests queue when the effective saturation reaches the applicable ceiling and resume as it falls. Include timed-out and failed requests when interpreting latency.
- If testing priority bands, verify the request-to-objective mapping and compare each band's completions and queueing. Do not infer priority protection from aggregate throughput.
- Restore the previous configuration after the comparison and confirm that requests complete again.

Record the router image, configuration, workload, and measurement window with the results. This example does not establish performance gains or a production-safe threshold.
