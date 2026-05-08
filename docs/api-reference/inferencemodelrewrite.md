# InferenceModelRewrite

`InferenceModelRewrite` defines rules for rewriting inference requests, such as traffic splitting, A/B tests, or canary rollouts across weighted model targets.

**Group:** `inference.networking.x-k8s.io`  
**Version:** `v1alpha2`

---

## InferenceModelRewrite

| Field | Description |
| --- | --- |
| `apiVersion` | `inference.networking.x-k8s.io/v1alpha2` |
| `kind` | `InferenceModelRewrite` |
| `metadata` | [metav1.ObjectMeta](https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.28/#objectmeta-v1-meta) |
| `spec` | [InferenceModelRewriteSpec](#inferencemodelrewritespec) <br> Spec defines the desired state of the rewrite rules. |
| `status` | [InferenceModelRewriteStatus](#inferencemodelrewritestatus) <br> Status defines the observed state of the resource. |

## InferenceModelRewriteSpec

| Field | Description |
| --- | --- |
| `poolRef` | [PoolObjectReference](#poolobjectreference) <br> **Required** <br> Reference to the target `InferencePool`. |
| `rules` | [][InferenceModelRewriteRule](#inferencemodelrewriterule) <br> **Required** <br> Ordered set of rules. The first rule to match a request is used. |

## InferenceModelRewriteRule

`InferenceModelRewriteRule` defines the match criteria and corresponding actions (targets).

| Field | Description |
| --- | --- |
| `matches` | [][Match](#match) <br> **Optional** <br> Criteria for matching a request. Logical OR if multiple criteria are specified. If empty, matches all requests. |
| `targets` | [][TargetModel](#targetmodel) <br> **Optional** <br> How to distribute traffic across weighted model targets. Min items: 1. |

## Match

`Match` defines the criteria for matching LLM requests.

| Field | Description |
| --- | --- |
| `model` | [ModelMatch](#modelmatch) <br> **Required** <br> Criteria for matching the `model` field in the JSON request body. |

## ModelMatch

| Field | Description |
| --- | --- |
| `type` | `string` <br> Kind of string matching to use. Supported value: `Exact`. Defaults to `Exact`. |
| `value` | `string` <br> **Required** <br> The model name string to match against. |

## TargetModel

`TargetModel` defines a weighted model destination.

| Field | Description |
| --- | --- |
| `weight` | `int32` <br> **Optional** <br> Proportion of requests forwarded to the model. Computed as `weight/(sum of all weights)`. Min: 1, Max: 1000000. If set for one, must be set for all. |
| `modelRewrite` | `string` <br> **Required** <br> The static model name to rewrite the request to. |

## InferenceModelRewriteStatus

| Field | Description |
| --- | --- |
| `conditions` | [][metav1.Condition](https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.28/#condition-v1-meta) <br> Conditions track the state. Known type: `Accepted`. |

## PoolObjectReference

`PoolObjectReference` identifies an API object within the same namespace.

| Field | Description |
| --- | --- |
| `group` | `string` <br> Group of the referent. Defaults to `inference.networking.k8s.io`. |
| `kind` | `string` <br> Kind of the referent. Defaults to `InferencePool`. |
| `name` | `string` <br> **Required** <br> Name of the referent. |

---

## Precedence and Conflict Resolution

1. **Model Match Precision:** Rules with an `Exact` model match take precedence over generic matches (empty `matches`).
2. **Resource Age:** If multiple resources target the same pool with identical matches, the oldest resource (by creation timestamp) takes precedence.
3. **Rule Order:** Within a single resource, the FIRST matching rule (in list order) is used.

---

## Condition Types and Reasons

### Accepted
Indicates if the rewrite is valid, non-conflicting, and applied to the pool.

- **True Reasons:**
  - `Accepted`: Rewrite is valid and successfully applied.
- **Unknown Reasons:**
  - `Pending`: Initial state, controller has not yet reconciled the resource.
