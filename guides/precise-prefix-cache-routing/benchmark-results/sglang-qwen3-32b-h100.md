# Qwen/Qwen3-32B Precise Routing Benchmark on SGLang (16×H100)

The benchmark runs on 16× H100 GPUs, distributed across 8 model servers (2 H100s per server with TP=2).

## Comparing llm-d Scheduling to a Simple Kubernetes Service

Summary across the full ladder (rates 3 → 60):

| Metric              | k8s service (RR) | llm-d Precise | Δ% vs k8s |
| :------------------ | :--------------- | :------------ | :-------- |
| Output tokens/sec   | 4,667            | 9,808         | +110.2%   |
| Requests/sec        | 4.71             | 9.87          | +109.6%   |
| TTFT mean (s)       | 69.76            | 0.466         | −99.33%   |
| TTFT p90 (s)        | 157.64           | 0.672         | −99.57%   |
| ITL mean (ms)       | 37.9             | 47.4          | +25.1%    |

<details>
<summary><b><i>Click</i></b> to view the per-rate breakdown across the full ladder</summary>

Output tokens/sec — higher is better; TTFT in seconds — lower is better.

| Rate | k8s Output | llm-d Output | k8s TTFT mean | llm-d TTFT mean | k8s TTFT p90 | llm-d TTFT p90 |
| ---: | ---------: | -----------: | ------------: | --------------: | -----------: | -------------: |
|  3   | 1,698      | 1,556        | 0.511         | 0.160           | 0.824        | 0.194          |
| 10   | 4,359      | 5,049        | 0.849         | 0.178           | 1.459        | 0.218          |
| 15   | 4,608      | 7,188        | 2.734         | 0.179           | 3.696        | 0.245          |
| 20   | 5,035      | 11,268       | 27.104        | 0.250           | 62.562       | 0.436          |
| 22   | 4,684      | 11,945       | 31.012        | 0.202           | 68.263       | 0.328          |
| 25   | 5,056      | 12,797       | 31.411        | 0.195           | 69.237       | 0.255          |
| 30   | 4,953      | 13,412       | 34.123        | 0.217           | 72.725       | 0.415          |
| 35   | 5,601      | 13,707       | 33.340        | 0.215           | 74.115       | 0.310          |
| 40   | 5,773      | 15,914       | 85.332        | 1.171           | 152.247      | 0.754          |
| 43   | 5,395      | 16,485       | 87.314        | 0.999           | 157.234      | 0.762          |
| 46   | 5,794      | 16,376       | 88.325        | 0.514           | 160.052      | 0.716          |
| 49   | 5,622      | 16,576       | 86.050        | 0.320           | 161.950      | 0.631          |
| 52   | 5,905      | 16,627       | 89.924        | 0.328           | 162.860      | 0.692          |
| 55   | 5,714      | 16,534       | 88.526        | 0.367           | 162.728      | 0.802          |
| 57   | 5,744      | 16,459       | 88.682        | 0.374           | 163.161      | 0.781          |
| 60   | 5,833      | 16,481       | 88.046        | 0.375           | 161.321      | 0.749          |

</details>

> Benchmark contributed by @liu-cong.
