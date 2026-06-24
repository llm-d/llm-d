# XPU WideEP experiment matrix

These experiments determine which WideEP layers work on Intel XPU and document the full-path validation status.

## Current evidence

| Layer | Status | Evidence |
| --- | --- | --- |
| vLLM EP | Validated | On `super-node0`, DeepSeek-V2-Lite completed requests with TP=2 and TP=4 plus `--enable-expert-parallel`; logs showed XCCL, EP ranks, XPU MoE, and KV cache allocation. |
| vLLM DP+EP | Partially validated | On `super-node0`, PowerMoE DP=3 plus EP initialized DP ranks 0/1/2 and EP ranks 0/1/2 with XCCL and XPU MoE, but did not become healthy before repeated shared-memory broadcast wait messages. Rerun with larger `/dev/shm` or Kubernetes `emptyDir` shared memory. |
| DeepSeek-V2-Lite P/D+EP with XPU KV buffer | Validated | On `xpu-817225`, full LWS/router e2e passed with decode TP=2 + prefill TP=2, `kv_buffer_device=xpu`, and `UCX_TLS=tcp,ze_copy`. Logs showed `use_mla: True`, `use_host_buffer: False`, XCCL, EP ranks, NIXL Scheduler, and a successful completion through EPP. |
| DeepSeek-V2-Lite P/D+EP with NIXL host buffer | Validated fallback | On `xpu-817225`, full LWS/router e2e passed with decode TP=2 + prefill TP=2 and decode TP=4 + prefill TP=4. Logs showed XCCL, EP ranks, NIXL Scheduler, NIXL compatibility checks, and transfer plans. |
| XPU KV buffer with `UCX_TLS=tcp` only | Expected failure | A minimal Qwen TP=1 NIXL control failed with `VRAM memory is detected as host by UCX` followed by `NIXL_ERR_BACKEND`; adding `ze_copy` made the same XPU KV-buffer path start successfully. |

## Resource checks

Before running an experiment on a shared host:

```bash
xpu-smi discovery
xpu-smi ps
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
kubectl get pods -A -o wide 2>/dev/null || true
```

Do not stop unrelated containers or pods just to run these experiments.

> [!WARNING]
> The Docker experiments use privileged XPU device access and vLLM
> `--trust-remote-code`; the P/D NIXL script also uses host networking for local
> prefill/decode communication. Run them only on dedicated trusted hosts and only
> with trusted model code.

## Experiment 1: DeepSeek-V2-Lite TP+EP

Use Docker Compose for the previously validated DeepSeek-V2-Lite EP cases:

```bash
cd guides/wide-ep-lws/experimental-xpu/experiments
export HF_TOKEN=your_huggingface_token

# Requires two free XPUs.
docker compose --profile v2lite-ep-tp2 up

# Requires four free XPUs.
docker compose --profile v2lite-ep-tp4 up
```

Successful logs should include:

```text
Expert parallelism is enabled
Using XPU Unquantized MoE backend
backend=xccl
allgather_reducescatter
```

## Experiment 2: MoE DP+EP

This checks whether XPU can run vLLM data parallelism together with expert parallelism:

```bash
cd guides/wide-ep-lws/experimental-xpu/experiments
export HF_TOKEN=your_huggingface_token

# Requires three free XPUs.
docker compose --profile powermoe-ep-dp3 up
```

This is not full llm-d WideEP, but it helps determine whether DP process layout is viable on XPU before adding router/rank targeting.

On the latest run, this experiment reached DP/EP initialization but not readiness:

```text
Defaulting api_server_count to data_parallel_size (3)
world_size=3 ... backend=xccl
rank 0 ... DP rank 0 ... EP rank 0
rank 1 ... DP rank 1 ... EP rank 1
rank 2 ... DP rank 2 ... EP rank 2
Expert parallelism is enabled
Using XPU backend for Unquantized MoE
No available shared memory broadcast block found in 60 seconds
```

## Experiment 3: DeepSeek-V2-Lite P/D+EP

This checks the WideEP candidate model in a vLLM prefill/decode shape on XPU without the llm-d router. It defaults to the XPU KV-buffer NIXL path that passed full LWS validation:

```bash
cd guides/wide-ep-lws/experimental-xpu/experiments
export VLLM_SRC=/path/to/vllm/source
export HF_TOKEN=your_huggingface_token
export MODEL_NAME=deepseek-ai/DeepSeek-V2-Lite-Chat
export MAX_MODEL_LEN=512
export KV_BUFFER_DEVICE=xpu
export UCX_TLS=tcp,ze_copy

# Defaults require four free XPUs: two for prefill and two for decode.
bash run-pd-nixl-docker.sh
```

The script starts only containers whose names begin with `xpu-pd-nixl-` and removes only those containers on cleanup. `MODEL_NAME` defaults to `deepseek-ai/DeepSeek-V2-Lite-Chat`. If the goal is only to debug NIXL independent of MoE, override it with a known-good dense model such as `facebook/opt-125m`; that does not count as WideEP validation.

To reproduce the UCX transport failure, keep `KV_BUFFER_DEVICE=xpu` and set `UCX_TLS=tcp`. That configuration fails during NIXL KV cache memory registration because UCX cannot register XPU VRAM without the `ze_copy` transport.

## Full LWS/router validation

The full llm-d path was validated on `xpu-817225` with temporary manifests:

| Shape | XPUs | Result | Key evidence |
| --- | --- | --- | --- |
| Decode TP=2 + prefill TP=2, `kv_buffer_device=xpu`, `UCX_TLS=tcp,ze_copy` | 4 | Passed | Decode pod `2/2`, prefill pod `1/1`, completion via EPP, `Registering KV_Caches. use_mla: True, kv_buffer_device: xpu, use_host_buffer: False`, and `Application startup complete`. |
| Decode TP=2 + prefill TP=2, `kv_buffer_device=cpu` | 4 | Passed | Decode pod `2/2`, prefill pod `1/1`, completion via EPP, `TransferTopology(... local_tp=2, remote_tp=2 ...)`. |
| Decode TP=4 + prefill TP=4, `kv_buffer_device=cpu` | 8 | Passed | 4-XPU DRA claims for each LWS pod, completion via EPP, `world_size=4 backend=xccl`, `[EP Rank 0/4]`, `TransferTopology(... local_tp=4, remote_tp=4 ...)`. |
| Decode TP=2 + prefill TP=2, `kv_buffer_device=xpu`, `UCX_TLS=tcp` | 4 | Failed as expected | `Registering KV_Caches. use_mla: True, kv_buffer_device: xpu, use_host_buffer: False` followed by `NIXL_ERR_BACKEND`; the minimal Qwen control produced the explicit UCX diagnosis that VRAM was detected as host. |

The table above is the PR-safe summary of the raw validation logs captured during the XPU runs.
