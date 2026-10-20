# Batch Gateway Metrics

Only relevant if you deployed the [Batch Gateway guide](../../../guides/batch-serving/batch-gateway/README.md). These are the operationally useful series; the definitional source of truth for names, types, and labels is [`docs/guides/metrics.md`](https://github.com/llm-d/llm-d-batch-gateway/blob/main/docs/guides/metrics.md) in the component repo, which also documents the token, cancellation, and startup-recovery counters not listed here.

Unlike the [EPP](./router-metrics.md) and [model server](./model-server-metrics.md) metrics, these names carry no `llm_d_` prefix, so scope queries by `namespace` to avoid picking up unrelated workloads.

| Metric | What it measures | Why it matters |
| -------- | ----------------- | ---------------- |
| `http_requests_total` | API server requests by method, path, and status | Baseline for API-side error rate — batch submission failures show up here, not in job metrics |
| `http_request_duration_seconds` | API server request latency distribution | Submission and status-poll responsiveness |
| `jobs_processed_total` | Jobs processed by `result` (`success`, `failed`, `skipped`, `re_enqueued`, `expired`) and `reason` | The primary outcome signal. `expired` means jobs aged out before running, which is a capacity problem rather than a failure |
| `job_queue_wait_duration_seconds` | Time in the priority queue before pickup | Leading indicator of backlog; rises before end-to-end latency does |
| `job_processing_duration_seconds` | End-to-end processing duration by `size_bucket` | Separates "large jobs are slow" from "everything is slow" |
| `batch_job_e2e_latency_seconds` | Submission to terminal state, by `status` | What the user actually waits for, including queue time |
| `active_workers` / `total_workers` | Active vs configured worker pool size | Their ratio is the saturation signal — sustained near 1.0 means the pool is the bottleneck |
| `processor_inflight_requests` | In-flight inference requests during execution | Load the batch path is placing on the inference backends |
| `batch_reconciler_errors_total` | GC reconciler cycle errors | Non-zero means orphaned jobs are not being recovered |
| `batch_reconciler_orphans_recovered_total` | Orphans recovered by `action` | Sustained non-zero points at processor crashes upstream |
| `file_storage_operations_total` | File storage operations by `operation`, `component`, and `status` | `status="exhausted"` means retries gave up — input or output data is inaccessible |

> [!NOTE]
> The Batch Gateway dashboards compute histogram quantiles by summing buckets across pods before applying `histogram_quantile`, which yields a fleet-wide approximate percentile rather than an exact one. This is acceptable because each job is processed by exactly one pod, but it can mask a single consistently-slow pod. Add `by (le, pod)` for a per-pod breakdown.

For alerts built on these metrics, see [Configure Alerts](./alerting.md#batch-gateway-batch-gatewayrules).
