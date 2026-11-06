# Docker deployment on Ascend NPUs

Run two vLLM-Ascend workers behind the llm-d endpoint picker (EPP) and Envoy on
one Linux ARM64 host. This extends the [no-Kubernetes guide](../README.md) with
an Ascend NPU example using Docker and file discovery.

The recipe was validated with two Ascend 910B3 NPUs serving `Qwen/Qwen3-0.6B`,
one NPU per worker. Checks covered non-streaming and SSE completions, routing to
both workers, and endpoint-file reload without restarting EPP. Other hardware
and models, multi-node deployment, and prefill/decode disaggregation are outside
this recipe's scope.

## Prerequisites

- A Linux ARM64 host with Docker, Bash, curl, and two allocated Ascend NPUs.
- Compatible Ascend drivers; see the
  [vLLM-Ascend prerequisites](https://github.com/vllm-project/vllm-ascend/blob/v0.23.0/docs/source/quick_start.md).
- A complete local `Qwen/Qwen3-0.6B` snapshot, including tokenizer files and
  weights. Symlink targets must be accessible within the mounted model directory.
- Available ports 18080–18086. Each worker is limited to 16 CPUs and 32 GiB host RAM.

| Component | Tested configuration |
| --- | --- |
| Host | Linux ARM64, Docker 18.09.0 with `runc` |
| NPUs | Two Ascend 910B3 devices, 64 GiB HBM each |
| Driver / CANN | Driver 26.0.rc1, CANN 9.1.0 |
| Model | Qwen3-0.6B, BF16, TP=1, context length 2048 |

The commands below use vLLM-Ascend v0.23.0, EPP v1.0.0, and Envoy v1.33.2.
EPP uses file discovery and token-load scoring; these settings provide a
functional starting point without hardware-specific throughput calibration.

Containers use host networking. Envoy and vLLM bind to loopback. EPP's gRPC and
metrics ports listen on all host interfaces without TLS or authentication, so
restrict access to those ports on the host.

## Installation

Run the following commands in the same Bash session on the Ascend host.

### 1. Configure the environment

From the repository root, set the model path and your allocated NPU IDs:

```bash
ASCEND_CONFIG_DIR="$(git rev-parse --show-toplevel)-tmp/ascend"
cd guides/no-kubernetes-deployment/ascend || exit 1

MODEL_DIR=/absolute/path/to/Qwen3-0.6B
NPU_DEVICES=(0 1)
ASCEND_NAME="llmd-ascend-$(id -u)"
VLLM_IMAGE=quay.io/ascend/vllm-ascend:v0.23.0
EPP_IMAGE=ghcr.io/llm-d/llm-d-router-endpoint-picker:v1.0.0
ENVOY_IMAGE=envoyproxy/envoy:distroless-v1.33.2

mkdir -p "${ASCEND_CONFIG_DIR}/epp" "${ASCEND_CONFIG_DIR}/envoy"
cp epp.yaml "${ASCEND_CONFIG_DIR}/epp/config.yaml"
cp endpoints.yaml "${ASCEND_CONFIG_DIR}/epp/endpoints.yaml"
cp envoy.yaml "${ASCEND_CONFIG_DIR}/envoy/envoy.yaml"
```

Configuration copies are stored beside the checkout in `llm-d-tmp/ascend`.
The supplied [endpoint inventory](./endpoints.yaml) lists workers on ports
18081 and 18082; [Envoy](./envoy.yaml) listens for inference requests on 18080.

### 2. Start the vLLM-Ascend workers

Each container mounts one NPU, the host driver, and the model directory.
Model downloads are disabled because the complete model is supplied locally.

```bash
for i in 0 1; do
  docker run -d --name "${ASCEND_NAME}-vllm-${i}" --network host \
    --shm-size 4g --cpus 16 --memory 32g \
    --device "/dev/davinci${NPU_DEVICES[$i]}" \
    --device /dev/davinci_manager --device /dev/devmm_svm --device /dev/hisi_hdc \
    --mount type=bind,src=/usr/local/dcmi,dst=/usr/local/dcmi,readonly \
    --mount type=bind,src=/usr/local/Ascend/driver,dst=/usr/local/Ascend/driver,readonly \
    --mount type=bind,src=/usr/local/bin/npu-smi,dst=/usr/local/bin/npu-smi,readonly \
    --mount type=bind,src=/etc/ascend_install.info,dst=/etc/ascend_install.info,readonly \
    --mount "type=bind,src=${MODEL_DIR},dst=/model,readonly" \
    -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 -e OMP_NUM_THREADS=4 \
    "${VLLM_IMAGE}" vllm serve /model --served-model-name Qwen/Qwen3-0.6B \
    --host 127.0.0.1 --port "$((18081 + i))" --tensor-parallel-size 1 \
    --dtype bfloat16 --max-model-len 2048 --max-num-seqs 8 \
    --gpu-memory-utilization 0.25 --enforce-eager --enable-prefix-caching
done
```

Wait for both workers to become healthy before starting EPP:

```bash
for port in 18081 18082; do
  curl --noproxy '*' -fsS --max-time 10 \
    --retry 60 --retry-delay 5 --retry-connrefused \
    "http://127.0.0.1:${port}/health" || break
done
```

If a worker fails to start, inspect `docker logs "${ASCEND_NAME}-vllm-0"`
or `docker logs "${ASCEND_NAME}-vllm-1"` before continuing.

### 3. Start EPP and Envoy

```bash
docker run -d --name "${ASCEND_NAME}-epp" --network host --cpus 2 --memory 2g \
  --mount "type=bind,src=${ASCEND_CONFIG_DIR}/epp,dst=/etc/epp,readonly" \
  "${EPP_IMAGE}" --config-file=/etc/epp/config.yaml \
  --pool-name=ascend-local --pool-namespace=default \
  --grpc-port=18083 --grpc-health-port=18084 --metrics-port=18085 \
  --secure-serving=false --health-checking=true \
  --enable-pprof=false --tracing=false --drain-timeout=3s --v=2

docker run --rm \
  --mount "type=bind,src=${ASCEND_CONFIG_DIR}/envoy,dst=/etc/envoy,readonly" \
  "${ENVOY_IMAGE}" --mode validate -c /etc/envoy/envoy.yaml

docker run -d --name "${ASCEND_NAME}-envoy" --network host --cpus 2 --memory 512m \
  --mount "type=bind,src=${ASCEND_CONFIG_DIR}/envoy,dst=/etc/envoy,readonly" \
  "${ENVOY_IMAGE}" -c /etc/envoy/envoy.yaml \
  --concurrency 2 --file-flush-interval-msec 100
```

## Verification

Check Envoy readiness, its EPP health check, and the ready worker count.
The expected results are `LIVE`, `membership_healthy: 1`, and
`llm_d_epp_ready_endpoints{name="ascend-local"} 2`, respectively:

```bash
curl --noproxy '*' -fsS http://127.0.0.1:18086/ready
curl --noproxy '*' -fsS \
  'http://127.0.0.1:18086/stats?filter=cluster.ext_proc.membership_healthy'
curl --noproxy '*' -fsS http://127.0.0.1:18085/metrics \
  | grep '^llm_d_epp_ready_endpoints'
```

Send a completion request through Envoy:

```bash
curl --noproxy '*' -fsS http://127.0.0.1:18080/v1/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"Qwen/Qwen3-0.6B","prompt":"The capital of France is","max_tokens":16,"temperature":0}'
```

For streaming output, add `"stream":true` and use `-N`:

```bash
curl --noproxy '*' -fsSN http://127.0.0.1:18080/v1/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"Qwen/Qwen3-0.6B","prompt":"The capital of France is","max_tokens":16,"temperature":0,"stream":true}'
```

Expect generated text in `choices`; the SSE response ends with `data: [DONE]`.
To check routing, inspect Envoy's access logs:

```bash
docker logs --tail 10 "${ASCEND_NAME}-envoy"
```

For successful requests, `status` is 200 and `selected` matches `upstream`,
with either `127.0.0.1:18081` or `127.0.0.1:18082`. Requests do not need to
alternate between workers.

EPP watches `${ASCEND_CONFIG_DIR}/epp/endpoints.yaml` for changes. To update
the worker pool, prepare `endpoints.yaml.tmp` in the same directory and rename
it to `endpoints.yaml`; EPP reloads the inventory without restarting.

## Cleanup

```bash
docker stop --time 30 "${ASCEND_NAME}-envoy" "${ASCEND_NAME}-epp" \
  "${ASCEND_NAME}-vllm-0" "${ASCEND_NAME}-vllm-1"
docker rm "${ASCEND_NAME}-envoy" "${ASCEND_NAME}-epp" \
  "${ASCEND_NAME}-vllm-0" "${ASCEND_NAME}-vllm-1"
```

The configuration copies and model files remain on the host.
