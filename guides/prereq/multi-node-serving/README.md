# Multi-Node Serving Prerequisites

## (Optional) Install LeaderWorkerSet for multi-host inference

The LeaderWorkerSet (LWS) Kubernetes workload controller specializes in deploying serving workloads where each replica is composed of multiple pods spread across hosts, specifically accelerator nodes. llm-d defaults to LWS for deployment of multi-host inference for rank to pod mappings, topology aware placement to ensure optimal accelerator network performance, and all-or-nothing failure and restart semantics to recover in the event of a bad node or accelerator.

Use the [LWS installation guide](https://lws.sigs.k8s.io/docs/installation/) to install 0.7.0 or newer when deploying an llm-d guide using LWS. We provide an [`install-lws.sh`](./install-lws.sh) script:

    ./install-lws.sh          # install
    ./install-lws.sh delete   # uninstall

You may override the version and namespace:

    export LWS_VERSION="0.7.0"
    export LWS_NAMESPACE="lws-system"
    ./install-lws.sh

## (Optional) Install Kueue and Kueue Populator for Topology Aware Scheduling for multi-host inference

[Kueue](https://github.com/kubernetes-sigs/kueue/tree/main) is a Kubernetes controller for job queueing. When combined with [Kueue-Populator](https://github.com/kubernetes-sigs/kueue/tree/main/cmd/experimental/kueue-populator), it can schedule a multi-host inference workload for optimal accelerator network performance.

Use the [TAS + LWS user guide](https://lws.sigs.k8s.io/docs/examples/tas/) to setup topology aware scheduling when deploying an llm-d guide using LWS.
