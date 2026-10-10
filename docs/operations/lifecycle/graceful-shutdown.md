# Graceful Shutdown & Request Draining

## Overview

When a pod in an llm-d deployment is terminated — during a scale-down, a rolling
update, or a node drain — in-flight inference requests are at risk. Because
inference requests are long-running and compute-intensive, abruptly killing a pod
can fail requests that were seconds away from completing.

Handling termination gracefully spans **two layers**:

1. **Routing layer** (Router / Endpoint Picker) — stop sending *new* requests to
   the terminating pod, and drain any requests still queued in the scheduler.
2. **Model server layer** (vLLM) — let *in-flight* inference requests finish (or
   drain for a bounded window) before the process exits.

This guide covers graceful shutdown for **general (non-disaggregated) serving**.
For prefill/decode disaggregation, where in-flight requests span multiple servers
and KV caches are held across instances, see
[Disaggregated Serving Operations (vLLM)](../disaggregation/vllm.md).

## The Kubernetes Pod Termination Sequence

llm-d relies on the standard
[Kubernetes pod termination process](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#pod-termination):

1. **Termination triggered** — the pod's state is set to `Terminating`.
2. **`InferencePool` update** — the pod is removed from the endpoints of its
   associated `InferencePool`, so the Router stops routing **new** traffic to it.
   (For standard Kubernetes objects this is equivalent to removal from a
   `Service`.)
3. **`preStop` hook** — if defined, the container's `preStop` hook executes.
4. **`SIGTERM`** — Kubernetes sends `SIGTERM` to the main process in each
   container.
5. **Termination grace period** — the pod is given
   `terminationGracePeriodSeconds` (default **30s**) to exit. If it has not
   exited by the end of this window, Kubernetes sends `SIGKILL` to force
   termination.

New traffic is handled automatically by step 2. The rest of this guide is about
what happens to requests that are **already in flight** when `SIGTERM` arrives.

## Draining the Model Server (vLLM)

How vLLM responds to `SIGTERM` determines whether in-flight requests are drained
or dropped:

- **Default** — vLLM immediately `aborts` in-flight requests and exits. Those
  requests fail with an error status code.
- **With `--shutdown-timeout N`** — vLLM catches `SIGTERM` and continues serving
  the currently running requests for up to `N` seconds. After the timeout, any
  requests still in flight are `aborted` and returned an error.

> [!IMPORTANT]
> `terminationGracePeriodSeconds` must be **greater than** `--shutdown-timeout`
> (plus a small buffer for the `preStop` hook and process cleanup). Otherwise
> Kubernetes sends `SIGKILL` before the drain completes, and the graceful window
> is never fully used.

## Request Cancellation on Client Disconnect

Independent of shutdown, model servers like vLLM support **request
cancellation**: when a client disconnects, the in-progress request is freed
rather than run to completion. When a request is disconnected, vLLM triggers its
`abort` codepath, releasing the KV cache and compute resources held by that
request.

This matters during shutdown and rolling updates: clients (or the gateway) that
retry against a draining pod should disconnect the original request so its
resources are reclaimed promptly instead of being held until the request
finishes on its own.

For the disaggregated case — where a cancelled request may have KV blocks held on
a separate prefill instance — see
[Request Cancellation](../disaggregation/vllm.md#request-cancellation).

## Draining the Routing Layer (EPP)

The Endpoint Picker (EPP) queues requests before dispatching them to model
servers. During a graceful shutdown, the EPP's flow controller **evicts queued
requests with a retryable `503 Service Unavailable`** (outcome
`rejected-shutting-down`), signaling transient unavailability so that clients and
upstream gateways **retry** rather than treating the drain as a hard fault.

These retryable semantics only apply while the EPP is still reachable over its
`ext_proc` stream. Once the EPP process is actually gone, the outcome is governed
by the `InferencePool`'s
[`failureMode`](../../architecture/router/inferencepool.md). For high-availability
Router configurations and `failOpen` behavior during leader teardown, see
[Router Operations](../components/router.md).

For the full set of flow-control outcome codes, see
[Flow Control](../../architecture/router/flow-control.md).

## Recommended Configuration

A model server pod configured for graceful draining:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: vllm
spec:
  # Must exceed --shutdown-timeout plus a buffer for preStop + cleanup.
  terminationGracePeriodSeconds: 120
  containers:
  - name: vllm
    image: ghcr.io/llm-d/llm-d:latest
    args:
    - "--shutdown-timeout"
    - "90"          # Drain in-flight requests for up to 90s after SIGTERM.
    ports:
    - containerPort: 8000
      protocol: TCP
    lifecycle:
      preStop:
        exec:
          # Delay SIGTERM so the Router stops picking this pod first.
          command: ["/bin/sleep", "15"]
    # Readiness gates traffic; see readiness-probes.md.
    readinessProbe:
      httpGet:
        path: /v1/models
        port: 8000
      periodSeconds: 5
      timeoutSeconds: 2
      failureThreshold: 3
```

Guidance:

- Set `--shutdown-timeout` to roughly the p99 request duration you want to allow
  to complete on drain.
- Set `terminationGracePeriodSeconds` above that with headroom. The grace period
  starts when the `preStop` hook starts, so budget for the hook, the drain, and
  engine teardown (here 120s = 15s `preStop` + 90s drain + 15s teardown).
- Pair with model-aware readiness probes so pods are only marked `Ready` once the
  model is loaded — see [Readiness Probes](readiness-probes.md).

### Defaults in the guides

These vLLM model server overlays ship with this pattern, using a shorter drain
window than the example above:

- [Optimized Baseline](../../../guides/optimized-baseline/README.md): NVIDIA GPU,
  AMD, CPU, Intel XPU, TPU v6 and TPU v7.
- [Precise Prefix Cache Routing](../../../guides/precise-prefix-cache-routing/README.md):
  NVIDIA GPU, AMD, CPU, Intel XPU, TPU v6 and TPU v7.
- [Predicted Latency Routing](../../../guides/predicted-latency-routing/README.md):
  the aggregated NVIDIA GPU, AMD, Intel XPU, TPU v6 and TPU v7 model servers.

This includes their `gke` and `amd-ci` `INFRA_PROVIDER` variants, which build
on the `base` overlays.

Other overlays do not drain yet (tracked in
[llm-d#1525](https://github.com/llm-d/llm-d/issues/1525)), including the
Optimized Baseline Iluvatar, MetaX, Rebellions NPU and TPU v7 dynamic-slice
overlays, and the model servers Predicted Latency Routing reuses from other
guides for its P/D and multimodal paths.

| Setting | Value | Where it is set |
| --- | --- | --- |
| `--shutdown-timeout` | 45s | The vLLM `args` in each guide's model server patch |
| `preStop` sleep | 15s | [`graceful-shutdown` component](../../../guides/recipes/modelserver/components/graceful-shutdown/kustomization.yaml) |
| `terminationGracePeriodSeconds` | 75s (15s + 45s + 15s) | [`graceful-shutdown` component](../../../guides/recipes/modelserver/components/graceful-shutdown/kustomization.yaml) |

If your requests run longer, raise `--shutdown-timeout` and set a matching
`terminationGracePeriodSeconds` in the same patch. A value in the guide's patch
overrides the component.

## Verification

Send a long-running request, then delete the pod and confirm the request
completes instead of failing:

```bash
# Start a long generation in the background against a target pod.
# Then trigger termination:
kubectl delete pod -n llm-d <pod-name>

# Watch the drain in the model server logs.
kubectl logs -n llm-d <pod-name> --tail=50 | grep -iE "shutdown|drain|abort|SIGTERM"

# Observe the pod staying in Terminating until the drain window elapses.
kubectl get pod -n llm-d <pod-name> -w
```

Expected behavior with `--shutdown-timeout` set:

1. Pod enters `Terminating`; it is removed from the `InferencePool` (no new
   traffic).
2. vLLM catches `SIGTERM` and keeps serving in-flight requests.
3. The in-flight request completes successfully.
4. The pod exits before `terminationGracePeriodSeconds` elapses (no `SIGKILL`).

## Troubleshooting

**In-flight requests still fail on scale-down / rollout**

- Confirm `--shutdown-timeout` is set; without it vLLM aborts immediately.
- Confirm `terminationGracePeriodSeconds > --shutdown-timeout`; otherwise
  `SIGKILL` fires mid-drain.

**Clients see hard errors instead of retries during shutdown**

- Ensure clients/gateway treat `503` as retryable — the EPP drains queued
  requests with a retryable `503`, not a fatal error.
- Check the `InferencePool` `failureMode` for behavior once the EPP is gone.

## Additional Resources

- [Disaggregated Serving Operations (vLLM)](../disaggregation/vllm.md)
- [Readiness Probes](readiness-probes.md)
- [Router Operations](../components/router.md)
- [Flow Control](../../architecture/router/flow-control.md)
- [Kubernetes Pod Termination](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#pod-termination)
