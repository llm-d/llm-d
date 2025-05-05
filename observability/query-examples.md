
# Example PromQL Queries for vLLM Monitoring

This document provides example [PromQL](https://prometheus.io/docs/prometheus/latest/querying/basics/) queries using vLLM’s built-in metrics

---

## Tokens per Minute

```promql
sum(rate(vllm:request_generation_tokens_count[1m]))
```

Shows the total number of generated tokens per minute across all pods.

---

## Average Engine Latency

```promql
rate(vllm:e2e_request_latency_seconds_sum[5m]) / rate(vllm:e2e_request_latency_seconds_count[5m])
```

Computes the average engine latency over a 5-minute window.

---

## Total Number of Requests (Last Hour)

```promql
sum(increase(vllm:num_requests_running[1h]))
```

Shows the total number of requests processed in the past hour.

---

## Approximate GPU Token-Hours

```promql
sum(increase(vllm:request_max_num_generation_tokens_sum[1h])) / 3600
```

Estimates GPU usage in token-hours. Useful as a proxy for workload size.

---

## Prompt vs Completion Tokens per Model

```promql
sum by(model_name) (rate(vllm:prompt_tokens_total[5m]))
sum by(model_name) (rate(vllm:generation_tokens_total[5m]))
```

Breaks down prompt and generated token throughput by model.

---
