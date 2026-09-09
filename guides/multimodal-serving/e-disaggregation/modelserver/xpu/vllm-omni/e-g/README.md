# Wan Text-to-Video on Intel XPU — Disaggregated Encoder/Generator (EG, two-pool)

Serve **Wan2.1-T2V-1.3B** text-to-video on **Intel XPU** with **vLLM-Omni**, split
into two independently-scalable pools:

- **encode pool** — runs only the UMT5 text encoder (Stage 0, `model_stage=text_encode`),
  emits prompt embeddings. This pod is also the **Omni master + HTTP API** (`/v1/videos`).
- **generator pool** — runs the DiT denoise loop + VAE decode (Stage 1, `model_stage=dit`),
  consuming the encode stage's embeddings. Runs **headless** (no HTTP), registering
  to the master.

This is the disaggregated counterpart of the aggregated single-pod guide
[`../../../../../pd-disaggregation/modelserver/xpu/vllm-omni/`](../../../../../pd-disaggregation/modelserver/xpu/vllm-omni/).
The two pools scale independently — encode is a light UMT5 pass, generator is
the heavy, throughput-dominant DiT loop.

> **No Dynamo, no etcd, no NATS.** Both pods run `vllm serve <model> --omni` using
> vLLM-Omni's **own native multi-stage distributed serving**. The only cross-pod
> channel is the master's ZMQ port (the `omni-master` Service). This mirrors the
> shipped example `vllm_omni/examples/online_serving/bagel/run_server_stage_cli.sh`.

## Why split encoder/generator?

Wan's diffusion pipeline is normally a single monolithic stage (UMT5 + DiT + VAE).
The heterogeneous phases have very different cost/memory profiles: the UMT5 text
encode is cheap and stateless, while the DiT denoise loop + VAE decode dominate
runtime and hold ~13.5 GiB of weights. Splitting them lets you scale the generator
pool on its own signal and keep encode cards from carrying idle DiT weights.

> Wan has a single DiT and no autoregressive KV cache, so "prefill" and "decode"
> are the same denoise loop — the practical split is **Encode | (Prefill+Decode)**,
> i.e. two pools, not three.

## Architecture

```
                       POST /v1/videos[/sync]
                                 │
                                 ▼
        ┌───────────────── encode pod (card 0) ─────────────────┐
        │  vllm serve --omni --stage-id 0 --port 8000           │
        │    --omni-master-address $POD_IP --omni-master-port 8092
        │  = Omni master + HTTP API + Stage 0 (UMT5)            │
        └───────────────────────────────────────────────────────┘
              ▲  prompt_embeds (ar2diffusion stage 0→1)  │
              │  via omni-master Service (ZMQ :8092)      ▼
        ┌─────────────── generator pod (card 1) ────────────────┐
        │  vllm serve --omni --stage-id 1 --headless            │
        │    --omni-master-address omni-master --omni-master-port 8092
        │  = Stage 1 (DiT denoise + VAE decode)                 │
        └───────────────────────────────────────────────────────┘
```

## Contents

| File | Purpose |
|---|---|
| `stage-config.configmap.yaml` | The validated 2-stage topology `wan2_2_epd.yaml` (legacy filename), mounted at `/etc/wan/` in both pods. |
| `encode-deployment.yaml` | Encode pod (master + API + Stage 0) **and** the **`omni-master` Service** (required). |
| `decode-deployment.yaml` | Headless generator pod (Stage 1), connects to the master via the `omni-master` Service. |
| `resource-claim-templates.yaml` | DRA `ResourceClaimTemplate`s — one `gpu.intel.com` device per pool. |
| `kustomization.yaml` | Ties resources together; **no `namePrefix`** (see note). |
| `httproute.yaml` | Path-scoped `HTTPRoute` sending `/v1/videos` to `InferencePool wan-video-eg-xpu`. |
| `../../../../router/eg-video-xpu.values.yaml` | Router/EPP diffusion profile; selects **only** the master pods (`role=encode`) for HTTP. |

> **The `omni-master` Service is required** — it is the only cross-pod channel
> (the headless generator pod's ZMQ stage sockets connect to it). The HTTP
> `/v1/videos` surface needs no Service: the gateway routes via
> `HTTPRoute → InferencePool` and the EPP selects the master pod by label.
>
> **No `namePrefix`** is applied: the generator pod reaches the master via the fixed
> `omni-master` DNS name, so renaming Services would break that in-manifest
> dependency. Deploy into a dedicated namespace for isolation instead.

## Prerequisites

Same as the aggregated guide: Intel GPU DRA (`gpu.intel.com`), a
`llm-d-inference-gateway` Gateway, a `llm-d-hf-token` secret, and an XPU +
vLLM-Omni image to replace `REPLACE_MODEL_SERVER_IMAGE`.

## Deploy

```bash
cd guides/multimodal-serving/e-disaggregation/modelserver/xpu/vllm-omni/e-g

# 1) Set your image in kustomization.yaml (replace REPLACE_MODEL_SERVER_IMAGE).

# 2) Apply into a dedicated namespace (no namePrefix — see note above).
kubectl create namespace wan-eg
kubectl kustomize . | less
kubectl apply -k . -n wan-eg

# 3) Route /v1/videos and install the router/EPP profile.
kubectl apply -f httproute.yaml -n wan-eg
helm upgrade --install wan-video-eg-xpu <router-chart> \
  -f guides/recipes/router/base.values.yaml \
  -f guides/multimodal-serving/e-disaggregation/router/eg-video-xpu.values.yaml
```

The generator pod is co-scheduled on the same node as the master for single-node
bring-up (the ZMQ handshake is node-local). Cross-**node** embedding transport
(NIXL/UCX `ze_copy` over RDMA) is a follow-up.

## Test

```bash
kubectl port-forward deploy/encode 8000:8000 -n wan-eg

curl -X POST http://localhost:8000/v1/videos/sync \
  -F model=Wan-AI/Wan2.1-T2V-1.3B-Diffusers \
  -F prompt='A serene lakeside sunrise with mist over the water.' \
  -F width=256 -F height=256 -F num_frames=17 \
  -F num_inference_steps=8 -F guidance_scale=4.0 -F fps=16 -F seed=42 \
  -o out.mp4
```

The response headers report per-stage timing, e.g.
`X-Stage-Durations: {"stage_0_gen_ms":..., "stage_1_gen_ms":...}` — confirming the
encode and generator stages ran on their respective pools.

## Scaling

- Scale the **generator** Deployment `replicas` independently (it is the throughput
  bottleneck). The EPP load-balances across master replicas; generator replicas
  register to the master and are picked by the master's StagePool
  (`--omni-lb-policy`).
- For Wan2.2 A14B MoE, raise the generator claim `count` and set
  `tensor_parallel_size` in the stage config.
- `--enforce-eager` is required on XPU (Wan rotary embedding breaks `torch.compile`).

## Validation

The exact pod commands here were verified on an Intel Arc Pro B60 with two
containers (encode on card 0, generator on card 1): `POST /v1/videos/sync` returned
a valid MP4, with `stage_0_gen_ms` (encode) and `stage_1_gen_ms` (DiT+VAE)
executing on their separate cards and the `ar2diffusion stage=0→1` handoff
crossing the container boundary over the `omni-master` ZMQ channel.
