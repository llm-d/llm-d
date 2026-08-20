# vLLM on GKE TPU7x Dynamic Sub-Slices

These recipes deploy aggregated (non-disaggregated) vLLM model servers onto dynamically formed TPU7x sub-slices, as variants of the [Optimized Baseline guide](../../../../README.md). Instead of provisioning one static node pool per TPU topology, capacity is pre-provisioned as `4x4x4` sub-blocks and [GKE dynamic slicing](../../../../../../docs/infrastructure/providers/gke/dynamic-slicing/README.md) forms a sub-slice per model server replica at scheduling time, via Kueue Topology-Aware Scheduling.

Each replica is a `LeaderWorkerSet` group; the GKE slice controller activates the requested sub-slice shape for the group and re-forms it on healthy partitions after hardware failure.

## Provided Topologies

| Directory | Slice shape | Chips | Hosts (LWS `size`) | TP | Model |
| --- | --- | --- | --- | --- | --- |
| [`2x2x1/`](./2x2x1/) | `2x2x1` | 4 | 1 | 8 | `Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8` |
| [`2x2x2/`](./2x2x2/) | `2x2x2` | 8 | 2 | 16 | `Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8` |

Both variants serve `Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8`, the model used for load testing these recipes; engine arguments are carried over from those load tests. To target `2x2x4` (4 hosts, TP up to 32) or `2x4x4` (8 hosts, TP up to 64), copy the `2x2x2` variant and change the `cloud.google.com/gke-tpu-slice-topology` annotation, the `cloud.google.com/gke-tpu-partition-<shape>-state` node selector, the LWS `size`, and `--tensor-parallel-size` (2 cores per chip).

## Prerequisites

1. Complete the cluster and Kueue TAS setup in [TPU Dynamic Slicing on GKE](../../../../../../docs/infrastructure/providers/gke/dynamic-slicing/README.md).
2. Deploy the llm-d router by following the [Optimized Baseline guide](../../../../README.md) through the router installation step, with `NAMESPACE=llm-d-optimized-baseline`.
3. Create the `LocalQueue` in the guide namespace:

   ```bash
   kubectl apply -n ${NAMESPACE} -f ${REPO_ROOT}/docs/infrastructure/providers/gke/dynamic-slicing/kueue-localqueue.yaml
   ```

## Deploy

Choose one topology variant:

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/optimized-baseline/modelserver/tpu/v7/vllm-dynamic-slice/2x2x1/
```

or

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/optimized-baseline/modelserver/tpu/v7/vllm-dynamic-slice/2x2x2/
```

## Verify

Workloads are admitted by Kueue once their `Slice` custom resources are `ACTIVE`:

```bash
kubectl get workloads -n ${NAMESPACE}
kubectl get slices -n ${NAMESPACE}
kubectl get pods -n ${NAMESPACE}
```

Then follow the [Optimized Baseline verification steps](../../../../README.md#verification), using the model name from the table above in the completion request.

## Benchmark Results

See [benchmark-results.md](./benchmark-results.md) for measured recovery time after node failure (MTTR 42-58s to a re-formed sub-slice) and workload scale-up latency at 16 to 256 concurrent slices (p50 30-41s). Serving-engine throughput and latency match the static-topology Optimized Baseline TPU recipe, since the underlying vLLM configuration is identical; dynamic slicing changes only provisioning, scheduling, and recovery.

## Notes

* Increasing `spec.replicas` on the `LeaderWorkerSet` scales out one sub-slice per replica; replicas are formed from any sub-block with healthy partitions of the requested shape.
* Different topology variants (and the [P/D dynamic-slice recipes](../../../../../pd-disaggregation/modelserver/tpu/v7/vllm-dynamic-slice/)) can share the same node pools and `ClusterQueue`; this is the primary utilization benefit over static per-topology node pools.
* On failure of a host in a multi-host group, `RecreateGroupOnPodRestart` restarts the group and the slice controller re-forms the sub-slice on healthy partitions.

## Testing Status

These recipes are not yet covered by the nightly e2e matrix. An end-to-end run requires a GKE Standard (Rapid channel) cluster with at least one full TPU7x cube - a `4x4x4` sub-block of 64 chips (16 `tpu7x-standard-4t` nodes) in an All Capacity mode reservation - and this capacity is not currently available to llm-d CI. Until it is, the recipes are validated by kustomize dry-run in CI, and functionally by load tests on internal Google Cloud capacity (see [benchmark-results.md](./benchmark-results.md)). A nightly e2e workflow (`nightly-e2e-optimized-baseline-gke-acc-tpu-vllm-*`) will be added once capacity is secured.
