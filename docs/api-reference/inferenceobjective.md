# InferenceObjective

`InferenceObjective` represents the desired state of a specific model use case. It allows "Inference Workload Owners" to define performance and latency goals for a model within an `InferencePool`.

**Group:** `inference.networking.x-k8s.io`  
**Version:** `v1alpha2`

---

## InferenceObjective

| Field | Description |
| --- | --- |
| `apiVersion` | `inference.networking.x-k8s.io/v1alpha2` |
| `kind` | `InferenceObjective` |
| `metadata` | [metav1.ObjectMeta](https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.28/#objectmeta-v1-meta) |
| `spec` | [InferenceObjectiveSpec](#inferenceobjectivespec) <br> Spec represents the desired state of the model use case. |
| `status` | [InferenceObjectiveStatus](#inferenceobjectivestatus) <br> Status defines the observed state of the InferenceObjective. |

## InferenceObjectiveSpec

`InferenceObjectiveSpec` defines the priority and the pool reference for the model workload.

| Field | Description |
| --- | --- |
| `priority` | `int` <br> **Optional** <br> Defines how important it is to serve the request compared to others in the same pool. Higher values have higher priority. Unset value is treated as `0`. Requests of higher priority are served first when resources are scarce. |
| `poolRef` | [PoolObjectReference](#poolobjectreference) <br> **Required** <br> Reference to the inference pool. The pool must exist in the same namespace. |

## InferenceObjectiveStatus

`InferenceObjectiveStatus` defines the observed state of InferenceObjective.

| Field | Description |
| --- | --- |
| `conditions` | [][metav1.Condition](https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.28/#condition-v1-meta) <br> Conditions track the state of the InferenceObjective. Known type: `Accepted`. |

## PoolObjectReference

`PoolObjectReference` identifies an API object within the same namespace.

| Field | Description |
| --- | --- |
| `group` | `string` <br> Group of the referent. Defaults to `inference.networking.k8s.io`. |
| `kind` | `string` <br> Kind of the referent. Defaults to `InferencePool`. |
| `name` | `string` <br> **Required** <br> Name of the referent. |

---

## Condition Types and Reasons

### Accepted
Indicates if the objective configuration is accepted.

- **True Reasons:**
  - `Accepted`: Model conforms to the state of the pool.
- **Unknown Reasons:**
  - `Pending`: Initial state, controller has not yet reconciled the resource.
