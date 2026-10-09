# Router Operations Guide

This guide covers operational best practices, high availability deployment architectures, and container sizing recommendations for the llm-d Router components. For deep-dive tuning parameters and component internals, see the [`llm-d-router` Operations Guide](https://github.com/llm-d/llm-d-router/blob/main/docs/operations.md).

---

## 1. Endpoint Picker Operations

When deploying the Endpoint Picker (EPP) in either **Standalone** or **Gateway** mode, resource allocations and multi-replica scaling behaviors depend on expected query throughput, prefix cache matching complexity, and high availability (HA) requirements.

### High Availability & Scaling Modes

When running multiple replicas of the Endpoint Picker (`router.epp.replicas > 1`), its behavior depends on the configured HA mode:

1. **Active-Passive**: Traffic routes to a single primary replica set while standby replicas remain available for failover.
   - **Priority Routing (Recommended)**: Available in standalone service mode (`router.proxy.mode: service`). Uses Envoy Priority Routing and outlier detection to route traffic to Primary EPP replicas (Priority 0) and shift traffic to warm Standby EPP replicas (Priority 1) upon primary failure, reducing failover switchover time to **sub-second** (`< 1s`) while preserving optimized EPP scheduling. In GKE Gateway mode, `provider.gke.preferredBackends.enabled: true` provides equivalent primary/standby tiering via GKE Preferred Backends. See [Priority Routing](#priority-routing) for configuration.
   - **Leader Election with Fail-Open (Default)**: Uses Kubernetes `coordination.k8s.io/Lease` coordination so only the elected leader serves inference extension requests while standby pods remain idle. If the active leader fails, fail-open mode prevents dropped requests by routing traffic directly to backend model servers, but **leader switchover takes 10 to 30 seconds**. During that window while EPP is unavailable, **routing is purely unoptimized**.
2. **Active-Active**: Multiple EPP replicas run concurrently and share load across all instances. Suitable when scheduling algorithms and plugins do not require unified state across pods or a synchronization mechanism is in place.

#### Active-Passive Mode: Leader Election and Fail-Open (Default)

By default, multi-replica EPP deployments without priority routing automatically enable `--ha-enable-leader-election`. One leader replica actively serves routing decisions on Port 9002 and coordinates lease status, while standby replicas answer readiness probes with `NOT_SERVING` so they remain out of Service endpoints.

- **Sizing & Capacity Impact**: Scaling replica count does not increase total request throughput capacity, as only the single active leader replica handles external processing requests.
- **Fail-Open Resiliency**: With `router.proxy.failOpen: true` (default in standalone Envoy mode) or `router.inferencePool.failureMode: FailOpen` (default in Gateway mode), client requests are passed directly to backend model servers and are not dropped if the active leader restarts or fails.
- **Switchover Disadvantage (10-30s Unoptimized Routing Window)**: When the active leader fails, Kubernetes lease expiration (`--ha-lease-duration`, default `15s`), standby readiness transition, and Service endpoint propagation take **10 to 30 seconds** before a standby replica begins serving traffic. Although fail-open prevents dropped requests during this window, EPP is unavailable and **routing is purely unoptimized** (requests bypass KV-cache affinity, prefix-cache scoring, load-aware scheduling, and flow control). To reduce switchover time to **sub-second** (`< 1s`) and keep optimized routing active across failovers, use [Priority Routing](#priority-routing) as the recommended Active-Passive setup.

```yaml
router:
  epp:
    replicas: 2
    flags:
      ha-enable-leader-election: true
  proxy:
    failOpen: true
```

> [!NOTE]
> Because standby replicas stay `NotReady` under leader election, `helm --wait` and Flux upgrades can block on Deployment availability with the default `Recreate` strategy. Configure a `RollingUpdate` strategy with `maxUnavailable: 1` (`>= replicas - 1`) and `maxSurge: 0`, or see the [`llm-d-router` Operations Guide](https://github.com/llm-d/llm-d-router/blob/main/docs/operations.md#multi-replica-epp-and-helm---wait).

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

- **Flow Control Scope**: Flow control state (queues, fairness accounting, and saturation view) is maintained per replica and is not shared. Priority, fairness, and per-band capacity limits apply within each replica's share of traffic, so fleet-wide queued volume scales with the replica count.
- **Plugin Compatibility**: EPP replicas do not share local routing state. Active-Active mode works with stateless schedulers (`random-picker`), session affinity (`session-affinity-filter`), or plugins that query real-time metrics from backend model servers (such as queue depth, KV-cache utilization, or precise prefix cache scorers). Avoid approximate prefix routing in Active-Active mode because replicas do not share prefix state (see [Issue #1290](https://github.com/llm-d/llm-d-router/issues/1290)).

### Horizontal Pod Autoscaling (HPA)

EPP supports HorizontalPodAutoscaler (HPA v2) in **Active-Active mode** (`router.epp.autoscaling.enabled: true`). Autoscaling is incompatible with leader election and StatefulSet-based topologies (`router.proxy.priorityRouting.enabled: true` or `provider.gke.preferredBackends.enabled: true`). Set target CPU utilization around **80%** to leave headroom for traffic bursts while new pods initialize:

```yaml
router:
  epp:
    autoscaling:
      enabled: true
      minReplicas: 1
      maxReplicas: 5
      targetCPUUtilizationPercentage: 80
```

For full HPA constraints and custom metric options, see [Horizontal Pod Autoscaling (HPA)](https://github.com/llm-d/llm-d-router/blob/main/docs/operations.md#horizontal-pod-autoscaling-hpa).

### Container Resource Sizing

#### CPU Allocation

- **Rule of Thumb**: Allocate **0.5 to 1.0 CPU cores per request/second** of expected throughput for large agentic workloads (~100k input / 1k output tokens).
- **Scaling Behavior**: CPU utilization scales linearly with the request rate, and increases with both the input prompt size and output token length.
- **Prefix Matching Overhead**: Increasing `maxPrefixTokensToMatch` increases EPP CPU utilization. At lower throughputs, a large prefix limit (such as 400,000 tokens / 6,250 blocks with effective `blockSizeTokens: 64`) can increase EPP CPU utilization by over 100% compared to a small limit (16,384 tokens / 256 blocks) due to block search overhead.
- **Idle Scraping Overhead**: Idle CPU consumption scales with total model-serving pods due to background Prometheus scraping. In a cluster with 100 pods, EPP idle consumption reaches approximately **7.5 cores**.

#### Memory Allocation

- **Inflight Concurrency**: Memory footprint is stable with small output token requests, and scales directly with concurrent inflight requests and output decode length.
- **Flow Control Queues**: When flow control is enabled, saturated requests (including request bodies) are buffered in EPP memory up to per-band limits (`priorityBands[].maxRequests`, default `5000`; `maxBytes`, default `1G`). Because global `flowControl.maxRequests` / `maxBytes` caps default to unlimited, set a global `maxBytes` cap below the container memory limit and budget for the sum of active priority bands on top of inflight-request memory.
- **Sizing Guidelines**:
  - At 50 to 100 requests/second with 1k output tokens, EPP requires **4 to 6 GiB** of memory.
  - For long-output generation (e.g., 5k+ output tokens), memory footprint can exceed **20 GiB** due to concurrent request state accumulation.

### Performance Reference Data

Empirical benchmark reference data for `llm-d-simulator` simulating Qwen/Qwen3-8B across 100 model-serving pods:

#### Throughput and Prefix Block Sizing (100k Input / 1k Output Tokens)

| Configuration | Request Rate (Req/s) | maxPrefixTokensToMatch | Peak CPU (Cores) | Peak Memory (GiB) | Scheduler P50 Latency (s) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Small Prefix Match | 5.0 | 4096 | 1.19 | 0.26 | 0.00010 |
| Large Prefix Match | 5.0 | 100000 | 3.82 | 0.65 | 0.00010 |
| Small Prefix Match | 98.7 | 4096 | 35.17 | 2.46 | 0.00014 |
| Large Prefix Match | 98.8 | 100000 | 46.50 | 3.41 | 0.00020 |

#### Output Length Variation (50 Req/s Constant Throughput)

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

By default, the standalone chart deploys the proxy as a `sidecar` container inside the EPP pod. To scale data plane throughput independently from control plane intelligence, deploy the proxy as a separate horizontally scalable Deployment and Service by setting `router.proxy.mode: service` (with static `router.proxy.replicas` or HPA via `router.proxy.autoscaling.enabled: true`).

In `service` mode, the proxy communicates with EPP over the in-cluster EPP Service. If EPP undergoes active-passive leader failover or momentary pod restarts, the Envoy proxy fails open by default (`router.proxy.failOpen: true`), preserving uninterrupted client request processing. (`router.proxy.failOpen` applies to `proxyType: envoy` only; `agentgateway` fails closed when EPP is unreachable.)

```bash
helm install my-standalone-router ./config/charts/llm-d-router-standalone \
  --set router.modelServers.matchLabels.app=my-vllm-service \
  --set router.inferencePool.create=false \
  --set router.proxy.mode=service \
  --set router.proxy.replicas=3
```

#### High Availability with Fail-Open

By default, in service mode, fail open is enabled. To disable it, set `router.proxy.failOpen=false`.

Empirical benchmark reference data for Qwen/Qwen2.5-1.5B-Instruct simulation across 2 model server replicas with forceful and graceful Leader EPP pod termination. Across various Envoy proxy replica counts, `router.proxy.failOpen=true` maintains high request availability during leader pod teardown. Residual errors occur during socket teardown at the exact moment of pod termination. Because lease-based leader switchover takes 10 to 30 seconds, requests served via fail-open during that transition bypass EPP scheduling and receive unoptimized routing until the new leader is ready.

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

Priority Routing (`router.proxy.priorityRouting.enabled: true`) is the **recommended Active-Passive setup** in standalone service mode (`router.proxy.mode: service`). It runs EPP as a StatefulSet and uses Envoy Priority Routing with active gRPC health checks and outlier detection (`connectTimeout: 0.250s`) to route traffic to **Priority 0 (Primary)** pods and fail over to warm **Priority 1 (Standby)** pods in **sub-second** time (`< 1s`), avoiding the 10 to 30 second unoptimized fail-open window of lease-based leader election:

```yaml
router:
  proxy:
    mode: service
    priorityRouting:
      enabled: true
      primaryReplicas: 1
      standbyReplicas: 1
```

For failover mechanics and health-check tuning parameters, see [Priority Routing in the `llm-d-router` Operations Guide](https://github.com/llm-d/llm-d-router/blob/main/docs/operations.md#priority-routing).

### Proxy Container Resource Sizing

When running Envoy as the standalone proxy (`sidecar` or `service` mode), CPU consumption scales linearly with client request rate, while memory consumption remains stable across workloads.

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
