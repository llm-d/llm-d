# Security: Lock Down the ModelExpress Metadata Broker

Assumes the [main guide](./README.md) is deployed and its environment variables are set.

> [!NOTE]
> The broker's gRPC API (`:8001`) does not authenticate callers in the `0.5.0` release, so on a shared cluster restrict who can talk to it. This section does that with Istio mTLS plus an AuthorizationPolicy. (Optional, requires Istio; skip it on a single-tenant cluster.)

**Scope.** These policies protect only the broker's gRPC API. The weight transfers themselves ride the RDMA fabric, which the mesh never sees; this guide assumes that fabric is trusted. The decode pods join the mesh with the NIXL/worker ports excluded from the sidecar, so weight transfer keeps working.

> **Pre-stage weights when the mesh is on.** With a sidecar injected, the seed pod's large HuggingFace download can stall behind the sidecar proxy (the gRPC control plane and small API calls are fine; the multi-GB transfer is the problem). Point the seed at a pre-staged checkpoint (for example, the prewarmed `fst-model-cache` NFS PVC from [measuring-storage-paths](./measuring-storage-paths.md), mounted at the model path) instead of downloading from HF in-mesh. Receivers are unaffected, because they pull over RDMA, which bypasses the sidecar.

What gets applied:

* A `PeerAuthentication` (STRICT) scoped to the `modelexpress-server` workload. The broker only accepts mutually-authenticated callers. It is workload-scoped, not namespace-wide, so model-serving traffic (EPP → decode `:8000`) and other workloads are unaffected.
* An `AuthorizationPolicy` on the broker that allows only this guide's decode ServiceAccount (`modelexpress-p2p-nvidia-gpu-vllm-sa`) to reach `:8001`. An ALLOW policy that selects a workload implicitly denies everyone else.

## 1. Enable sidecar injection and put decode in the mesh

```bash
# Requires Istio. Inject sidecars into the broker (and the namespace generally):
kubectl label namespace ${NAMESPACE} istio-injection=enabled --overwrite
```

Layer the `istio-mesh` component onto your model-server overlay so the decode pods get a sidecar **with the RDMA/NIXL ports excluded** (the prewarm Job already opts out of injection, so it can complete):

```yaml
# guides/modelexpress-p2p/modelserver/gpu/vllm/coreweave/kustomization.yaml
components:
  - ../components/istio-mesh
```

Re-apply the model-server overlay ([main guide Step 3](./README.md#3-deploy-the-model-server)) and re-roll the broker so both pick up sidecars:

```bash
kubectl rollout restart deploy/modelexpress-server -n ${NAMESPACE}
```

The decode pods should now show a second container (`istio-proxy`). Confirm the NIXL ports are excluded:

```bash
kubectl get pod -n ${NAMESPACE} -l llm-d.ai/guide=modelexpress-p2p \
    -o jsonpath='{.items[0].metadata.annotations.traffic\.sidecar\.istio\.io/excludeOutboundPorts}{"\n"}'
# -> 5555,5556,6555,6556
```

## 2. Apply mTLS + the AuthorizationPolicy

`${NAMESPACE}` is expanded into the AuthorizationPolicy principal (the SPIFFE ID embeds the namespace):

```bash
: "${NAMESPACE:?export NAMESPACE first}" # an empty namespace would render a principal that matches nothing
envsubst '$NAMESPACE' < ${REPO_ROOT}/guides/${GUIDE_NAME}/security/istio-mtls-authz.yaml \
    | kubectl apply -n ${NAMESPACE} -f -
```

## 3. Verify it's enforced

```bash
# P2P still works: scale out and confirm receivers reach Ready (sidecars + mTLS
# in place, RDMA ports excluded). Then prove an unauthorized caller is denied.
# A bare TCP connect (nc -z) is not a valid test: the handshake completes at the
# broker's sidecar before RBAC resets it. Use a client that actually sends data:
kubectl run mx-probe --rm -it -n ${NAMESPACE} --image=nicolaka/netshoot --restart=Never \
    -- grpcurl -plaintext -max-time 5 modelexpress-server:8001 list
# A pod NOT running as the decode ServiceAccount should get a connection reset
# or PermissionDenied here, while the decode pods keep working.
```

If P2P breaks after enabling this, the usual cause is the NIXL port exclusion not matching your TP size. Widen `excludeInbound/OutboundPorts` in `components/istio-mesh/patch-istio.yaml` to cover `MX_METADATA_PORT..+TP-1` and `MX_WORKER_GRPC_PORT..+TP-1`.
