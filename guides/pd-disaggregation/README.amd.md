# AMD GPU well-lit Path for P/D Disaggregation

## Overview

This guide demonstrates how to deploy models using vLLM's P/D disaggregation support with RIXL. This guide has been validated on:

* a cluster with 2 8xMI300x nodes connected with RoCE networking

> WARNING: We are still investigating and optimizing performance across hardware and networking configurations

**_NOTE:_** The ROCm Attention backend must be used `ROCM_ATTN` when tensor parallelism values for prefill and decode are different. There's a PR in progress to address this issue with other attention backends.

In this example, we will demonstrate a deployment of `amd/Llama-3.3-70B-Instruct-FP8-KV`.

## P/D Best Practices

P/D disaggregation provides more flexibility in navigating the trade-off between throughput and interactivity([ref](https://arxiv.org/html/2506.05508v1)).
In particular, due to the elimination of prefill interference to the decode phase, P/D disaggregation can achieve lower inter token latency (ITL), thus
improving interactivity. For a given ITL goal, P/D disaggregation can benefit overall throughput by:

* Specializing P and D workers for compute-bound vs latency-bound workloads
* Reducing the number of copies of the model (increasing KV cache RAM) with wide parallelism

However, P/D disaggregation is not a target for all workloads. We suggest exploring P/D disaggregation for workloads with:

* Large models (e.g. Llama-70B+, not Llama-8B)
* Longer input sequence lengths (e.g 10k ISL | 1k OSL, not 200 ISL | 200 OSL)
* Sparse MoE architectures with opportunities for wide-EP

As a result, as you tune your P/D deployments, we suggest focusing on the following parameters:

* **Heterogeneous Parallelism**: deploy P workers with less parallelism and more replicas and D workers with more parallelism and fewer replicas
* **xPyD Ratios**: tuning the ratio of P workers to D workers to ensure balance for your ISL|OSL ratio

For very large models leveraging wide-EP, traffic for KV cache transfer may contend with expert parallelism when the ISL|OSL ratio is also high. We recommend starting with RDMA for KV cache transfer before attempting to leverage TCP, as TCP transfer requires more tuning of UCX under RIXL.

## Hardware Requirements

This guide expects 8 AMD GPUs of any kind, and RDMA via RoCE between all pods in the workload.

## Prerequisites

* Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md) to use this guide.
* Configure and deploy your [Gateway control plane](../prereq/gateway-provider/README.md).
* Have the [Monitoring stack](../../docs/monitoring/README.md) installed on your system.
* Create a namespace for installation.

  ```
  export NAMESPACE=llm-d-pd # or any other namespace (shorter names recommended)
  kubectl create namespace ${NAMESPACE}
  ```

* [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN` matching a valid HuggingFace token](../../helpers/hf-token.md) to pull models.

## Installation

Use the helmfile to compose and install the stack. The Namespace in which the stack will be deployed will be derived from the `${NAMESPACE}` environment variable. If you have not set this, it will default to `llm-d-pd` in this example.

### Deploy with Operators

This section covers deploying P/D disaggregation using Kubernetes operators to manage hardware resources.

#### Prerequisites

Before proceeding, ensure your Kubernetes cluster is configured with the necessary operators to expose hardware devices to your containers.

1. GPU Support

* [AMD GPU Operator](https://instinct.docs.amd.com/projects/gpu-operator/en/latest/index.html): Required to expose GPU devices to containers.

2. Network Support (NIC)

* A network operator is required to expose NIC devices. Choose the operator that matches your hardware:
    - [AMD Network Operator](https://instinct.docs.amd.com/projects/network-operator/en/main/overview.html): For AMD AINIC hardware.
    - [NVIDIA Network Operator](https://docs.nvidia.com/networking/display/kubernetes2410/nvidia+network+operator): For NVIDIA NIC hardware.

If you haven't installed these yet, follow the official [AMD GPU Installation Guide](https://instinct.docs.amd.com/projects/gpu-operator/en/latest/installation/kubernetes-helm.html) and the [AMD Network Operator Installation Guide](https://instinct.docs.amd.com/projects/network-operator/en/main/installation/kubernetes-helm.html).

#### Deployment

To enact a P/D disaggregation deployment on AMD hardware, use the `-e amd` argument to helmfile as follow:
```bash
cd guides/pd-disaggregation
helmfile apply -e amd -n ${NAMESPACE}
```

This command triggers the deployment of the disaggregated configuration described in `ms-pd/values_amd.yaml`.

**_NOTE:_** RDMA NIC resource labels used in `ms-pd/values_amd.yaml` are specific to your cluster setup. Please consult your cluster administrator for more information.

**_NOTE:_** You can set the `$RELEASE_NAME_POSTFIX` env variable to change the release names. This is how we support concurrent installs. Ex: `RELEASE_NAME_POSTFIX=pd-2 helmfile apply -e amd -n ${NAMESPACE}`

**_NOTE:_** This uses Istio as the default provider, see [Gateway Options](./README.md#gateway-options) for installing with a specific provider.

### RIXL Configuration

#### UCX Transport & Logging
RIXL uses the UCX library as its underlying transport mechanism for KV transfers. While UCX typically automates its configuration, you may need to manually tune the following environment variables if the default selections are suboptimal.

**Network & Transport Tuning**
Use these variables to specify how and where data travels across your hardware:
* UCX_TLS: Defines the allowed Transport Layer protocols (e.g., tcp, rc, ud, sm).
* UCX_IB_GID_INDEX: Specifies the Global Identifier (GID) index for InfiniBand or RoCE devices.
* UCX_IB_TRAFFIC_CLASS: Specifies the KV transfer traffic class to use for InfiniBand or RoCE devices.

**Debugging & Logging**
Use these variables to verify your configuration or troubleshoot issues:
* UCX_LOG_LEVEL: Adjusts the verbosity of UCX logs (e.g., info, debug, trace, etc..).
* UCX_PROTO_INFO: Set to 'y' to display the specific protocols and devices selected for both intra-node (local) and inter-node (remote) communications.

For more UCX customizations, please refer to the [UCX documentation](https://openucx.org/documentation/)


### Gateway options

To specify your gateway choice, you can use the `-e <gateway option>` flag, ex:

```bash
helmfile apply -e agentgateway -n ${NAMESPACE} # preferred agentgateway path
helmfile apply -e kgateway -n ${NAMESPACE}     # deprecated migration path
```

**_WARNING:_** `kgateway` is deprecated in llm-d and will be removed in the next release. Prefer `agentgateway` for new self-installed inference deployments.

To see what gateway options are supported refer to our [gateway provider prereq doc](../prereq/gateway-provider/README.md#supported-providers). Gateway configurations per provider are tracked in the [gateway-configurations directory](../prereq/gateway-provider/common-configurations/).

You can also customize your gateway, for more information on how to do that see our [gateway customization docs](../04_customizing_a_guide.md).

#### Infrastructure provider specifics

This guide uses RDMA via RoCE for disaggregated serving kv-cache transfer. The resource attributes required to configure accelerator networking are not yet standardized via [Kubernetes Dynamic Resource Allocation](https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/) and so are parameterized per infra provider in the Helm charts. If your provider has a custom setting you will need to update the charts before deploying.

### Install HTTPRoute

Follow provider specific instructions for installing HTTPRoute.

#### Install for "agentgateway", "kgateway" (deprecated), or "istio"

```bash
kubectl apply -f httproute.yaml -n ${NAMESPACE}
```

## Verify the Installation

* First, you should be able to list all helm releases to view the 3 charts got installed into your chosen namespace:

```bash
helm list -n ${NAMESPACE}
NAME        NAMESPACE   REVISION    UPDATED                                 STATUS      CHART                       APP VERSION
gaie-pd     llm-d-pd    1           2026-02-04 16:08:57.461356878 +0000 UTC deployed    inferencepool-v1.4.0   v1.4.0
infra-pd    llm-d-pd    1           2026-02-04 16:08:56.394680393 +0000 UTC deployed    llm-d-infra-v1.4.0          v0.4.0
ms-pd       llm-d-pd    1           2026-02-04 16:08:59.144726828 +0000 UTC deployed    llm-d-modelservice-v0.4.9   v0.4.0
```

* Out of the box with the example you should have the following resources:

```bash
kubectl get all -n ${NAMESPACE}
NAME                                                    READY   STATUS             RESTARTS   AGE
pod/gaie-pd-epp-6497968956-rthr7                        1/1     Running            0          6m5s
pod/infra-pd-inference-gateway-istio-5968644956-2sjc5   1/1     Running            0          6m6s
pod/ms-pd-llm-d-modelservice-decode-984f58799-jnbs2     2/2     Running            0          6m3s
pod/ms-pd-llm-d-modelservice-prefill-b4558dd49-2d7km    1/1     Running            0          6m3s
pod/ms-pd-llm-d-modelservice-prefill-b4558dd49-ghrc2    1/1     Running            0          6m3s
pod/ms-pd-llm-d-modelservice-prefill-b4558dd49-jn5ph    1/1     Running            0          6m3s
pod/ms-pd-llm-d-modelservice-prefill-b4558dd49-jtmwx    1/1     Running            0          6m3s
pod/poker                                               1/1     Running            0          12h

NAME                                       TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)             AGE
service/gaie-pd-epp                        ClusterIP   10.96.138.251   <none>        9002/TCP,9090/TCP   6m5s
service/gaie-pd-ip-bb618139                ClusterIP   None            <none>        54321/TCP           6m5s
service/infra-pd-inference-gateway-istio   ClusterIP   10.96.216.249   <none>        15021/TCP,80/TCP    6m6s

NAME                                               READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/gaie-pd-epp                        1/1     1            1           6m5s
deployment.apps/infra-pd-inference-gateway-istio   1/1     1            1           6m6s
deployment.apps/ms-pd-llm-d-modelservice-decode    1/1     1            1           6m3s
deployment.apps/ms-pd-llm-d-modelservice-prefill   4/4     4            4           6m3s

NAME                                                          DESIRED   CURRENT   READY   AGE
replicaset.apps/gaie-pd-epp-6497968956                        1         1         1       6m5s
replicaset.apps/infra-pd-inference-gateway-istio-5968644956   1         1         1       6m6s
replicaset.apps/ms-pd-llm-d-modelservice-decode-984f58799     1         1         1       6m3s
replicaset.apps/ms-pd-llm-d-modelservice-prefill-b4558dd49    4         4         4       6m3s

```

**_NOTE:_** This assumes no other guide deployments in your given `${NAMESPACE}` and you have not changed the default release names via the `${RELEASE_NAME}` environment variable.

## Using the stack

For instructions on getting started making inference requests see [our docs](../02_verifying_a_guide.md)

## Tuning Selective PD

Selective PD is a feature in the `inference-scheduler` within the context of prefill-decode disaggregation, although it is disabled by default. This feature enables routing to just decode even with the P/D deployed. To enable it, you will need to set `threshold` value for the `pd-profile-handler` plugin, in the [GAIE values file](./gaie-pd/values.yaml). You can see the value of this here:

```bash
cat gaie-pd/values.yaml | yq '.inferenceExtension.pluginsCustomConfig."pd-config.yaml"' | yq '.plugins[] | select(.type == "pd-profile-handler")'
type: pd-profile-handler
parameters:
  threshold: 0 # update this
  hashBlockSize: 5
```

Some examples in which you might want to do selective PD might include:

* When the prompt is short enough, the overhead of splitting inference into prefill and decode phases and transferring the KV cache between GPUs becomes larger than simply running both phases on a single decode inference worker.
* When Prefill units are at full capacity.

For information on this plugin, see our [`pd-profile-handler` docs in the inference-scheduler](https://github.com/llm-d/llm-d-inference-scheduler/blob/v0.3.0/docs/architecture.md?plain=1#L205-L210)

## Cleanup

To remove the deployment:

```bash
helmfile destroy -n ${NAMESPACE}
```

**_NOTE:_** If you set the `$RELEASE_NAME_POSTFIX` environment variable, your release names will be different from the command above: `infra-$RELEASE_NAME_POSTFIX`, `gaie-$RELEASE_NAME_POSTFIX` and `ms-$RELEASE_NAME_POSTFIX`.

### Cleanup HTTPRoute

Follow provider specific instructions for deleting HTTPRoute.

#### Cleanup for "agentgateway", "kgateway" (deprecated), or "istio"

```bash
kubectl delete -f httproute.yaml -n ${NAMESPACE}
```

## Customization

For information on customizing a guide and tips to build your own, see [our docs](../04_customizing_a_guide.md)
