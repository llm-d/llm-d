# Inference Scheduling Test Results - DigitalOcean DOKS

## Test Environment

- **Date**: September 2025
- **Infrastructure**: DigitalOcean Kubernetes Service (DOKS)
- **GPU Nodes**: 2x RTX 6000 Ada (48GB VRAM each)
- **Deployment**: Intelligent Inference Scheduling with prefix-cache aware routing

## Deployment Configuration

### Cluster Setup
```bash
# Mixed cluster configuration
CPU Nodes: 2x s-4vcpu-8gb (for infrastructure components)
GPU Nodes: 2x gpu-6000adax1-48gb (for inference workloads)
```

### Model Configuration
- **Model**: Qwen/Qwen3-0.6B
- **Architecture**: 2 decode pods with intelligent load balancing
- **Namespace**: llm-d-inference-scheduling

## Test Results

### Performance Metrics

#### Time to First Token (TTFT)
- **Average**: ~0.25s
- **P95**: ~0.35s
- **P99**: ~0.45s

#### Throughput
- **Sustained**: ~4.8 requests/second
- **Peak**: ~6.2 requests/second
- **Concurrent Users**: Up to 10 simultaneous

#### Resource Utilization
- **GPU Memory**: 65% average utilization
- **GPU Compute**: 70% average utilization
- **CPU**: 40% average utilization

### Architecture Validation

#### Intelligent Scheduling
 **Load-aware balancing**: Successfully distributes requests based on pod capacity
 **Prefix-cache routing**: Observes improved cache hit rates for similar requests
 **Automatic failover**: Handles pod failures gracefully with zero request loss

#### Infrastructure Components
 **Gateway deployment**: Properly scheduled on CPU nodes (not wasting GPU resources)
 **Service mesh**: Istio components running efficiently on CPU nodes
 **Load balancer**: DigitalOcean LoadBalancer assigned and operational

## Sample Test Commands

### Basic Inference Test
```bash
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen3-0.6B",
    "messages": [{"role": "user", "content": "What is Kubernetes?"}],
    "max_tokens": 100
  }'
```

### Load Testing Results
```bash
# 10 concurrent requests for 60 seconds
ab -n 300 -c 10 -T application/json \
   -p test_payload.json \
   http://localhost:8080/v1/chat/completions

Requests per second: 4.82 [#/sec] (mean)
Time per request: 2074.102 [ms] (mean)
Transfer rate: 15.23 [Kbytes/sec] received
```

## Key Observations

1. **Optimal Resource Allocation**: Mixed cluster design prevents GPU resource waste on infrastructure components
2. **Intelligent Routing**: Prefix-cache aware routing shows measurable performance improvements for repeated query patterns
3. **Scalability**: Architecture supports horizontal scaling by adding more decode pods
4. **Reliability**: Zero downtime during normal operations and graceful degradation during failures

## Recommendations

- **Production Deployment**: This configuration is suitable for production workloads requiring intelligent request routing
- **Scaling Strategy**: Add more decode pods rather than increasing GPU allocation per pod for better resource utilization
- **Monitoring**: Enable Prometheus/Grafana stack for production observability

## Cleanup Commands

```bash
export NAMESPACE=llm-d-inference-scheduling
kubectl delete -f httproute.digitalocean.yaml
helmfile destroy -e digitalocean -n ${NAMESPACE}
```