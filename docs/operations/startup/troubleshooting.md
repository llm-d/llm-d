# Troubleshoot Model Startup

Use this page to measure model-server startup and fix slow or failing starts. For model sources and caches, see [Load and Cache Model Weights](./model-loading-and-startup.md).

## Measure Startup

Compare cold starts, warm-cache restarts, and scale-outs with fixed model revision, image, hardware, and parallelism. Record weight-loading, compilation, and total time to all target Pods Ready, noting cache state and whether downloads or pre-staging are timed. Keep compilation settings fixed when comparing [storage paths](../../../guides/modelexpress-p2p/measuring-storage-paths.md).

## Hugging Face Rate Limiting

During large-scale rollouts or scale-outs, concurrent model weight downloads across Pods (including prefill and decode replicas) can trigger Hugging Face rate limiting (HTTP 429) and delay startup. Reuse [model caches](./model-loading-and-startup.md#model-caches-and-internal-registries) or pre-stage model files to reduce concurrent downloads; longer request timeouts do not remove rate limits.

## Hub Request Timeouts

If Hub requests time out, adjust the [Hub timeout settings](https://huggingface.co/docs/huggingface_hub/en/package_reference/environment_variables) in `modelserver.env`: `HF_HUB_DOWNLOAD_TIMEOUT` controls file-download response timeouts, and `HF_HUB_ETAG_TIMEOUT` controls metadata request timeouts. Both values are in seconds. Adjust the example values below for your network conditions:

```yaml
- name: HF_HUB_DOWNLOAD_TIMEOUT
  value: "60"
- name: HF_HUB_ETAG_TIMEOUT
  value: "60"
```

## Xet Download Failures

For failures specific to the `hf-xet` download backend, try `HF_HUB_DISABLE_XET=1` while diagnosing the problem. Do not disable Xet by default or treat it as a rate-limit workaround.

## Container Restarts During Startup

Check Pod events and startup logs to confirm that failed startup probes, rather than a process crash, are causing restarts. If initialization is still progressing, size `startupProbe.failureThreshold * startupProbe.periodSeconds` to cover the measured worst-case cold startup, including downloads, weight loading, compilation, and engine initialization, with a margin.

Preserve the existing probe handler when adjusting these fields. This avoids premature container restarts; it does not accelerate startup. See the [probe configuration guide](../lifecycle/readiness-probes.md#recommended-probe-configuration) for a complete example.

## Cache Storage Errors

* [Insufficient cache space](https://github.com/llm-d/llm-d/issues/857): Confirm that downloads use the intended mount. Ensure the cache volume has enough space for the full checkpoint and temporary download files.
* [Read-only file system while Hugging Face writes its cache](https://github.com/llm-d-incubation/llm-d-modelservice/issues/243): Keep a complete preloaded checkpoint read-only, but provide a separate writable mount for a download cache.
