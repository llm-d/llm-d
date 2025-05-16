# Prefix Aware Routing/Scoring 🎯

## Overview

Prefix aware routing/scoring is an advanced feature that optimizes inference performance by intelligently distributing requests across model instances based on their input prefixes. This approach significantly improves system efficiency by reducing redundant computations and maximizing resource utilization.

## Why Prefix Aware Routing? 🤔

In a typical inference serving system, requests are often distributed across model instances without considering the similarity of their inputs. This can lead to:

- Redundant computation of the same prefix across different instances
- Inefficient use of KV-cache
- Higher latency due to repeated prefix processing
- Increased resource consumption

Prefix aware routing addresses these challenges by:

1. **Cache Optimization** 🔄: Routes similar requests to the same instance to maximize KV-cache hit rates
2. **Resource Efficiency** 💪: Reduces redundant computations by reusing previously computed prefixes
3. **Latency Reduction** ⚡: Minimizes processing time by leveraging cached computations
4. **Throughput Improvement** 📈: Enables higher request throughput through better resource utilization

## How It Works 🛠️

The prefix aware routing system consists of two main components:

1. **Prefix Analyzer** 🔍
   - Analyzes incoming requests to identify their prefixes
   - Computes prefix similarity scores
   - Maintains a prefix-to-instance mapping

2. **Request Router** 🚦
   - Uses prefix similarity scores to determine optimal instance routing
   - Considers instance load and capacity
   - Balances between prefix similarity and system load

## Usage Example 📝

The prefix aware routing is implemented as a scorer in the inference scheduler. It works in conjunction with the other scorers to make optimal routing decisions. The configuration is handled through environment variables in the [llm-d-deployer](https://github.com/llm-d/llm-d-deployer/blob/main/charts/llm-d/README.md#values) project, which sets up the inference scheduler with the appropriate scorer configuration.

By default, the prefix aware scorer is configured with double weight (2.0) compared to the load aware scorer (1.0), giving higher priority to prefix similarity in routing decisions. This configuration works seamlessly with prefill/decode disaggregation scenarios, ensuring efficient request distribution while maintaining load balancing.

For detailed configuration options and implementation details, refer to the [inference scheduler architecture documentation](https://github.com/llm-d/llm-d-inference-scheduler/blob/main/docs/architecture.md#scorers--configuration).

## Practical Example 🎯

Here's a simple example of how prefix aware routing works in practice:

```
Request 1: "What is the capital of France?"
Request 2: "What is the capital of Germany?"
Request 3: "Tell me about quantum computing."

With prefix aware routing:
- Requests 1 and 2 are routed to the same instance due to similar prefix "What is the capital of"
- Request 3 is routed to a different instance due to different prefix
- Opportunistically, the KV-cache from processing "What is the capital of" is reused for both requests
- Load balancing ensures no single instance is overwhelmed
```

## Related Resources 📚

- [Gateway API Inference Extension](https://github.com/kubernetes-sigs/gateway-api-inference-extension)
- [vLLM Documentation](https://vllm.readthedocs.io/)
- [LLM-D Deployer](https://github.com/llm-d/llm-d-deployer)
- [LLM-D Inference Scheduler](https://github.com/llm-d/llm-d-inference-scheduler)
