# llm-d on CoreWeave

This document covers configuring CoreWeave Kubernetes Service (CKS) clusters for running high performance LLM inference with llm-d.

## Cluster Configuration

### RDMA

CoreWeave nodes reach the InfiniBand fabric through an RDMA device plugin that advertises `rdma/ib` inside pods (one per pod). See [RDMA resources and capabilities](../../rdma/README.md#rdma-resources-and-capabilities) for how llm-d model servers request it.

### Storage

The guides' CoreWeave overlays cache model weights on a `ReadWriteMany` PVC (see below), so the cluster's default StorageClass must support `ReadWriteMany`.

## Caching Model Weights on a Shared PVC

`/var/cache/huggingface` on CoreWeave nodes is a 15&nbsp;GB ramdisk (`/dev/ram0`). A node-local `hostPath` HuggingFace cache there cannot hold a large model — `gpt-oss-120b` alone is 60.8&nbsp;GiB of safetensors — and the first download fails with `OSError: [Errno 28] No space left on device`.

The CoreWeave model server overlays therefore mount the HuggingFace cache from an RWX PVC named `model-pvc` instead, which also downloads the weights once for the whole deployment rather than once per node. Create it in the guide's namespace before applying the model server overlay:

```bash
kubectl apply -n ${NAMESPACE} -f guides/recipes/modelserver/components/model-cache/model-cache-pvc.yaml
```

[`model-cache-pvc.yaml`](../../../../guides/recipes/modelserver/components/model-cache/model-cache-pvc.yaml) omits `storageClassName`, so the PVC binds the cluster's default StorageClass.

Guides that automate this run the step above when `INFRA_PROVIDER=coreweave`; see the [Prefill/Decode disaggregation guide](../../../../guides/pd-disaggregation/README.md#prerequisites) for an example.

### Cold cache with multiple replicas

On a cold cache every replica pulls at once. `huggingface_hub` takes a per-blob lock under `${HF_HOME}/hub/.locks`, which serializes the download only where the storage backend honors `flock` — on RWX NFS that is not guaranteed, and a `nolock` mount makes the lock node-local. A lost race is loud rather than silent: all replicas share one blob path, so they append to the same `.incomplete` file and the post-download size check fails (`Consistency check failed: file should be of size ...`), restarting the pod.

To avoid depending on lock behaviour, pre-populate the PVC with a one-off download Job before applying the overlay, as in [`fastsafetensors-prewarm/prewarm-job.yaml`](../../../../guides/modelexpress-p2p/modelserver/gpu/vllm/fastsafetensors-prewarm/prewarm-job.yaml).

For the full set of model source, caching, and startup options, see the [Model Loading and Startup Acceleration operations guide](../../../operations/startup/model-loading-and-startup.md).
