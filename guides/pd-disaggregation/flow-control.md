# Flow control with prefill/decode

Flow control lets you prioritize requests while managing load across the prefill and decode pools. With the default settings, the Endpoint Picker queues requests when either pool is saturated. It uses the configured priorities to choose which queued requests to dispatch as capacity becomes available.

This guide adds flow control to the [P/D deployment](README.md), then walks through configuring priorities and checking the stage metrics.

## Enable flow control

Edit `router.epp.pluginsCustomConfig.pd-config.yaml` in [`router/pd-disaggregation.values.yaml`](router/pd-disaggregation.values.yaml). If your accelerator values file also defines `pd-config.yaml`, edit that file instead; it is applied last.

Add `featureGates` at the same indentation as `apiVersion` and `kind`:

```yaml
featureGates:
- flowControl
```

To read the metrics below, set `MONITORING=true` and select `MONITORING_VALUES` for your platform in the [router setup](README.md#1-deploy-the-llm-d-router) before installing or upgrading.

For a new deployment, continue with the [P/D guide](README.md). For an existing deployment, restore the guide's environment variables and use the original chart version, values files, and `--set` overrides. Add any extra overrides to this command:

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

## Prioritize requests (optional)

To assign different priorities, install the InferenceObjective CRD described in the flow-control guide's [prerequisites](../flow-control/README.md#prerequisites). Keep using the P/D deployment's environment variables for the commands below.

Copy the [example objectives](../flow-control/objectives.yaml) to a file you can edit:

```bash
cp ${REPO_ROOT}/guides/flow-control/objectives.yaml ./pd-objectives.yaml
```

In each objective, set `spec.poolRef.name` to the value of `${GUIDE_NAME}`. Choose the objective names and `spec.priority` values for your deployment, and update the metadata labels to match. Apply the edited file in the P/D namespace:

```bash
kubectl apply -f ./pd-objectives.yaml -n ${NAMESPACE}
```

Send the objective's name in the `x-llm-d-inference-objective` request header. For production traffic, follow the flow-control guide's [client integration instructions](../flow-control/README.md#2-client-integration) to assign these headers at a trusted gateway.

## Check the stage metrics

After sending requests, use the stage metrics to check how flow control responds to load. Query them in your configured Prometheus or [GKE Managed Prometheus](../../docs/operations/observability/setup.md).

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

On a test deployment, compare the same workload with flow control on and off. Watch stage saturation and queue size alongside client latency, completions, errors, and timeouts. Confirm that queued requests dispatch and complete as saturation falls; a smaller queue can also mean requests timed out.
