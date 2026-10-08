# GLM-5.2-FP8 on H200 with SGLang (Rust frontend, `/generate`)

## Overview

This recipe deploys [GLM-5.2-FP8](https://huggingface.co/zai-org/GLM-5.2-FP8) (753B MoE) on H200 GPUs with SGLang, P/D disaggregated, in a wide expert parallel pattern with DP-aware scheduling. Prefill runs DEP8 (TP=1, DP=EP=8) on 1 node by default; decode runs DEP16 (TP=1, DP=EP=16) across 2 nodes. DeepEP `normal` mode for prefill and `low_latency` mode for decode.

It is the SGLang counterpart of the [vLLM GLM-5.2 recipe](../vllm-glm-5.2/README.md) and reuses the [wide-ep guide](../../../README.md) for the router and shared prerequisites. It does not aim for feature parity with the vLLM recipe. Tracking issues: [llm-d/llm-d#2198](https://github.com/llm-d/llm-d/issues/2198) and [llm-d/llm-d#2554](https://github.com/llm-d/llm-d/issues/2554).

> [!WARNING]
> This is an initial recipe. It was brought up once end to end on GKE A3 Ultra (3 nodes, `gke-p1w1d1w2`, Spot H200) with the NVSHMEM-fixed image described in [Limitations](#limitations); no benchmark numbers are published yet. The SGLang pieces it depends on are only in SGLang `main` (nightly image), not in a release.

### How llm-d talks to SGLang here

llm-d treats every attention-DP rank as its own endpoint (pod IP x port) so it can route per rank. Two SGLang choices make that work without a Python frontend in the middle:

- **Rust frontend** (`SGLANG_RUST_SERVER=1`). Each DP rank embeds a Rust HTTP listener on `--port` + local rank, so a pod with 8 ranks listens on 8000 to 8007. Multi-node groups reuse the same port range on every pod ([sgl-project/sglang#34430](https://github.com/sgl-project/sglang/pull/34430)).
- **Native `/generate` API**. The Rust listener serves SGLang's `/generate` with pre-tokenized `input_ids`. The EPP parses these bodies with the `sglanghttp-parser` for prefix-cache scoring, and the routing sidecar disaggregates them ([llm-d/llm-d-router#2525](https://github.com/llm-d/llm-d-router/pull/2525)).

Request flow for one `/generate` call:

1. The EPP picks a prefill rank (`prefillIP:800r`) and a decode rank (`decodeIP:800r`) and sends the request to the decode pod's routing sidecar.
2. The sidecar (`--kv-connector=sglang`) adds `bootstrap_host` (the prefill pod IP), `bootstrap_port` (8000) and a random `bootstrap_room`, then sends the same body to the prefill rank and to its local decode rank (`localhost:820r`) at the same time.
3. The prefill rank registers the room it served with the bootstrap registry. In Rust mode the registry is served by the rank-0 listener (port 8000) of every prefill pod ([sgl-project/sglang#36234](https://github.com/sgl-project/sglang/pull/36234)); the prefill is started with `--load-balance-method round_robin` so any rank the EPP picks is accepted.
4. The decode rank looks the room up on `bootstrap_host:8000`, pulls the KV cache over NIXL, and streams tokens back through the sidecar.

## Default Configuration

| Parameter | Value |
| --- | --- |
| Model | [zai-org/GLM-5.2-FP8](https://huggingface.co/zai-org/GLM-5.2-FP8) |
| Accelerator | NVIDIA H200 (8 GPUs per node) |
| Frontend | SGLang Rust server, one listener per DP rank (`SGLANG_RUST_SERVER=1`) |
| API | `/generate` with `input_ids` |
| Prefill parallelism | TP=1, DP=8, EP=8 (DEP8), 1 node |
| Decode parallelism | TP=1, DP=16, EP=16 (DEP16, wide), 2 nodes |
| All-to-all | DeepEP, `normal` (prefill) and `low_latency` (decode) |
| KV transfer | NIXL (`DISAGG_TRANSFER_BACKEND`; only `nixl` has been tested) |
| MTP speculative decoding | On (EAGLE, 3 steps, 4 draft tokens; opt-out via `no-mtp` component) |
| `mem-fraction-static` | 0.85 on both roles |
| Decode running requests | 32 per rank (`DECODE_MAX_RUNNING_REQUESTS_PER_RANK`, passed to SGLang as per-rank x DP), CUDA graph max batch 32 |
| Prefill chunk | 8192 tokens per rank (`PREFILL_CHUNK_TOKENS_PER_RANK`) |
| Reasoning / tool-call parsers | glm45 / glm47 (only used by OpenAI-style routes, see [Limitations](#limitations)) |

The decode limits follow from DeepEP low-latency mode, which dispatches at most `SGLANG_DEEPEP_NUM_MAX_DISPATCH_TOKENS_PER_RANK` (128) tokens per rank per step: 32 running requests x 4 draft tokens. Raise all three together.

### P/D Deployment Options

Deployments are named `gke-p<replicas>w<nodes>d<replicas>w<nodes>`. GKE is the only provider overlay so far; other providers need their own `providers/<name>` overlay (RDMA device requests, HCA selection, cache volumes).

| Deployment | Prefill | Decode | Nodes / GPUs |
| --- | --- | --- | --- |
| `gke-p1w1d1w1` | 1 replica, 1 node, DEP8 | 1 replica, 1 node, DEP8 | 2 / 16 |
| `gke-p1w1d1w2` | 1 replica, 1 node, DEP8 | 1 replica, 2 nodes, DEP16 | 3 / 24 |
| `gke-p1w2d1w2` | 1 replica, 2 nodes, DEP16 | 1 replica, 2 nodes, DEP16 | 4 / 32 |
| `gke-p2w1d1w2` | 2 replicas, 1 node, DEP8 | 1 replica, 2 nodes, DEP16 | 4 / 32 |
| `gke-p2w2d2w2` | 2 replicas, 2 nodes, DEP16 | 2 replicas, 2 nodes, DEP16 | 8 / 64 |

### Supported Hardware Backends

| Backend | Directory | Notes |
| --- | --- | --- |
| NVIDIA GPU (GKE A3 Ultra, RoCE) | `providers/gke/`, `deployments/gke-*` | GPUs and RDMA NICs through DRA (`gke-rdma` claims), privileged containers for GPU-initiated RDMA, block/subblock affinity; needs the NVSHMEM fix in [Limitations](#limitations) |

## Layout

```text
prefill pod                                decode pod
+--------------------------------+         +----------------------------------------+
| sglang (Rust frontend)         |         | routing-proxy   8000 8001 ... 8007     |
|  rank0 8000  <- bootstrap      |         |   --kv-connector=sglang                |
|  rank1 8001     registry       |         |   SGLANG_BOOTSTRAP_PORT=8000           |
|  ...                           |         |        |    |          |               |
|  rank7 8007                    |         | sglang  8200 8201 ... 8207 (Rust)      |
+--------------------------------+         +----------------------------------------+
```

The InferencePool lists ports 8000 to 8007 (`router/wide-ep.values.yaml`), so the EPP sees every rank of every pod as an endpoint.

## Components

Add [kustomize Components](https://kubectl.docs.kubernetes.io/guides/config_management/components/) to a deployment's `kustomization.yaml` under `components:`.

| Component | Targets | Effect |
| --- | --- | --- |
| `no-mtp` | prefill + decode | Disables MTP speculative decoding (`ENABLE_MTP=0`) |

Component env entries merge by name, so their values replace the base defaults.

## Prerequisites

In addition to the [wide-ep prerequisites](../../../README.md#prerequisites) (client tools, GAIE CRDs, LeaderWorkerSet controller `v0.10.0`+ with `DisaggregatedSet` enabled, namespace, HF token secret):

```bash
export KUBECONFIG=~/.kube/config
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
source ${REPO_ROOT}/guides/env.sh
export GUIDE_NAME="wide-ep"
export NAMESPACE=<your-namespace>
export MODEL=zai-org/GLM-5.2-FP8
```

For GKE, prepare the cluster as described in [the GKE provider docs](../../../../../docs/infrastructure/providers/gke/README.md) (GPUDirect RDMA, DRA and DRANET drivers) so the `gke-rdma-template` claims resolve.

## Deploy the llm-d Router

Install the router with the wide-ep values and the SGLang overrides on top. The overrides swap the EPP plugin config for one that parses `/generate` and only uses signals the EPP computes itself (see [Limitations](#limitations)).

```bash
helm install ${GUIDE_NAME} \
    ${ROUTER_STANDALONE_CHART} \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
    -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/sglang-glm-5.2.values.yaml \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

For Gateway mode, add the same `sglang-glm-5.2.values.yaml` file after `${GUIDE_NAME}.values.yaml` in the [Gateway Mode command](../../../README.md#1-deploy-the-llm-d-router).

## Deploy the Model Server

### P/D Disaggregated

Pick a deployment from the [P/D Deployment Options](#pd-deployment-options) table (run from this directory) and apply:

```bash
export DEPLOYMENT=gke-p1w1d1w2 # gke- followed by a topology from the table
kubectl apply -n ${NAMESPACE} -k deployments/${DEPLOYMENT}
```

On GKE the stock nightly image cannot start the multi-node decode group (see [Limitations](#limitations)), so wrap the deployment in a small overlay that swaps in your NVSHMEM-fixed build:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - <path-to>/sglang-glm-5.2/deployments/gke-p1w1d1w2
images:
  - name: docker.io/lmsysorg/sglang
    newName: <your-registry>/sglang-roce
    newTag: <tag>
```

Do not deploy this recipe and the vLLM GLM-5.2 recipe in the same namespace: the InferencePool selects pods by `llm-d.ai/guide` only and would mix both engines into one pool.

Wait for the pods to become ready. Model load takes time; the startup probe allows up to 45 minutes:

```bash
kubectl get pods -n ${NAMESPACE} -l llm-d.ai/model=GLM-5.2-FP8,llm-d.ai/engine-type=sglang -w
```

A pod is ready once a one-token `/generate` probe on its local rank-0 listener succeeds. The Rust `/health` endpoints answer 503 on every listener except global DP rank 0, so the recipe does not use them for readiness. The probe uses SGLang's fake bootstrap host, so every 30s the prefill log shows one `Prefill bootstrap failed ... Fake KVSender Exception` line; that is the probe, not a failing request.

## Verification

### 1. Get the IP of the Proxy

```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```

### 2. Tokenize a prompt

`/generate` requests must carry `input_ids`. The EPP's `sglanghttp-parser` rejects bodies that only have `text`, because prefix-cache routing needs the token IDs. Produce them with the model tokenizer inside a prefill pod, which already has the tokenizer files in its HF cache:

```bash
export PREFILL_POD=$(kubectl get pod -n ${NAMESPACE} -l llm-d.ai/role=prefill,llm-d.ai/engine-type=sglang -o jsonpath='{.items[0].metadata.name}')
export INPUT_IDS=$(kubectl exec -n ${NAMESPACE} ${PREFILL_POD} -c sglang -- python3 -c "
from transformers import AutoTokenizer
import json
tok = AutoTokenizer.from_pretrained('zai-org/GLM-5.2-FP8', trust_remote_code=True)
print(json.dumps(tok.encode('How are you today?')))
")
echo ${INPUT_IDS}
```

### 3. Send Test Requests

Open a temporary shell inside the cluster:

```bash
kubectl run curl-debug --rm -it \
    --image=cfmanteiga/alpine-bash-curl-jq \
    --namespace="$NAMESPACE" \
    --env="IP=$IP" \
    --env="INPUT_IDS=$INPUT_IDS" \
    -- /bin/bash
```

Send a `/generate` request through the EPP:

```bash
curl -X POST http://${IP}/generate \
    -H 'Content-Type: application/json' \
    -d "{
        \"input_ids\": ${INPUT_IDS},
        \"sampling_params\": {\"max_new_tokens\": 64, \"temperature\": 0}
    }" | jq
```

The response is SGLang's native format: generated `text` plus `meta_info` with `prompt_tokens`, `completion_tokens` and `cached_tokens`. Add `"stream": true` for server-sent events.

### 4. Check the P/D path

Every prefill rank listener must expose the full bootstrap topology. Query the rank-0 listener of each prefill pod (replace the pod IP):

```bash
curl -s "http://<prefill-pod-ip>:8000/route?prefill_dp_rank=-1&prefill_cp_rank=-1&target_tp_rank=-1&target_pp_rank=-1" | jq
```

A `200` with one row per DP rank means the registry is populated. A `503` means the pod's listener has no topology; check that the image contains sgl-project/sglang#36234.

## Optional Features

### MTP Speculative Decoding

On by default for both roles as EAGLE with 3 steps and 4 draft tokens (GLM-5.2 ships one MTP layer). Disable with the `no-mtp` component or `ENABLE_MTP=0`; tune with `MTP_NUM_STEPS` and `MTP_NUM_DRAFT_TOKENS`.

### EPP Routing

`router/sglang-glm-5.2.values.yaml` replaces the wide-ep plugin config. It parses `/generate` with `sglanghttp-parser`, scores prefill endpoints with the approximate prefix-cache scorer (weight 3) and the active-request scorer (weight 2), and scores decode endpoints with the active-request scorer. All 8 DP rank ports (8000-8007) are inherited as `targetPorts`. The metrics refresh interval is raised to 10s because the Rust listeners expose no `/metrics`.

### Tunables

All engine knobs are container env vars on the `sglang` container of each role, so overlays change them with name-based env merges as the `no-mtp` component does.

| Variable | Role | Default | Purpose |
| --- | --- | --- | --- |
| `DP_SIZE_LOCAL` | both | `8` | Ranks per pod; DP = EP = `DP_SIZE_LOCAL` x group size |
| `ENABLE_MTP`, `MTP_NUM_STEPS`, `MTP_NUM_DRAFT_TOKENS` | both | `1`, `3`, `4` | EAGLE/MTP speculative decoding |
| `PREFILL_MEM_FRACTION_STATIC`, `DECODE_MEM_FRACTION_STATIC` | prefill, decode | `0.85` | `--mem-fraction-static` |
| `PREFILL_CHUNK_TOKENS_PER_RANK` | prefill | `8192` | Per-rank chunked prefill budget; SGLang divides `--chunked-prefill-size` by DP, so the manifest passes per-rank x DP |
| `PREFILL_MAX_RUNNING_REQUESTS_PER_RANK` | prefill | `64` | Per-rank running requests; SGLang splits `--max-running-requests` across DP, so the manifest passes per-rank x DP |
| `DECODE_MAX_RUNNING_REQUESTS_PER_RANK`, `DECODE_CUDA_GRAPH_MAX_BS` | decode | `32`, `32` | Per-rank decode batch (passed as per-rank x DP); bounded by the DeepEP dispatch cap |
| `SGLANG_DEEPEP_NUM_MAX_DISPATCH_TOKENS_PER_RANK` | decode | `128` | DeepEP low-latency per-rank token buffer |
| `CONTEXT_LENGTH` | both | unset | `--context-length`; unset keeps the model default |
| `DISAGG_TRANSFER_BACKEND` | both | `nixl` | `--disaggregation-transfer-backend` |

## Limitations

- **No per-rank `/metrics`.** The Rust listeners do not expose `/metrics`, so the EPP config in `router/sglang-glm-5.2.values.yaml` uses only the approximate prefix-cache scorer and the active-request scorer. Queue depth and KV utilization scoring, and the wide-ep monitoring overlay, do not apply. Tracked in [llm-d/llm-d#2198](https://github.com/llm-d/llm-d/issues/2198).
- **`/generate` only, pre-tokenized.** The EPP parser list only contains `sglanghttp-parser`, so OpenAI-style paths are rejected with `400` and `/generate` bodies need `input_ids`. Chat and completions on the Rust frontend are follow-up work ([sgl-project/sglang#36718](https://github.com/sgl-project/sglang/pull/36718)); once the image serves them, add `openai-parser` to the EPP parser list.
- **No KV-event based precise routing.** Per-rank KV-cache ownership for shared endpoints is still being designed ([llm-d/llm-d-router#2306](https://github.com/llm-d/llm-d-router/issues/2306)), so there is no `kv-events` component here.
- **NVSHMEM on RoCE (GKE).** The SGLang nightly ships NVSHMEM 3.4.5, whose IBGDA transport fails on RoCE with `create DCT share err` / `nvshmem setup connections failed` (uninitialized `ibv_ah_attr`; fixed upstream in NVSHMEM 3.5). DeepEP low-latency mode on multi-node decode therefore cannot start on GKE A3 Ultra with the stock image. InfiniBand fabrics are not affected.
- **Fixed image for RoCE.** llm-d carries the one-line fix as `patches/nvshmem_zero_ibv_ah_attr_v3.4.5-0.patch`. `image/Dockerfile` rebuilds only the `nvshmem_transport_ibgda.so.3` plugin from patched 3.4.5 source and copies it over the wheel's copy, so the DeepEP kernels keep their ABI. Build it and point the `docker.io/lmsysorg/sglang` image at your build with a kustomize `images:` override:

  ```bash
  cp ${REPO_ROOT}/patches/nvshmem_zero_ibv_ah_attr_v3.4.5-0.patch image/
  # BASE_IMAGE must be the SGLang image the gpu-sglang/nightly component resolves to (pin it by digest).
  docker build --build-arg BASE_IMAGE=docker.io/lmsysorg/sglang@sha256:<digest> -t <your-registry>/sglang-roce:<tag> image/
  ```

- **`/health` on non-zero ranks.** The Rust listeners of DP ranks other than global rank 0 answer `503` to `/health` and `/health_generate` while `/generate` works. The recipe therefore uses TCP startup and liveness probes plus a one-token `/generate` readiness probe.
- **Latency floor on the P/D path.** With the Rust frontend, a single `/generate` through P/D takes about 3s end to end even for a 5-token prompt, and the latency is quantized in 1s steps (3.0s, 4.0s, 5.0s). Long generations are fast once started (256 tokens in about 5.6s at 1 request), so the cost sits in the bootstrap and KV-transfer phase; the suspected cause is the Rust server's 1s idle tick gating the decode scheduler's bootstrap polling. Treat TTFT numbers from this recipe as not representative until this is resolved upstream.
- **DeepEP is untuned.** The engine logs `Only use 20 SMs for DeepEP communication`; no `--deepep-config` is set. Throughput numbers need tuning before they are comparable with the vLLM recipe.
- **Nightly images.** The base tracks the `gpu-sglang/nightly` and `routing-sidecar/nightly` image components. Pin them in an overlay for anything beyond experimentation.

## Cleanup

```bash
kubectl delete -n ${NAMESPACE} -k deployments/${DEPLOYMENT}
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
```
