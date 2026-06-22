# Benchmark Report

The benchmark runs with decoders model Qwen/Qwen3-32B on 16 × H100 GPUs, distributed across 8 model servers (2 H100s per server with TP=2).

## Comparing llm-d Routing to a Simple Kubernetes Service (vLLM)

Graphs below compare optimized-baseline routing to a stock Kubernetes Service that round-robins requests across the same 8 vLLM pods (no EPP, no scoring).

<img src="./throughput_vs_qps.png" width="900" alt="Throughput vs QPS">
<img src="./latency_vs_qps.png" width="900" alt="Latency vs QPS">
<img src="./ttft_p90_vs_qps.png" width="900" alt="TTFT p90 vs QPS">

Summary across the full ladder (rates 3 → 60):

| Metric              | k8s service (RR) | llm-d Optimized | Δ% vs k8s |
| :------------------ | :--------------- | :-------------- | :-------- |
| Output tokens/sec   | 5,722            | 13,163          | +130.0%   |
| Requests/sec        | 35.87            | 36.38           | +1.4%     |
| TTFT mean (s)       | 58.10            | 0.156           | −99.73%   |
| TTFT p90 (s)        | 107.43           | 0.206           | −99.81%   |
| ITL mean (ms)       | 44.0             | 47.0            | +6.8%     |

<details>
<summary><b><i>Click</i></b> to view the per-rate breakdown across the full ladder</summary>

Output tokens/sec — higher is better; TTFT in seconds — lower is better.

| Rate | k8s Output | llm-d Output | k8s TTFT mean | llm-d TTFT mean | k8s TTFT p90 | llm-d TTFT p90 |
| ---: | ---------: | -----------: | ------------: | --------------: | -----------: | -------------: |
|  3   | 1,797      | 1,777        | 0.415         | 0.133           | 0.522        | 0.162          |
| 10   | 4,215      | 5,066        | 0.630         | 0.125           | 1.014        | 0.172          |
| 15   | 5,381      | 7,053        | 0.881         | 0.122           | 1.593        | 0.187          |
| 20   | 6,205      | 11,688       | 18.103        | 0.174           | 35.344       | 0.283          |
| 22   | 5,517      | 12,436       | 20.171        | 0.116           | 39.436       | 0.148          |
| 25   | 5,965      | 12,501       | 21.842        | 0.116           | 42.813       | 0.146          |
| 30   | 5,702      | 13,862       | 24.597        | 0.117           | 46.036       | 0.148          |
| 35   | 5,890      | 14,026       | 24.162        | 0.117           | 45.190       | 0.150          |
| 40   | 6,336      | 16,041       | 68.673        | 0.153           | 126.238      | 0.216          |
| 43   | 6,588      | 16,339       | 72.429        | 0.254           | 130.275      | 0.218          |
| 46   | 6,459      | 16,665       | 70.084        | 0.154           | 129.810      | 0.220          |
| 49   | 6,265      | 16,126       | 70.659        | 0.151           | 133.718      | 0.209          |
| 52   | 6,303      | 16,474       | 74.326        | 0.152           | 134.981      | 0.219          |
| 55   | 6,290      | 16,854       | 72.564        | 0.153           | 134.034      | 0.215          |
| 57   | 6,089      | 16,641       | 72.329        | 0.153           | 135.023      | 0.217          |
| 60   | 6,551      | 17,064       | 75.586        | 0.154           | 138.663      | 0.217          |

</details>