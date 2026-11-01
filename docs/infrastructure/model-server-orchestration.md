# Model Server Orchestration

llm-d deploys model servers that span several hosts or several roles with two Kubernetes SIG Apps APIs, both served by the LeaderWorkerSet (LWS) controller:

* **`LeaderWorkerSet`** runs each replica as a group of pods across hosts, for multi-host inference.
* **`DisaggregatedSet`** runs several roles, such as prefill and decode, as one versioned unit. The prefill/decode disaggregation and wide expert parallelism guides deploy their model servers this way.

The [wide expert parallelism guide](../../guides/wide-ep/README.md#grove-orchestration) can also run its NVIDIA GPU model servers on [Grove](https://github.com/ai-dynamo/grove), as an alternative to the `DisaggregatedSet`.

See [Model Server Orchestration](../architecture/model-server-orchestration/README.md) for how the two APIs work and when to use each. This page covers installing the LWS controller, Kueue for topology-aware scheduling, and Grove.

## Install the LeaderWorkerSet Controller

Install the LWS controller when deploying an llm-d guide that uses `LeaderWorkerSet` or `DisaggregatedSet`. llm-d recommends release 1.0.0; see the [LWS installation guide](https://lws.sigs.k8s.io/docs/installation/) for other installation methods.

Every install includes the `DisaggregatedSet` CRD and controller. Installing with `enableDisaggregatedSet=true`, as below, also adds the `DisaggregatedSet` validating webhook and editor, viewer, and admin ClusterRoles, which the guides that deploy a `DisaggregatedSet` expect.

### Install LWS

To install LWS release 1.0.0, with the `DisaggregatedSet` API enabled, into the `lws-system` namespace:

```bash
export LWS_VERSION="1.0.0"
export LWS_NAMESPACE="lws-system"

helm install lws oci://registry.k8s.io/lws/charts/lws \
  --version "${LWS_VERSION}" \
  --namespace "${LWS_NAMESPACE}" \
  --create-namespace \
  --set enableDisaggregatedSet=true
```

> [!NOTE]
> To upgrade an existing LWS install, apply the new release's CRDs first. `helm upgrade` never updates them, so resources that use new fields (for example `DisaggregatedSet` `groupIdentity` in 0.11.1) are rejected. See [Upgrade by Helm](https://lws.sigs.k8s.io/docs/installation/#upgrade-by-helm):
>
> ```bash
> helm pull oci://registry.k8s.io/lws/charts/lws --version "${LWS_VERSION}" --untar
> kubectl apply --server-side --force-conflicts -f lws/crds
> helm upgrade lws oci://registry.k8s.io/lws/charts/lws \
>   --version "${LWS_VERSION}" \
>   --namespace "${LWS_NAMESPACE}" \
>   --set enableDisaggregatedSet=true
> ```

> [!WARNING]
> If you installed LWS 0.7.0 or earlier with Helm, do not upgrade directly to a later release. Helm may delete the LWS CRD and cascade-delete existing `LeaderWorkerSet` resources during upgrade; see [kubernetes-sigs/lws#880](https://github.com/kubernetes-sigs/lws/issues/880).

### Uninstall LWS

To uninstall LWS:

```bash
export LWS_NAMESPACE="lws-system"

helm uninstall lws --namespace "${LWS_NAMESPACE}"
```

## (Optional) Install Kueue and Kueue Populator for Topology Aware Scheduling for multi-host inference

[Kueue](https://github.com/kubernetes-sigs/kueue/tree/main) is a Kubernetes controller for job queueing. When combined with [Kueue-Populator](https://github.com/kubernetes-sigs/kueue/tree/main/cmd/experimental/kueue-populator), it can schedule a multi-host inference workload for optimal accelerator network performance.

Use the [TAS + LWS user guide](https://lws.sigs.k8s.io/docs/examples/tas/) to setup topology aware scheduling when deploying an llm-d guide using LWS.

## (Alternative) Install Grove

[Grove](https://github.com/ai-dynamo/grove) runs multi-role, multi-node inference workloads as a single `PodCliqueSet`, with gang scheduling and topology-aware placement through a scheduler backend such as the [KAI Scheduler](https://github.com/NVIDIA/KAI-Scheduler). The wide expert parallelism guide's `grove/*` overlays deploy it in place of the `DisaggregatedSet`; they do not need LWS.

To install the Grove operator with Helm (see the [Grove installation guide](https://github.com/ai-dynamo/grove/blob/main/docs/installation.md) for the scheduler profiles, topology-aware scheduling and other options):

```bash
export GROVE_VERSION="v0.1.0-alpha.17"

helm upgrade -i grove oci://ghcr.io/ai-dynamo/grove/grove-charts \
  --version "${GROVE_VERSION}" \
  --namespace grove-system \
  --create-namespace
```

To uninstall Grove:

```bash
helm uninstall grove --namespace grove-system
```
