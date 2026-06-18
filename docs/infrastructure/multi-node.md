# Multi-Node Serving Orchestration

## (Optional) Install LeaderWorkerSet for multi-host inference

The LeaderWorkerSet (LWS) Kubernetes workload controller specializes in deploying serving workloads where each replica is composed of multiple pods spread across hosts, specifically accelerator nodes. llm-d defaults to LWS for deployment of multi-host inference for rank to pod mappings, topology aware placement to ensure optimal accelerator network performance, and all-or-nothing failure and restart semantics to recover in the event of a bad node or accelerator.

Use the [LWS installation guide](https://lws.sigs.k8s.io/docs/installation/) to install the recommended 0.9.0 release when deploying an llm-d guide using LWS.

### Install LWS

To install LWS release 0.9.0 into the `lws-system` namespace:

```bash
export LWS_VERSION="0.9.0"
export LWS_NAMESPACE="lws-system"

helm install lws oci://registry.k8s.io/lws/charts/lws \
  --version "${LWS_VERSION}" \
  --namespace "${LWS_NAMESPACE}" \
  --create-namespace
```

> [!WARNING]
> If you installed LWS 0.7.0 or earlier with Helm, do not upgrade directly to 0.9.0. Helm may delete the LWS CRD and cascade-delete existing `LeaderWorkerSet` resources during upgrade; see [kubernetes-sigs/lws#880](https://github.com/kubernetes-sigs/lws/issues/880).

### Uninstall LWS

To uninstall LWS:

```bash
export LWS_NAMESPACE="lws-system"

helm uninstall lws --namespace "${LWS_NAMESPACE}"
```

## (Optional) Install Kueue and Kueue Populator for Topology Aware Scheduling for multi-host inference

[Kueue](https://github.com/kubernetes-sigs/kueue/tree/main) is a Kubernetes controller for job queueing. When combined with [Kueue-Populator](https://github.com/kubernetes-sigs/kueue/tree/main/cmd/experimental/kueue-populator), it can schedule a multi-host inference workload for optimal accelerator network performance.

Use the [TAS + LWS user guide](https://lws.sigs.k8s.io/docs/examples/tas/) to setup topology aware scheduling when deploying an llm-d guide using LWS.
