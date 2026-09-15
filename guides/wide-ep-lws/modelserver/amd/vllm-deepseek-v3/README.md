# DeepSeek-V3 on AMD Instinct (wide-EP, MoRI-IO P/D)

Deploys DeepSeek-V3 with vLLM P/D disaggregation in a wide expert-parallel pattern on AMD
Instinct, using LeaderWorkerSets. MoRI-EP carries the expert all-to-all and MoRI-IO carries
the prefill-to-decode KV transfer in WRITE mode, in place of DeepEP and NIXL. This is the
one backend in this guide that runs on a rail-only RDMA fabric.

Each role is one logical 16-rank engine split across two pods of 8 GPUs — DP = EP = 16,
TP = 1, `replicas: 1` with `size: 2`. Two roles, four pods, 32 GPUs on four distinct nodes.

This packaging has been deployed on 4 x 8 MI355X: the four pods land on four distinct nodes,
all 16 data-parallel ranks come up with the correct global offsets (0-7 on each leader, 8-15
on each worker), and both roles reach Ready. Storage and node placement need adapting to your
environment.

This recipe reuses the [wide-ep-lws guide](../../../README.md) for the router/gateway and
shared prerequisites (namespace, HF token secret, LeaderWorkerSet controller). The notes
below cover only what is specific to this deployment.

## Prerequisites

In addition to the [wide-ep-lws prerequisites](../../../README.md#prerequisites):

* **32 AMD Instinct GPUs on four nodes**, 8 per node. Each pod requests every GPU on its
  node and a `required` `podAntiAffinity` keeps the four pods on four distinct nodes.

* **An 8-rail RDMA fabric** with a Multus attachment per rail, 8 `amd.com/vnic`
  per pod, and every rail reachable between every pair of the four nodes. One unreachable
  rail hangs the whole 16-rank group. `base/` deliberately carries none of the
  fabric-specific configuration: a NetworkAttachmentDefinition name, RDMA device names and
  RoCE QoS values are properties of one cluster, so they come from the provider overlay
  instead. `amd-ci/patch-amd-ci-fabric.yaml` is a worked example, and the block comment in
  `base/prefill.yaml` lists every variable involved and what it does.

* **A `model-pvc` claim in the namespace, provisioned before you apply.** No overlay creates
  it. `ReadWriteMany`, `1Ti` recommended (the model is 688.7 GB); leave `storageClassName`
  unset to bind the cluster default. It may be empty; see "Model weights".

* **The AMD router override.** Set `ACCELERATOR=amd` for the
  [router install](../../../README.md#1-deploy-the-llm-d-router), which selects
  `router/amd.values.yaml`. It is required, not tuning.

## Images

`base/kustomization.yaml` pins both images.

| Role | Image | What it carries |
| --- | --- | --- |
| vLLM engine | `ghcr.io/vcave/rocm/llm-d/vllm:wide-ep-v0.2.0` | ROCm vLLM with MoRI-EP and MoRI-IO compiled in, MoRI pinned to `c22c33a72` |
| Routing sidecar | `ghcr.io/vcave/rocm/llm-d/llm-d-router-disagg-sidecar:wide-ep-v0.1.0` | llm-d-router disagg sidecar carrying [PR #2816](https://github.com/llm-d/llm-d-router/pull/2816) |

Both repositories serve anonymous pulls, so no image pull secret is needed.

The `components/images/amd-vllm/llm-d` and `components/images/routing-sidecar/nightly`
defaults will not work: neither carries MoRI, and a stock sidecar lacks the multi-pod DP
support from PR #2816.

To build an equivalent engine image from public sources: take `vllm-project/vllm` `main`,
cherry-pick [PR #51681](https://github.com/vllm-project/vllm/pull/51681) and
[PR #56073](https://github.com/vllm-project/vllm/pull/56073) (which pins MoRI to
`c22c33a72`), then build `docker/Dockerfile.rocm_base` followed by
`docker/Dockerfile.rocm --target vllm-openai` with `BASE_IMAGE` set to the first stage. For
the sidecar, build `llm-d/llm-d-router` `main` with the two commits from PR #2816 applied,
using that repo's `Dockerfile.sidecar`.

To point the overlay at your own builds instead:

```bash
cd ${REPO_ROOT}/guides/wide-ep-lws/modelserver/amd/vllm-deepseek-v3/base
kustomize edit set image ghcr.io/llm-d/llm-d-rocm=<your-registry>/<engine-repo>:<tag>
kustomize edit set image ghcr.io/llm-d/llm-d-router-disagg-sidecar=<your-registry>/<sidecar-repo>:<tag>
cd -
```

> [!CAUTION]
> `ghcr.io/vcave/llm-d/llm-d-router-disagg-sidecar:v0.1.0` is **not** this recipe's sidecar
> despite the matching version string — it is a different image by digest and does not carry
> PR #2816. Substituting it makes KV dispatch silently collapse to half the rank pairs.

## Model weights

Both roles serve the HuggingFace model id `deepseek-ai/DeepSeek-V3` with `HF_HOME` on the
`model-pvc` claim, mounted at `/model-cache`. The first pod to start downloads the weights
there; every later pod and every later run reads them from the volume.

Nothing in this recipe creates `model-pvc` — provision it in the target namespace ahead of
time, as the AMD P/D guide does with the shared
[`model-cache` component](../../../../recipes/modelserver/components/model-cache/). Use
`ReadWriteMany` and `1Ti` rather than that component's 200Gi default: DeepSeek-V3 is 688.7 GB
across 163 shards in hub layout.

The claim name is shared across AMD nightly namespaces, which is harmless here — a namespace
whose `model-pvc` already holds this model simply skips the download.

## Deploy the Model Server

| Overlay | Use |
| --- | --- |
| `base` | Portable recipe. Not deployable on its own: it attaches no rail interfaces and sets no fabric configuration |
| `amd-ci` | AMD CI cluster |

For another cluster, copy `amd-ci/` and edit it — at minimum the NAD name and namespace, the
`MORI_RDMA_DEVICES` names, and the RoCE service level and traffic class.

```bash
export MODEL=deepseek-ai/DeepSeek-V3
kubectl apply -n ${NAMESPACE} -k ${OVERLAY}
```

Startup takes tens of minutes: 688.7 GB of weights across 8 local ranks per pod, a cross-pod DP
rendezvous, AITER JIT compilation and graph capture. The first run also downloads the model.

## Verification

Follow the [Verification steps in the wide-ep-lws guide](../../../README.md#verification),
using model `deepseek-ai/DeepSeek-V3` in the request body.

Under load, all 16 engines should be non-zero on both roles. `4 of 8` is the signature of a
sidecar without PR #2816.

Uneven load across prefill's ranks is expected rather than a misconfiguration. Prefill has no
sidecar, so its eight API servers share port 8000 via `SO_REUSEPORT`, and
[vllm-project/vllm#24308](https://github.com/vllm-project/vllm/issues/24308) reports the
kernel concentrating load on 3-4 of them when requests come from few source addresses — which
is this case, since the endpoint picker reaches prefill over pooled keep-alive connections.

## Cleanup

```bash
kubectl delete -n ${NAMESPACE} -k ${OVERLAY}
```

`model-pvc` is not part of any overlay, so it survives this and the next run reuses the
downloaded weights.
