# Router Operations Guide

This guide covers operational best practices, high availability deployment architectures, and container sizing recommendations for the llm-d Router components. For deep-dive architecture and tuning parameters, see the [`llm-d-router` Operations Guide](https://github.com/llm-d/llm-d-router/blob/main/docs/operations.md).

---

## 1. Endpoint Picker Operations

When deploying the Endpoint Picker (EPP) in either **Standalone** or **Gateway** mode, resource allocations and multi-replica scaling behaviors depend on expected query throughput, prefix cache matching complexity, and high availability (HA) requirements.

### High Availability & Scaling Modes

When running multiple replicas of the Endpoint Picker (`router.epp.replicas > 1`), its behavior depends on the configured HA mode.

#### Active-Passive Mode

In Active-Passive mode, traffic routes to primary replica(s) while standby replicas remain available for failover.

- **Sizing & Capacity Impact**: Scaling standby replicas (or total replica count under single-leader election) does not increase total request throughput capacity, as only the active primary replica(s) handle external processing requests.
- **Priority Routing (Recommended)**: In standalone service mode (`router.proxy.priorityRouting.enabled: true`), Envoy routes traffic to primary EPP replicas (Priority 0) and uses outlier detection to shift traffic to warm standby EPP replicas (Priority 1) upon primary connection failure, reducing failover switchover time to **sub-second** (`< 1s`) while preserving optimized EPP scheduling. (In GKE Gateway mode, `provider.gke.preferredBackends.enabled: true` configures equivalent primary/standby tiering via GKE Preferred Backends.)
- **Leader Election with Fail-Open**: Coordinates a single active leader via a Kubernetes `coordination.k8s.io/Lease` (`router.epp.flags.ha-enable-leader-election: true`) while standby replicas do not serve external processing traffic until acquiring the lease. With fail-open enabled (`router.proxy.failOpen: true` in standalone mode or `router.inferencePool.failureMode: FailOpen` in Gateway mode), the proxy routes client requests directly to backend model servers to preserve request availability if the leader fails, but **leader switchover takes 10 to 30 seconds**. During that window while EPP is unavailable, **routing is purely unoptimized**.

```yaml
# Priority Routing (Recommended, standalone service mode)
router:
  proxy:
    mode: service
    priorityRouting:
      enabled: true
      primaryReplicas: 1
      standbyReplicas: 1
```

```yaml
# Leader Election with Fail-Open
router:
  epp:
    replicas: 2
    flags:
      ha-enable-leader-election: true
  proxy:
    failOpen: true # Standalone mode (or router.inferencePool.failureMode: FailOpen in Gateway mode)
```

#### Active-Active Mode

To scale routing throughput concurrently across all EPP replicas, set `ha-enable-leader-election: false` under `router.epp.flags`:

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

- **Flow Control Scope**: Flow control state (queues, fairness accounting, and saturation view) is per replica and not shared, so priority, fairness, and per-band capacity limits apply within each replica's share of traffic.
- **Warning (Plugin & Prefix Compatibility)**: In active-active mode, you must only use active-active compatible plugins—specifically stateless schedulers (`random-picker`), session affinity (`session-affinity-filter`), or plugins that query backend model servers dynamically for real-time metrics and state (such as queue depth or KV-cache utilization scorers). Avoid approximate prefix caching plugins in active-active mode; because replicas do not share local memory state, prefix routing partitions across replicas and degrades cache hit rates significantly.

#### Horizontal Pod Autoscaling (HPA)

EPP supports HorizontalPodAutoscaler (HPA v2) in **Active-Active mode** (`router.epp.autoscaling.enabled: true`). Autoscaling is incompatible with leader election and priority routing. Set target CPU utilization around **80%** to leave headroom for traffic bursts while new pods initialize:

```yaml
router:
  epp:
    autoscaling:
      enabled: true
      minReplicas: 1
      maxReplicas: 5
      targetCPUUtilizationPercentage: 80
```

### Container Resource Sizing

#### CPU Allocation

- **Rule of Thumb**: Allocate **0.5 to 1.0 CPU cores per request/second** of expected throughput for large agentic workloads (~100k input / 1k output tokens).
- **Prefix Matching Overhead**: Increasing `maxPrefixTokensToMatch` increases CPU consumption. At lower throughputs, a large prefix limit (such as 400,000 tokens / 6,250 blocks with effective `blockSizeTokens: 64`) can increase CPU consumption by over 100% compared to a small limit (16,384 tokens / 256 blocks) due to block search overhead.
- **Idle Scraping Overhead**: Idle CPU consumption scales with total model-serving pods due to background Prometheus scraping. In a cluster with 100 pods, EPP idle consumption reaches approximately **7.5 cores**.

#### Memory Allocation

- **Inflight Concurrency**: Memory footprint scales directly with concurrent inflight requests and output decode length.
- **Flow Control Queues**: When flow control is enabled, saturated requests (including request bodies) are buffered in EPP memory up to per-band limits (`priorityBands[].maxRequests`, default `5000`; `maxBytes`, default `1G`). Set a global `flowControl.maxBytes` cap below the container memory limit and budget for active priority bands on top of inflight-request memory.
- **Sizing Guidelines**:
  - At 50 to 100 requests/second with 1k output tokens, EPP requires **4 to 6 GiB** of memory.
  - For long-output generation (e.g., 5k+ output tokens), memory footprint can exceed **20 GiB** due to concurrent request state accumulation.

### Performance Reference Data

Empirical benchmark reference data for Qwen/Qwen3-8B simulation across 100 serving pods:

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

By default, the standalone chart deploys the proxy as a sidecar container inside the EPP pod (`router.proxy.mode: sidecar`). To scale data plane throughput independently from control plane intelligence, deploy the proxy as a separate horizontally scalable Deployment and Service by setting `router.proxy.mode: service`.

In this decoupled architecture, the proxy communicates with EPP over the in-cluster EPP Service. If EPP undergoes active-passive leader failover or momentary pod restarts, the proxy fails open by default (`router.proxy.failOpen: true`), preserving uninterrupted client request processing. To disable fail-open, set `router.proxy.failOpen: false`.

```yaml
router:
  inferencePool:
    create: false
  proxy:
    mode: service
    replicas: 3
    failOpen: true
```

#### Standalone Proxy Autoscaling (Service Mode)

When running in `service` mode (`router.proxy.mode: service`), the standalone proxy Deployment can be autoscaled independently from EPP via HorizontalPodAutoscaler (HPA v2) by setting `router.proxy.autoscaling.enabled: true`:

```yaml
router:
  proxy:
    mode: service
    autoscaling:
      enabled: true
      minReplicas: 2
      maxReplicas: 10
      targetCPUUtilizationPercentage: 80
```

### Proxy Container Resource Sizing

When running Envoy as the standalone proxy, CPU consumption scales linearly with client request rate, while memory consumption remains stable across workloads.

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
