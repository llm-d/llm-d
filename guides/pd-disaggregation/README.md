# P/D Disaggregation

[![E2E (CKS GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-pd-disaggregation-cks-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-pd-disaggregation-cks-acc-gpu-vllm-x.yaml)
[![E2E (GKE GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-pd-disaggregation-gke-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-pd-disaggregation-gke-acc-gpu-vllm-x.yaml)
[![E2E (GKE GPU SGLang)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-pd-disaggregation-gke-acc-gpu-sglang-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-pd-disaggregation-gke-acc-gpu-sglang-x.yaml)
[![E2E (GKE TPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-pd-disaggregation-gke-acc-tpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-pd-disaggregation-gke-acc-tpu-vllm-x.yaml)
[![E2E (OCP GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-pd-disaggregation-ibm-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-pd-disaggregation-ibm-acc-gpu-vllm-x.yaml)
[![E2E (Intel XPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-pd-disaggregation-intel-acc-xpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-pd-disaggregation-intel-acc-xpu-vllm-x.yaml)
[![E2E (AMD ROCM NIXL)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-pd-disaggregation-amd-ci-acc-rocm-vllm-nixl.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-pd-disaggregation-amd-ci-acc-rocm-vllm-nixl.yaml)
[![E2E (AMD ROCM MORI)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-pd-disaggregation-amd-ci-acc-rocm-vllm-moriio.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-pd-disaggregation-amd-ci-acc-rocm-vllm-moriio.yaml)

## Overview

This guide splits inference into separate **prefill** and **decode** pools. Prefill pods process the prompt and produce its KV cache; decode pods pull that KV cache over a KV transfer connector (NIXL by default) and generate the output tokens. Both pools belong to one `InferencePool`, and the llm-d Router schedules every request twice: once onto a prefill pod and once onto a decode pod. Because disaggregation is built into the router, it composes with its other scorers:

- **Prefill** — the `prefix-cache-affinity-filter` keeps prefix groups on cache-warm prefill pods (gated by a calibrated `peakPrefillThroughput`), and the `token-load-scorer` picks the prefill pod with the least queued prompt work.
- **Decode** — the `active-request-scorer` picks the decode pod with the fewest in-flight requests, since pure decode is bound by concurrency rather than prompt throughput.

The default deployment serves `openai/gpt-oss-120b` on NVIDIA GPUs with **1 prefill pod (TP=1) and 1 decode pod (TP=4)**, 5 GPUs in total: the smallest topology that exercises the full P/D path. Production deployments scale the two pools independently (see [When to use P/D and how to tune it](../../docs/architecture/advanced/disaggregation/README.md#when-to-use-pd-and-how-to-tune-it)).

How the two pools are deployed depends on the accelerator:

- **NVIDIA GPU** (vLLM and SGLang): one LWS [`DisaggregatedSet`](https://lws.sigs.k8s.io/docs/concepts/disaggregatedset/) (`pd-disagg-vllm` or `pd-disagg-sglang`) with a `prefill` and a `decode` role, in a single slice. The set rolls both roles out as one version and can replicate the whole topology into independent copies (`slices`); it requires the LeaderWorkerSet controller (see [Prerequisites](#prerequisites)) and is covered in [Operating the DisaggregatedSet](#operating-the-disaggregatedset).
- **All other accelerators** (AMD, Intel XPU, Google TPU, Iluvatar, MetaX, Rebellions NPU): a prefill and a decode `Deployment` (`LeaderWorkerSet` groups for TPU7x dynamic sub-slices).

For why P/D disaggregation helps, how requests flow between the two pools, and tuning guidance, see [Disaggregated Serving](../../docs/architecture/advanced/disaggregation/README.md).

## Supported Accelerators and Model Servers

This guide includes configurations for the following accelerator and model server combinations (set `ACCELERATOR_TYPE` and `MODEL_SERVER` accordingly). Each accelerator serves one model; its platform overlays are selected with `INFRA_PROVIDER` (see [Configure the environment](#configure-the-environment)):

<!-- guide:support start -->
| Accelerator | `ACCELERATOR_TYPE` | Served model | vLLM | SGLang | Notes |
| --- | --- | --- | --- | --- | --- |
| NVIDIA GPU | `gpu` | `openai/gpt-oss-120b` | ✅ validated | ✅ validated | H200 · 1P (TP=1) + 1D (TP=4), 5 GPUs; SGLang 1P (TP=2) + 1D (TP=2) · NIXL |
| AMD GPU | `amd` | `amd/Llama-3.3-70B-Instruct-FP8-KV` | ✅ validated | — | AMD Instinct · 1P (TP=1) + 1D (TP=4) · NIXL |
| Intel XPU | `xpu` | `Qwen/Qwen3-0.6B` | ✅ validated | — | Intel GPU via DRA · 1P + 1D, 1 GPU each · NIXL |
| Google TPU v6e | `tpu/v6` | `Qwen/Qwen3-32B` | ✅ validated | — | `2x4` v6e nodes on GKE · 1P + 1D, 8 chips each (TP=8) · TPUConnector |
| Google TPU v7 | `tpu/v7` | `Qwen/Qwen3.5-397B-A17B-FP8` | 🟡 community | — | `2x2x1` TPU7x nodes on GKE · 1P + 1D, 4 chips each (TP=8) · TPUConnectorHMA |
| Google TPU v7 (dynamic slicing) | `tpu/v7-dynamic-slice` | `Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8` | 🟡 community | — | TPU7x sub-slices via dynamic slicing + Kueue · 1P + 1D, one `2x2x1` sub-slice each · TPUConnectorHMA |
| Iluvatar GPU | `iluvatar` | `Qwen/Qwen3-32B` | 🟡 community | — | BI-V150 · 1P + 1D, 2 boards each (TP=4) · IluNixlConnector |
| MetaX GPU | `metax` | `Qwen/Qwen3-32B` | 🟡 community | — | C500X · 1P (TP=2) + 1D (TP=4) · NIXL over TCP |
| Rebellions NPU | `npu` | `MiniMaxAI/MiniMax-M2.7` | 🟡 community | — | 4 NPUs + 1 RoCE VF per pod via DRA · 1P (PP=4) + 1D (DP=4, EP) · RblnNixlConnector |

✅ validated: covered by a nightly E2E workflow · 🟡 community: maintained by the hardware vendor or community, not covered by nightly E2E · ❌ not supported: tracked in the linked issue · — no configuration.
<!-- guide:support end -->

Community rows are compatibility configurations maintained by their hardware vendors, often with reduced sizing (smaller models, fewer accelerators); review and adjust them for your environment before production use.

### KV Transfer Connectors

P/D disaggregation requires a KV transfer connector to move KV cache blocks from prefill workers to decode workers. On vLLM it is configured with the `--kv-transfer-config` flag:

| Connector | Overlays | Transport | Notes |
| --------- | -------- | --------- | ----- |
| NixlConnector | default on NVIDIA GPU, AMD GPU, Intel XPU and MetaX | UCX (RDMA / TCP) | Supports heterogeneous TP across P/D. |
| RblnNixlConnector | `npu/vllm/base` | NIXL over a RoCE VF | Rebellions' NIXL connector with `kv_buffer_device=rbln`. See the [Rebellions NPU tab](#2-deploy-the-model-server). |
| MooncakeConnector | `gpu/vllm/cks-mooncake` | RDMA via Mooncake Transfer Engine | CKS with InfiniBand. See the platform table in [Prerequisites](#prerequisites). |
| MoRIIOConnector | `amd/vllm/moriio/*` | RDMA via MoRI-IO | AMD GPU. |
| TPUConnector / TPUConnectorHMA | `tpu/*` | TPU ICI / DCN | From `tpu_inference`; HMA on TPU7x. |
| IluNixlConnector | `iluvatar/vllm/base` | UCX with CUDA-aware transports | Iluvatar's fork of NixlConnector. See the [Iluvatar tab](#2-deploy-the-model-server). |

NIXL works over TCP, but RDMA networking (InfiniBand, RoCE, EFA) is **highly recommended** for production.

### vLLM vs SGLang

Both engines run on NVIDIA GPU with the same router configuration and move KV cache over NIXL, but they disaggregate differently:

| Aspect | vLLM | SGLang |
| --- | --- | --- |
| P/D decider | Can disaggregate conditionally (`prefix-based-pd-decider`); this guide's router uses `always-disagg-pd-decider` | Must use `always-disagg-pd-decider`: a decode worker has no local-prefill path. See [Unconditional Disaggregation](../../docs/operations/disaggregation/sglang.md#unconditional-disaggregation) |
| Peer discovery | NIXL side-channel handshake (TCP 5600) | Prefill bootstrap server on port `8998`. To change it, set `SGLANG_BOOTSTRAP_PORT` on the sidecar and `--disaggregation-bootstrap-port` on the engine so the two match |
| Request cancellation | Prefill-side free notification releases the KV cache | No prefill-side free notification or reclaim timeout: a request cancelled before decode pulls the KV cache can strand it on the prefill until the pod restarts |
| Engine flags | `--kv-transfer-config` (see above) | `--disaggregation-mode={prefill,decode}`, `--disaggregation-transfer-backend=nixl`; the decode routing sidecar runs with `--kv-connector=sglang` |
| Operations | [Disaggregated Serving: Operations (vLLM)](../../docs/operations/disaggregation/vllm.md) | [Disaggregated Serving: Operations (SGLang)](../../docs/operations/disaggregation/sglang.md) |

## Prerequisites

- Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md) to use this guide.

- Ensure your cluster has enough accelerators for your configuration (default NVIDIA GPU configuration: 1 prefill pod with tensor parallelism 1 and 1 decode pod with tensor parallelism 4, 5 GPUs in total). The prefill and decode pods exchange KV cache over the network, so place them on nodes connected by a high-bandwidth fabric.

- Create a [HuggingFace token](../../helpers/hf-token.md) and export it as `HF_TOKEN` in your shell.

- (Optional) Install the [monitoring stack](../../docs/operations/observability/setup.md) if you plan to enable Prometheus monitoring.

- For NVIDIA GPU, the [LeaderWorkerSet controller](https://lws.sigs.k8s.io/docs/installation/) `v0.11.1` or newer with the `DisaggregatedSet` API enabled (`--set enableDisaggregatedSet=true` when installing with Helm, which also installs its validating webhook and RBAC). The [environment step](#configure-the-environment) below installs it for `ACCELERATOR_TYPE=gpu`; skip that step if your cluster already runs it.

- Prepare the cluster for your platform (`INFRA_PROVIDER`). `base`, `amd-ci`, `moriio/base`, and the TPU and community accelerators need nothing beyond the accelerator's device plugin (see the [model server tabs](#2-deploy-the-model-server)); the other platforms need:

  | Accelerator | `INFRA_PROVIDER` | Cluster requirements | Setup |
  | --- | --- | --- | --- |
  | NVIDIA GPU | `gke`, `gke/a4x`, `gke/a4xmax` | GPU DRA (manual node labels and driver install, not yet GKE-managed) and managed DRANET for RoCE; `gke` targets A3/A4, `gke/a4x` / `gke/a4xmax` target A4X / A4X Max (GB200 / GB300); DRANet must support hairpin (same-node) and cross-rail (inter-node multi-rail) routing | [GKE: GPU DRA and DRANET](../../docs/infrastructure/providers/gke/README.md#gpu-dynamic-resource-allocation-dra-and-dranet-roce-on-gke), [GKE A4X setup](../../docs/infrastructure/providers/gke/README.md#gke-a4x-gb200-setup) |
  | | `coreweave` | An RDMA device plugin exposing `rdma/ib` inside pods (one per pod) | [RDMA resources](../../docs/infrastructure/rdma/README.md#rdma-resources-and-capabilities) |
  | | `cks-mooncake` | vLLM only. GPUs via `nvidia.com/gpu`; InfiniBand devices exposed inside pods via an `rdma/ib` device plugin (NVIDIA Network Operator, Multus with SR-IOV, or equivalent); a vLLM image with `mooncake-transfer-engine` (the standard image may not include it); `VLLM_MOONCAKE_BOOTSTRAP_PORT` (default `8998`) unique per co-located instance. Configures `MooncakeConnector` transport only, not Mooncake Store | [RDMA resources](../../docs/infrastructure/rdma/README.md#rdma-resources-and-capabilities), [verify RDMA in a pod](../../docs/infrastructure/rdma/README.md#2-inter-pod-network), [Mooncake installation](https://kvcache-ai.github.io/Mooncake/) |
  | | `aws` | `p5en.48xlarge` nodes with the EFA device plugin exposing `vpc.amazonaws.com/efa` | [AWS EFA notes](../../docs/infrastructure/rdma/README.md#aws-efa-deprecated---use-upstream-aws-images) |
  | AMD GPU | `oci` | OKE `BM.GPU.MI300X.8` nodes; NVIDIA Network Operator SR-IOV VFs as `nvidia.com/sriov-rdma-vf`, with a `sriov-rdma-vf` SriovNetwork in namespace `default` | [RDMA resources](../../docs/infrastructure/rdma/README.md#rdma-resources-and-capabilities) |
  | | `tensorwave` | An RDMA device plugin exposing `rdma/ib` inside pods | [RDMA resources](../../docs/infrastructure/rdma/README.md#rdma-resources-and-capabilities) |
  | | `moriio/amd-ci`, `moriio/amd-ci-1p1d-tp8` | `amd.com/vnic` resources and an `amd-host-device-nad` NetworkAttachmentDefinition in namespace `default` (the AMD CI cluster) | [RDMA resources](../../docs/infrastructure/rdma/README.md#rdma-resources-and-capabilities) |
  | Intel XPU | `rdma` | A network DRA driver publishing a `dranet-rdma` DeviceClass with RDMA-capable NICs (`dra.net` `rdma` attribute); each claim aligns the NIC with its GPU's PCIe root | [RDMA resources](../../docs/infrastructure/rdma/README.md#rdma-resources-and-capabilities) |

### Get the guide

Every command below runs from a local clone of the [llm-d repository](https://github.com/llm-d/llm-d): the manifests, Helm values, and Kustomize overlays it applies live next to this guide. Set the branch and clone the repo (if you already have a checkout, skip this and run the remaining commands from inside it):

<!-- guide:prerequisites.clone start -->
<!-- llm-d-cicd:skip start -->
```bash
export BRANCH=main
git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout ${BRANCH}
```
<!-- llm-d-cicd:skip end -->
<!-- guide:prerequisites.clone end -->

### Configure the environment

`INFRA_PROVIDER` selects the platform overlay for your `ACCELERATOR_TYPE` and, where an accelerator offers more than one, the KV transfer connector. Valid values per accelerator:

| `ACCELERATOR_TYPE` | `INFRA_PROVIDER` values |
| --- | --- |
| `gpu` | `base`, `gke`, `gke/a4x`, `gke/a4xmax`, `coreweave`, `aws`, `cks-mooncake` (SGLang: `base`, `gke`, `coreweave`, `aws`) |
| `amd` | `base`, `amd-ci`, `oci`, `tensorwave`, `moriio/base`, `moriio/amd-ci`, `moriio/amd-ci-1p1d-tp8` |
| `xpu` | `base`, `rdma` |
| `tpu/v6`, `tpu/v7`, `tpu/v7-dynamic-slice` | `gke` |
| `iluvatar`, `metax`, `npu` | `base` |

- `base`: generic Kubernetes cluster.
- `gke`: on NVIDIA GPU, GKE A3/A4 with GPU DRA and DRANet RoCE (see the platform table in [Prerequisites](#prerequisites)); `gke/a4x` and `gke/a4xmax` target GKE A4X / A4X Max (GB200 / GB300). On TPU, the GKE TPU node pools described in the model server step.
- `coreweave`: CoreWeave with `rdma/ib`. `aws`: AWS with EFA.
- `cks-mooncake`: CoreWeave CKS with `MooncakeConnector` over InfiniBand, vLLM only (see the platform table in [Prerequisites](#prerequisites)).
- `amd-ci`: the AMD CI cluster. `oci`: OCI OKE `BM.GPU.MI300X.8` with SR-IOV RDMA VFs. `tensorwave`: TensorWave.
- `moriio/*`: `MoRIIOConnector` instead of NIXL, serving `Qwen/Qwen3-32B` (set `MODEL` accordingly); `moriio/amd-ci-1p1d-tp8` runs TP=8 for both roles.
- `rdma` (Intel XPU): NIXL over RDMA instead of TCP.

**Set the guide-specific environment variables:**

<!-- guide:env.static start -->
```bash
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
export GUIDE_NAME=pd-disaggregation
export NAMESPACE=llm-d-pd-disaggregation
export MONITORING=false # options: false, true
export MONITORING_VALUES=
export ACCELERATOR_VALUES=
export ACCELERATOR_TYPE=gpu # options: gpu, amd, xpu, tpu/v6, tpu/v7, tpu/v7-dynamic-slice, iluvatar, metax, npu
export MODEL_SERVER=vllm # options: vllm, sglang
export INFRA_PROVIDER=base # options: base, gke, gke/a4x, gke/a4xmax, coreweave, aws, cks-mooncake, amd-ci, oci, tensorwave, moriio/base, moriio/amd-ci, moriio/amd-ci-1p1d-tp8, rdma; valid values per accelerator: table above
export MODEL=openai/gpt-oss-120b # set to the model your accelerator serves (Supported Accelerators and Model Servers table); the AMD MoRIIO overlays (INFRA_PROVIDER=moriio/*) serve Qwen/Qwen3-32B
source ${REPO_ROOT}/guides/env.sh # defines GAIE_VERSION, ROUTER_CHART_VERSION, router chart URLs, and CURL_TEST_IMAGE
```
<!-- guide:env.static end -->

**(NVIDIA GPU only) Install the LeaderWorkerSet controller with the `DisaggregatedSet` API** (skip if your cluster already runs LWS `v0.11.1` or newer with `enableDisaggregatedSet=true`):

<!-- guide:prerequisites.lws start -->
<!-- variants:start -->
<details open data-when="ACCELERATOR_TYPE=gpu">
<summary><b>NVIDIA GPU</b></summary>

<!-- llm-d-cicd:skip start -->
```bash
helm upgrade --install lws oci://registry.k8s.io/lws/charts/lws \
  --version=0.11.1 \
  --namespace lws-system --create-namespace \
  --set enableDisaggregatedSet=true \
  --wait --timeout 300s
```
<!-- llm-d-cicd:skip end -->

</details>
<!-- variants:end -->
<!-- guide:prerequisites.lws end -->

**Install the Gateway API Inference Extension CRDs:**

<!-- guide:prerequisites.gaie start -->
```bash
# GAIE_URL is automatically calculated from GAIE_VERSION at ${REPO_ROOT}/guides/env.sh
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/${GAIE_URL}/v1-manifests.yaml
```
<!-- guide:prerequisites.gaie end -->

**Create a target namespace for the installation:**

<!-- guide:prerequisites.namespace start -->
```bash
kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
```
<!-- guide:prerequisites.namespace end -->

**Create the `llm-d-hf-token` secret** in your target namespace with the key [`HF_TOKEN`](../../helpers/hf-token.md) matching a valid HuggingFace token to pull models:

<!-- guide:prerequisites.secrets start -->
<!-- llm-d-cicd:skip start -->
```bash
kubectl create secret generic llm-d-hf-token \
  --from-literal="HF_TOKEN=${HF_TOKEN}" \
  --namespace "${NAMESPACE}" \
  --dry-run=client -o yaml | kubectl apply -f -
```
<!-- llm-d-cicd:skip end -->
<!-- guide:prerequisites.secrets end -->

## Installation Instructions

### 1. Deploy the llm-d Router

**Prepare the paths to the `helm` values files** for the `llm-d` router (used in the deployment command below):

<!-- guide:deploy.router_values start -->
```bash
# Paths to values files
export ROUTER_BASE_VALUES="${REPO_ROOT}/guides/recipes/router/base.values.yaml"
export ROUTER_VALUES="${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml"
```
<!-- guide:deploy.router_values end -->

The router configuration in [`router/pd-disaggregation.values.yaml`](router/pd-disaggregation.values.yaml) disaggregates every request (`always-disagg-pd-decider`) and schedules it with a `prefill` and a `decode` profile, which select pods by their `llm-d.ai/role` label.

> [!NOTE]
> The `prefix-cache-affinity-filter` uses a `peakPrefillThroughput` of `33821`, calibrated for gpt-oss-120b on TP=1 H200 prefill workers through the full P/D path (including the NIXL KV transfer). On a different model or accelerator, measure and set your value with the [calibration guide](../recipes/router/calibration/README.md) before performance work.

**(Optional) Enable Prometheus monitoring on the `llm-d` router** by defining the `helm` values file (requires installing the monitoring stack mentioned in [Prerequisites](#prerequisites)):

<!-- guide:deploy.monitoring_values start -->
<!-- llm-d-cicd:skip start -->
```bash
# only when MONITORING=true:
export MONITORING_VALUES="-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml"
```
<!-- llm-d-cicd:skip end -->
<!-- guide:deploy.monitoring_values end -->

**(Rebellions NPU only) Layer the NPU router values**, which list every decode rank port:

<!-- guide:deploy.accelerator_values start -->
<!-- variants:start -->
<details data-when="ACCELERATOR_TYPE=npu">
<summary><b>Rebellions NPU</b></summary>

<!-- llm-d-cicd:skip start -->
```bash
export ACCELERATOR_VALUES="-f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/npu.rbln.values.yaml"
```
<!-- llm-d-cicd:skip end -->

</details>
<!-- variants:end -->
<!-- guide:deploy.accelerator_values end -->

**Deploy the router** in [Standalone Mode](../../docs/architecture/core/router/proxy.md), with an Envoy sidecar in front of the router. The release name `${GUIDE_NAME}` is mandatory: the `InferencePool` selector matches a guide label that pairs with this release. To front the router with a Kubernetes Gateway instead, see Gateway Mode in the [Optimized Baseline](../optimized-baseline/README.md#1-deploy-the-llm-d-router).

<!-- guide:deploy.standalone start -->
```bash
helm install ${GUIDE_NAME} \
  ${ROUTER_STANDALONE_CHART} \
  -f ${ROUTER_BASE_VALUES} \
  ${MONITORING_VALUES} \
  -f ${ROUTER_VALUES} \
  ${ACCELERATOR_VALUES} \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```
<!-- guide:deploy.standalone end -->

### 2. Deploy the Model Server

For model sources, caching, and startup optimization, see the [Model Loading and Startup Acceleration operations guide](../../docs/operations/startup/model-loading-and-startup.md).

**Apply the Kustomize overlay** for your accelerator and model server. Each overlay deploys a prefill role (pods labeled `llm-d.ai/role=prefill`) and a decode role (`llm-d.ai/role=decode`, with the routing sidecar in front of the engine): the `prefill` and `decode` roles of a `DisaggregatedSet` on NVIDIA GPU, two `Deployments` elsewhere. See [Configure the environment](#configure-the-environment) for the `INFRA_PROVIDER` values of each accelerator.

<!-- tabs:start group=modelserver -->
<details open>
<summary><b>Default (NVIDIA GPU, AMD, Intel XPU)</b></summary>

<!-- guide:deploy.modelserver.standard[0] start -->
```bash
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}/
```
<!-- guide:deploy.modelserver.standard[0] end -->

The GPU overlays enable tool calling and reasoning parsing with `--enable-auto-tool-choice --tool-call-parser=openai --reasoning-parser=openai_gptoss`.

</details>
<details data-when="ACCELERATOR_TYPE=tpu/v6,tpu/v7">
<summary><b>Google TPU</b></summary>

<!-- guide:deploy.modelserver.tpu[0] start -->
<!-- llm-d-cicd:skip start -->
```bash
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}/
```
<!-- llm-d-cicd:skip end -->
<!-- guide:deploy.modelserver.tpu[0] end -->

The TPU overlays use the `TPUConnector` (v6e) or `TPUConnectorHMA` (TPU7x) KV connector from `tpu_inference` in place of `NixlConnector`, and run on GKE only (`INFRA_PROVIDER=gke`). The NVIDIA GPU `gke` platform requirements in [Prerequisites](#prerequisites) (GPU DRA / DRANet) do not apply.

**Requirements:**

- **TPU v6e**: nodes with `2x4` topology (`tpu-v6e-slice`, 8 chips per node, 1 core per chip). Each prefill and decode pod requests all 8 chips (`google.com/tpu: 8`).
- **TPU7x**: nodes with `2x2x1` topology (`tpu7x`, 4 chips per node, 2 cores per chip). Each prefill and decode pod requests 4 chips (`google.com/tpu: 4`).
- **Boot disk**: the TPU7x model is 406 GB on disk; size the TPU node boot disk so that this much space remains free above the kubelet ephemeral-storage eviction threshold, or the pod is evicted during the first download.

**Configuration notes:**

- Model weights are cached on the node under `/var/cache/huggingface` (a `hostPath` volume, as in the GKE GPU overlays), so restarts and re-creations of a pod do not download them again.

**Known issues:**

> [!NOTE]
> The TPU7x overlays pin `vllm/vllm-tpu:v0.26.0` through the `tpu-vllm/release-v0.26.0` image component. In `v0.27.0` through `v0.29.0` the vLLM scheduler reads `connector._kv_transfer_config`, which the bundled `TPUConnectorHMA` never initializes, and EngineCore fails at startup. The fix is [tpu-inference#3566](https://github.com/vllm-project/tpu-inference/pull/3566); the pin is removed once a `vllm-tpu` release includes it. The TPU v6e overlay uses the non-HMA `TPUConnector` and is unaffected.

- Tool calling is unavailable: the GPU overlays' `--tool-call-parser=openai` and `--reasoning-parser=openai_gptoss` are properties of `gpt-oss-120b`, not of the deployment, so the TPU overlays do not set them.
  To enable it for the Qwen models these overlays serve, add `--enable-auto-tool-choice` together with the tool parser for your variant from vLLM's [tool-calling docs](https://github.com/vllm-project/vllm/blob/main/docs/features/tool_calling.md#automatic-function-calling) (`hermes` per [Qwen's own guidance](https://qwen.readthedocs.io/en/latest/framework/function_call.html#vllm), `qwen3_xml` for Qwen3-Coder)
  and `--reasoning-parser=qwen3` for the Qwen3 series ([reasoning outputs](https://github.com/vllm-project/vllm/blob/main/docs/features/reasoning_outputs.md)). Tracked in #2640.

</details>
<details data-when="ACCELERATOR_TYPE=tpu/v7-dynamic-slice">
<summary><b>Google TPU v7 (dynamic slicing)</b></summary>

<!-- guide:deploy.modelserver.dynamic_slice[0] start -->
<!-- llm-d-cicd:skip start -->
```bash
# Prefill and decode as LeaderWorkerSet groups, one 2x2x1 sub-slice per replica, admitted by Kueue
kubectl apply -n ${NAMESPACE} -f ${REPO_ROOT}/docs/infrastructure/providers/gke/dynamic-slicing/kueue-localqueue.yaml
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}/
```
<!-- llm-d-cicd:skip end -->
<!-- guide:deploy.modelserver.dynamic_slice[0] end -->

`ACCELERATOR_TYPE=tpu/v7-dynamic-slice` deploys `Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8` (`TP=8` per `2x2x1` sub-slice) with prefill and decode as `LeaderWorkerSet` groups. Kueue Topology-Aware Scheduling places each replica on a sub-slice that [GKE dynamic slicing](../../docs/infrastructure/providers/gke/dynamic-slicing/README.md) forms on demand from pre-provisioned `4x4x4` sub-blocks, instead of a statically provisioned `2x2x1` node pool.

**Requirements:**

- Pre-provisioned TPU7x `4x4x4` sub-blocks to form the sub-slices from.
- Cluster preparation, the cluster-scoped Kueue resources, and the per-pod workload requirements from the [GKE dynamic slicing](../../docs/infrastructure/providers/gke/dynamic-slicing/README.md) provider page, which also maps each slice shape to the LWS `size` and maximum TP.

**Configuration notes:**

- Once those prerequisites are in place and the router is deployed, the command above creates the `LocalQueue` and applies the overlay.
- Pods are admitted once their `Slice` resources are `ACTIVE` (`kubectl get slices -n ${NAMESPACE}`).
- Adjust `spec.replicas` of the `prefill` and `decode` LeaderWorkerSets independently for other xPyD ratios; each replica receives its own `2x2x1` sub-slice. For a worked multi-host (`2x2x2`) example, see the [aggregated dynamic-slice recipes](../optimized-baseline/README.md#2-deploy-the-model-server).

**Known issues:**

- The dynamic-slice variant is not in the nightly e2e matrix: an end-to-end run requires one full TPU7x `4x4x4` sub-block (64 chips, 16 `tpu7x-standard-4t` nodes) in an All Capacity mode reservation, which is not available to llm-d CI. The manifests are validated by kustomize dry-run in CI and were load tested on internal Google Cloud capacity during the dynamic-slicing beta.

</details>
<details data-when="ACCELERATOR_TYPE=iluvatar">
<summary><b>Iluvatar</b></summary>

<!-- guide:deploy.modelserver.iluvatar[0] start -->
<!-- llm-d-cicd:skip start -->
```bash
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}/
```
<!-- llm-d-cicd:skip end -->
<!-- guide:deploy.modelserver.iluvatar[0] end -->

The `iluvatar` overlay runs vLLM on Iluvatar BI-V150 with `IluNixlConnector`, Iluvatar's fork of vLLM's `NixlConnector`, and `kv_buffer_device=cuda` (KV stays in VRAM).

**Requirements:**

- The Iluvatar device plugin (ix-device-plugin) exposing `iluvatar.com/gpu`. With its default `splitboard: false`, `iluvatar.com/gpu` counts boards.
- CUDA-aware UCX transports: `UCX_TLS=cuda_copy,cuda_ipc,tcp,self,posix,sysv` plus `UCX_CUDA_IPC_ENABLE_SAME_PROCESS=y`, set by the overlay. Without them UCX misdetects VRAM as host memory and the prefill engine crashes (SIGSEGV) during the KV read.

**Configuration notes:**

- Each BI-V150 board is dual-die (32&nbsp;GiB per die, 64&nbsp;GiB per board), and vLLM `--tensor-parallel-size` counts CUDA devices (2 per board). The overlay requests 2 boards per role (4 CUDA devices, TP=4) and expands `IX_VISIBLE_DEVICES` from `ixsmi`.
- Decode sets `VLLM_ENFORCE_CUDA_GRAPH=1` so `--compilation-config '{"cudagraph_mode":"FULL_DECODE_ONLY","mode":0}'` is not overridden to eager.
- Prefill keeps `max-model-len` / `block-size` aligned with decode and does not enable `FULL_DECODE_ONLY`.

</details>
<details data-when="ACCELERATOR_TYPE=metax">
<summary><b>MetaX</b></summary>

<!-- guide:deploy.modelserver.metax[0] start -->
<!-- llm-d-cicd:skip start -->
```bash
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}/
```
<!-- llm-d-cicd:skip end -->
<!-- guide:deploy.modelserver.metax[0] end -->

The `metax` overlay runs vLLM on MetaX C500X over `NixlConnector` and the llm-d routing sidecar. It is a compatibility configuration, not a production xPyD sizing example.

**Requirements:**

- The MetaX device plugin exposing `metax-tech.com/gpu`.
- The public MetaX vLLM image (`ghcr.io/project-hami/vllm-metax`); air-gapped sites can retag it from a private registry.
- A pod network that allows Prefill↔Decode **TCP 5600** (NIXL side channel) in addition to HTTP 8000/8200. There is no RDMA requirement; TCP is enough for functional validation.

**Configuration notes:**

- UCX on this path is `maca_ipc,maca_copy,tcp`. Do not copy NVIDIA `cuda_ipc` / `cuda_copy` values.
- `kv_load_failure_policy=fail` makes a failed KV pull error out instead of decode silently recomputing the prompt (which looks like HTTP 200 without a real P/D transfer).

**Known issues:**

- Qwen3 chat completions may emit a `<think>` channel unless the client sets `chat_template_kwargs.enable_thinking=false`.

</details>
<details data-when="ACCELERATOR_TYPE=npu">
<summary><b>Rebellions NPU</b></summary>

<!-- guide:deploy.modelserver.npu[0] start -->
<!-- llm-d-cicd:skip start -->
```bash
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}/
```
<!-- llm-d-cicd:skip end -->
<!-- guide:deploy.modelserver.npu[0] end -->

The Rebellions configuration serves [MiniMax-M2.7](https://huggingface.co/MiniMaxAI/MiniMax-M2.7) with heterogeneous parallelism across the two roles. Prefill uses pipeline parallelism because it measured faster per prefill chunk than data parallelism on the same four NPUs.

**Requirements:**

- The [RBLN NPU Operator](https://docs.rbln.ai/latest/software/system_management/kubernetes/about_npu_operator.html).
- A network DRA driver publishing a `dranet` DeviceClass, so each role can claim a RoCE VF alongside its NPUs. The DeviceClass config has to give that VF an IPv4 address (for example `interface.dhcp`); without one the NIXL side channel cannot bind.
- Kubernetes 1.34 or later for the `resource.k8s.io/v1` DRA APIs.
- Nodes that can place four NPUs and one RoCE VF on a single NUMA node. A pod whose node cannot supply them stays Pending on `0/3 nodes are available: 1 cannot allocate all claims`.

**Configuration notes:**

| Parameter | Prefill | Decode |
| --- | --- | --- |
| Parallelism | Pipeline, 4 stages | Data, 4 ranks, expert parallel on |
| NPUs | 4 | 4 |
| API servers per pod | 1 (port 8000) | 4 (ports 8200-8203, fronted by the sidecar on 8000-8003) |
| `--num-gpu-blocks-override` | 201 | 51 |
| `--max-num-seqs` | 4 | 4 |

- Both roles share `--block-size=4096`, automatic prefix caching, and the on-device sampler. Neither sets `--max-model-len`, since the model already declares its 204800-token context.
- Neither sets `--kv-cache-dtype` either: the runtime accepts `fp8` here, but the `--num-gpu-blocks-override` counts above were measured at the backend default, and halving the bytes per block would leave them describing something else.
- KV transfer uses `RblnNixlConnector` (NIXL) with `kv_buffer_device=rbln`. Expert parallelism is on for decode only: a pipeline-parallel prefill rank holds one stage, so there is no expert group to split.
- Because the decode role runs one API server per data-parallel rank, the router must be given every rank port: with `ACCELERATOR_TYPE=npu`, the [router step](#1-deploy-the-llm-d-router) layers [`router/npu.rbln.values.yaml`](./router/npu.rbln.values.yaml) over the guide's own values file.
- **NUMA alignment**: each role claims four NPUs and one RoCE VF, and NIXL moves KV blocks over that VF. The claim constrains all five devices to one NUMA node on `resource.kubernetes.io/numaNode`, which both the NPU driver and dranet advertise. Without that constraint the scheduler may pair a NUMA-1 NPU with a NUMA-0 VF: the pods reach ready and the transfer is what fails, with `no usable data-plane RoCE NIC on numa=1`.

**Known issues:**

- Neither the DRA allocation nor the NIXL transfer can be checked by a server dry-run against a cluster without these DeviceClasses. Both need a run on the real hardware.

</details>
<!-- tabs:end -->

### 3. Enable Monitoring (optional)

**(Optional) Deploy the monitoring resources for model servers** (requires installing the monitoring stack mentioned in [Prerequisites](#prerequisites) and `MONITORING=true` when deploying the router):

<!-- guide:deploy.monitoring start -->
```bash
# only when MONITORING=true:
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring-pd
```
<!-- guide:deploy.monitoring end -->

### 4. Observability & Troubleshooting

Once monitoring is enabled, use the signals below to operate P/D disaggregation. This section covers the metrics that matter **for this path** and how to read them; full metric definitions live in the [metric reference](../../docs/operations/observability/metrics.md#metric-reference) and ready-to-run queries in the [PromQL reference](../../docs/operations/observability/promql.md).

In a P/D deployment the prefill and decode pools scale and fail independently, and every decode step depends on a KV transfer from a prefill worker over NIXL. Most problems show up as an **imbalance between the two pools** or as **KV-transfer stalls**, so watch them as a pair rather than as a single aggregate.

#### Key metrics for this path

| Signal | Why it matters for P/D | Where to look |
|--------|------------------------|---------------|
| Prefill worker utilization (`vllm:num_requests_running{pod=~".*prefill.*"}`) | Prefill is short and bursty. Sustained saturation here means prompts queue before decode can even start, inflating TTFT | [PromQL → Prefill/Decode](../../docs/operations/observability/promql.md#prefilldecode-disaggregation) |
| Decode KV cache utilization (`vllm:kv_cache_usage_perc{pod=~".*decode.*"}`) | Decode holds KV for the full generation. Above ~0.9 the decode pool preempts or rejects, so decode — not prefill — is usually the scaling bottleneck | [PromQL → Prefill/Decode](../../docs/operations/observability/promql.md#prefilldecode-disaggregation) |
| P/D decision ratio (`llm_d_epp_disagg_decision_total`) | Confirms the router is actually splitting prefill and decode (`decision_type="prefill-decode"`). A ratio drifting toward `decode-only` means requests are falling back to aggregated serving | [PromQL → Prefill/Decode](../../docs/operations/observability/promql.md#prefilldecode-disaggregation) |
| EPP scheduler e2e latency (`llm_d_epp_scheduler_e2e_duration_seconds`) | Rising scheduler latency with healthy pools points at the routing layer, not the model servers | [PromQL → Tier 1](../../docs/operations/observability/promql.md) |
| TTFT vs. ITL split (`vllm:time_to_first_token_seconds`, `vllm:inter_token_latency_seconds`) | TTFT regressions localize to prefill or KV transfer; ITL regressions localize to decode. Splitting them tells you which pool to investigate | [Metrics → vLLM](../../docs/operations/observability/model-server-metrics.md#vllm) |

> SGLang deployments expose the equivalent signals under `sglang_*` (`sglang_num_running_reqs`, `sglang_token_usage`); the PromQL reference lists both.

#### Common failure modes

- **TTFT regression, decode healthy** — prefill pool is saturated or KV transfer is stalling. Check prefill utilization and TTFT together; if prefill is idle but TTFT is high, suspect NIXL transfer (see the [SGLang operations doc](../../docs/operations/disaggregation/sglang.md) for the prefill-side KV-strand caveat).
- **ITL regression, prefill healthy** — decode pool is the bottleneck. Check decode KV cache utilization; sustained values near 1.0 mean the decode role needs more replicas or a larger TP degree.
- **Both pools underutilized but latency high** — routing problem. Check the P/D decision ratio and EPP scheduler e2e latency before touching the model servers.
- **`cks-mooncake`: no RDMA in pod** — check that `rdma/ib` appears in the node's allocatable resources (`kubectl describe node`) and that the RDMA device plugin is running.
- **`cks-mooncake`: MooncakeConnector fails to initialize** — verify that `mooncake-transfer-engine` is installed in the image (`pip show mooncake-transfer-engine` inside the pod) and that the bootstrap port is free.
- **`cks-mooncake`: KV transfer failures** — confirm that the prefill and decode pods can reach each other over the RDMA network.

For alert rules covering these signals, see [Alerting](../../docs/operations/observability/alerting.md).

## Operating the DisaggregatedSet

The NVIDIA GPU overlays run prefill and decode as one LWS [DisaggregatedSet](https://lws.sigs.k8s.io/docs/concepts/disaggregatedset/) (`pd-disagg-vllm`, or `pd-disagg-sglang` for SGLang) with `groupIdentity: Hash`.
This guide ships `slices: 1` with one prefill and one decode replica; each role of each slice runs as its own LeaderWorkerSet (of size 1 here, so every pod is a leader).
Raise the per-role `replicas` to change the xPyD ratio, or `slices` to add complete, independently rolled copies of the topology.
Scaling, rollouts, placement policy, router slice affinity, and per-role autoscaling are covered in [Disaggregated Serving: Operations (DisaggregatedSet)](../../docs/operations/disaggregation/disaggregatedset.md).

## Verification

### 1. Get the IP of the Proxy

<!-- guide:verify.endpoint.standalone start -->
```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```
<!-- guide:verify.endpoint.standalone end -->

### 2. Send Test Requests

**Send a completion request from a temporary pod inside the cluster** (`MODEL` must be the model your accelerator serves, see [Supported Accelerators and Model Servers](#supported-accelerators-and-model-servers)):

<!-- guide:verify.tests.request start -->
```bash
kubectl run curl-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'curl -sS -X POST "http://${IP}/v1/completions" -H "Content-Type: application/json" -d "{\"model\": \"${MODEL}\", \"prompt\": \"How are you today?\"}"'
```
<!-- guide:verify.tests.request end -->

### 3. Verify P/D disaggregation

A served request only proves the stack is up: the decode pod's routing sidecar can also run a request end to end on its own. To confirm that requests are actually split, send a few requests with a long prompt, then check that the prefill pod processed them and the decode pod received their KV cache from it.

**Send 5 requests with a ~1k-token prompt:**

<!-- guide:verify.tests.pd_requests start -->
```bash
# 5 requests with a ~1k-token prompt: each is prefilled on a prefill
# pod, and its KV cache is transferred to a decode pod
kubectl run pd-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'P=$(for i in $(seq 1 60); do printf "The prefill pod computes the KV cache and the decode pod pulls it to generate tokens. "; done)
    for i in $(seq 1 5); do
      curl -sS -o /dev/null -w "request ${i}: HTTP %{http_code}\n" -X POST "http://${IP}/v1/completions" \
        -H "Content-Type: application/json" \
        -d "{\"model\": \"${MODEL}\", \"prompt\": \"${P} Question ${i}: what is llm-d?\", \"max_tokens\": 16}"
    done'
```
<!-- guide:verify.tests.pd_requests end -->

**Read the request and KV-transfer counters** of the prefill and decode pods:

<!-- guide:verify.tests.pod_metrics start -->
```bash
# Request and KV-transfer counters of the prefill and decode pods, read
# through the Kubernetes API server proxy (no port-forward needed).
# Prefill engines serve on port 8000; decode engines on 8200, behind
# the routing sidecar on 8000.
for role in prefill decode; do
  port=8000; [ "${role}" = decode ] && port=8200
  for pod in $(kubectl get pods -n ${NAMESPACE} -l llm-d.ai/guide=${GUIDE_NAME},llm-d.ai/role=${role} -o jsonpath='{.items[*].metadata.name}'); do
    echo "== ${role}: ${pod}"
    kubectl get --raw "/api/v1/namespaces/${NAMESPACE}/pods/${pod}:${port}/proxy/metrics" \
      | grep -E '^(vllm:(request_success_total|prompt_tokens_total|external_prefix_cache_hits_total)|sglang:(num_requests_total|prompt_tokens_total))' || true
  done
done
```
<!-- guide:verify.tests.pod_metrics end -->

On Rebellions NPU the decode pod runs one API server per data-parallel rank (ports 8200-8203); the loop reads rank 0, which serves a share of the requests.

What to expect:

<!-- tabs:start group=engine -->
<details open>
<summary><b>vLLM</b></summary>

The prefill pods' `vllm:request_success_total` counts the requests (prefill runs them with `max_tokens=1`), and the decode pods' `vllm:external_prefix_cache_hits_total` grows by roughly the prompt length of each request: those tokens were loaded through the KV transfer connector instead of being recomputed.

</details>
<details>
<summary><b>SGLang</b></summary>

`sglang:num_requests_total` and `sglang:prompt_tokens_total` grow on both the prefill and the decode pods: the prefill pod computes each prompt, and the decode pod receives its KV cache over NIXL.

</details>
<!-- tabs:end -->

If the prefill pods count no requests, the router is not disaggregating: check that the pods carry the `llm-d.ai/role` labels (on NVIDIA GPU, also that the DisaggregatedSet's LeaderWorkerSets are ready: `kubectl get leaderworkerset -n ${NAMESPACE}`) and the router logs (`kubectl logs -n ${NAMESPACE} deploy/${GUIDE_NAME}-epp`). If prefill counts the requests but decode reports no external prefix-cache hits, the KV transfer is failing and decode recomputes the prompt: check the decode pod's logs for connector errors.

**(Optional) Read the router's P/D decisions** (`MONITORING=true` only: without the monitoring values the router's metrics endpoint requires authentication). Each request disaggregated by the router counts under `decision_type="prefill-decode"`:

<!-- guide:verify.tests.router_metrics start -->
```bash
# only when MONITORING=true:
# P/D decisions taken by the router, read through the API server service proxy
kubectl get --raw "/api/v1/namespaces/${NAMESPACE}/services/${GUIDE_NAME}-epp:9090/proxy/metrics" \
  | grep -E '^llm_d_epp_disagg_decision_total' || true
```
<!-- guide:verify.tests.router_metrics end -->

Performance benchmarks for this configuration are not part of this guide: they live with the model-specific guides.

## Cleanup

To remove the deployed components:

<!-- guide:cleanup.modelserver start -->
```bash
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}
```
<!-- guide:cleanup.modelserver end -->

On NVIDIA GPU, deleting the DisaggregatedSet cascades to its LeaderWorkerSets, their pods, and per-slice Services. For TPU dynamic sub-slices, delete the overlay (the `LeaderWorkerSet` resources) before removing any node pools so that Kueue releases the `Slice` resources it created.

<!-- guide:cleanup.rest start -->
```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}

kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring-pd --ignore-not-found=true
```
<!-- llm-d-cicd:skip start -->
```bash
kubectl delete namespace ${NAMESPACE}
```
<!-- llm-d-cicd:skip end -->
<!-- guide:cleanup.rest end -->
