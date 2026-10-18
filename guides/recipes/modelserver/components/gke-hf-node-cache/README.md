# GKE Patches - Node-Local Hugging Face Cache

## Caching Model Weights on the Node

You need this patch component when model-server pods download their weights to the container's writable layer while several pods share a node: each pod downloads its own copy to the node boot disk, which the scheduler does not account for and the kubelet eventually evicts.

### Diagnosis

Model weights land in `/root/.cache/huggingface` unless `HF_HOME` says otherwise, so the overlay's `/.cache` (torch), `/.triton`, and `/.config` mounts receive nothing:

```bash
kubectl -n "${NAMESPACE}" exec deploy/mm-baseline-nvidia-gpu-vllm-decode -- du -sh /root/.cache/huggingface
```

When the node runs out of disk, pods cycle through `Evicted` and the node reports `DiskPressure`:

```
The node was low on resource: ephemeral-storage
```

The eviction threshold defaults to 10% of the node disk, so a node holding four TP=2 pods of a 63 GB model needs roughly 4 × 63 GB plus the image.

### Fix

Include this component from an overlay that renders a `Deployment`:

```yaml
components:
  - ../../../../../../recipes/modelserver/components/gke-hf-node-cache
```

It sets `HF_HOME`, the download timeout, and `HF_HUB_DISABLE_XET` on the first container, and mounts `/var/cache/huggingface` there as a `DirectoryOrCreate` `hostPath`. Downloads then happen once per node instead of once per pod, and restarts and redeployments start from the warm cache.

Overlays that render a `DisaggregatedSet` instead of a `Deployment` need the same settings as an inline role patch; see the `e-pd` and `e-p-d` GKE overlays under [`guides/multimodal-serving`](../../../../multimodal-serving/e-disaggregation/modelserver/gpu/vllm/e-pd/gke/kustomization.yaml). The component patches only the first container, so use it with overlays where that container is `modelserver`.

The cache survives pod restarts, has no eviction policy or size limit, and keeps every model and revision ever downloaded, so it is not a substitute for a PVC- or object-storage-backed cache when several models are served from one cluster, when weights are private, or when the model is larger than the node disk. See [Load and Cache Model Weights](../../../../../docs/operations/startup/model-loading-and-startup.md).
