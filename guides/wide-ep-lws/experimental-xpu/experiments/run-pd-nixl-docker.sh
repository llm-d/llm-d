#!/usr/bin/env bash
set -euo pipefail

VLLM_XPU_IMAGE=${VLLM_XPU_IMAGE:-ghcr.io/llm-d/llm-d-xpu:v0.7.0}
VLLM_SRC=${VLLM_SRC:?set VLLM_SRC to a vLLM source checkout containing tests/v1/kv_connector/nixl_integration}

MODEL_NAME=${MODEL_NAME:-deepseek-ai/DeepSeek-V2-Lite-Chat}
# DeepSeek-V2-Lite is the WideEP candidate model. For NIXL-only debugging,
# override MODEL_NAME with a known-good dense model such as facebook/opt-125m.
MAX_MODEL_LEN=${MAX_MODEL_LEN:-512}
BLOCK_SIZE=${BLOCK_SIZE:-64}
GPU_MEMORY_UTILIZATION=${GPU_MEMORY_UTILIZATION:-0.8}
# XPU device-buffer NIXL requires UCX Level Zero transport. Use tcp,ze_copy for
# local or non-RDMA P/D debugging; RDMA deployments should include ze_copy in
# their RDMA transport set as well.
KV_BUFFER_DEVICE=${KV_BUFFER_DEVICE:-xpu}
UCX_TLS=${UCX_TLS:-tcp,ze_copy}
UCX_MEMTYPE_CACHE=${UCX_MEMTYPE_CACHE:-0}
SHM_SIZE=${SHM_SIZE:-16g}

PREFILL_MASK=${PREFILL_MASK:-0,1}
DECODE_MASK=${DECODE_MASK:-2,3}
PREFILL_TP_SIZE=${PREFILL_TP_SIZE:-2}
DECODE_TP_SIZE=${DECODE_TP_SIZE:-2}
PREFILL_PORT=${PREFILL_PORT:-8100}
PREFILL_NIXL_SIDE_PORT=${PREFILL_NIXL_SIDE_PORT:-5577}
DECODE_PORT=${DECODE_PORT:-8200}
DECODE_NIXL_SIDE_PORT=${DECODE_NIXL_SIDE_PORT:-5578}
PROXY_PORT=${PROXY_PORT:-8192}

PREFILL_CONTAINER=${PREFILL_CONTAINER:-xpu-pd-nixl-prefill}
DECODE_CONTAINER=${DECODE_CONTAINER:-xpu-pd-nixl-decode}
PROXY_CONTAINER=${PROXY_CONTAINER:-xpu-pd-nixl-proxy}

for container_name in "${PREFILL_CONTAINER}" "${DECODE_CONTAINER}" "${PROXY_CONTAINER}"; do
  if [[ "${container_name}" != xpu-pd-nixl-* ]]; then
    echo "Container name ${container_name} must start with xpu-pd-nixl- for safe cleanup" >&2
    exit 1
  fi
done

if [[ ! -f "${VLLM_SRC}/tests/v1/kv_connector/nixl_integration/toy_proxy_server.py" ]]; then
  echo "toy_proxy_server.py not found under VLLM_SRC=${VLLM_SRC}" >&2
  exit 1
fi

docker_env_args=()
if [[ -n "${HF_TOKEN:-}" ]]; then
  docker_env_args+=(-e "HF_TOKEN=${HF_TOKEN}")
fi

cleanup() {
  docker rm -f "${PROXY_CONTAINER}" "${DECODE_CONTAINER}" "${PREFILL_CONTAINER}" >/dev/null 2>&1 || true
}

wait_for_server() {
  local port=$1
  local name=$2
  local deadline=$((SECONDS + 1200))
  until curl -fsS "http://127.0.0.1:${port}/v1/models" >/dev/null 2>&1; do
    local status
    status=$(docker inspect -f '{{.State.Status}}' "${name}" 2>/dev/null || true)
    if [[ "${status}" == "exited" || "${status}" == "dead" ]]; then
      echo "${name} exited while waiting for port ${port}" >&2
      docker logs --tail 200 "${name}" >&2 || true
      return 1
    fi
    if (( SECONDS > deadline )); then
      echo "Timed out waiting for ${name} on port ${port}" >&2
      docker logs --tail 120 "${name}" >&2 || true
      return 1
    fi
    sleep 2
  done
}

trap cleanup EXIT
cleanup

echo "Warning: this experiment uses privileged XPU containers, host networking, and vLLM --trust-remote-code. Run only on a dedicated trusted host with trusted model code." >&2

docker run -d --name "${PREFILL_CONTAINER}" --network host --privileged \
  --shm-size "${SHM_SIZE}" \
  --entrypoint vllm \
  "${docker_env_args[@]}" \
  -e "ZE_AFFINITY_MASK=${PREFILL_MASK}" \
  -e VLLM_WORKER_MULTIPROC_METHOD=spawn \
  -e VLLM_ENABLE_V1_MULTIPROCESSING=1 \
  -e VLLM_MULTIPROC_EXECUTE_MODEL_TIMEOUT_S=1200 \
  -e "UCX_TLS=${UCX_TLS}" \
  -e "UCX_MEMTYPE_CACHE=${UCX_MEMTYPE_CACHE}" \
  -e VLLM_NIXL_SIDE_CHANNEL_HOST=127.0.0.1 \
  -e "VLLM_NIXL_SIDE_CHANNEL_PORT=${PREFILL_NIXL_SIDE_PORT}" \
  -v /dev/dri:/dev/dri \
  "${VLLM_XPU_IMAGE}" \
  serve "${MODEL_NAME}" \
    --host 127.0.0.1 \
    --port "${PREFILL_PORT}" \
    --trust-remote-code \
    --max-model-len "${MAX_MODEL_LEN}" \
    --block-size "${BLOCK_SIZE}" \
    --enforce-eager \
    --dtype float16 \
    --tensor-parallel-size "${PREFILL_TP_SIZE}" \
    --enable-expert-parallel \
    --all2all-backend allgather_reducescatter \
    --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION}" \
    --kv-transfer-config "{\"kv_connector\":\"NixlConnector\",\"kv_role\":\"kv_both\",\"kv_buffer_device\":\"${KV_BUFFER_DEVICE}\"}"

docker run -d --name "${DECODE_CONTAINER}" --network host --privileged \
  --shm-size "${SHM_SIZE}" \
  --entrypoint vllm \
  "${docker_env_args[@]}" \
  -e "ZE_AFFINITY_MASK=${DECODE_MASK}" \
  -e VLLM_WORKER_MULTIPROC_METHOD=spawn \
  -e VLLM_ENABLE_V1_MULTIPROCESSING=1 \
  -e VLLM_MULTIPROC_EXECUTE_MODEL_TIMEOUT_S=1200 \
  -e "UCX_TLS=${UCX_TLS}" \
  -e "UCX_MEMTYPE_CACHE=${UCX_MEMTYPE_CACHE}" \
  -e VLLM_NIXL_SIDE_CHANNEL_HOST=127.0.0.1 \
  -e "VLLM_NIXL_SIDE_CHANNEL_PORT=${DECODE_NIXL_SIDE_PORT}" \
  -v /dev/dri:/dev/dri \
  "${VLLM_XPU_IMAGE}" \
  serve "${MODEL_NAME}" \
    --host 127.0.0.1 \
    --port "${DECODE_PORT}" \
    --trust-remote-code \
    --max-model-len "${MAX_MODEL_LEN}" \
    --block-size "${BLOCK_SIZE}" \
    --enforce-eager \
    --dtype float16 \
    --tensor-parallel-size "${DECODE_TP_SIZE}" \
    --enable-expert-parallel \
    --all2all-backend allgather_reducescatter \
    --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION}" \
    --kv-transfer-config "{\"kv_connector\":\"NixlConnector\",\"kv_role\":\"kv_both\",\"kv_buffer_device\":\"${KV_BUFFER_DEVICE}\"}"

wait_for_server "${PREFILL_PORT}" "${PREFILL_CONTAINER}"
wait_for_server "${DECODE_PORT}" "${DECODE_CONTAINER}"

docker run -d --name "${PROXY_CONTAINER}" --network host \
  --entrypoint python3 \
  -v "${VLLM_SRC}:/workspace/vllm-src:ro" \
  "${VLLM_XPU_IMAGE}" \
  /workspace/vllm-src/tests/v1/kv_connector/nixl_integration/toy_proxy_server.py \
    --prefiller-host 127.0.0.1 \
    --prefiller-port "${PREFILL_PORT}" \
    --decoder-host 127.0.0.1 \
    --decoder-port "${DECODE_PORT}" \
    --host 127.0.0.1 \
    --port "${PROXY_PORT}"

sleep 2

curl -fsS "http://127.0.0.1:${PROXY_PORT}/v1/completions" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"${MODEL_NAME}\",\"prompt\":\"Hello\",\"max_tokens\":16}"

echo
echo "XPU P/D NIXL request completed through proxy port ${PROXY_PORT}."
