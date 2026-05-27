# No-Kubernetes Deployment

## Overview

This guide runs the llm-d routing stack — the EPP (Endpoint Picker), Envoy,
and one or more vLLM workers — **without a Kubernetes cluster**. The EPP
gets its endpoint inventory from a YAML file on disk via the
[file-discovery plugin][filediscovery-plugin], which makes the deployment
work on bare metal, Slurm, Ray, or a single workstation.

Background: ["No Kubernetes? No Problem: llm-d Now Runs Anywhere"][blog].

The configs in this directory are plain YAML — there is no Helm chart and
no Kustomize overlay. Drop them on a host, point your services at them,
and run.

## When to use this guide

This guide fits when:

- HPC schedulers (Slurm, LSF) launch workers dynamically.
- Ray-based stacks run workers as actors instead of pods.
- Bare-metal inference farms operate without a Kubernetes control plane.
- A single workstation with a couple of GPUs is enough for development.

For parity caveats compared to the Kubernetes-based
[optimized-baseline guide](../optimized-baseline/README.md), see
[What's not included](#whats-not-included) at the end.

The EPP plugin set, vLLM arguments, and Envoy listener parameters all match
the [optimized-baseline guide](../optimized-baseline/README.md). The only
substantive change is the data layer: instead of watching an
`InferencePool` over the Kubernetes API, the EPP reads
[`router/epp/endpoints.yaml`](./router/epp/endpoints.yaml) from disk and reconciles
changes via `fsnotify`.

> [!NOTE]
> This guide targets **vLLM on NVIDIA GPUs** — the model-server image,
> CLI flags, and `--gpus` invocation are NVIDIA + vLLM specific. The EPP
> and Envoy configs are accelerator-agnostic; for AMD, Intel XPU, Gaudi,
> TPU, or CPU substitute the model-server step with the corresponding
> backend from the
> [optimized-baseline modelserver overlays](../optimized-baseline/modelserver/).

## What's in this directory

| Path                                       | Purpose                                                       |
| ------------------------------------------ | ------------------------------------------------------------- |
| [`router/epp/config.yaml`](./router/epp/config.yaml)     | EPP plugin config (optimized-baseline plugin set + file-discovery) |
| [`router/epp/endpoints.yaml`](./router/epp/endpoints.yaml) | The endpoints file the EPP watches                          |
| [`router/envoy/envoy.yaml`](./router/envoy/envoy.yaml)   | Envoy config: ext_proc to EPP, ORIGINAL_DST to vLLM           |

## How traffic flows

```
client -> Envoy listener :8081
       -> ext_proc gRPC :9002 (EPP picks endpoint, sets header)
       -> ORIGINAL_DST cluster
       -> reads x-gateway-destination-endpoint from EPP response
       -> <address>:<port> of the vLLM worker chosen by the EPP
```

The EPP's datastore is populated entirely from
[`router/epp/endpoints.yaml`](./router/epp/endpoints.yaml). With `watchFile: true` the
file is hot-reloaded on every atomic rewrite — adding, removing, or
relabelling a worker takes effect without restarting the EPP.

## Default configuration

Same as the [optimized-baseline guide](../optimized-baseline/README.md):

| Parameter          | Value                                                   |
| ------------------ | ------------------------------------------------------- |
| Model              | [Qwen/Qwen3-32B](https://huggingface.co/Qwen/Qwen3-32B) |
| Tensor parallelism | 2                                                       |
| GPUs per replica   | 2                                                       |

Replica count is whatever you list in `router/epp/endpoints.yaml`. Resource
shapes (cpu/memory/GPU) come from your launcher; the optimized-baseline
references are 16 cpu / 128 GiB / 2 GPUs per replica.

The EPP scheduling profile is the optimized-baseline mix:
`prefix-cache-scorer` (weight 3), `queue-scorer`, `kv-cache-utilization-scorer`,
and `no-hit-lru-scorer` (each weight 2), composed by `max-score-picker`.

## Prerequisites

- A host with the GPUs your model requires (the default config calls for
  two GPUs per vLLM replica).
- Docker (or Podman) for vLLM and Envoy, plus the EPP binary or container
  image.
- A Hugging Face token in `HUGGING_FACE_HUB_TOKEN` so vLLM can pull
  `Qwen/Qwen3-32B` on first start.

The EPP binary is built from [`cmd/epp` of the llm-d-router repo][router-repo]
or pulled from `ghcr.io/llm-d/llm-d-router-endpoint-picker-dev`.

```bash
export EPP_IMAGE=ghcr.io/llm-d/llm-d-router-endpoint-picker-dev:main
export ENVOY_IMAGE=docker.io/envoyproxy/envoy:distroless-v1.33.2
export VLLM_IMAGE=vllm/vllm-openai:v0.19.1
export MODEL=Qwen/Qwen3-32B
```

## Installation

### 1. Stage the configs

Copy the configs to standard paths so the commands below match the
defaults baked into the YAML files:

```bash
sudo mkdir -p /etc/epp /etc/envoy
sudo cp router/epp/config.yaml      /etc/epp/config.yaml
sudo cp router/epp/endpoints.yaml   /etc/epp/endpoints.yaml
sudo cp router/envoy/envoy.yaml     /etc/envoy/envoy.yaml
```

If you stage them elsewhere, update the `path:` field in
[`router/epp/config.yaml`](./router/epp/config.yaml) (and the `--mount` paths in the
commands below) to match.

### 2. Start the vLLM workers

Run one container per replica. Adjust `--gpus`, the
`--tensor-parallel-size` value, and the host port for each replica.

The image, entrypoint, and argument list mirror the optimized-baseline
modelserver patch
([`guides/optimized-baseline/modelserver/gpu/vllm/base/patch-vllm.yaml`](../optimized-baseline/modelserver/gpu/vllm/base/patch-vllm.yaml));
`--shm-size=20g` reproduces the 20 GiB `/dev/shm` `emptyDir` from the
recipe (required for NCCL when `--tensor-parallel-size > 1`).

```bash
docker run -d --name vllm-0 --gpus '"device=0,1"' \
    --shm-size=20g \
    -p 8000:8000 \
    -e "HUGGING_FACE_HUB_TOKEN=${HUGGING_FACE_HUB_TOKEN}" \
    -v "${HOME}/.cache/huggingface:/root/.cache/huggingface" \
    --entrypoint vllm \
    "${VLLM_IMAGE}" \
    serve "${MODEL}" \
    --disable-access-log-for-endpoints=/health,/metrics,/v1/models \
    --tensor-parallel-size=2 \
    --gpu-memory-utilization=0.95
```

The optimized-baseline pod also sets resource requests/limits
(8–16 cpu, 96–128 GiB memory, 2 GPUs) and Kubernetes liveness/readiness
probes. Translate those to your runtime as needed (`--cpus`, `--memory`,
`--health-cmd` for Docker; equivalent fields for systemd or nomad).

Wait for `/v1/models` to respond before continuing:

```bash
until curl -sf http://127.0.0.1:8000/v1/models >/dev/null; do sleep 2; done
```

For multiple replicas, repeat with different container names, GPU sets,
and host ports — and add an entry per replica in `endpoints.yaml`.

### 3. Edit the endpoints file

`endpoints.yaml` ships with a single `127.0.0.1:8000` entry. For a
multi-host or multi-replica deployment, edit it now to list each worker:

```yaml
endpoints:
  - name: vllm-0
    address: 10.0.0.10
    port: "8000"
    labels:
      model: Qwen/Qwen3-32B
  - name: vllm-1
    address: 10.0.0.11
    port: "8000"
    labels:
      model: Qwen/Qwen3-32B
```

`address` must be a literal IPv4 address — the plugin does not resolve
hostnames. The full schema is documented in [the file-discovery
blog post][blog-endpoints-file].

### 4. Start the EPP

```bash
docker run -d --name epp --network host \
    -v /etc/epp:/etc/epp:ro \
    "${EPP_IMAGE}" \
    --config-file=/etc/epp/config.yaml \
    --pool-name=file-discovery \
    --pool-namespace=default \
    --grpc-port=9002 \
    --grpc-health-port=9003 \
    --metrics-port=9090 \
    --secure-serving=false \
    --v=2
```

`--pool-name` and `--pool-namespace` are not Kubernetes references in
file-discovery mode; they are only used as labels in the EPP's metrics
and log lines.

`--network host` lets Envoy reach the EPP on `127.0.0.1:9002` and the EPP
scrape vLLM's `/metrics` on the host loopback. Drop it and use a Docker
network if the workers and the EPP are on separate hosts.

### 5. Start Envoy

```bash
docker run -d --name envoy --network host \
    -v /etc/envoy/envoy.yaml:/etc/envoy/envoy.yaml:ro \
    "${ENVOY_IMAGE}" \
    --service-node envoy-proxy \
    --log-level warn \
    --concurrency 8 \
    --drain-strategy immediate \
    --drain-time-s 60 \
    -c /etc/envoy/envoy.yaml
```

The `--concurrency`, `--log-level`, and `--drain-*` flags match the
optimized-baseline guide's Envoy sidecar arguments.

## Verification

```bash
# Envoy admin
curl -s http://127.0.0.1:19000/ready
curl -s http://127.0.0.1:19000/clusters | grep -E '^(ext_proc|original_destination_cluster)'

# EPP
curl -s http://127.0.0.1:9090/metrics | head

# End-to-end completion through Envoy -> EPP -> vLLM
curl -s http://127.0.0.1:8081/v1/completions \
    -H 'Content-Type: application/json' \
    -d '{
        "model": "Qwen/Qwen3-32B",
        "prompt": "How are you today?"
    }'
```

## Live reload

`watchFile: true` in [`router/epp/config.yaml`](./router/epp/config.yaml) means edits to
the endpoints file take effect without restarting the EPP. The watcher
reacts cleanly to atomic-rename writes, so update via a temp file:

```bash
# Add or remove workers, then:
sudo mv /etc/epp/endpoints.yaml.tmp /etc/epp/endpoints.yaml
```

The EPP logs `endpoints file changed, reloading` and reconciles the pool.
Plain in-place edits also work for most editors but are less robust — see
the troubleshooting section of the [blog post][blog-troubleshooting].

## Cleanup

```bash
docker rm -f envoy epp vllm-0     # add other vllm-N containers if any
sudo rm -rf /etc/epp /etc/envoy   # if you no longer need the configs
```

## What's not included

Everything in the optimized-baseline guide that depends on Kubernetes
(`InferencePool`, `InferenceObjective`-driven FlowControl, the
`InferenceModelRewrite` model-name rewriter, `PodMonitor` for Prometheus)
is intentionally out of scope here. The blog post lists the
[parity caveats][blog-parity] in detail; in short: scoring,
prefix-cache affinity, saturation-based admission, and Prometheus metrics
on `--metrics-port` all work; FlowControl and model rewriting do not.

For monitoring, point your existing Prometheus at the EPP's
`--metrics-port` (`9090` in the command above) and at each vLLM worker's
`/metrics` endpoint.

For prefill/decode disaggregation outside Kubernetes, follow the
[pd-disaggregation guide](../pd-disaggregation/README.md) for the EPP plugin
config and vLLM/sidecar setup, and use the file-discovery plugin in place of
`InferencePool` discovery (set `llm-d.ai/role: prefill` or `decode` on each
endpoint's `labels`).

[filediscovery-plugin]: https://github.com/llm-d/llm-d-router/blob/main/pkg/epp/framework/plugins/datalayer/discovery/file/plugin.go
[blog]: https://llm-d.ai/blog/running-llm-d-without-kubernetes
[blog-endpoints-file]: https://llm-d.ai/blog/running-llm-d-without-kubernetes#1-the-endpoints-file
[blog-parity]: https://llm-d.ai/blog/running-llm-d-without-kubernetes#parity-with-the-kubernetes-native-llm-d-deployment
[blog-troubleshooting]: https://llm-d.ai/blog/running-llm-d-without-kubernetes#troubleshooting
[router-repo]: https://github.com/llm-d/llm-d-router
