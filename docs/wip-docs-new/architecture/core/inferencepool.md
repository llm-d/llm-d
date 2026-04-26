# InferencePool

## Functionality

An [InferencePool](https://gateway-api-inference-extension.sigs.k8s.io/api-types/inferencepool/) is a Kubernetes custom resource defined by the Gateway API Inference Extension project. It is the set of Model Servers that an EPP (Endpoint Picker Pod) considers in routing a request.

Model Server Pods within an InferencePool share the same:

- **Compute configuration** (CPU, memory, GPU resources)
- **Accelerator type** (e.g., NVIDIA H100, AMD MI300X, Google TPUv6e)
- **Model server** (vLLM or SGLang)
- **Model** (e.g. `openai/gpt-oss-120`)

The EPP uses the InferencePool to discover available Model Server pods and intelligently route requests to the optimal replica based on metrics like KV-cache utilization, queue depth, and prefix cache hits. Each InferencePool has exactly one associated EPP (and vice-versa).

## Design

### InferencePool Spec

The InferencePool custom resource has three core fields:

#### `selector`

A set of label key-value pairs used to identify which Pods belong to the pool. Labels must exactly match the labels applied to your Model Server Pods. Model Servers join a pool automatically when their labels match -- no explicit registration is required.

#### `targetPorts`

The port number(s) the gateway uses to route traffic to Model Server Pods within the pool. For standard deployments, a single port (typically `8000`) is sufficient. For advanced use cases like Data Parallelism (DP)-aware routing, multiple ports can be specified to address individual DP ranks within a Pod. Each port is considered as a separate endpoint in the EPP selection logic.

#### `extensionRef`

A reference to the Endpoint Picker extension service that monitors metrics and provides routing decisions. This is managed by the Helm chart and includes the service name, port number, and failure mode.

A raw InferencePool resource looks like this:

```yaml
apiVersion: inference.networking.k8s.io/v1
kind: InferencePool
metadata:
  name: my-infpool
spec:
  targetPorts:
    - number: 8000
  selector:
    llm-d.ai/model: my-model
  extensionRef:
    name: my-epp
    port: 9002
    failureMode: FailOpen
```

The full spec is defined [in the GAIE documentation](https://gateway-api-inference-extension.sigs.k8s.io/reference/spec/#inferencepool).

### Model Server Discovery

Model Servers are discovered dynamically via Kubernetes label selectors. To add a Model Server to an InferencePool, apply the labels specified in the InferencePool's `modelServers.matchLabels` to the Model Server's Pod template. For example, to add to a Model Server to `my-infpool`, you can add the following label to the pod:

```yaml
labels:
  llm-d.ai/model: my-model
```

No explicit registration or enrollment is required. Once the labels match, the Model Server Pods automatically appear as endpoints in the InferencePool and the EPP begins routing traffic to them, simplifying operational workflows.

## Configuration

### Installing the CRDs

The InferencePool CRDs can be installed from the Gateway Inference Extension API repository:

```bash
IGW_LATEST_RELEASE=$(curl -s https://api.github.com/repos/kubernetes-sigs/gateway-api-inference-extension/releases \
  | jq -r '.[] | select(.prerelease == false) | .tag_name' \
  | sort -V \
  | tail -n1)

kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${IGW_LATEST_RELEASE}/manifests.yaml
```

### Deploying an InferencePool

An instance of an InferencePool and associated EPP can be deployed using the Helm charts:
- [Chart For Deployment with Standalone Proxy](https://github.com/kubernetes-sigs/gateway-api-inference-extension/tree/main/config/charts/standalone)
- [Chart For Deployment with Gateway API](https://github.com/kubernetes-sigs/gateway-api-inference-extension/tree/main/config/charts/inferencepool)

#### Helm Values

Configuration is split into two sections in the Helm values file:

- `inferencePool` which defines the pool itself

| Field | Description | Example |
|---|---|---|
| `targetPorts` | List of port numbers to route traffic to on Model Server Pods | `[{number: 8000}]` |
| `modelServerType` | Type of model server (`vllm` or `sglang`) | `vllm` |
| `modelServers.matchLabels` | Kubernetes label selector for discovering Model Server Pods | `{llm-d.ai/model: "my-model"}` |

- `inferenceExtension` which defines the EPP deployed alongside the pool:

| Field | Description | Example |
|---|---|---|
| `replicas` | Number of EPP replicas | `1` |
| `image` | Container image for the EPP | `ghcr.io/llm-d/llm-d-inference-scheduler:v0.7.0` |
| `extProcPort` | Port the EPP listens on for ext-proc traffic from the proxy | `9002` |
| `pluginsConfigFile` | Filename for the scheduling plugin configuration | `"custom-plugins.yaml"` |
| `pluginsCustomConfig` | Inline scheduling plugin configuration (see [EPP](epp/README.md)) | See examples below |
| `tracing.enabled` | Enable OpenTelemetry distributed tracing | `false` |
| `monitoring.prometheus.enabled` | Enable Prometheus metrics scraping | `true` |

> See [EPP](epp/README.md) for more details on `inferenceExtension` design and configuration

#### Connecting to a Proxy

#### Standalone

When using a Standalone Proxy, the InferencePool and EPP can be deployed via Helm.

```bash
 helm install my-infpool \
 --set inferencePool.modelServers.matchLabels.llm-d.ai/model=my-model \
  oci://us-central1-docker.pkg.dev/k8s-staging-images/gateway-api-inference-extension/charts/standalone
```

> Note that in the "Standalone" deployment, the proxy is deployed as a sidecar to the EPP via the above Chart.

See the [full Helm Chart](https://github.com/kubernetes-sigs/gateway-api-inference-extension/tree/main/config/charts/standalone) for configuration details.

##### Gateway API

When using Gateway API, the InferencePool is referenced as a backend in an `HTTPRoute`. This simple example routes all incoming traffic through the Gateway to the InferencePool, where the EPP selects the optimal Model Server within the InferencePool for each request.

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: my-route
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: llm-d-inference-gateway
  rules:
    - backendRefs:
        - group: inference.networking.k8s.io
          kind: InferencePool
          name: my-infpool
          port: 8000
      matches:
        - path:
            type: PathPrefix
            value: /
```

An HTTPRoute can have:

- multiple backendRefs that reference the same InferencePool and therefore routes to the same EPP
- multiple backendRefs that reference different InferencePools and therefore routes to different EPP (e.g. for traffic splitting in roll-outs)

The following example creates an InferencePool and with the default EPP:

```bash
helm install my-infpool \
  --set inferencePool.modelServers.matchLabels.llm-d.ai/model=my-model \
  oci://us-central1-docker.pkg.dev/k8s-staging-images/gateway-api-inference-extension/charts/inferencepool
```

> Note that the Gateway providers and Gateway provider specific resources (e.g. the `HTTPRoute`) are deployed independently.

- See the [full Helm Chart](https://github.com/kubernetes-sigs/gateway-api-inference-extension/tree/main/config/charts/inferencepool) for configuration details.
- See the [Gateway API guides](../../resources/gateways/istio.md) for more details on Gateway API.
