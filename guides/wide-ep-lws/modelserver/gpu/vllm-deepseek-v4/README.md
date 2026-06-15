# DeepSeek-V4-Pro on GB200 (wide-EP)

Deploys DeepSeek-V4-Pro with vLLM P/D disaggregation (NIXL) in a wide expert-parallel
pattern on NVIDIA GB200 NVL72, using LeaderWorkerSets.

These manifests were tested on Oracle Cloud Infrastructure (OCI). Storage and DRA may need
to be adapted to your environment.

This recipe reuses the [wide-ep-lws guide](../../../README.md) for the router/gateway and
shared prerequisites (namespace, HF token secret, LeaderWorkerSet controller). The notes
below cover only what is specific to this deployment.

## Prerequisites

In addition to the [wide-ep-lws prerequisites](../../../README.md#prerequisites):

* **NVIDIA DRA driver for GPUs.** Wide-EP spans multiple nodes over GB200's cross-node
  NVLink (MNNVL) fabric, which is provisioned through a [`ComputeDomain`](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/dra-cds.html#computedomains-multi-node-nvlink-simplified)
  resource. The cluster must have the NVIDIA DRA driver installed and the `ComputeDomain`
  CRD present:

  ```bash
  kubectl get crd computedomains.resource.nvidia.com
  ```

  If this returns `NotFound`, install the [NVIDIA DRA driver](https://github.com/NVIDIA/k8s-dra-driver-gpu)
  before continuing. The recipe ships its own `ComputeDomain` CR
  (`providers/oci/compute-domain.yaml`); applying a deployment creates it and the workers
  claim a channel from it, so no manual fabric setup is needed.

## Deploy the Model Server

Each deployment is a different prefill : decode operating point. Pick one and apply it:

| Deployment                          | Layout                                | Nodes / GPUs |
| ----------------------------------- | ------------------------------------- | ------------ |
| `oci-mid-curve`                     | 1 prefill : 1 decode (DEP8 each)      | 4 / 16       |
| `oci-high-tpt`                      | 2 prefill : 1 decode (DEP8 each)      | 6 / 24       |
| `oci-max-tpt`                       | 3 prefill : 1 decode (DEP8 each)      | 8 / 32       |
| `oci-3p2d-dep8-dep16-flashinfer`    | 3 prefill (DEP8) : 2 decode (DEP16), flashinfer decode | 14 / 56 |

```bash
kubectl apply -n ${NAMESPACE} -k deployments/${DEPLOYMENT}
```

## Verification

Follow the [Verification steps in the wide-ep-lws guide](../../../README.md#verification),
using model `deepseek-ai/DeepSeek-V4-Pro` in the request body.

## Cleanup

```bash
kubectl delete -n ${NAMESPACE} -k deployments/${DEPLOYMENT}
```
