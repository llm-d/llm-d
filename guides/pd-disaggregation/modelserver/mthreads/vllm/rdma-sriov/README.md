# MThreads vLLM P/D over RDMA (SR-IOV VF)

Example overlay that runs the single-host `mthreads/vllm/base` topology
(1 prefill `TP=4` + 1 decode `TP=4`, Qwen3-32B) with `MooncakeConnector` over
RDMA instead of the TCP transport used by the base.

The overlay only changes the transport. The model, ports, probes and the
routing sidecar are inherited from `../base`; the RDMA additions are:

- `mooncake_protocol=rdma` plus a per-role `device_name` in the connector's
  `kv_connector_extra_config`;
- one SR-IOV VF NAD per pod and the matching Multus annotation;
- a `rdma/rdma_shared_device` request and limit;
- `IPC_LOCK`, which `../base` already grants: with it, RDMA memory
  registration (`ib_umem_get()`) does not enforce `RLIMIT_MEMLOCK`, so the
  overlay needs no `ulimit` wrapper.

## Prerequisites

- An RDMA device plugin advertising `rdma/rdma_shared_device` (the resource
  name differs between plugins; adjust the patches if yours is e.g.
  `rdma/hca`).
- SR-IOV CNI and at least two VFs on the RoCE network, one per pod. The VF
  addresses must be routable between the prefill and decode pods.
- Both pods on the same node. The topology is single-host; pin them with a
  `nodeSelector` (or an equivalent scheduling constraint) for your cluster.
- The LeaderWorkerSet controller with the `DisaggregatedSet` API enabled: the
  base renders a `DisaggregatedSet`, so the controller creates the role pods
  (see the [P/D guide prerequisites](../../../../README.md#prerequisites)).
- A vLLM image with `MooncakeConnector` and `mooncake-transfer-engine-musa`.
- The `llm-d-hf-token` Secret, as for the base.

## Values to edit before applying

Everything below is site-specific and shipped as an example:

| File | Field | What to set |
| --- | --- | --- |
| `examples/network-attachment-definitions.yaml` | `spec.config` → `deviceID` | PCI address of the VF assigned to `vf0` / `vf1` |
| `examples/network-attachment-definitions.yaml` | `spec.config` → `ipam` | Subnet and address range of the VF network |
| `patch-prefill.yaml` | `device_name` | RDMA device of the prefill pod's VF (example: `mlx5_10`) |
| `patch-decode.yaml` | `device_name` | RDMA device of the decode pod's VF (example: `mlx5_11`) |
| `patch-*.yaml` | `rdma/rdma_shared_device` | Resource name advertised by your RDMA device plugin |
| `patch-*.yaml` | `nodeSelector` | Add one to pin both pods to the same node |

The two NADs are pinned to a single address each, which matches the base's
one prefill + one decode replica. If you raise the replica count, give each
replica its own NAD (or non-overlapping IP ranges).

## Apply

The VF networks are a cluster prerequisite and are not part of the kustomization,
so create them first:

```bash
kubectl apply -f ${REPO_ROOT}/guides/pd-disaggregation/modelserver/mthreads/vllm/rdma-sriov/examples/network-attachment-definitions.yaml
```

Deploy the router next, as in the [P/D guide](../../../../README.md#1-deploy-the-llm-d-router),
then the model servers:

```bash
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/pd-disaggregation/modelserver/mthreads/vllm/rdma-sriov/
```

## Verify

- Both roles are Ready: the `DisaggregatedSet` creates one LeaderWorkerSet per
  role, and each pod has the VF interface from its NAD.
- The vLLM log shows `The Mooncake Transfer Engine is using rdma as its
  protocol.` and the transfer engine reports `Using RDMA transport`.
- Send several requests with a prompt longer than one KV block through the
  EPP and compare the prompt/generation counters on both engines before and
  after each one: prefill accounts for the prompt plus one token, decode for
  the prompt plus the completion. The first request is slower while the RDMA
  handshake and the runner warm up.
- The prefill log then prints a `KV Transfer metrics` line with
  `Num failed transfers=0` and `Num failed recvs=0`; a ~730-token prompt moves
  about 48 MB per transfer.

A single `200` response does not prove the transfer happened: the decode pod
can also serve a request on its own. Send a few requests and check both
engines' counters rather than a single response.
