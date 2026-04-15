# HTTPRoute

These manifests are sample `HTTPRoutes` uses across llm-d. `HTTPRoutes` are used to attach an `InferencePool` to a `Gateway`.

A few notes:
- These `HTTPRoutes` set the timeouts to the maximum, since LLM inference requests are much longer than standard application traffic.
- GKE Gateway does not allow timeouts to be set, so there is no timeout for the `httproute-gke.yaml` example.
