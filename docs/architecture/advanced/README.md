# Advanced

Advanced patterns and features for production LLM inference deployments. These extend the core llm-d architecture to handle complex workloads and specialized infrastructure.

## Overview

llm-d's core design supports optional advanced patterns for production-scale deployments:

- **[Disaggregated Serving](disaggregation/README.md)** - Split inference into specialized prefill and decode workers for improved efficiency and predictable latency.

- **[KV Cache Management](kv-management/README.md)** - Comprehensive ecosystem for managing and reusing KV cache across the inference pool, including prefix-cache aware routing, real-time cache indexing, and tiered offloading.

- **[Latency Predictor](latency-predictor.md)** - Machine learning-based routing that predicts request latency for SLO-aware scheduling and intelligent endpoint selection.

- **[Autoscaling](autoscaling/README.md)** - Proactive, SLO-aware autoscaling through HPA/KEDA metrics or the Workload Variant Autoscaler for globally optimized cost efficiency.

- **[Batch Inference](batch/README.md)** - OpenAI-compatible Batch API and async processing for high-volume offline workloads.

## When to Use Advanced Features

Start with the [core architecture](../README.md) and add advanced features as your deployment requirements grow:

- **Disaggregated Serving**: When serving large models with long prompts, or when deploying Mixture-of-Experts models with Data Parallelism/Expert Parallelism.

- **KV Cache Management**: When prefix cache hit rates impact latency, or when cache capacity limits throughput.

- **Latency Predictor**: When workload variance makes queue-depth a poor proxy for load, or when strict SLOs require admission control.

- **Autoscaling**: When traffic patterns vary significantly, or when optimizing cost across heterogeneous hardware.

- **Batch Inference**: When processing large offline datasets alongside interactive traffic, or when implementing async processing pipelines.

## Composability

These features compose independently. You can adopt prefix-cache aware routing without disaggregation, or use the Latency Predictor with standard aggregated serving. Choose the patterns that address your specific bottlenecks.
