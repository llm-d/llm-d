# InferencePool

`InferencePool` is the Schema for the `InferencePools` API. It defines a pool of inference endpoints that can be used by a Gateway.

**Group:** `inference.networking.k8s.io`  
**Version:** `v1`

---

## InferencePool

| Field | Description |
| --- | --- |
| `apiVersion` | `inference.networking.k8s.io/v1` |
| `kind` | `InferencePool` |
| `metadata` | [metav1.ObjectMeta](https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.28/#objectmeta-v1-meta) |
| `spec` | [InferencePoolSpec](#inferencepoolspec) <br> **Required** <br> Spec defines the desired state of the InferencePool. |
| `status` | [InferencePoolStatus](#inferencepoolstatus) <br> Status defines the observed state of the InferencePool. |

## InferencePoolSpec

`InferencePoolSpec` defines the desired state of the InferencePool.

| Field | Description |
| --- | --- |
| `selector` | [LabelSelector](#labelselector) <br> **Required** <br> Selector determines which Pods are members of this inference pool. It matches Pods by their labels only within the same namespace; cross-namespace selection is not supported. <br> The structure is intentionally simple to be compatible with Kubernetes Service selectors. |
| `targetPorts` | [][Port](#port) <br> **Required** <br> TargetPorts defines a list of ports that are exposed by this InferencePool. Every port will be treated as a distinctive endpoint by EPP, addressable as a `podIP:portNumber` combination. <br> Max items: 8. Port numbers must be unique. |
| `appProtocol` | [AppProtocol](#appprotocol) <br> AppProtocol describes the application protocol for all the target ports. If unspecified, the protocol defaults to `http` (HTTP/1.1). |
| `endpointPickerRef` | [EndpointPickerRef](#endpointpickerref) <br> **Required** <br> EndpointPickerRef is a reference to the Endpoint Picker extension and its associated configuration. |

## InferencePoolStatus

`InferencePoolStatus` defines the observed state of the InferencePool.

| Field | Description |
| --- | --- |
| `parents` | [][ParentStatus](#parentstatus) <br> Parents is a list of parent resources, typically Gateways, that are associated with the InferencePool, and the status of the InferencePool with respect to each parent. <br> Max items: 32. |

## Port

`Port` defines the network port that will be exposed by this InferencePool.

| Field | Description |
| --- | --- |
| `number` | `int32` <br> **Required** <br> Number defines the port number to access the selected model server Pods. Must be in range 1 to 65535. |

## AppProtocol

`AppProtocol` describes the application protocol for a port.

Supported values:
- `http`: HTTP/1.1. This is the default.
- `kubernetes.io/h2c`: HTTP/2 over cleartext. Typically used for gRPC workloads where TLS is terminated at the Gateway.

## EndpointPickerRef

`EndpointPickerRef` specifies a reference to an Endpoint Picker extension and its associated configuration.

| Field | Description |
| --- | --- |
| `group` | `string` <br> Group of the referent API object. Defaults to "" (Core API group). |
| `kind` | `string` <br> Kind of the referent. Defaults to `Service`. Implementations MUST NOT support `ExternalName` Services. |
| `name` | `string` <br> **Required** <br> Name of the referent API object. |
| `port` | [Port](#port) <br> Port of the Endpoint Picker extension service. Required when `kind` is `Service`. |
| `failureMode` | `string` <br> Configures how the parent handles cases when the Endpoint Picker extension is non-responsive. <br> Defaults to `FailClose`. <br> Supported values: `FailOpen`, `FailClose`. |

## ParentStatus

`ParentStatus` defines the observed state of InferencePool from a Parent, i.e. Gateway.

| Field | Description |
| --- | --- |
| `conditions` | [][metav1.Condition](https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.28/#condition-v1-meta) <br> Conditions provide information about the observed state. Supported types: `Accepted`, `ResolvedRefs`. |
| `parentRef` | [ParentReference](#parentreference) <br> **Required** <br> Identifies the parent resource this status is associated with. |
| `controllerName` | `string` <br> Name of the controller that wrote this status (e.g., `example.net/gateway-controller`). |

## ParentReference

`ParentReference` identifies an API object, such as a Gateway.

| Field | Description |
| --- | --- |
| `group` | `string` <br> Group of the referent. Defaults to `gateway.networking.k8s.io`. |
| `kind` | `string` <br> Kind of the referent. Defaults to `Gateway`. |
| `name` | `string` <br> **Required** <br> Name of the referent. |
| `namespace` | `string` <br> Namespace of the referenced object. Defaults to the local namespace. |

## LabelSelector

`LabelSelector` defines a query for resources based on their labels.

| Field | Description |
| --- | --- |
| `matchLabels` | `map[string]string` <br> **Required** <br> A set of {key,value} pairs. An object must match every label in this map (AND operation). <br> Max properties: 64. |

---

## Condition Types and Reasons

### Accepted
Indicates whether the `InferencePool` has been accepted or rejected by a Parent.

- **True Reasons:**
  - `Accepted`: Supported by parent.
- **False Reasons:**
  - `NotSupportedByParent`: Parent does not support InferencePool as a backend.
  - `HTTPRouteNotAccepted`: Referenced by an HTTPRoute that has been rejected.
- **Unknown Reasons:**
  - `Pending`

### ResolvedRefs
Indicates whether the controller was able to resolve all object references.

- **True Reasons:**
  - `ResolvedRefs`
- **False Reasons:**
  - `InvalidExtensionRef`: Extension is invalid (unsupported kind/group or not found).

### Exported
Indicates whether the controller was able to export the InferencePool to specified clusters.

- **True Reasons:**
  - `Exported`
- **False Reasons:**
  - `NotRequested`: No export was requested.
  - `NotSupported`: Export requested but not supported by implementation.
