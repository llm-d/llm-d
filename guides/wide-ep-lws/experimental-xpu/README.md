# Experimental: Intel XPU WideEP validation

This experimental guide records how far Intel XPU support currently reaches for `wide-ep-lws`. It includes a Kubernetes expert-parallel smoke manifest, Docker experiments for focused debugging, and the current full-path validation result.

The latest full-path proof target is DeepSeek-V2-Lite with decode + prefill, LWS, router/EPP, XPU expert parallelism, and NIXL KV transfer. That path is validated with XPU KV buffers (`kv_buffer_device=xpu`) when UCX enables the Level Zero copy transport (`UCX_TLS=tcp,ze_copy` on the tested non-RDMA cluster). The host-buffer path (`kv_buffer_device=cpu`) is also validated as a fallback.

## Positioning

The checked-in Kubernetes manifest intentionally stays as a small expert-parallel smoke test. The full `wide-ep-lws` validation was run with temporary LWS/router manifests and is summarized in the experiment matrix. The current PR scope is therefore:

- preserve a reproducible XPU EP Kubernetes smoke test;
- document the full XPU `wide-ep-lws` validation status;
- provide experiments that reproduce or debug the XPU runtime layers;
- document which NVIDIA-specific optimizations are omitted or replaced on XPU.

## Configuration

The checked-in smoke manifest defaults to:

| Parameter | Value |
| --- | --- |
| Model | `deepseek-ai/DeepSeek-V2-Lite-Chat` |
| Accelerator | Intel XPU |
| Kubernetes allocation | DRA device class `gpu.intel.com` |
| Smoke manifest XPU count | 4 per pod |
| Smoke manifest tensor parallelism | 4 |
| Expert parallelism | enabled |
| All2All backend | `allgather_reducescatter` |
| Image | `ghcr.io/llm-d/llm-d-xpu:v0.7.0` |

The separately validated full-path run used:

| Parameter | Value |
| --- | --- |
| Full LWS proof shape | decode TP=2 + prefill TP=2 |
| Full P/D KV transfer | NIXL with `kv_buffer_device=xpu` and `UCX_TLS=tcp,ze_copy` |
| Full-path manifests | Temporary LWS/router manifests, summarized in the experiment matrix |

## Experiment matrix

The scripts under [`experiments`](./experiments/) are ordered from lowest to highest integration risk:

| Experiment | What it proves | Expected XPU count |
| --- | --- | --- |
| DeepSeek-V2-Lite TP+EP | vLLM MoE expert parallelism on XPU | 2 or 4 |
| PowerMoE DP+EP | vLLM data parallel plus expert parallel process layout | 3 |
| DeepSeek-V2-Lite P/D + EP | vLLM P/D composition with the validated MoE model | 4 or 8 |
| Full llm-d LWS/router path | End-to-end WideEP request through EPP, decode sidecar, prefill, NIXL, XCCL, and EP | 4 or 8 |

DeepSeek-V2-Lite TP+EP has been validated on 2 and 4 XPUs. Full DeepSeek-V2-Lite `wide-ep-lws` has been validated on 4 XPUs (decode TP=2 + prefill TP=2) with XPU KV buffers and `UCX_TLS=tcp,ze_copy`. The host-buffer fallback has been validated on both 4 XPUs (decode TP=2 + prefill TP=2) and 8 XPUs (decode TP=4 + prefill TP=4). PowerMoE DP+EP initializes DP and EP ranks on XPU but still needs a clean readiness run with sufficient shared memory.

## XPU differences from the NVIDIA WideEP manifests

The NVIDIA WideEP manifests include several optimizations that are intentionally not copied here:

| NVIDIA setting | XPU status | Reason |
| --- | --- | --- |
| `--all2all-backend deepep_low_latency` / `deepep_high_throughput` | replaced | XPU uses `allgather_reducescatter`; DeepEP is NVIDIA/CUDA-specific. |
| `--moe-backend deep_gemm` | omitted | DeepGEMM is not available for XPU; vLLM uses the XPU/Triton MoE path. |
| `--enable-dbo` | omitted | DBO support has not been validated on XPU for this guide. |
| `--enable-eplb` | omitted | EPLB support has not been validated on XPU for this guide. |
| `--kv_transfer_config` | NIXL with XPU KV buffers | `kv_buffer_device=xpu` passes when `UCX_TLS` includes `ze_copy`; `kv_buffer_device=cpu` remains a validated fallback. |
| `--data-parallel-*` | omitted from baseline manifest | The Kubernetes manifest uses TP=4 in one model server; DP+EP rank initialization is documented in the experiment matrix but still needs a clean readiness run. |

Performance-only parameters such as `--max-num-batched-tokens`, `--max-num-seqs`, and `--api-server-count` are also left out until there is a repeatable XPU benchmark for this guide.

## Limitations and validation status

- The checked-in manifest below is still a single-pod EP smoke test, not the full LWS deployment.
- Full LWS/router/P-D validation passed on `xpu-817225` with XPU KV buffers when `UCX_TLS=tcp,ze_copy`.
- `UCX_TLS=tcp` alone reproduces `nixl_cu12._bindings.nixlBackendError: NIXL_ERR_BACKEND` because UCX does not register XPU VRAM through the Level Zero copy transport.
- The host-buffer NIXL path (`kv_buffer_device=cpu`) passed at both TP=2+2 and TP=4+4 and is useful as a fallback or debugging control.
- DP+EP without P/D still needs a clean readiness run; previous runs reached DP/EP initialization but stalled on shared-memory broadcast.

## Prerequisites

- Install the [Intel Resource Drivers for Kubernetes](https://github.com/intel/intel-resource-drivers-for-kubernetes) so the `gpu.intel.com` DRA device class is available.
- Create the `llm-d-hf-token` secret in the target namespace with the key `HF_TOKEN`.

## Deploy

```bash
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
export NAMESPACE=llm-d-wide-ep-xpu

kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/wide-ep-lws/experimental-xpu/manifests/modelserver/intel
```

## Verify

Wait for the modelserver pod to become ready, then port-forward it:

```bash
kubectl get pods -n ${NAMESPACE}
export MODELSERVER_POD=$(kubectl get pods -n ${NAMESPACE} \
  -l llm-d.ai/guide=wide-ep-lws-experimental-xpu \
  -o jsonpath='{.items[0].metadata.name}')
kubectl port-forward -n ${NAMESPACE} pod/${MODELSERVER_POD} 8000:8000
```

Send a request:

```bash
curl -X POST http://localhost:8000/v1/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "deepseek-ai/DeepSeek-V2-Lite-Chat",
    "prompt": "Hello",
    "max_tokens": 20
  }'
```

Check the vLLM logs for XPU EP initialization:

```bash
kubectl logs -n ${NAMESPACE} ${MODELSERVER_POD} -c vllm | grep -E "Expert parallelism|EP Rank|backend=xccl|all2all"
```

## Optional local container smoke test

For quick debugging outside Kubernetes, run the same model with local XPU devices. This path is not a replacement for the Kubernetes manifest because device selection is manual.

> [!WARNING]
> The local Docker smoke test uses privileged XPU device access and
> `--trust-remote-code`. Run it only on a dedicated trusted host and only with
> trusted model code.

```bash
export HF_TOKEN=your_huggingface_token
export ZE_AFFINITY_MASK=0,1,2,3
export TP_SIZE=4

docker run --rm --privileged \
  --entrypoint vllm \
  -e HF_TOKEN=${HF_TOKEN} \
  -e ZE_AFFINITY_MASK=${ZE_AFFINITY_MASK} \
  -e VLLM_WORKER_MULTIPROC_METHOD=spawn \
  -e UCX_TLS=tcp,ze_copy \
  -v /dev/dri:/dev/dri \
  -p 127.0.0.1:8000:8000 \
  ghcr.io/llm-d/llm-d-xpu:v0.7.0 \
  serve deepseek-ai/DeepSeek-V2-Lite-Chat \
    --host 0.0.0.0 \
    --port 8000 \
    --trust-remote-code \
    --max-model-len 512 \
    --gpu-memory-utilization 0.8 \
    --tensor-parallel-size ${TP_SIZE} \
    --enable-expert-parallel \
    --all2all-backend allgather_reducescatter \
    --disable-access-log-for-endpoints=/health,/metrics,/v1/models
```

For the broader validation matrix, see [`experiments/README.md`](./experiments/README.md).
