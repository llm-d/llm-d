# Load and Cache Model Weights

Use this page to control where model servers get their weights and how they reuse downloads and compiled artifacts across restarts and scale-outs. To cut startup further by transferring weights between replicas, restoring snapshots, or keeping warm instances, see [Other ways to speed up startup](#other-ways-to-speed-up-startup).

File caches reduce repeated downloads and JIT caches reuse compiled artifacts; backend-specific settings are noted below. These caches are separate from [KV-cache management](../../architecture/kv-management/README.md).

## Loading from Hugging Face Hub

In `modelserver`, pass a Hub model ID (e.g. `Qwen/Qwen3-0.6B`) to [`vllm serve`](https://docs.vllm.ai/en/latest/configuration/engine_args/). For [SGLang](https://docs.sglang.ai/advanced_features/server_arguments.html), use `--model-path` instead. Both support `--served-model-name` for the API model name. Pin `--revision` to a commit SHA; for vLLM, also pin `--tokenizer-revision` and `--code-revision` when applicable.

Gated or private models require [access and a token](../../../helpers/hf-token.md). For public models, remove the `llm-d-hf-token` Secret reference or mark it optional:

```yaml
- name: HF_TOKEN
  valueFrom:
    secretKeyRef:
      name: llm-d-hf-token
      key: HF_TOKEN
      optional: true
```

An optional Secret does not bypass authorization. Keep tokens out of manifests.

## Model Caches and Internal Registries

Use node-local storage or a PVC to reuse model files, and an internal registry for a self-hosted source. None of these choices alone makes the deployment air-gapped.

### Node-Local Cache

Where nodes provide suitable local storage, mount a host directory at the Hugging Face cache path:

```yaml
containers:
  - name: modelserver
    env:
      - name: HF_HOME
        value: /root/.cache/huggingface
    volumeMounts:
      - name: huggingface-cache
        mountPath: /root/.cache/huggingface
volumes:
  - name: huggingface-cache
    hostPath:
      path: /var/cache/huggingface
      type: DirectoryOrCreate
```

This reuses downloads on one node, but not across nodes or after node replacement.

> [!NOTE]
> `/var/cache/huggingface` is an example host path. Choose a directory that fits the node's disk allocation and storage policies and complies with the cluster's security policy.

### PVC Cache

Use the [model-cache component](../../../guides/recipes/modelserver/components/model-cache/kustomization.yaml) to persist and share Hugging Face downloads on an RWX PVC. It patches the first container of each `Deployment`; use a model-server overlay where that container is `modelserver`. Run from the repository root with your guide's `NAMESPACE`, model configuration, and credentials.

1. **Create `model-pvc`.** Adjust the [example](../../../guides/recipes/modelserver/components/model-cache/model-cache-pvc.yaml) for capacity and an RWX-capable StorageClass:

   ```bash
   kubectl -n "${NAMESPACE}" apply \
     -f guides/recipes/modelserver/components/model-cache/model-cache-pvc.yaml
   ```

2. **Add the component to your existing overlay.** In its `kustomization.yaml`, add the [model-cache component](../../../guides/recipes/modelserver/components/model-cache/kustomization.yaml) to `components`, using the component directory's path relative to your overlay directory. Preserve existing resources, components, and patches. The [AMD CI overlay](../../../guides/optimized-baseline/modelserver/amd/vllm/amd-ci/kustomization.yaml) is a reference for component inclusion, not the deployment target for this example.

3. **Render and verify.** Set `MODEL_SERVER_OVERLAY` to your modified overlay directory:

   ```bash
   kubectl kustomize "${MODEL_SERVER_OVERLAY}"
   ```

   Check `modelserver` for `HF_HOME=/model-cache` and a `/model-cache` mount backed by `model-pvc`, without duplicate entries. Keep the remote model ID; follow your guide to deploy, then verify cache write access and [inference](../../../guides/optimized-baseline/README.md#verification).

### Self-Hosted Registry with MatrixHub

[MatrixHub](https://github.com/matrixhub-ai/matrixhub) serves cached model weight files through a self-hosted, Hugging Face-compatible API. [Pre-cache the model](https://matrixhub.ai/docs/guides/mirror-from-huggingface/) and use its repository ID.

Following the [vLLM MatrixHub documentation](https://docs.vllm.ai/en/latest/models/supported_models/#matrixhub), set `HF_ENDPOINT` in `modelserver.env` to your registry address, reachable from model-server Pods:

```yaml
- name: HF_ENDPOINT
  value: "http://<matrixhub-host>:9527"
```

This example assumes anonymous access on a trusted network. Remove the inherited `HF_TOKEN` Secret reference and ensure no saved or explicitly supplied Hugging Face credentials are used.

## Reuse the Compilation Cache

vLLM's compilation cache (JIT cache) stores compiled artifacts, not model weights, to reduce compilation overhead on later starts. Point `VLLM_CACHE_ROOT` to a persistent, writable directory. The Wide EP [cache configuration](../../../guides/wide-ep/modelserver/gpu/vllm/base/disaggregatedset.yaml) places it under `/var/cache/vllm`; the [CoreWeave overlay](../../../guides/wide-ep/modelserver/gpu/vllm/coreweave/kustomization.yaml) persists that mount using node-local `hostPath` storage.

With an empty cache, the first startup still compiles and populates it; later compatible starts can reuse the results. Configuration or code changes may trigger recompilation, so persistence does not guarantee compilation-free startup. A node-local cache is reusable only on that node. See [vLLM's compilation-cache documentation](https://docs.vllm.ai/en/latest/design/torch_compile/#compilation-cache).

## When Hugging Face Access Is Limited

If Pods cannot reliably reach the Hub or its artifact endpoints, use a reachable alternative platform such as ModelScope. It still requires network access.

### Using ModelScope

For [vLLM](https://docs.vllm.ai/en/latest/models/supported_models/#modelscope), set `VLLM_USE_MODELSCOPE=True`; if `modelscope` is missing, install a compatible, pinned version with `pip` when building the image. Use ModelScope IDs, revisions, and `MODELSCOPE_CACHE`, not `HF_HOME`. For fixed checkpoints, pre-stage verified files and use their local directory as the model path.

## Other Ways to Speed Up Startup

| Approach | What it avoids | Guide |
| -------- | -------------- | ----- |
| Peer-to-peer weight transfer | Loading weights from storage on every replica: one seed replica loads them, peers receive them over GPU-to-GPU NIXL/RDMA (`--load-format=mx`) | [Transfer Weights Peer-to-Peer (ModelExpress)](../../../guides/modelexpress-p2p/README.md) |
| Pod snapshots | Model download and engine initialization: new Pods restore a checkpointed, initialized model server (single-GPU vLLM on GKE) | [Restore from Pod Snapshots](../../../guides/pod-snapshot/README.md) |
| Warm instances (sleep/wake) | Process start and module import: resident instances sleep and wake, and a pre-warmed launcher spawns new ones | [Reuse Warm Model Servers (FMA)](../../../guides/fast-model-actuation-base/README.md) |
| Warm instances with scale from zero | Idle GPU cost while keeping fast actuation: KEDA scales FMA on EPP flow-control metrics | [Scale from Zero with FMA and KEDA](../../../guides/fast-model-actuation-keda/README.md) |

The ModelExpress guide also covers [checkpoint pre-staging](../../../guides/modelexpress-p2p/measuring-storage-paths.md#1-prewarm-the-checkpoint-onto-nfs-once) (ordinary files, not an `HF_HOME` cache), [compilation-cache distribution via P2P transfer or a shared RWX PVC](../../../guides/modelexpress-p2p/compile-cache.md), and storage-backed alternatives to P2P using [fastsafetensors on NFS or local NVMe](../../../guides/modelexpress-p2p/measuring-storage-paths.md); follow each path's prerequisites.

## Verification

Check model-server startup logs to confirm loading completed. After changing the model name or routing, [test a request through llm-d](../../../guides/optimized-baseline/README.md#verification). To measure startup or fix slow or failing starts, see [Troubleshoot Model Startup](./troubleshooting.md).
