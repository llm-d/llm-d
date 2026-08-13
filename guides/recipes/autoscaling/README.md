# Autoscaling recipes

Shared, reusable building blocks for the llm-d workload-autoscaling guides.

**KEDA is the autoscaling backend for llm-d.**

## `metrics-reader/` — OpenShift Thanos auth (kustomize Component)

On OpenShift, KEDA reads metrics from Thanos Querier, whose `:9091` endpoint
rejects unauthenticated queries with a 401 — and KEDA silently serves its
`fallback` replica count when a trigger errors, so an unauthenticated trigger
looks healthy while autoscaling does nothing. This component provisions the
credentials KEDA needs:

- a dedicated `keda-epp-metrics-reader` ServiceAccount,
- a `cluster-monitoring-view` ClusterRoleBinding (the role Thanos requires),
- its token Secret (OpenShift injects both the bearer `token` and the
  `service-ca.crt` that verifies Thanos's serving certificate).

Guides that run **no** WVA controller — `keda-epp-queue` and `keda-epp-saturation`
— include this component from their OCP overlay:

```yaml
# <guide>/optimized-baseline/ocp/kustomization.yaml
components:
  - ../../../../recipes/autoscaling/metrics-reader
```

Each guide keeps its own `ScaledObject` and `TriggerAuthentication`, repointing
the TriggerAuth at `keda-epp-metrics-reader-token`.

The **WVA path does not use this component** — it borrows the WVA
controller-manager's ServiceAccount token, which the WVA install already grants
`cluster-monitoring-view`.

### Deploying to multiple namespaces on one cluster

The ClusterRoleBinding is cluster-scoped, so give it a namespace-unique name with
a **targeted patch on `metadata.name`** in the consumer overlay. Do **not** use a
global `nameSuffix`: it also renames the token Secret, and kustomize cannot
rewrite the CRD field `TriggerAuthentication.spec.secretTargetRef.name`, so the
auth reference would dangle. A patch on just the ClusterRoleBinding name is safe
— nothing references it by name.
