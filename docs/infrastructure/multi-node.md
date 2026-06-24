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

## (Optional) Install Grove for multi-host inference

[Grove](https://github.com/ai-dynamo/grove) is a Kubernetes workload controller for multi-component AI systems. Grove deploys model-server workloads with `PodCliqueSet`, `PodClique`, and `PodCliqueScalingGroup` resources, and it integrates with schedulers that support Grove `PodGang` resources.

Use Grove when deploying an llm-d guide that calls for Grove, such as the GB200 variant of the [Wide Expert Parallelism guide](../../guides/wide-ep/README-grove.md).

### Install KAI Scheduler

For Grove deployments that use gang scheduling or topology-aware scheduling, install [KAI Scheduler](https://github.com/NVIDIA/KAI-Scheduler) first:

```bash
export KAI_SCHEDULER_VERSION="<version>"

helm upgrade -i kai-scheduler \
  oci://ghcr.io/kai-scheduler/kai-scheduler/kai-scheduler \
  --namespace kai-scheduler \
  --create-namespace \
  --version "${KAI_SCHEDULER_VERSION}"
```

### Install Grove with Auto-MNNVL and TAS enabled

Install Grove from the published Helm chart and enable the operator features used by GB200/MNNVL deployments:

```bash
export GROVE_VERSION="<version>"
export GROVE_NAMESPACE="grove"

helm upgrade -i grove \
  oci://ghcr.io/ai-dynamo/grove/grove-charts \
  --namespace "${GROVE_NAMESPACE}" \
  --create-namespace \
  --version "${GROVE_VERSION}" \
  --set config.network.autoMNNVLEnabled=true \
  --set config.topologyAwareScheduling.enabled=true
```

### Install NVIDIA DRA driver for GB200/MNNVL

Grove Auto-MNNVL creates the GB200 MNNVL fabric resources, including the `ComputeDomain`, but the NVIDIA DRA driver CRDs must already be installed. Install the [NVIDIA DRA driver for GPUs](https://github.com/NVIDIA/k8s-dra-driver-gpu) before applying the Grove model-server manifest, and verify the `ComputeDomain` CRD is present:

```bash
kubectl get crd computedomains.resource.nvidia.com
```

### Verify Grove

```bash
kubectl get pods -n "${GROVE_NAMESPACE}" -l app.kubernetes.io/name=grove-operator
kubectl get crd podcliquesets.grove.io podcliques.grove.io podcliquescalinggroups.grove.io podgangs.scheduler.grove.io
```
