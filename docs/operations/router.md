# llm-d Router Operations Guide

This guide covers operational best practices, high availability deployment architectures, and container sizing recommendations for the llm-d Router components.

---

## 1. Endpoint Picker Operations

When deploying the Endpoint Picker (EPP) in either **Standalone** or **Gateway** mode, resource allocations and multi-replica scaling behaviors depend on expected query throughput, prefix cache matching complexity, and high availability (HA) requirements.

### High Availability & Scaling Modes

When running multiple replicas of the Endpoint Picker (`router.epp.replicas > 1`), its behavior depends on the configured HA mode.

#### Active-Passive Mode (Default)
By default, multi-replica EPP deployments automatically enable the `--ha-enable-leader-election` flag. One leader replica actively serves routing decisions and coordinates lease status, while remaining replicas act as warm standbys.
- **Sizing & Capacity Impact**: Scaling replica count does not increase total request throughput capacity, as only the single active leader replica handles external processing requests.

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

- **Warning (Plugin & Prefix Compatibility)**: In active-active mode, you must only use active-active compatible plugins—specifically plugins that query backend model servers dynamically for real-time metrics and state (such as queue depth or KV-cache utilization scorers). Avoid approximate prefix caching plugins in active-active mode; because replicas do not share local memory state, prefix routing partitions across replicas and degrades cache hit rates significantly.

### Container Resource Sizing

#### CPU Allocation
- **Rule of Thumb**: Allocate **0.5 to 1.0 CPU cores per request/second** of expected throughput for large agentic workloads (~100k input / 1k output tokens).
- **Prefix Matching Overhead**: Increasing `maxPrefixBlocksToMatch` increases CPU consumption. At lower throughputs, a limit of 6250 blocks can increase CPU consumption by over 100% compared to 256 blocks due to block search overhead.
- **Idle Scraping Overhead**: Idle CPU consumption scales with total model-serving pods due to background Prometheus scraping. In a cluster with 100 pods, EPP idle consumption reaches approximately **7.5 cores**.

#### Memory Allocation
- **Inflight Concurrency**: Memory footprint scales directly with concurrent inflight requests and output decode length.
- **Sizing Guidelines**:
  - At 50 to 100 requests/second with 1k output tokens, EPP requires **4 to 6 GiB** of memory.
  - For long-output generation (e.g., 5k+ output tokens), memory footprint can exceed **20 GiB** due to concurrent request state accumulation.

### Performance Reference Data

Empirical benchmark reference data for Qwen/Qwen3-8B simulation across 100 serving pods:

#### Throughput and Prefix Block Sizing (100k Input / 1k Output Tokens)

| Configuration | Request Rate (Req/s) | maxPrefixBlocksToMatch | Peak CPU (Cores) | Peak Memory (GiB) | Scheduler P50 Latency (s) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Small Prefix Match | 5.0 | 256 | 1.19 | 0.26 | 0.00010 |
| Large Prefix Match | 5.0 | 6250 | 3.82 | 0.65 | 0.00010 |
| Small Prefix Match | 98.7 | 256 | 35.17 | 2.46 | 0.00014 |
| Large Prefix Match | 98.8 | 6250 | 46.50 | 3.41 | 0.00020 |

#### Output Length Variation (50 Req/s Constant Throughput)

| Input Tokens | Output Tokens | maxPrefixBlocksToMatch | Peak CPU (Cores) | Peak Memory (GiB) |
| :--- | :--- | :--- | :--- | :--- |
| 100k | 500 | 256 | 15.13 | 2.27 |
| 100k | 500 | 2048 | 17.14 | 3.76 |
| 100k | 1000 | 256 | 17.51 | 3.66 |
| 100k | 1000 | 2048 | 20.28 | 5.23 |
| 100k | 5000 | 1024 | 30.95 | 12.54 |
| 100k | 10000 | 512 | 32.53 | 12.54 |

---

## 2. Proxy Operations in Standalone Mode

The following operational guidelines and proxy scaling architectures apply **exclusively to Standalone Mode** (`llm-d-router-standalone`), where a proxy (Envoy or Agentgateway) intercepts client requests and external-processes them via EPP.

### Horizontally Scalable Proxy Service

By default, the standalone chart deploys the proxy as a sidecar container inside the EPP pod. To scale data plane throughput independently from control plane intelligence, deploy the proxy as a separate horizontally scalable Deployment and Service by setting `router.proxy.mode=service`.

In this decoupled architecture, the proxy communicates with EPP over the in-cluster EPP Service. If EPP undergoes active-passive leader failover or momentary pod restarts, the proxy fails open by default (`router.proxy.failOpen=true`), preserving uninterrupted client request processing.

```bash
helm install my-standalone-router ./config/charts/llm-d-router-standalone \
  --set router.modelServers.matchLabels.app=my-vllm-service \
  --set router.inferencePool.create=false \
  --set router.proxy.mode=service \
  --set router.proxy.replicas=3
```

### Proxy Container Resource Sizing

When running Envoy as the standalone proxy, CPU consumption scales linearly with client request rate, while memory consumption remains stable across workloads.

#### CPU & Memory Guidelines
- **CPU Allocation**: For < 10 requests/second, **1.2 to 2.0 cores** is sufficient. For 100 requests/second at 100k context lengths, allocate at least **8 cores** (peak observed at 7.27 cores). For high concurrency at smaller context lengths (892 requests/second at 10k context), allocate at least **10 cores**.
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
