# General Serving Operations

Operational flows for a standard (non-disaggregated) llm-d deployment: a single model-serving
replica type behind the router, with no separate prefill and decode instances. For the
disaggregated-serving equivalents, see [Disaggregated Serving Operations](disaggregation/README.md).

## Readiness and Health Probes

Covered in full in [Model-Aware Readiness Probes](readiness-probes.md): use vLLM's
`/v1/models` endpoint for readiness so a pod is only marked Ready once its model has finished
loading, and reserve `/health` for liveness.

## Request Cancellation

Model servers like vLLM support request cancellation: when a client disconnects, the
in-progress request is aborted and its resources are freed, rather than running to completion
for a client that is no longer listening.

The router's coordinator proxies requests with Go's standard `net/http/httputil.ReverseProxy`
(`pkg/coordinator/server/passthrough.go`, `pkg/coordinator/steps/decode_proxy.go` in
llm-d-router). `ReverseProxy` derives the outbound request's context from the inbound one, so
a client disconnect cancels the inbound request's context, which cancels the proxied request to
the model server in turn. The model server sees its connection close and runs its own abort
codepath.

In a non-disaggregated deployment this is the whole path: client disconnects, the router's
proxied connection to the model server closes, the model server frees the request's resources.
There is no second server to notify, unlike the disaggregated case where the prefill instance
also has to release the request's KV cache (see
[Disaggregated Serving: Request Cancellation](disaggregation/vllm.md#request-cancellation)
for that additional step).

## Graceful Shutdown

A model-serving pod should stop receiving new requests before it starts terminating in-flight
ones. The router's own EPP pods already follow this pattern for planned termination
(`router.epp.terminationGracePeriodSeconds`, see
[Router Operations: Graceful Pod Termination](https://github.com/llm-d/llm-d-router/blob/main/docs/operations.md)),
and it applies equally to model-serving deployments in the request path:

1. **Stop intake**: a `preStop` hook delays the SIGTERM long enough for the endpoint to be
   removed from Service/InferencePool routing and for in-flight health checks to observe the
   pod as terminating, so the router stops sending it new requests.
2. **Drain**: `terminationGracePeriodSeconds` gives already-accepted requests time to finish
   before Kubernetes sends SIGKILL. Size it to comfortably exceed your p99 request duration;
   a request still running when the grace period expires is killed mid-flight rather than
   drained.
3. **SIGKILL**: any request still in flight after the grace period is terminated abruptly.
   Request cancellation (above) governs client-visible behavior up to that point; it does not
   extend the grace period.

```yaml
spec:
  terminationGracePeriodSeconds: 120
  containers:
  - name: vllm
    lifecycle:
      preStop:
        exec:
          command: ["sleep", "5"]
```

Set `terminationGracePeriodSeconds` from your own p99 request duration; the value above is
illustrative, not a sizing recommendation.
