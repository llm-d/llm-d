# Operations

This guide provides an overview of basic operational considerations for running llm-d, including:

- startup probes;
- request cancellation;
- graceful scale-down;
- EPP HA;
- request rejection.

> [!NOTE]
> For detailed operational considerations for feature-specific variants, use the relevant feature guide. For prefill/decode-disaggregated serving, see [Disaggregated Serving: Operations (vLLM)](../architecture/advanced/disaggregation/operations-vllm.md).

## Startup probes

Use Kubernetes probes to separate process health from model readiness:

- `startupProbe` gives the model time to download and load before liveness and readiness checks begin.
- `livenessProbe` checks the server process, commonly with vLLM `/health`.
- `readinessProbe` controls traffic eligibility, commonly with vLLM `/v1/models` after models are available.

Use [vLLM Model-Aware Readiness Probes](../readiness-probes.md) for the concrete YAML, including startup failure thresholds, readiness timing, and port choices such as `8000` for standalone serving or `8200` when traffic is proxied through a sidecar.

## Request cancellation

Inference requests can hold GPU scheduler slots, KV cache, network connections, and streaming state. For vLLM-based general serving, client disconnects on OpenAI-compatible requests flow through vLLM's cancellation path and abort in-progress generation so per-request resources can be released.

Operational checks:

- confirm your image includes the vLLM cancellation behavior you rely on, such as [vllm-project/vllm#24885](https://github.com/vllm-project/vllm/pull/24885);
- audit Envoy, Inference Gateway, ingress, and service-mesh settings for response buffering, retry policies, and idle-stream timeouts that can hide downstream disconnects or retry partially served requests;
- keep request limits, client deadlines, maximum token limits, and upstream timeout policies aligned;
- validate cancellation with the same gateway, client, and model server version used in production.

General serving does not have the remote prefill KV-cache cleanup paths that P/D-disaggregated serving requires. If you run disaggregated serving, follow the [P/D-specific cancellation guidance](../architecture/advanced/disaggregation/operations-vllm.md#request-cancellation).

## Graceful scale-down

During pod termination, Kubernetes may keep terminating pods in EndpointSlices while marking them not ready for regular traffic, and the kubelet sends `SIGTERM` to containers. The default termination grace period is often shorter than long-running inference requests, so model server shutdown behavior and Kubernetes grace periods must be configured together.

llm-d builds on the Kubernetes API server for model server discovery: the `InferencePool` selector defines pool membership, and the EPP updates its endpoint set as matching pods become ready, unready, or terminating. Expect small propagation windows in EndpointSlices, EPP watches, and gateway or mesh routing caches.

For vLLM, `--shutdown-timeout N` configures how long the server drains running requests after receiving `SIGTERM`. If the timeout expires, remaining requests can be aborted. Set `terminationGracePeriodSeconds` long enough to cover:

- endpoint propagation or pre-stop delay;
- the vLLM `--shutdown-timeout`;
- normal process exit time.

Example shape:

```yaml
spec:
  terminationGracePeriodSeconds: 180
  containers:
  - name: vllm
    command: ["vllm", "serve"]
    args:
    - <model>
    - --shutdown-timeout
    - "150"
    lifecycle:
      preStop:
        exec:
          command: ["/bin/sh", "-c", "sleep 10"]
```

Kubernetes runs `preStop` within the same termination grace period, so include that sleep time in the total budget. Keep Gateway, ingress, and service-mesh drain settings aligned with pod termination and model server drain settings. Shorter gateway drains can cut off streams before vLLM finishes; longer gateway drains can hold or route connections to pods that Kubernetes has already killed.

During rolling updates, preserve enough ready capacity while replacement pods start, avoid mixing incompatible model, tokenizer, or serving argument changes in one serving pool without traffic shifting, and make sure autoscalers do not remove GPU nodes before terminating pods complete their grace period.

## EPP HA

Run the EPP with more than one replica for active-passive failover:

```yaml
inferenceExtension:
  replicas: 2
```

The `InferencePool` references the EPP Service through `endpointPickerRef`. In the default HA mode, replicas provide active-passive failover through leader election. Active-active operation is guide-specific and should only be used when every EPP replica can maintain equivalent state, such as pod-discovery based prefix-cache indexing. Also review:

- `failureMode` on the `InferencePool` `endpointPickerRef`: `FailOpen` and `FailClose` control Gateway behavior when the EPP is unresponsive.
- EPP readiness and resource sizing: overloaded EPP pods can become the routing bottleneck even when model servers have capacity; use [Metrics](monitoring/metrics.md) for readiness and saturation signals.
- Feature state: flow control queues are in-memory, so queued requests on a failed EPP process are lost. Other features, such as prefix-cache indexing, may need feature-specific active-active settings.

## Request rejection

Request rejection is an admission and overload-control behavior, not a model server crash signal.

- With flow control disabled, saturated pools may reject sheddable requests, such as negative-priority requests, with HTTP 429.
- With flow control enabled, queue capacity limits reject requests with HTTP 429, while queue TTL expiry or client disconnect can return HTTP 503.
- SLO-aware admitters can reject requests that cannot meet their configured objective.

For details, see [EPP Flow Control](../architecture/core/router/epp/flow-control.md) and [EPP request handling](../architecture/core/router/epp/request-handling.md).
