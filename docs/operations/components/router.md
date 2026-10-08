# Router Operations Guide

This guide covers operational best practices, high availability deployment architectures, and container sizing recommendations for the llm-d Router components.

---

## 1. Endpoint Picker Operations

When deploying the Endpoint Picker (EPP) in either **Standalone** or **Gateway** mode, resource allocations and multi-replica scaling behaviors depend on expected query throughput, prefix cache matching complexity, and high availability (HA) requirements.

### High Availability & Scaling Modes

When running multiple replicas of the Endpoint Picker (`router.epp.replicas > 1`), its behavior depends on the configured HA mode:

1. **Active-Passive**: Traffic routes to a single primary replica set while standby replicas remain available for failover.
   - **Leader Election with Fail-Open**: Uses Kubernetes `coordination.k8s.io/Lease` coordination so only the elected leader serves inference extension requests. Standby pods remain idle until acquiring the lease. If the active leader fails, the proxy operates in fail-open mode, routing traffic directly to model servers until a standby acquires leadership.
   - **Priority Routing**: Available when proxy mode is set to service (`router.proxy.mode: service`). Uses Envoy Priority Routing and outlier detection to route traffic to Primary EPP replicas (Priority 0) and shift traffic to Standby EPP replicas (Priority 1) upon primary failure. See [Priority Routing](#priority-routing) for configuration details.
2. **Active-Active**: Multiple EPP replicas run concurrently and share load across all instances. Suitable when scheduling algorithms and plugins do not require unified state across pods or a synchronization mechanism is in place.

#### Active-Passive Mode: Leader Election and Fail-Open (Default)

In multi-replica deployments without priority routing (`router.epp.replicas > 1`), the router coordinates active-passive replicas using Kubernetes lease-based leader election:

- **Leader Coordination**: EPP replicas contend for a `coordination.k8s.io/Lease`. The `--ha-enable-leader-election` flag enables leader election in EPP (automatically injected by Helm when `router.epp.replicas > 1`). The elected leader responds to active gRPC extension requests on Port 9002, while standby replicas answer readiness probes with `NOT_SERVING` so they remain out of Service endpoints.
- **Sizing & Capacity Impact**: Scaling replica count does not increase total request throughput capacity, as only the single active leader replica handles external processing requests.
- **Fail-Open Resiliency**: With `router.proxy.failOpen: true` (the default in standalone Envoy mode) or `router.inferencePool.failureMode: FailOpen` (the default in Gateway mode), if the active leader crashes or restarts, the proxy passes requests directly to backend model servers without dropping traffic during the lease transition period.

```yaml
router:
  epp:
    replicas: 2
    flags:
      ha-enable-leader-election: true
  proxy:
    failOpen: true
```

- **Multi-Replica EPP and `helm --wait`**: Because standby replicas remain `NotReady` by design, the EPP Deployment settles at `readyReplicas: 1` out of `replicas: N`.
  With the default `Recreate` strategy (`maxUnavailable: 0`), Kubernetes reports `Available=False` (`MinimumReplicasUnavailable`), which blocks `helm --wait` and Flux upgrades.
  On Helm 3 or Flux, configure a `RollingUpdate` strategy whose `maxUnavailable` covers the standby replicas (`>= replicas - 1`) and `maxSurge: 0`, or install without `--wait` and gate on the EPP Service `Endpoints` resource instead:

```yaml
router:
  epp:
    replicas: 2
    deploymentStrategy:
      type: RollingUpdate
      rollingUpdate:
        maxUnavailable: 1 # >= replicas - 1
        maxSurge: 0
```

#### Active-Active Mode

To scale routing throughput concurrently across all EPP replicas, disable leader election by passing `ha-enable-leader-election: false` under `router.epp.flags`:

```yaml
router:
  epp:
    replicas: 3
    flags:
      ha-enable-leader-election: false
```

- **Near-Linear Throughput Scaling**: Multiple EPP replicas share incoming request load concurrently:

  | Replicas | Scaling Factor |
  | :--- | :--- |
  | 1 | 1.0x |
  | 2 | 2.0x |
  | 3 | 2.7x |
  | 4 | 3.5x |

- **Note (Flow Control)**: Flow control state (queues, fairness accounting, and the saturation view) is per replica and not shared.
  In Active-Active mode, priority and fairness are enforced only within each replica's share of the traffic, and per-band capacity limits apply per replica, so the fleet-wide queued volume scales with the replica count.
- **Plugin Compatibility**: EPP replicas do not share routing state.
  Active-Active mode works with stateless schedulers (`random-picker`), session affinity (`session-affinity-filter`), or plugins that read metrics from backend model servers (such as queue depth, KV-cache utilization, or precise prefix cache scorers).
  Avoid approximate prefix routing in Active-Active mode because replicas do not share prefix state; for technical details and context on EPP replica state synchronization and scaling limitations, see [Issue #1290](https://github.com/llm-d/llm-d-router/issues/1290).

### Horizontal Pod Autoscaling (HPA)

EPP supports horizontal pod autoscaling through Kubernetes HorizontalPodAutoscaler (HPA v2). When autoscaling is enabled (`router.epp.autoscaling.enabled: true`), Helm omits `spec.replicas` on the EPP Deployment, and the HPA controller manages replica counts.

#### Operational Prerequisites and Constraints

- **Active-Active Mode Required**: Autoscaling requires Active-Active EPP operation. Standby replicas in leader-elected setups remain `NotReady` by design, which blocks HPA stabilization. The chart enforces active-active mode when autoscaling is enabled and blocks explicit leader election (`--ha-enable-leader-election`).
- **Incompatible with StatefulSet Topologies**: Priority routing (`router.proxy.priorityRouting.enabled: true`) and GKE preferred backends (`provider.gke.preferredBackends.enabled: true`) render EPP as a StatefulSet with fixed ordinal hostnames (`<name>-0`, `<name>-1`) for static routing. Autoscaling requires a standard Deployment managing a dynamically changing replica set in active-active mode. Enabling autoscaling alongside priority routing or GKE preferred backends fails chart validation.
- **RollingUpdate Strategy**: The Deployment defaults to `RollingUpdate` strategy (`maxUnavailable: 0`, `maxSurge: 1`) under autoscaling to keep serving capacity during scale events. Setting `router.epp.deploymentStrategy` overrides this default.
- **Replica Count Configuration**: When autoscaling is enabled, `router.epp.replicas` is ignored. Replica counts are controlled by `autoscaling.minReplicas` and `autoscaling.maxReplicas`.
- **Plugin Compatibility**: Autoscaling requires active-active compatible plugins; see [Active-Active Mode](#active-active-mode).

#### Target Utilization Guidance

- **Target CPU Utilization**: The recommended starting default is **80%**. This leaves headroom to absorb traffic spikes while new pods initialize and pass readiness probes. Higher utilization leaves less headroom for traffic bursts while new pods start up. Operators should tune this target based on their workload shape, token lengths, and latency SLAs.
- **Container Sizing**: Set container CPU requests based on expected steady-state per-pod load (refer to [Container Resource Sizing](#container-resource-sizing) for CPU core-to-throughput estimates). In standalone `sidecar` mode (`router.proxy.mode: sidecar`), each EPP pod runs both `epp` and `envoy-proxy`, and Kubernetes resource-based HPA calculates pod CPU utilization across the combined CPU usage and requests of both containers.

#### Helm Configuration

```yaml
router:
  epp:
    autoscaling:
      enabled: true
      minReplicas: 1
      maxReplicas: 5
      targetCPUUtilizationPercentage: 80
      behavior:
        scaleDown:
          stabilizationWindowSeconds: 300
    resources:
      requests:
        cpu: "8"
        memory: 16Gi
      limits:
        cpu: "8"
        memory: 16Gi
```

Custom HPA v2 metrics can be supplied via `router.epp.autoscaling.metrics` to replace auto-generated CPU and memory metrics (target percentage fields remain range-validated if defined).

### Container Resource Sizing

#### CPU Allocation

- **Rule of Thumb**: Allocate **0.5 to 1.0 CPU cores per request/second** of expected throughput for large agentic workloads (~100k input / 1k output tokens).
- **Scaling Behavior**: CPU utilization scales linearly with the request rate, and increases with both the input prompt size and output token length.
- **Prefix Matching Overhead**: Increasing `maxPrefixTokensToMatch` increases EPP CPU utilization. At lower throughputs, a large prefix limit (such as 400,000 tokens / 6,250 blocks with effective `blockSizeTokens: 64`) can increase EPP CPU utilization by over 100% compared to a small limit (16,384 tokens / 256 blocks) due to the overhead of searching and matching prefix blocks.
- **Idle Scraping Overhead**: Idle CPU consumption scales with total model-serving pods due to background Prometheus scraping. In a cluster with 100 pods, EPP idle consumption reaches approximately **7.5 cores**.

#### Memory Allocation

- **Inflight Concurrency**: Memory footprint is stable with small output token requests, and scales directly with concurrent inflight requests and output decode length.
- **Flow Control Queues**: With flow control enabled, requests that cannot dispatch under saturation are buffered in EPP memory, including their request bodies.
  The buffered volume is bounded per priority band by `priorityBands[].maxRequests` (default `5000`) and `maxBytes` (default `1G`), which `defaultPriorityBand` sets as a template for bands you do not list; budget for the sum of the per-band `maxBytes` limits of the priority levels your traffic uses, on top of the inflight-request sizing below.
  The global `flowControl.maxRequests` / `maxBytes` caps default to unlimited, so set a global `maxBytes` under the container memory limit: at the per-band default, a handful of bands clears the sizing guidance below before any band cap engages.
  Lower these limits (or set a shorter `defaultRequestTTL`) to trade queueing for earlier shedding.
  A `noEndpointRequestTTL` sized for a cold start holds bodies for that whole budget while the pool is empty, so the band caps bound queue memory during a scale-from-zero.
- **Sizing Guidelines**:
  - At 50 to 100 requests/second with 1k output tokens, EPP requires **4 to 6 GiB** of memory.
  - For long-output generation (e.g., 5k+ output tokens), memory footprint can exceed **20 GiB** due to concurrent request state accumulation.

### Performance Reference Data

Empirical benchmark reference data for `llm-d-simulator` simulating Qwen/Qwen3-8B across 100 model-serving pods:

#### Throughput and Prefix Block Sizing (100k Input / 1k Output Tokens)

Peak CPU and memory utilization for EPP under a 100k token workload (95k system prompt, 5k question prompt, and 1k output tokens) when using approximate prefix caching across 100 model-serving pods ([configuration #1287](https://github.com/llm-d/llm-d-router/issues/1287#issuecomment-4666058475)):

| Configuration | Request Rate (Req/s) | maxPrefixTokensToMatch | Peak CPU (Cores) | Peak Memory (GiB) | Scheduler P50 Latency (s) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Small Prefix Match | 5.0 | 4096 | 1.19 | 0.26 | 0.00010 |
| Large Prefix Match | 5.0 | 100000 | 3.82 | 0.65 | 0.00010 |
| Small Prefix Match | 98.7 | 4096 | 35.17 | 2.46 | 0.00014 |
| Large Prefix Match | 98.8 | 100000 | 46.50 | 3.41 | 0.00020 |

#### Output Length Variation (50 Req/s Constant Throughput)

EPP peak resource usage at a constant request rate of 50 requests/second with a 100k input token workload, varying the output token length and `maxPrefixTokensToMatch` ([configuration #1287](https://github.com/llm-d/llm-d-router/issues/1287#issuecomment-4619775397)):

| Input Tokens | Output Tokens | maxPrefixTokensToMatch | Peak CPU (Cores) | Peak Memory (GiB) |
| :--- | :--- | :--- | :--- | :--- |
| 100k | 500 | 4096 | 15.13 | 2.27 |
| 100k | 500 | 32768 | 17.14 | 3.76 |
| 100k | 1000 | 4096 | 17.51 | 3.66 |
| 100k | 1000 | 32768 | 20.28 | 5.23 |
| 100k | 5000 | 16384 | 30.95 | 12.54 |
| 100k | 10000 | 8192 | 32.53 | 12.54 |

---

## 2. Proxy Operations in Standalone Mode

The following operational guidelines and proxy scaling architectures apply **exclusively to Standalone Mode** (`llm-d-router-standalone`), where a proxy (Envoy or Agentgateway) intercepts client requests and external-processes them via EPP.

### Horizontally Scalable Proxy Service (Service Mode)

Standalone mode supports two proxy topologies:

- **`sidecar` mode (default)**: Each EPP pod includes one proxy container, so the EPP replica count also determines the proxy replica count.
- **`service` mode (`router.proxy.mode: service`)**: To scale data plane throughput independently from control plane intelligence, the proxy runs in a separate horizontally scalable Deployment and Service. Set `router.proxy.replicas` (or enable [Standalone Proxy Autoscaling](#standalone-proxy-autoscaling-service-mode)) to scale service-mode proxies independently from EPP.

In `service` mode, the proxy communicates with EPP over the in-cluster EPP Service. If EPP undergoes active-passive leader failover or momentary pod restarts, the Envoy proxy fails open by default (`router.proxy.failOpen: true`), preserving uninterrupted client request processing. (`router.proxy.failOpen` applies to `proxyType: envoy` only; `agentgateway` exposes no fail-open setting and fails closed when EPP is unreachable.)

```bash
helm install my-standalone-router ./config/charts/llm-d-router-standalone \
  --set router.modelServers.matchLabels.app=my-vllm-service \
  --set router.inferencePool.create=false \
  --set router.proxy.mode=service \
  --set router.proxy.replicas=3
```

#### High Availability with Fail-Open

By default, in service mode, fail open is enabled. To disable it, set `router.proxy.failOpen=false`.

Empirical benchmark reference data for Qwen/Qwen2.5-1.5B-Instruct simulation across 2 model server replicas with forceful and graceful Leader EPP pod termination. Across various Envoy proxy replica counts, `router.proxy.failOpen=true` maintains high request availability during leader pod teardown. Residual errors occur during socket teardown at the exact moment of pod termination.

| Scenario | Envoy Replicas | EPP Replicas | Total Requests | Successful Requests (Throughput) | Errors |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Scenario 1**<br>Leader election with standby pod, pod terminates immediately without graceful termination | 1 | 2 | 598 | 598 (100%) | 0 |
| | 2 | 2 | 591 | 584 (98.8%) | 7 |
| **Scenario 2**<br>Leader election with no standby pod, pod terminates immediately without graceful termination | 1 | 1 | 598 | 588 (98.3%) | 1 |
| | 2 | 1 | 592 | 589 (99.3%) | 4 |
| **Scenario 3**<br>Leader election with standby pod, pod terminates with graceful termination | 1 | 2 | 593 | 591 (99.7%) | 2 |
| | 2 | 2 | 594 | 583 (98.1%) | 11 |
| **Scenario 4**<br>Leader election with no standby pod, pod terminates with graceful termination | 1 | 1 | 591 | 591 (100%) | 0 |
| | 2 | 1 | 593 | 589 (99.3%) | 4 |

#### Priority Routing

Priority Routing is available in standalone service mode (`router.proxy.mode: service`). When priority routing is enabled (`router.proxy.priorityRouting.enabled: true`), the router uses [Envoy Priority Routing](https://www.envoyproxy.io/docs/envoy/latest/intro/arch_overview/upstream/load_balancing/priority) to organize EPP endpoints into distinct priority tiers:

- **Priority 0 (Primary / Active)**: Handles 100% of steady-state scheduling traffic.
- **Priority 1 (Standby / Passive)**: Warm standby pods ready to accept failover traffic upon primary pod failure.

##### Architecture and Failover Mechanics

1. **Deterministic Endpoint Discovery**: EPP pods run as a StatefulSet with a headless Service (`publishNotReadyAddresses: true`). Envoy targets individual pod DNS entries (`<release>-epp-0`, `<release>-epp-1`, etc.) mapped to distinct priority levels.
2. **Active Health Probing**: Envoy actively probes EPP Port 9002 via gRPC health check (`grpc.health.v1.Health`).
3. **Outlier Detection Failover**: When priority routing is enabled, if a primary pod fails or crashes, Envoy's Outlier Detection detects TCP connection failure and ejects the primary host, shifting traffic to Priority 1 standbys in sub-second time without lease expiration delays.
4. **Graceful Pod Termination**: EPP pods include a native `lifecycle.preStop.sleep` hook (5 seconds) during planned deletion or rollout on Kubernetes 1.30+ with `PodLifecycleSleepAction` enabled. This gives Envoy active health checks time to detect pod shutdown and redirect new traffic to standby endpoints before SIGTERM, allowing in-flight gRPC streams to drain.
5. **Safe Failback**: When a replacement primary pod is rescheduled, the health check `healthy_threshold` requires consecutive passing health probes before Envoy restores traffic to Priority 0, ensuring the replacement EPP pod has finished syncing model server state and inference pools.

##### Helm Configuration

```yaml
router:
  proxy:
    mode: service
    priorityRouting:
      enabled: true
      primaryReplicas: 1
      standbyReplicas: 1
```

##### Tuning Parameters

| Parameter | Default | Description |
| :--- | :--- | :--- |
| `router.proxy.priorityRouting.healthyPanicThreshold` | `10.0` | Threshold percentage to prevent panic routing during primary ejection. |
| `router.proxy.priorityRouting.dnsRefreshRate` | `5s` | DNS resolution refresh rate for headless EPP endpoints. |
| `router.proxy.priorityRouting.connectTimeout` | `0.250s` | Connection timeout to detect unreachable primary pods. |
| `router.proxy.healthCheckInterval` | `10s` | Active gRPC health check probe interval. |
| `router.proxy.healthCheckTimeout` | `2s` | Health check probe timeout. |
| `router.proxy.healthCheckUnhealthyThreshold` | `3` | Number of failed probes before marking an endpoint unhealthy. |
| `router.proxy.healthCheckHealthyThreshold` | `2` | Number of passing probes required before admitting recreated pods. |
| `router.epp.terminationGracePeriodSeconds` | `130` | Grace period (seconds) before SIGKILL on pod teardown. |

#### Standalone Proxy Autoscaling (Service Mode)

In standalone service mode (`router.proxy.mode: service`), the proxy runs as an independent Deployment and Service that can be autoscaled using Kubernetes HorizontalPodAutoscaler (HPA v2). When enabled via `router.proxy.autoscaling.enabled: true`, Helm omits `spec.replicas` on the proxy Deployment and generates an HPA targeting the proxy Deployment.

##### Operational Prerequisites and Constraints

- **Service Mode Required**: Proxy autoscaling is supported only when `router.proxy.mode: service` and `router.proxy.enabled: true`. In `sidecar` mode, the proxy lifecycle and replica count are tied to the EPP pod.
- **Replica Count Configuration**: When autoscaling is enabled, `router.proxy.replicas` is ignored. Replica counts are managed by `router.proxy.autoscaling.minReplicas` and `router.proxy.autoscaling.maxReplicas`.
- **Drain and Termination Grace Period**: Envoy drains in-flight connections over a 60-second window (`--drain-time-s 60`). The proxy pod defaults `terminationGracePeriodSeconds: 70` and includes a 5-second `preStop` delay on Kubernetes 1.30+ to allow Service endpoint deregistration before Envoy terminates listeners.

##### Helm Configuration

```yaml
router:
  proxy:
    mode: service
    autoscaling:
      enabled: true
      minReplicas: 2
      maxReplicas: 10
      targetCPUUtilizationPercentage: 80
      behavior:
        scaleDown:
          stabilizationWindowSeconds: 300
    resources:
      requests:
        cpu: "4"
        memory: 8Gi
      limits:
        memory: 16Gi
```

Custom HPA v2 metrics can be supplied via `router.proxy.autoscaling.metrics` to replace auto-generated CPU and memory metrics (target percentage fields remain range-validated if defined).

### Proxy Container Resource Sizing

Sizing each Envoy proxy container depends primarily on the request throughput handled by that replica and the request and response payload size. The `router.proxy.resources` setting applies to each proxy container in either `sidecar` or `service` topology. When running Envoy as the standalone proxy, CPU consumption scales linearly with client request rate, while memory consumption remains stable across workloads.

#### CPU & Memory Guidelines

- **CPU Allocation**: For < 10 requests/second, **1.2 to 2.0 cores** is sufficient. For 100 requests/second at 100k context lengths, allocate at least **8 cores** (peak observed at 7.27 cores). For high concurrency at smaller context lengths (892 requests/second at 10k context), allocate at least **10 cores** (peak observed at 8.78 cores).
- **Memory Footprint**: Envoy memory footprint remains stable between **1.3 and 1.4 GiB** across all tested throughputs and context lengths. Allocate **2 GiB** baseline.

#### Envoy Performance Reference Data

| Input Tokens | Output Tokens | Throughput (Req/s) | Peak CPU (Cores) | Peak Memory (GiB) |
| :--- | :--- | :--- | :--- | :--- |
| 100k | 1k | 10.0 | 1.20 | 1.30 |
| 100k | 1k | 100.0 | 7.27 | < 1.40 |
| 10k | 1k | 892.0 | 8.78 | 1.40 |

### Helm Resource Override Example

Example `resource_overrides.yaml` configuring container resources for both EPP and standalone Envoy proxy containers supporting 50 requests/second for 100k/1k token workloads:

```yaml
router:
  epp:
    resources:
      requests:
        cpu: "32"
        memory: "64Gi"
      limits:
        memory: "128Gi"

  proxy:
    resources:
      requests:
        cpu: "8"
        memory: "2Gi"
      limits:
        memory: "4Gi"
```

```bash
helm install optimize-baseline ./config/charts/llm-d-router-standalone -f resource_overrides.yaml
```
