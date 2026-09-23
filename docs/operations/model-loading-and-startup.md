# Model Loading and Startup Acceleration

Use this guide to optimize model startup time in existing llm-d deployments, covering model-file retrieval, weight loading, compilation, and other engine initialization.

File caches reduce repeated downloads, JIT caches reuse compiled artifacts, and ModelExpress accelerates weight transfer; backend-specific settings are noted below. These caches are separate from [KV-cache management](../architecture/advanced/kv-management/README.md).

## Loading from Hugging Face Hub

In `modelserver`, pass a Hub model ID (e.g. `Qwen/Qwen3-0.6B`) to [`vllm serve`](https://docs.vllm.ai/en/latest/configuration/engine_args/). For [SGLang](https://docs.sglang.ai/advanced_features/server_arguments.html), use `--model-path` instead. Both support `--served-model-name` for the API model name. Pin `--revision` to a commit SHA; for vLLM, also pin `--tokenizer-revision` and `--code-revision` when applicable.

Gated or private models require [access and a token](../../helpers/hf-token.md). For public models, remove the `llm-d-hf-token` Secret reference or mark it optional:

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

Use the [model-cache component](../../guides/recipes/modelserver/components/model-cache/kustomization.yaml) to persist and share Hugging Face downloads on an RWX PVC. It patches the first container of each `Deployment`; use a model-server overlay where that container is `modelserver`. Run from the repository root with your guide's `NAMESPACE`, model configuration, and credentials.

1. **Create `model-pvc`.** Adjust the [example](../../guides/recipes/modelserver/components/model-cache/model-cache-pvc.yaml) for capacity and an RWX-capable StorageClass:

   ```bash
   kubectl -n "${NAMESPACE}" apply \
     -f guides/recipes/modelserver/components/model-cache/model-cache-pvc.yaml
   ```

2. **Add the component to your existing overlay.** In its `kustomization.yaml`, add the [model-cache component](../../guides/recipes/modelserver/components/model-cache/kustomization.yaml) to `components`, using the component directory's path relative to your overlay directory. Preserve existing resources, components, and patches. The [AMD CI overlay](../../guides/optimized-baseline/modelserver/amd/vllm/amd-ci/kustomization.yaml) is a reference for component inclusion, not the deployment target for this example.

3. **Render and verify.** Set `MODEL_SERVER_OVERLAY` to your modified overlay directory:

   ```bash
   kubectl kustomize "${MODEL_SERVER_OVERLAY}"
   ```

   Check `modelserver` for `HF_HOME=/model-cache` and a `/model-cache` mount backed by `model-pvc`, without duplicate entries. Keep the remote model ID; follow your guide to deploy, then verify cache write access and [inference](../../guides/optimized-baseline/README.md#verification).

### Self-Hosted Registry with MatrixHub

[MatrixHub](https://github.com/matrixhub-ai/matrixhub) serves cached model weight files through a self-hosted, Hugging Face-compatible API. [Pre-cache the model](https://matrixhub.ai/docs/guides/mirror-from-huggingface/) and use its repository ID.

Following the [vLLM MatrixHub documentation](https://docs.vllm.ai/en/latest/models/supported_models/#matrixhub), set `HF_ENDPOINT` in `modelserver.env` to your registry address, reachable from model-server Pods:

```yaml
- name: HF_ENDPOINT
  value: "http://<matrixhub-host>:9527"
```

This example assumes anonymous access on a trusted network. Remove the inherited `HF_TOKEN` Secret reference and ensure no saved or explicitly supplied Hugging Face credentials are used.

## Accelerating Model Startup

### Compilation Cache Reuse

vLLM's compilation cache (JIT cache) stores compiled artifacts, not model weights, to reduce compilation overhead on later starts. Point `VLLM_CACHE_ROOT` to a persistent, writable directory. The Wide EP [cache configuration](../../guides/wide-ep-lws/modelserver/gpu/vllm-deepseek-r1-0528/base/disaggregatedset.yaml) places it under `/var/cache/vllm`; the [CoreWeave overlay](../../guides/wide-ep-lws/modelserver/gpu/vllm-deepseek-r1-0528/coreweave/kustomization.yaml) persists that mount using node-local `hostPath` storage.

With an empty cache, the first startup still compiles and populates it; later compatible starts can reuse the results. Configuration or code changes may trigger recompilation, so persistence does not guarantee compilation-free startup. A node-local cache is reusable only on that node. See [vLLM's compilation-cache documentation](https://docs.vllm.ai/en/latest/design/torch_compile/#compilation-cache).

### ModelExpress

[ModelExpress](../../guides/modelexpress-p2p/README.md) transfers weights from a seed replica over NIXL/RDMA using vLLM's `--load-format=mx`. The seed needs a checkpoint source; use the guide's image and follow its version, CRD, GPU, and fabric requirements.

The guide also covers [checkpoint pre-staging](../../guides/modelexpress-p2p/measuring-storage-paths.md#1-prewarm-the-checkpoint-onto-nfs-once) (ordinary files, not an `HF_HOME` cache), [compilation-cache distribution via P2P transfer or a shared RWX PVC](../../guides/modelexpress-p2p/compile-cache.md), and storage-backed alternatives to P2P using [fastsafetensors on NFS or local NVMe](../../guides/modelexpress-p2p/measuring-storage-paths.md); follow each path's prerequisites.

For process reuse, see [FMA sleep/wake](../../guides/fast-model-actuation-base/README.md) and follow the guide's prerequisites.

### Pod Snapshots

[Pod snapshots](../../guides/pod-snapshot/README.md) capture a model server's initialized state so subsequent Pods can restore it instead of repeating model downloads and engine initialization. The linked guide covers single-GPU vLLM on GKE using GKE Pod Snapshots, GKE Sandbox (gVisor), and Google Cloud Storage. Follow the guide's prerequisites and wait for the first snapshot to be ready before scaling out.

## When Hugging Face Access Is Limited

If Pods cannot reliably reach the Hub or its artifact endpoints, use a reachable alternative platform such as ModelScope. It still requires network access.

### Using ModelScope

For [vLLM](https://docs.vllm.ai/en/latest/models/supported_models/#modelscope), set `VLLM_USE_MODELSCOPE=True`; if `modelscope` is missing, install a compatible, pinned version with `pip` when building the image. Use ModelScope IDs, revisions, and `MODELSCOPE_CACHE`, not `HF_HOME`. For fixed checkpoints, pre-stage verified files and use their local directory as the model path.

## Verification and Troubleshooting

Check model-server startup logs to confirm loading completed. After changing the model name or routing, [test a request through llm-d](../../guides/optimized-baseline/README.md#verification).

Compare cold starts, warm-cache restarts, and scale-outs with fixed model revision, image, hardware, and parallelism. Record weight-loading, compilation, and total time to all target Pods Ready, noting cache state and whether downloads or pre-staging are timed. Keep compilation settings fixed when comparing [storage paths](../../guides/modelexpress-p2p/measuring-storage-paths.md).

### Hugging Face Rate Limiting

During large-scale rollouts or scale-outs, concurrent model weight downloads across Pods (including prefill and decode replicas) can trigger Hugging Face rate limiting (HTTP 429) and delay startup. Reuse [model caches](#model-caches-and-internal-registries) or pre-stage model files to reduce concurrent downloads; longer request timeouts do not remove rate limits.

### Hub Request Timeouts

If Hub requests time out, adjust the [Hub timeout settings](https://huggingface.co/docs/huggingface_hub/en/package_reference/environment_variables) in `modelserver.env`: `HF_HUB_DOWNLOAD_TIMEOUT` controls file-download response timeouts, and `HF_HUB_ETAG_TIMEOUT` controls metadata request timeouts. Both values are in seconds. Adjust the example values below for your network conditions:

```yaml
- name: HF_HUB_DOWNLOAD_TIMEOUT
  value: "60"
- name: HF_HUB_ETAG_TIMEOUT
  value: "60"
```

### Xet Download Failures

For failures specific to the `hf-xet` download backend, try `HF_HUB_DISABLE_XET=1` while diagnosing the problem. Do not disable Xet by default or treat it as a rate-limit workaround.

### Container Restarts During Startup

Check Pod events and startup logs to confirm that failed startup probes, rather than a process crash, are causing restarts. If initialization is still progressing, size `startupProbe.failureThreshold * startupProbe.periodSeconds` to cover the measured worst-case cold startup, including downloads, weight loading, compilation, and engine initialization, with a margin.

Preserve the existing probe handler when adjusting these fields. This avoids premature container restarts; it does not accelerate startup. See the [probe configuration guide](readiness-probes.md#recommended-probe-configuration) for a complete example.

### Cache Storage Errors

* [Insufficient cache space](https://github.com/llm-d/llm-d/issues/857): Confirm that downloads use the intended mount. Ensure the cache volume has enough space for the full checkpoint and temporary download files.
* [Read-only file system while Hugging Face writes its cache](https://github.com/llm-d-incubation/llm-d-modelservice/issues/243): Keep a complete preloaded checkpoint read-only, but provide a separate writable mount for a download cache.
