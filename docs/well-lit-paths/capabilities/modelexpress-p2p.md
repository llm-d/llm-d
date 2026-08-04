# ModelExpress P2P Weight Transfer

Large model scale-outs often spend their first minutes moving the same checkpoint into every replica. With a dense checkpoint such as `meta-llama/Llama-3.3-70B-Instruct`, each replica must materialize roughly 140 GB of bf16 weights before it can serve traffic.

ModelExpress changes that startup path from "every pod reads the checkpoint from storage" to "one seed pod loads the checkpoint, then peer pods pull materialized tensors directly from that seed over RDMA." The ModelExpress server coordinates metadata only: it tracks which workers are ready sources for a given model identity and returns the NIXL endpoints that receiver pods need to pull from peer HBM.

This is useful when:

* Multiple replicas serve the same model checkpoint.
* Pods have access to an InfiniBand, RoCE, EFA, or equivalent RDMA-capable fabric.
* Cold scale-outs, rolling restarts, or training rollout workers are sensitive to repeated weight-loading latency.

## Deploy

See the [ModelExpress P2P Weight Transfer guide](../../guides/modelexpress-p2p) for manifests and step-by-step deployment.

## Architecture

```text
HuggingFace/storage
        |
        v
  seed vLLM pod
  loads checkpoint
        |
        | publishes tensor metadata
        v
ModelExpress server
 metadata broker only
        |
        | receiver discovers source
        v
 receiver vLLM pods  <==== RDMA/NIXL HBM-to-HBM ====  seed vLLM pod
```

The first pod that completes a normal model load registers its tensors with NIXL and publishes a ready source through the ModelExpress Kubernetes metadata backend. Later pods ask the ModelExpress server for matching sources and transfer tensors directly from the seed pod's GPU memory. The transfer does not require shared storage on the receiver side, and the ModelExpress server does not proxy weight bytes.

The current guide deploys a small 2-replica pool so operators can verify the path with one seed and one receiver. Wider pools increase the fan-out and make the repeated storage-read avoidance more visible.

## Measurement Notes

The guide includes optional commands for measuring local P2P and storage-backed loading paths in the same cluster. The included numbers are environment-specific observations from one CoreWeave H200 + InfiniBand setup, not official NVIDIA benchmark results. Treat them as examples of what to measure locally while NVIDIA prepares official benchmark data.
