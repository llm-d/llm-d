# InferenceObjective

`InferenceObjective` represents the desired state of a specific model use case. It allows "Inference Workload Owners" to define performance and latency goals for a model across one or more `InferencePool` resources in the same namespace.

**Group:** `llm-d.ai`
**Version:** `v1` (served; `v1alpha2` is deprecated and will be removed in v1.2)

---

## InferenceObjective

| Field | Description |
| --- | --- |
| `apiVersion` | `llm-d.ai/v1` <br/> (for `llm-d.ai/v1alpha2`, see [Deprecation and Migration](#deprecation-and-migration)) |
| `kind` | `InferenceObjective` |
| `metadata` | [metav1.ObjectMeta](https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.28/#objectmeta-v1-meta) |
| `spec` | [InferenceObjectiveSpec](#inferenceobjectivespec) <br/> Spec represents the desired state of the model use case. |
| `status` | [InferenceObjectiveStatus](#inferenceobjectivestatus) <br/> Status defines the observed state of the InferenceObjective. |

## InferenceObjectiveSpec

`InferenceObjectiveSpec` defines the priority and the pool targeting for the model workload. At least one of `poolRefs` or `poolSelector` must be specified (when both are set, the objective applies to any pool matched by either field).

| Field | Description |
| --- | --- |
| `priority` | `int32` <br/> **Optional** <br/> Defines how important it is to serve the request compared to other requests in the same pool. Higher values indicate higher priority; negative values are allowed (designating requests as sheddable). An unset value is treated as `0`. When resources are scarce, flow control always allows requests of higher priority to be served first. Fairness is enforced only between requests of the same priority. |
| `poolRefs` | [][PoolObjectReference](#poolobjectreference) <br/> **Optional** <br/> Targets inference pools in the same namespace that this objective applies to. An objective applies to a pool when any entry matches. Contains 1 to 16 items, unique by pool `name`. Either `poolRefs` or `poolSelector` (or both) must be set. |
| `poolSelector` | [metav1.LabelSelector](https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.28/#labelselector-v1-meta) <br/> **Optional** <br/> Selects inference pools in the same namespace by label. An objective applies to a pool when the selector matches its labels. The selector must not be empty (`matchLabels` or `matchExpressions` must specify at least one requirement; targeting every pool in the namespace with `{}` is rejected). Either `poolRefs` or `poolSelector` (or both) must be set. |

## InferenceObjectiveStatus

`InferenceObjectiveStatus` defines the observed state of InferenceObjective.

| Field | Description |
| --- | --- |
| `conditions` | [][metav1.Condition](https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.28/#condition-v1-meta) <br/> **Optional** <br/> Conditions track the state of the InferenceObjective (maximum 8 items, keyed by `type`). Known type: `Accepted`. |

## PoolObjectReference

`PoolObjectReference` identifies an API object within the same namespace.

| Field | Description |
| --- | --- |
| `group` | `string` <br/> Group of the referent. Defaults to `inference.networking.k8s.io`. Max length 253 (RFC 1123 subdomain). |
| `kind` | `string` <br/> Kind of the referent. Defaults to `InferencePool`. Length 1–63. |
| `name` | `string` <br/> **Required** <br/> Name of the referent. Length 1–253. |

---

## Condition Types and Reasons

### Accepted

Indicates if the objective configuration is accepted.

- **True Reasons:**
  - `Accepted`: Model conforms to the state of the pool.
- **Unknown Reasons:**
  - `Pending`: Initial state, controller has not yet reconciled the resource.

---

## Deprecation and Migration

### `llm-d.ai/v1alpha2` Deprecation

`llm-d.ai/v1alpha2` InferenceObjective is deprecated and scheduled for removal in v1.2:

- **Deprecation warning**: `llm-d.ai/v1alpha2 InferenceObjective is deprecated; use llm-d.ai/v1`.
- **API Differences**:
  - In `v1alpha2` (which remains the storage version during the transition window), single-pool targeting used `spec.poolRef`. The `v1alpha2` schema is widened to also accept `spec.poolRefs` and `spec.poolSelector` so objects written through either version store losslessly under `None` conversion. At least one of `poolRef`, `poolRefs`, or `poolSelector` must be set, and `poolRef` cannot be combined with `poolRefs` or `poolSelector`.
  - In `v1`, `spec.poolRef` is removed. Objectives target pools via `spec.poolRefs` (1–16 pool references, unique by `name`) and/or `spec.poolSelector` (a non-empty label selector). At least one targeting field (`poolRefs` or `poolSelector`) must be set; if both are set, the objective applies to the union of matching pools.
- **Controller Behavior**: When both `v1` and `v1alpha2` are served, the Endpoint Picker (EPP) watches both versions, evaluates `v1` first (converting `v1alpha2` objects to the `v1` shape at the edge), and logs a deprecation warning for `v1alpha2` objects.

### Migrating to `llm-d.ai/v1`

To migrate existing manifests in place to `v1`:

1. Update `apiVersion` from `llm-d.ai/v1alpha2` to `llm-d.ai/v1`.
2. Replace `poolRef` with `poolRefs` (as a single-element list) or `poolSelector`.

#### Example: Migrating `poolRef` to `poolRefs`

```yaml
# Before (llm-d.ai/v1alpha2 - deprecated)
apiVersion: llm-d.ai/v1alpha2
kind: InferenceObjective
metadata:
  name: premium-traffic
spec:
  priority: 100
  poolRef:
    name: default-pool
```

```yaml
# After (llm-d.ai/v1)
apiVersion: llm-d.ai/v1
kind: InferenceObjective
metadata:
  name: premium-traffic
spec:
  priority: 100
  poolRefs:
  - name: default-pool
```

#### Example: Targeting Pools by Label Selector

You can also target multiple pools dynamically using `poolSelector`:

```yaml
apiVersion: llm-d.ai/v1
kind: InferenceObjective
metadata:
  name: premium-traffic
spec:
  priority: 100
  poolSelector:
    matchLabels:
      tiers: shared
```
