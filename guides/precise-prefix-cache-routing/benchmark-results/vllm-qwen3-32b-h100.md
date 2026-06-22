# Qwen/Qwen3-32B Precise Routing Benchmark on vLLM (16×H100)

The benchmark runs on 16× H100 GPUs, distributed across 8 model servers (2 H100s per server with TP=2).

## Comparing llm-d Scheduling to a Simple Kubernetes Service

Graphs below compare the precise path to a stock Kubernetes Service that round-robins requests across the same 8 vLLM pods (no EPP, no scoring).

<img src="./throughput_vs_qps.png" width="900" alt="Throughput vs QPS">
<img src="./latency_vs_qps.png" width="900" alt="Latency vs QPS">
<img src="./ttft_p90_vs_qps.png" width="900" alt="TTFT p90 vs QPS">

Summary across the full ladder (rates 3 → 60):

| Metric              | k8s service (RR) | llm-d Precise | Δ% vs k8s |
| :------------------ | :--------------- | :------------ | :-------- |
| Output tokens/sec   | 5,722            | 12,598        | +120.2%   |
| Requests/sec        | 35.87            | 36.01         | +0.4%     |
| TTFT mean (s)       | 58.10            | 0.247         | −99.57%   |
| TTFT p90 (s)        | 107.43           | 0.262         | −99.76%   |
| ITL mean (ms)       | 44.0             | 47.0          | +6.8%     |

<details>
<summary><b><i>Click</i></b> to view the per-rate breakdown across the full ladder</summary>

Output tokens/sec — higher is better; TTFT in seconds — lower is better.

| Rate | k8s Output | llm-d Output | k8s TTFT mean | llm-d TTFT mean | k8s TTFT p90 | llm-d TTFT p90 |
| ---: | ---------: | -----------: | ------------: | --------------: | -----------: | -------------: |
|  3   | 1,797      | 1,707        | 0.415         | 0.155           | 0.522        | 0.187          |
| 10   | 4,215      | 4,904        | 0.630         | 0.150           | 1.014        | 0.199          |
| 15   | 5,381      | 6,887        | 0.881         | 0.155           | 1.593        | 0.225          |
| 20   | 6,205      | 11,224       | 18.103        | 0.206           | 35.344       | 0.320          |
| 22   | 5,517      | 11,980       | 20.171        | 0.152           | 39.436       | 0.191          |
| 25   | 5,965      | 12,548       | 21.842        | 0.158           | 42.813       | 0.200          |
| 30   | 5,702      | 13,507       | 24.597        | 0.155           | 46.036       | 0.193          |
| 35   | 5,890      | 13,803       | 24.162        | 0.157           | 45.190       | 0.202          |
| 40   | 6,336      | 15,593       | 68.673        | 0.494           | 126.238      | 0.272          |
| 43   | 6,588      | 15,612       | 72.429        | 0.422           | 130.275      | 0.265          |
| 46   | 6,459      | 15,462       | 70.084        | 0.257           | 129.810      | 0.273          |
| 49   | 6,265      | 15,607       | 70.659        | 0.200           | 133.718      | 0.267          |
| 52   | 6,303      | 15,728       | 74.326        | 0.208           | 134.981      | 0.279          |
| 55   | 6,290      | 15,612       | 72.564        | 0.199           | 134.034      | 0.272          |
| 57   | 6,089      | 15,667       | 72.329        | 0.211           | 135.023      | 0.293          |
| 60   | 6,551      | 15,733       | 75.586        | 0.214           | 138.663      | 0.300          |

</details>
