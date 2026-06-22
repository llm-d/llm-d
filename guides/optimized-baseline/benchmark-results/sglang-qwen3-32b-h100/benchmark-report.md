# Benchmark Report

The benchmark runs on 16 × H100 GPUs, distributed across 8 model servers (2 H100s per server with TP=2).

## Comparing llm-d Routing to a Simple Kubernetes Service (SGLang)

The following results compare SGLang performance using a standard Kubernetes Service vs. the llm-d router on identical 16 × H100 hardware.

Summary across the full ladder (rates 3 → 60):

| Metric              | k8s service (RR) | llm-d Optimized | Δ% vs k8s |
| :------------------ | :--------------- | :-------------- | :-------- |
| Output tokens/sec   | 4,667            | 9,910           | +112.3%   |
| Requests/sec        | 4.71             | 10.00           | +112.3%   |
| TTFT mean (s)       | 69.76            | 0.30            | −99.57%   |
| TTFT p90 (s)        | 157.64           | 0.21            | −99.87%   |
| ITL mean (ms)       | 37.9             | 46.1            | +21.6%    |

<details>
<summary><b><i>Click</i></b> to view the per-rate breakdown across the full ladder</summary>

Output tokens/sec — higher is better; TTFT in seconds — lower is better.

| Rate | k8s Output | llm-d Output | k8s TTFT mean | llm-d TTFT mean | k8s TTFT p90 | llm-d TTFT p90 |
| ---: | ---------: | -----------: | ------------: | --------------: | -----------: | -------------: |
|  3   | 1,698      | 1,540        | 0.511         | 0.132           | 0.824        | 0.157          |
| 10   | 4,359      | 4,928        | 0.849         | 0.118           | 1.459        | 0.163          |
| 15   | 4,608      | 7,204        | 2.734         | 0.115           | 3.696        | 0.174          |
| 20   | 5,035      | 11,336       | 27.104        | 0.169           | 62.562       | 0.252          |
| 22   | 4,684      | 11,933       | 31.012        | 0.112           | 68.263       | 0.151          |
| 25   | 5,056      | 12,763       | 31.411        | 0.116           | 69.237       | 0.152          |
| 30   | 4,953      | 13,553       | 34.123        | 0.113           | 72.725       | 0.147          |
| 35   | 5,601      | 13,289       | 33.340        | 0.109           | 74.115       | 0.147          |
| 40   | 5,773      | 15,704       | 85.332        | 0.962           | 152.247      | 0.256          |
| 43   | 5,395      | 16,481       | 87.314        | 1.073           | 157.234      | 0.204          |
| 46   | 5,794      | 16,878       | 88.325        | 0.133           | 160.052      | 0.167          |
| 49   | 5,622      | 16,629       | 86.050        | 0.136           | 161.950      | 0.171          |
| 52   | 5,905      | 16,996       | 89.924        | 0.146           | 162.860      | 0.198          |
| 55   | 5,714      | 17,155       | 88.526        | 0.143           | 162.728      | 0.183          |
| 57   | 5,744      | 17,021       | 88.682        | 0.142           | 163.161      | 0.191          |
| 60   | 5,833      | 17,156       | 88.046        | 0.145           | 161.321      | 0.208          |

</details>