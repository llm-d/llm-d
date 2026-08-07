# AMD / vLLM / MoRI-IO P/D Disaggregation Overlay

Single-pod **1P1D** P/D disaggregation overlay for **DeepSeek-V3** on AMD
Instinct GPUs, using the **MoRI-IO** KV-transfer connector (RDMA WRITE) and
the **MoRI-EP** Wide-EP all-to-all backend (**DP=8, EP=8, TP=1**).

This is the MoRI-IO companion to the sibling NIXL overlay at `../base/`.
Use this overlay when you want RDMA-Write KV transfer (MoRI-IO) instead of
NIXL/UCX, and Wide-EP MoE expert parallelism inside a single pod instead of
cross-pod TP.

Validated on **AMD Instinct MI355X, ROCm 7.2.3** (Pensando/Ionic RDMA NICs
exposed as `amd.com/vnic`), with the latest tested llm-d + vLLM stack.

## Layout

Follows the same `base/` + `amd-ci/` split as the sibling NIXL overlay:

```
moriio/
├── base/        # the real, tested MoRI-IO config
│   ├── kustomization.yaml
│   ├── patch-prefill.yaml
│   ├── patch-decode.yaml
│   └── patch-sidecar.yaml
└── amd-ci/      # thin CI/cluster overlay on ../base
    └── kustomization.yaml
```

## Topology

| Role    | Replicas | TP | DP | EP        | KV connector                    | all2all backend        |
| ------- | -------- | -- | -- | --------- | ------------------------------- | ---------------------- |
| Prefill | 1        | 1  | 8  | enabled   | `MoRIIOConnector` (kv_producer) | `mori_high_throughput` |
| Decode  | 1        | 1  | 8  | enabled   | `MoRIIOConnector` (kv_consumer) | `mori_low_latency`     |

Each pod consumes 8 GPUs on a single node; vLLM runs 8 data-parallel ranks
locally with one OpenAI front-end per rank (`--api-server-count=8`). The
routing sidecar in front of the decode pod hashes each request's UUID with
blake2s and routes both the prefill and decode leg to the same DP rank, so a
single conversation stays affinitised to one (P, D) rank pair.

## Default configuration

| Setting                     | Value                                                              |
| --------------------------- | ----------------------------------------------------------------- |
| Model                       | `deepseek-ai/DeepSeek-V3` served from local PVC `/models/DeepSeek-V3` |
| Parallelism                 | TP=1, DP=8, EP enabled (Wide-EP)                                   |
| KV connector                | `MoRIIOConnector`, WRITE mode (`VLLM_MORIIO_CONNECTOR_READ_MODE=0`) |
| Networking                  | Pod networking (Calico) + Multus `amd-host-device-nad` ×8 + `amd.com/vnic: 8` |
| cudagraph                   | prefill `PIECEWISE`, decode `FULL_DECODE_ONLY`                     |
| AITER                       | MoE, RMSNorm, **MLA** enabled; paged-attn off                     |
| Weights                     | offline, local PVC (`HF_HUB_OFFLINE=1`, no HF token)              |
| ZMQ registration proxy      | not used (peer discovery via the routing sidecar)                 |

Nothing about DP, TP, EP, or the model is hard-coded inside vLLM or
`llm-d-router`; everything above is either a CLI flag or an env var, so this
overlay can be cloned and re-tuned (e.g. DP=4 for half-pod runs) without
touching binaries.

## How it differs from `../base/` (NIXL)

The sibling NIXL overlay runs Llama-3.3-70B-Instruct-FP8-KV with the upstream
`NixlConnector` (`kv_role=kv_both`). This overlay adapts the recipe base to:

1. **Model**: `deepseek-ai/DeepSeek-V3` (~700 GiB MoE) served offline from a PVC.
2. **Connector**: `MoRIIOConnector` with split `kv_producer` / `kv_consumer`
   roles (NIXL uses `kv_both`), WRITE mode.
3. **Parallelism**: TP=1, DP=8, EP enabled, with a **role-specific MoRI
   all-to-all backend** — prefill `mori_high_throughput` (`InterNodeV1`),
   decode `mori_low_latency` (`InterNodeV1LL`). The legacy single-name `mori`
   backend was split upstream; do **not** use it here.
4. **Networking / RDMA**: **pod networking** (not `hostNetwork`). The RDMA NIC
   is requested via `amd.com/vnic: 8` and the host RDMA netdevs are injected
   into the pod netns with Multus (`default/amd-host-device-nad`, one per vnic).
   This is what makes inter-node MoRI-IO RoCE work; `hostNetwork` + `rdma/ib`
   is **not** required.
5. **Routing sidecar**: extra CLI flags (`--moriio-write-mode`,
   `--moriio-dp-size=8`, `--moriio-tp-size=1`, `--moriio-parallel-dispatch`,
   `--moriio-local-pod-ip=$(POD_IP)`) plus a `POD_IP` downward-API env var. The
   sidecar image must be a `llm-d-router` build that supports these flags and
   the renamed `--kv-connector` flag.

## Fixes baked into this overlay (validated during bring-up)

- **RDMA netdevs via Multus** (`amd-host-device-nad` ×8): without them
  `ibv_modify_qp(RTR)` fails with `No such device` on inter-node KV transfer.
- **`VLLM_ROCM_USE_SKINNY_GEMM=0`**: the tested vLLM image overlays the MoRI-IO
  Python (PR #45043) on a base `_rocm_C.abi3.so` that does not export
  `wvSplitK`; disabling skinny-GEMM avoids an ABI ImportError (unquantized
  layers fall back to torch `F.linear`; FP8 block-scale MoE still uses AITER).
- **Sidecar `--kv-connector`**: the tested sidecar build renamed the old
  `--connector` flag to `--kv-connector`.
- **No ZMQ proxy**: `proxy_ip` is left unset, so the connector's heartbeat
  thread never starts; peer discovery is done entirely by the routing sidecar.

## Required overrides

`base/kustomization.yaml` pins the two validated private image tags. Swap them
for your own registry/tags via `kustomize edit set image`:

| Image reference                 | Set to                                                                 |
| ------------------------------- | ---------------------------------------------------------------------- |
| `REPLACE_MODEL_SERVER_IMAGE`    | A vLLM ROCm image with MoRI-IO + MoRI-EP compiled in (see the vLLM PR adding MoRI-IO + MoRI-EP). |
| `REPLACE_ROUTING_SIDECAR_IMAGE` | A `llm-d-router` build that accepts the `--moriio-*` and `--kv-connector` CLI flags. |

```bash
cd guides/pd-disaggregation/modelserver/amd/vllm/moriio/base
kustomize edit set image \
  REPLACE_MODEL_SERVER_IMAGE=<your-registry>/<vllm-rocm-moriio-image>:<tag> \
  REPLACE_ROUTING_SIDECAR_IMAGE=<your-registry>/<llm-d-router-image>:<tag>
```

## Prerequisites on the cluster

- AMD Instinct GPU nodes with 8 GPUs per node and an RDMA NIC exposed via the
  `amd.com/vnic` device plugin, plus a Multus `NetworkAttachmentDefinition`
  named `amd-host-device-nad` in the `default` namespace.
- AMD GPU operator providing `amd.com/gpu`.
- Pre-staged DeepSeek-V3 weights on an RWX PVC named `deepseek-v3-weights`,
  mounted read-only at `/models/DeepSeek-V3` (this overlay runs fully offline;
  no HF token is required).
- If your MoRI-IO images live in a private registry, an image pull secret in
  the target namespace attached to the model-server ServiceAccount. Add it in
  a small per-cluster overlay on top of `base/` (see "Required overrides").

See `../../../../prerequisites/` for cluster-bring-up details that apply to all
P/D guides.

## Quickstart

```bash
# Pin your images first (see "Required overrides"), then deploy the CI overlay:
kustomize build guides/pd-disaggregation/modelserver/amd/vllm/moriio/amd-ci \
  | kubectl apply -f -
```

Drive traffic at the decode pod's `Service` on port 8000 (the routing-proxy
initContainer); it forwards to vLLM on 8200 after the MoRI-IO handshake
completes.

## Verifying the deployment

```bash
kubectl -n llm-d get pods
# Expect: pd-disaggregation-rocm-moriio-vllm-prefill-* Running
#         pd-disaggregation-rocm-moriio-vllm-decode-*  Running

kubectl -n llm-d logs -l llm-d.ai/role=prefill --tail=50 \
  | grep -E 'all2all-backend|MoRIIOConnector|engine ready'
# Expect: --all2all-backend mori_high_throughput, kv_role=kv_producer

kubectl -n llm-d logs -l llm-d.ai/role=decode --tail=50 \
  | grep -E 'all2all-backend|MoRIIOConnector'
# Expect: --all2all-backend mori_low_latency, kv_role=kv_consumer
```

## Tear down

```bash
kustomize build guides/pd-disaggregation/modelserver/amd/vllm/moriio/amd-ci \
  | kubectl delete -f - --ignore-not-found
```
