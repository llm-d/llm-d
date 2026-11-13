# Intel XPU ModelExpress

For a comprehensive overview of ModelExpress P2P weight transfer, see the
[ModelExpress P2P guide](./README.md). This guide covers the Intel XPU variant: it distributes
ModelExpress weights between Intel Arc Pro B60 pods through host-staged TCP and Level Zero copies:

```text
source XPU -> ze_copy -> host -> TCP -> host -> ze_copy -> destination XPU
```

This overlay validates **functional XPU P2P** over TCP, not XPU-direct RDMA. Do not add `rc_mlx5`,
`rc`, `dc`, `ib`, or another verbs transport to `UCX_TLS` or `NIXL_UCX_TLS`.

## Validated stack

The XPU image recipe pins the stack that passed the B60 functional tests:

| Component | Version |
| --- | --- |
| Base image | `docker.io/vllm/vllm-openai-xpu:v0.26.0` |
| PyTorch | `2.12.0+xpu` |
| vLLM | `0.26.0+xpu` |
| UCX | commit `940c1c1d948873d64cba05adc756bae645eb618f`, built with verbs and Level Zero |
| NIXL | `1.3.0`, commit `5949ccf9f55ad60f6d7f79c28200bde152cbb203` |
| ModelExpress | `0.5.0`, commit `0406ac16d5daeef985de1bf4d09c9f0a5e188c1a` |

`image/Dockerfile.xpu` validates package versions, the ModelExpress XPU backend, UCX Level Zero
modules, and NIXL plugin linkage during the image build.

## Prerequisites

In addition to the main guide prerequisites:

- Kubernetes 1.35 or newer with DRA `resource.k8s.io/v1`.
- Intel Resource Driver for Kubernetes exposing the `gpu.intel.com` DeviceClass.
- At least two Intel XPU nodes for the default cross-node one-seed/one-receiver deployment.
- Pod-to-pod TCP connectivity between the model-server pods.

The overlay uses one GPU DRA claim per pod. It deliberately does not request an RDMA NIC or
verbs device.

## Build the XPU image

```bash
export REPO_ROOT=$(realpath "$(git rev-parse --show-toplevel)")
export GUIDE_NAME=modelexpress-p2p
export MODELSERVER_IMAGE=<your-registry>/modelexpress-p2p-vllm-xpu:latest

DOCKER_BUILDKIT=1 docker build \
  -f "${REPO_ROOT}/guides/${GUIDE_NAME}/image/Dockerfile.xpu" \
  -t "${MODELSERVER_IMAGE}" \
  "${REPO_ROOT}/guides/${GUIDE_NAME}/image"
docker push "${MODELSERVER_IMAGE}"
```

Point the XPU overlay at the resulting image:

```bash
cd "${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/xpu/vllm/base"
kustomize edit set image REPLACE_MODEL_SERVER_IMAGE="${MODELSERVER_IMAGE}"
cd -
```

## Deploy

Follow the main guide to create the namespace, install ModelExpress CRDs and server, create the
Hugging Face secret, and optionally deploy the router. Use Qwen3-0.6B for this XPU overlay:

```bash
export MODEL=Qwen/Qwen3-0.6B

kubectl apply -n "${NAMESPACE}" -k \
  "${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/xpu/vllm/base"
kubectl rollout status -n "${NAMESPACE}" \
  deploy/modelexpress-p2p-intel-xpu-vllm-decode \
  --timeout=30m
```

The portable `base` overlay uses the default Kubernetes pod network. On clusters whose management
network is slow, use the optional DRANET overlay to attach the high-speed NIC while retaining the
safe host-staged transport:

```bash
kubectl apply -n "${NAMESPACE}" -k \
  "${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/xpu/vllm/dranet"
```

The DRANET overlay requires a `dranet-rdma` DeviceClass. It retains the base Intel XPU claim and
allocates the RDMA-capable NIC through a separate claim. The host-staged path does not require
GPU/NIC peer-DMA alignment. Its startup wrapper selects the fastest UP interface and exports that
interface as `UCX_NET_DEVICES`; the selected DRANET device must have a usable cross-node IP
configuration.

The overlay recreates `/dev/dri/by-path` symlinks for only the XPU render devices allocated to the
Pod. This keeps oneCCL device discovery working after the network DRA injection without mounting
the host's complete device directory.

`UCX_TLS=tcp,ze_copy` remains unchanged: the NIC carries TCP and never reads XPU VRAM directly.

The selector prints the chosen interface and link speed before vLLM starts. If a site has multiple
equally fast data interfaces, replace the selector with an explicit `UCX_NET_DEVICES` value in a
site-specific overlay. Do not add a verbs transport.

The overlay sets:

```text
NIXL_UCX_TLS=tcp,ze_copy
UCX_TLS=tcp,ze_copy
UCX_MEMTYPE_CACHE=0
UCX_TCP_TX_SEG_SIZE=16M
UCX_TCP_RX_SEG_SIZE=16M
```

`UCX_MEMTYPE_CACHE=0` is required for reliable B60 memory-type detection. The larger TCP segments
avoid excessive synchronous Level Zero copy submissions.

### Restricted-egress clusters

The seed must read the checkpoint before it can publish tensors. If model-server pods cannot reach
Hugging Face, either pre-populate a read-only model volume or add `HTTP_PROXY`, `HTTPS_PROXY`, and
`NO_PROXY` to the model-server container. `NO_PROXY` must include the Kubernetes service domain,
service CIDR, pod CIDR, loopback addresses, and the ModelExpress server so metadata and P2P traffic
do not traverse the external proxy.

For an offline P2P validation, start one seed with the complete checkpoint and wait for its
ModelMetadata status to become Ready. Then scale to two with only config and tokenizer files
available to the receiver. The receiver must log a P2P transfer; do not copy the weight file to its
model volume.

## Verify

Confirm that both replicas are Ready and each received one XPU:

```bash
kubectl get pods -n "${NAMESPACE}" \
  -l llm-d.ai/guide=modelexpress-p2p,llm-d.ai/accelerator-variant=xpu
kubectl get resourceclaims -n "${NAMESPACE}"
```

Find the newer pod, which is normally the receiver, and inspect its transfer:

```bash
RECEIVER=$(kubectl get pods -n "${NAMESPACE}" \
  -l llm-d.ai/guide=modelexpress-p2p,llm-d.ai/accelerator-variant=xpu \
  --sort-by=.metadata.creationTimestamp \
  -o jsonpath='{.items[-1].metadata.name}')

kubectl logs -n "${NAMESPACE}" "${RECEIVER}" -c modelserver \
  | grep -E "MxModelLoader|Receiving|Transfer complete|Loading weights from disk"
```

Acceptance criteria:

1. The source publishes READY metadata and the receiver selects the ModelExpress P2P strategy.
2. The receiver logs `Transfer complete` and does not load model weights from disk.
3. UCX configuration contains only `tcp,ze_copy`; no `rma(rc_mlx5/...)` data lane is present.
4. Both pods return successful inference for `Qwen/Qwen3-0.6B`.
5. Both pods remain Ready with zero restarts and each sees exactly one assigned XPU.

The portable GPU-only DRA overlay transferred 339 Qwen3-0.6B tensors (1.20 GB) across two B60
nodes in 10.73 seconds, or 0.9 Gbps, through a 1 Gbps management network. The DRANET path avoids
that line-rate ceiling: the same transfer completed in 2.20 seconds, or 4.4 Gbps, on the 400 Gbps
interface. Both are functional results, not direct-RDMA bandwidth.

ModelExpress currently calls its P2P loader strategy `rdma` even when NIXL/UCX is restricted to
TCP. Use the selected UCX lanes and environment, not the strategy name alone, to identify the
actual transport.

## Cleanup

```bash
kubectl delete -n "${NAMESPACE}" -k \
  "${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/xpu/vllm/base"
```

Use `modelserver/xpu/vllm/dranet` instead of `base` in the cleanup command if that overlay was
deployed.
